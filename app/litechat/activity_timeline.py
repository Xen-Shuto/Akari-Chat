"""Versioned OODA records. Model reports and runtime receipts remain distinct."""
import hashlib
import json
import uuid
from .autonomy import encoded, now, text
from .file_editor import EditConflict
from tools.activity_provenance import capture, describe

PHASES=('observe','orient','decide','act','reflect')
STATES=('active','completed','partial','failed','cancelled','unknown')


class ActivityTimeline:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS activity_timelines(id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS activity_timelines_room ON activity_timelines(room)')
            db.execute('CREATE TABLE IF NOT EXISTS activity_timeline_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS activity_timeline_versions(id INTEGER PRIMARY KEY,room TEXT,timeline_id TEXT,payload TEXT,changed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS timeline_procedure_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,truncated INTEGER,PRIMARY KEY(room,request_id))')
            for row in db.execute("SELECT payload FROM activity_timelines WHERE json_extract(payload,'$.source_run')!='' AND json_extract(payload,'$.state')='active'").fetchall():
                old=json.loads(row[0]);item=dict(old,state='unknown',summary='再起動前の終了状態は未確認です。自動再実行しません。')
                self._write(db,item,old)

    def _get(self,db,room,key):
        row=db.execute('SELECT payload FROM activity_timelines WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームのタイムラインを指定してください。')
        return json.loads(row[0])

    def _write(self,db,item,old=None):
        item.update(revision=uuid.uuid4().hex,updated_at=now())
        if old:db.execute('INSERT INTO activity_timeline_versions(room,timeline_id,payload,changed_at) VALUES(?,?,?,?)',(item['room'],item['id'],encoded(old),now()))
        db.execute('INSERT OR REPLACE INTO activity_timelines VALUES(?,?,?)',(item['id'],item['room'],encoded(item)))

    def _header(self,item):
        return {key:value for key,value in item.items() if key!='steps'}|{'step_count':len(item['steps'])}

    def listing(self,room,offset=0,limit=10,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=5000 or type(limit) is not int or not 1<=limit<=20:raise ValueError('一覧の範囲が不正です。')
        with self.store.connect() as db:
            rows=db.execute("SELECT json_remove(payload,'$.steps'),json_array_length(payload,'$.steps') FROM activity_timelines WHERE room=? ORDER BY rowid DESC LIMIT 5000",(room,)).fetchall()
        items=[dict(json.loads(row[0]),step_count=row[1]) for row in rows]
        current=hashlib.sha256(encoded([(item['id'],item['revision']) for item in items]).encode()).hexdigest()
        if revision and revision!=current:raise EditConflict('タイムラインの一覧が変わりました。先頭から読み直してください。')
        return dict(entries=items[offset:offset+limit],more=len(items)>offset+limit,offset=offset,revision=current)

    def document(self,room,key,offset=0,limit=10,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=64 or type(limit) is not int or not 1<=limit<=20:raise ValueError('ステップの範囲が不正です。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            item=self._get(db,room,key)
            if 'input_references' in item:item['input_references']=describe(db,room,item['input_references'])
        if revision and revision!=item['revision']:raise EditConflict('タイムラインが変わりました。先頭から読み直してください。')
        return dict(self._header(item),steps=item['steps'][offset:offset+limit],more=len(item['steps'])>offset+limit,offset=offset)

    def change(self,room,action,request_id,key='',revision='',title='',phase='observe',summary='',step_id='',state='completed',related_id='',actor='model',checkpoint=lambda:None):
        self.store.room_path(room)
        if action not in ('start','append','edit','finish') or actor not in ('model','user'):raise ValueError('操作が不正です。')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        title=text(title,120);summary=text(summary,600,action in ('append','edit'))
        if phase not in PHASES or state not in ('completed','failed','cancelled'):raise ValueError('段階または状態が不正です。')
        signature=hashlib.sha256(encoded([action,key,revision,title,phase,summary,step_id,state,related_id,actor]).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM activity_timeline_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに別の内容が指定されています。')
                return json.loads(receipt[1]),False
            if action=='start':
                if key or revision or step_id:raise ValueError('新規作成には既存IDやrevisionを指定しないでください。')
                title=text(title,120,True)
                if related_id and db.execute('SELECT 1 FROM autonomy_documents WHERE room=? AND id=?',(room,related_id)).fetchone() is None:raise ValueError('関連する活動記録がありません。')
                if db.execute('SELECT COUNT(*) FROM activity_timelines WHERE room=?',(room,)).fetchone()[0]>=5000:raise ValueError('タイムラインは1ルーム5000件までです。')
                item=dict(id=uuid.uuid4().hex,room=room,title=title,state='active',summary=summary,related_id=related_id,source_run='',created_at=now(),steps=[])
                old=None
            else:
                old=self._get(db,room,key);item=json.loads(encoded(old))
                if revision!=old['revision']:raise EditConflict('タイムラインが変更されました。再読込してください。')
                if item['state']!='active':raise ValueError('終了済みのタイムラインへ追記・編集できません。')
                if action=='finish':
                    if item['source_run']:raise ValueError('自律実行の終了状態はアプリが記録します。')
                    item.update(state=state,summary=summary)
                elif action=='append':
                    self._append(item,phase,summary,actor)
                else:
                    found=next((step for step in item['steps'] if step['id']==step_id),None)
                    if found is None:raise ValueError('編集するステップがありません。')
                    if found['actor']=='system':raise ValueError('アプリが確認した記録は編集できません。注記を追加してください。')
                    found.update(phase=phase,summary=summary,edited_by=actor,edited_at=now())
            checkpoint();self._write(db,item,old)
            result=self._header(item)
            db.execute('INSERT INTO activity_timeline_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(result)))
        return result,True

    def _append(self,item,phase,summary,actor,evidence=None):
        if len(item['steps'])>=64:raise ValueError('ステップは1タイムライン64件までです。')
        summary=text(summary,600,True)
        item['steps'].append(dict(id=uuid.uuid4().hex,phase=phase,summary=summary,actor=actor,created_at=now(),evidence=evidence or {}))

    def start_runtime(self,room,run_id,activity_id='',working_reference=None,activity_revision=''):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=db.execute('SELECT payload FROM autonomy_runs WHERE room=? AND id=?',(room,run_id)).fetchone()
            if run is None:raise ValueError('実行記録がありません。')
            if db.execute('SELECT 1 FROM activity_timelines WHERE room=? AND id=?',(room,run_id)).fetchone():return self._header(self._get(db,room,run_id))
            if db.execute('SELECT COUNT(*) FROM activity_timelines WHERE room=?',(room,)).fetchone()[0]>=5000:raise ValueError('タイムライン上限です。')
            item=dict(id=run_id,room=room,title='自律実行',state='active',summary='',related_id='',source_run=run_id,created_at=now(),steps=[])
            references=capture(db,room,activity_id,working_reference,activity_revision)
            if references['activity'] or references['working']:
                item.update(related_id=activity_id,input_references=references)
            payload=json.loads(run[0]);memory='memory' in payload or 'routing' in payload
            if memory:item.update(title='会話から記憶草案を作成',source_type='memory')
            if 'routing' in payload:item['title']='会話草案から種類別の追記案を作成'
            if 'question_check' in payload:item.update(title='会話から問いの進展候補を判定',source_type='question')
            self._append(item,'observe','完了した会話を資料として確認待ちの記憶草案を準備しました。原本は未保存です。' if memory else '活動目的と現在の記録を入力として自律処理を準備しました。','system')
            self._write(db,item)
            return self._header(item)

    def record_tool(self,room,key,index,name,result,call_id):
        # No tool arguments, document bodies, keys or raw external output.
        truncated=len(name)>256;name=name[:256];call_id=call_id[:256]
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,room,key);item=json.loads(encoded(old))
            if item['state']!='active':raise ValueError('自律実行のタイムラインが終了しています。')
            self._append(item,'act',name+' → '+result['status'],'system',dict(tool=name,tool_name_truncated=truncated,status=result['status'],call_id=call_id,index=index))
            self._write(db,item,old)

    def start_sleep(self,db,room,run,imported=False):
        """App evidence for a nightly draft job; no prompt or generated body."""
        key=run['id']
        existing=db.execute('SELECT 1 FROM activity_timelines WHERE room=? AND id=?',(room,key)).fetchone()
        if existing:return self._header(self._get(db,room,key))
        if db.execute('SELECT COUNT(*) FROM activity_timelines WHERE room=?',(room,)).fetchone()[0]>=5000:
            raise ValueError('タイムライン上限です。')
        item=dict(id=key,room=room,title='夜間の記憶整理 / '+run['kind'],state='active',summary='',
                  related_id='',source_run=key,source_type='sleep',created_at=now(),steps=[])
        self._append(item,'observe','既存の夜間履歴を参照しました。実行開始を新たに確認した記録ではありません。' if imported else '指定時間帯と設定を確認し、確認待ち案の作成を開始しました。','system',
                     dict(night=run['night'],kind=run['kind'],config_revision=run['config_revision'],imported=imported))
        self._write(db,item)
        return self._header(item)

    def finish_sleep(self,db,room,run):
        if not db.execute('SELECT 1 FROM activity_timelines WHERE room=? AND id=?',(room,run['id'])).fetchone():return
        old=self._get(db,room,run['id'])
        if old.get('source_type')!='sleep':raise ValueError('夜間整理以外のタイムラインです。')
        if old['state']!='active':return
        item=json.loads(encoded(old));state=run['state']
        if state=='complete':
            summary='確認待ち案を作成しました。原本への保存や索引更新の完了を意味しません。'
            self._append(item,'act',summary,'system',dict(draft_id=run['draft_id'],draft_status='pending'))
        else:summary='夜間整理の終了状態: '+state+'。原本を自動更新した記録ではありません。'
        self._append(item,'reflect',summary,'system',dict(runtime_state=state))
        item.update(state={'complete':'completed','expired':'cancelled','skipped':'cancelled'}.get(state,state),summary=summary)
        self._write(db,item,old)

    def finish_runtime(self,db,room,key,state,summary):
        old=self._get(db,room,key);item=json.loads(encoded(old))
        self._append(item,'reflect','自律処理の終了状態: '+state+'。AIの応答本文が正しいことを保証する記録ではありません。','system',dict(runtime_state=state))
        item.update(state='completed' if state=='succeeded' else state,summary=summary[:600])
        self._write(db,item,old)

    def derive_procedure(self,autonomy,room,key,revision,title,request_id,checkpoint=lambda:None):
        self.store.room_path(room)
        title=text(title,120,True)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        signature=hashlib.sha256(encoded([key,revision,title]).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result,truncated FROM timeline_procedure_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに別の手順候補が指定されています。')
                return json.loads(receipt[1]),False,bool(receipt[2])
            item=self._get(db,room,key)
            if item['revision']!=revision:raise EditConflict('元タイムラインが変更されています。')
            if not item['source_run'] or item['state']!='completed' or item.get('source_type') in ('sleep','memory','question'):raise ValueError('正常終了した通常の自律実行のタイムラインが必要です。')
            actions=[step['evidence'] for step in item['steps'] if step['actor']=='system' and step['phase']=='act'
                     and step.get('evidence',{}).get('status') in ('ok','no_change','pending')]
            if not actions:raise ValueError('手順候補にできる、アプリ確認済みのツール操作がありません。')
            steps=[entry['tool']+'を実行し結果を確認する（元の状態: '+entry['status']+('、人の確認前で未反映' if entry['status']=='pending' else '')+'）' for entry in actions[:10]]
            body='アプリの実行記録から抽出した手順候補です。適用条件・成功条件・再実行の安全性を確認してから利用してください。'
            if len(actions)>10:body+='\n全'+str(len(actions))+'操作のうち先頭10操作のみを抽出しています。'
            details={'steps':steps,'preconditions':'要確認','success_criteria':'要確認','source_timeline':key,'source_revision':revision}
            references=item.get('input_references',{})
            activity=references.get('activity')
            if activity:
                row=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=?',(room,activity['id'])).fetchone()
                if row:
                    details['links']=[activity['id']]
                    if json.loads(row[0])['revision']!=activity['revision']:body+='\n元の活動は実行開始時から変更されています。関連先は現在の記録、元の版はタイムラインで確認してください。'
                else:body+='\n元の活動が見つからないため、活動へのリンクは追加していません。'
            working=references.get('working')
            if working and autonomy.slots:
                try:slot=autonomy.slots.preview(room,working['slot'])
                except (ValueError,OSError):slot={}
                if slot.get('exists') and slot.get('revision')==working['revision']:details['working_slots']=[working['slot']]
                else:body+='\n実行時の作業メモが変更済みまたは未配置のため、自動の関連付けは追加していません。'
            document,changed=autonomy.save(room,'procedure',title,body,state='paused',request_id=request_id,
                checkpoint=checkpoint,details=details,_db=db)
            db.execute('INSERT INTO timeline_procedure_receipts VALUES(?,?,?,?,?)',(room,request_id,signature,encoded(document),int(len(actions)>10)))
        return document,changed,len(actions)>10


    def record_night_save(self,db,room,key,state):
        if not db.execute('SELECT 1 FROM activity_timelines WHERE room=? AND id=?',(room,key)).fetchone():return
        old=self._get(db,room,key)
        if old.get('source_type')!='sleep':raise ValueError('夜間整理のタイムラインではありません。')
        item=json.loads(encoded(old))
        summary='夜間整理案をバックアップ付きで自動保存しました。索引更新の完了とは別です。' if state=='saved' else '夜間整理案の自動保存: '+state+'。保存先・草案・バックアップを確認してください。'
        self._append(item,'act',summary,'system',dict(auto_save_state=state))
        item['summary']=summary;item['auto_save_state']=state;self._write(db,item,old)

    def record_memory_save(self,db,room,run_id,key,state,kind,draft_id):
        if not db.execute('SELECT 1 FROM activity_timelines WHERE room=? AND id=?',(room,run_id)).fetchone():return
        old=self._get(db,room,run_id);item=json.loads(encoded(old))
        summary='会話後の記憶案を自動保存しました。索引更新の完了とは別です。' if state=='saved' else '会話後の記憶案の自動保存: '+state
        self._append(item,'act',summary,'system',dict(memory_save_id=key,auto_save_state=state,kind=kind,draft_id=draft_id));self._write(db,item,old)
