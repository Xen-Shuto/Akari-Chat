"""Human-reviewed research consolidation. Sources and subscriptions are retained."""
import hashlib
import json
import re
import uuid
from .autonomy import encoded,now
from .file_editor import EditConflict
from tools.research_transfers import selected_ids,move as move_subscriptions,restore as restore_subscriptions


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise ValueError('研究または統合記録のIDが不正です。')
    return value


class ResearchMerges:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_merges(id TEXT PRIMARY KEY,room TEXT,request_id TEXT,signature TEXT,payload TEXT,UNIQUE(room,request_id))')
            db.execute('CREATE INDEX IF NOT EXISTS research_merges_room ON research_merges(room)')

    def _thread(self,db,room,key):
        identifier(key)
        row=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=? AND kind="thread"',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームの研究スレッドを選んでください。')
        return json.loads(row[0])

    def _preview(self,db,room,target,source):
        if target==source:raise ValueError('異なる研究を選んでください。')
        left=self._thread(db,room,target);right=self._thread(db,room,source)
        def references(key):
            rows=db.execute("SELECT id,json_extract(payload,'$.revision') AS revision,json_extract(payload,'$.enabled') AS enabled,json_extract(payload,'$.source') AS source,json_extract(payload,'$.focus') AS focus,json_extract(payload,'$.archived') AS archived FROM research_subscriptions WHERE room=? AND json_extract(payload,'$.thread_id')=? ORDER BY id",(room,key)).fetchall()
            # The subscription structure is versioned independently; do not redirect it.
            incoming=db.execute("SELECT id,json_extract(payload,'$.revision') AS revision FROM autonomy_documents WHERE room=? AND EXISTS (SELECT 1 FROM json_each(json_extract(payload,'$.details.links')) WHERE value=?) ORDER BY id",(room,key)).fetchall()
            return dict(subscriptions=[dict(row) for row in rows],incoming=[dict(row) for row in incoming])
        refs=dict(target=references(target),source=references(source))
        version=hashlib.sha256(encoded([left,right,refs]).encode()).hexdigest()
        details=dict(left.get('details',{}));other=right.get('details',{})
        for field in ('open_questions','links','working_slots'):
            details[field]=list(dict.fromkeys(details.get(field,[])+other.get(field,[])))
        details['links']=[key for key in details['links'] if key!=target]
        findings=other.get('findings','')
        details['findings']=details.get('findings','')+('\n\n【統合元: '+right['title']+'】\n'+findings if findings else '')
        extra='\n\n【統合元: '+right['title']+'】\n'+right['body']
        if right.get('next_action'):extra+='\n\n元の次の一手: '+right['next_action']
        proposal=dict(title=left['title'],body=left['body']+extra,next_action=left.get('next_action',''),state=left['state'],details=details)
        warnings=[]
        for label,value,limit in [('本文',proposal['body'],4000),('判明事項',details['findings'],1500),('未解決の問い',details['open_questions'],10),('関連活動',details['links'],10),('作業メモ',details['working_slots'],3)]:
            if len(value)>limit:warnings.append(label+'が上限を超えています。保存前に内容を整理してください。')
        if refs['source']['subscriptions']:warnings.append('統合元に研究購読があります。元の研究を残します。指定したローカル購読だけ停止して移管できます。未選択の購読は変えません。')
        return dict(room=room,target=left,source=right,proposal=proposal,references=refs,revision=version,warnings=warnings)

    def preview(self,room,target,source):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN');return self._preview(db,room,target,source)

    def apply(self,room,target,source,revision,edited,request_id,checkpoint=lambda:None,transfer_ids=None):
        self.store.room_path(room)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        if not isinstance(edited,dict) or set(edited)!={'title','body','next_action','state','details'}:raise ValueError('統合案の項目が不正です。')
        transfer_ids=selected_ids(transfer_ids)
        if transfer_ids and edited['state']!='active':raise ValueError('購読を移管する統合先は進行中にしてください。')
        signature=hashlib.sha256(encoded([target,source,revision,edited]+([transfer_ids] if transfer_ids else [])).encode()).hexdigest()
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから研究を統合してください。')
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT signature,payload FROM research_merges WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if old:
                if old[0]!=signature:raise EditConflict('同じ操作IDに別の統合案が指定されています。')
                return json.loads(old[1]),False
            snapshot=self._preview(db,room,target,source)
            if snapshot['revision']!=revision:raise EditConflict('研究または参照関係が変更されています。比較し直してください。')
            if db.execute('SELECT COUNT(*) FROM research_merges WHERE room=?',(room,)).fetchone()[0]>=500:raise ValueError('統合記録は1ルーム500件までです。')
            key=uuid.uuid4().hex
            document,_=self.app.autonomy.save(room,'thread',key=target,revision=snapshot['target']['revision'],request_id='research-merge:'+key,checkpoint=checkpoint,_db=db,**edited)
            moved=move_subscriptions(db,room,source,target,transfer_ids,checkpoint)
            result=dict(id=key,room=room,state='applied',revision=uuid.uuid4().hex,target_before=snapshot['target'],source=snapshot['source'],target_after=document,created_at=now())
            if moved:result['subscription_moves']=moved
            db.execute('INSERT INTO research_merges VALUES(?,?,?,?,?)',(key,room,request_id,signature,encoded(result)))
        return result,True

    def history(self,room,before=0):
        self.store.room_path(room)
        if type(before) is not int or not 0<=before<2**63:raise ValueError('履歴の範囲が不正です。')
        with self.store.connect() as db:
            rows=db.execute("SELECT rowid AS cursor,id,json_extract(payload,'$.state') AS state,json_extract(payload,'$.revision') AS revision,json_extract(payload,'$.target_after.title') AS title,json_extract(payload,'$.source.title') AS source_title,json_extract(payload,'$.created_at') AS created_at,COALESCE(json_array_length(payload,'$.subscription_moves'),0) AS moved_subscriptions FROM research_merges WHERE room=? AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT 21",(room,before,before)).fetchall()
        return dict(entries=[dict(row) for row in rows[:20]],next_before=rows[19]['cursor'] if len(rows)>20 else 0)

    def undo(self,room,key,revision,checkpoint=lambda:None):
        self.store.room_path(room);identifier(key)
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから研究を戻してください。')
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM research_merges WHERE room=? AND id=?',(room,key)).fetchone()
            if row is None:raise ValueError('統合記録が見つかりません。')
            item=json.loads(row[0])
            if item['state']=='undone':return item,False
            if revision!=item['revision']:raise EditConflict('統合記録が変更されています。再読込してください。')
            current=self._thread(db,room,item['target_after']['id'])
            if current['revision']!=item['target_after']['revision']:raise EditConflict('統合先は後から編集されています。自動で戻せません。')
            restored=dict(item['target_before'],revision=uuid.uuid4().hex,updated_at=now())
            for linked in restored.get('details',{}).get('links',[]):
                if db.execute('SELECT 1 FROM autonomy_documents WHERE room=? AND id=?',(room,linked)).fetchone() is None:raise EditConflict('復元する関連先が見つかりません。内容を確認してください。')
            for slot in restored.get('details',{}).get('working_slots',[]):
                if not self.app.slots.preview(room,slot)['exists']:raise EditConflict('復元する作業メモが見つかりません。内容を確認してください。')
            subscriptions=restore_subscriptions(db,room,item.get('subscription_moves',[]),checkpoint)
            checkpoint()
            db.execute('INSERT INTO autonomy_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,current['id'],encoded(current),now()))
            db.execute('UPDATE autonomy_documents SET payload=? WHERE room=? AND id=?',(encoded(restored),room,restored['id']))
            if subscriptions:item['restored_subscriptions']=subscriptions
            item.update(state='undone',revision=uuid.uuid4().hex,restored=restored,undone_at=now())
            db.execute('UPDATE research_merges SET payload=? WHERE room=? AND id=?',(encoded(item),room,key))
        return item,True
