"""Versioned group transcript cuts; original rows remain available for auditing."""
import json
from litechat.autonomy import now
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict


def message_id(value):
    if type(value) is not int or not 1<=value<2**63:raise ValueError('発言IDが不正です。')
    return value


class GroupHistory:
    def __init__(self,app,groups):
        self.app,self.store,self.groups=app,app.store,groups
        with self.store.connect() as db:
            if 'deleted' not in {r[1] for r in db.execute('PRAGMA table_info(chat_group_messages)')}:
                db.execute('ALTER TABLE chat_group_messages ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS chat_group_changes(id INTEGER PRIMARY KEY,group_id TEXT,request_id TEXT,signature TEXT,before_payload TEXT,result TEXT,created TEXT,UNIQUE(group_id,request_id))')

    def revision(self,db,key):
        maximum=db.execute('SELECT COALESCE(MAX(id),0) FROM chat_group_messages WHERE group_id=? AND deleted=0',(key,)).fetchone()[0]
        change=db.execute('SELECT COALESCE(MAX(id),0) FROM chat_group_changes WHERE group_id=?',(key,)).fetchone()[0]
        return fingerprint([key,maximum]+([change] if change else []))

    def target(self,db,key,number):
        message_id(number)
        row=db.execute('SELECT * FROM chat_group_messages WHERE group_id=? AND id=? AND deleted=0',(key,number)).fetchone()
        if row is None:raise EditConflict('発言が見つかりません。共有履歴を読み直してください。')
        return dict(row)

    def prepare(self,db,key,action,number,text=''):
        target=self.target(db,key,number)
        if action=='edit':
            if target['role']!='user':raise ValueError('編集できるのはユーザーの発言だけです。')
            if target['content']==text:raise ValueError('本文に変更がありません。回答の再生成を利用してください。')
        elif action=='regenerate':
            if target['role']!='assistant':raise ValueError('再生成するAIの発言を選んでください。')
            if not db.execute("SELECT 1 FROM chat_group_messages WHERE group_id=? AND id<? AND deleted=0 AND role='user' AND status='complete'",(key,number)).fetchone():raise ValueError('再生成の元になるユーザー発言がありません。')
        elif action!='remove':raise ValueError('共有ログの操作が不正です。')
        rows,raw=self.tail(db,key,number)
        return target,rows,raw

    def tail(self,db,key,number):
        count,size=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(content AS BLOB))),0) FROM (SELECT content FROM chat_group_messages WHERE group_id=? AND id>=? AND deleted=0 ORDER BY id LIMIT 1001)',(key,number)).fetchone()
        if count>1000 or size>2*1024*1024:raise ValueError('一度の退避は1000発言・2MiBまでです。より後の発言から整理してください。')
        rows=[dict(r) for r in db.execute('SELECT * FROM chat_group_messages WHERE group_id=? AND id>=? AND deleted=0 ORDER BY id',(key,number))]
        raw=json.dumps(rows,ensure_ascii=False)
        if len(raw.encode('utf-8'))>2*1024*1024:raise ValueError('退避するデータが2MiBを超えています。')
        return rows,raw

    def apply(self,db,key,action,number,request_id,signature,prepared,text='',checkpoint=lambda:None,run_id=''):
        _,rows,raw=prepared;checkpoint()
        if action=='edit':
            db.execute("UPDATE chat_group_messages SET content=?,status='complete' WHERE group_id=? AND id=?",(text,key,number))
            db.execute('UPDATE chat_group_messages SET deleted=1 WHERE group_id=? AND id>? AND deleted=0',(key,number));retired=len(rows)-1
        else:
            db.execute('UPDATE chat_group_messages SET deleted=1 WHERE group_id=? AND id>=? AND deleted=0',(key,number));retired=len(rows)
        cursor=db.execute('INSERT INTO chat_group_changes(group_id,request_id,signature,before_payload,result,created) VALUES(?,?,?,?,?,?)',(key,request_id,signature,raw,'{}',now())).lastrowid
        result=dict(archive_id=cursor,action=action,target_id=number,retired=retired,history_revision=self.revision(db,key),replayed=False,
            snapshot_version=1,group_revision=self.groups.document(key,db)['revision'],before_hash=fingerprint(rows),after_hash=fingerprint(self.tail(db,key,number)[0]),run_id=run_id)
        db.execute('UPDATE chat_group_changes SET result=? WHERE id=?',(json.dumps(result),cursor))
        return result

    def remove(self,key,revision,history_revision,number,request_id,checkpoint=lambda:None):
        # The owner validates the same operation-id format used for group sends.
        from .group_chat import operation
        operation(request_id);message_id(number);signature=fingerprint([revision,history_revision,number,'remove'])
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成が終わってから共有ログを整理してください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM chat_group_changes WHERE group_id=? AND request_id=?',(key,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
                return dict(json.loads(receipt[1]),replayed=True)
            group=self.groups.document(key,db)
            if group['revision']!=revision or self.revision(db,key)!=history_revision:raise EditConflict('設定または共有ログが変更されています。読み直してください。')
            prepared=self.prepare(db,key,'remove',number)
            return self.apply(db,key,'remove',number,request_id,signature,prepared,checkpoint=checkpoint)

    def finished(self,db,key,archive_id,state):
        row=db.execute('SELECT result FROM chat_group_changes WHERE group_id=? AND id=?',(key,archive_id)).fetchone()
        if row is None:raise ValueError('生成に対応するログ操作記録がありません。')
        item=json.loads(row[0]);item.update(final_history_revision=self.revision(db,key),final_state=state,after_hash=fingerprint(self.tail(db,key,item['target_id'])[0]))
        db.execute('UPDATE chat_group_changes SET result=? WHERE group_id=? AND id=?',(json.dumps(item),key,archive_id))

    def eligible(self,item,group_revision,history_revision):
        if item.get('snapshot_version')!=1 or item.get('action') not in ('edit','regenerate','remove'):return False
        if item.get('group_revision')!=group_revision:return False
        if item.get('run_id'):
            if item.get('final_state') not in ('succeeded','error','cancelled'):return False
            expected=item.get('final_history_revision')
        else:expected=item.get('history_revision')
        return expected==history_revision

    def restore(self,key,revision,history_revision,archive_id,request_id,checkpoint=lambda:None):
        from .group_chat import operation
        operation(request_id);message_id(archive_id)
        signature=fingerprint([key,revision,history_revision,archive_id,'restore']);receipt_id='restore:'+request_id
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成が終わってから共有ログを戻してください。')
            db.execute('BEGIN IMMEDIATE');group=self.groups.document(key,db)
            receipt=db.execute('SELECT signature,result FROM chat_group_changes WHERE group_id=? AND request_id=?',(key,receipt_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDの復元内容が異なります。')
                return dict(json.loads(receipt[1]),replayed=True)
            latest=db.execute('SELECT id,result,substr(before_payload,1,2097153),length(CAST(before_payload AS BLOB)) FROM chat_group_changes WHERE group_id=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
            if latest is None or latest[0]!=archive_id:raise EditConflict('最新のログ操作だけを戻せます。履歴を確認してください。')
            item=json.loads(latest[1]);current=self.revision(db,key)
            if group['revision']!=revision or current!=history_revision or not self.eligible(item,revision,current):raise EditConflict('操作後の設定・共有ログ・生成状態が変わっています。自動で戻せません。')
            if latest[3]>2*1024*1024:raise ValueError('退避データが上限を超えています。')
            before=json.loads(latest[2])
            if not isinstance(before,list) or not 1<=len(before)<=1000 or fingerprint(before)!=item.get('before_hash'):raise EditConflict('退避データの一致を確認できません。')
            active,raw=self.tail(db,key,item['target_id'])
            if fingerprint(active)!=item.get('after_hash'):raise EditConflict('現在の本文が操作直後と異なります。上書きしません。')
            for original in before:
                row=db.execute('SELECT * FROM chat_group_messages WHERE group_id=? AND id=?',(key,original['id'])).fetchone()
                if row is None:raise EditConflict('元の発言が見つかりません。')
                if not (item['action']=='edit' and original['id']==item['target_id']) and dict(row)!=dict(original,deleted=1):raise EditConflict('退避された発言が変更されています。上書きしません。')
            checkpoint()
            db.execute('UPDATE chat_group_messages SET deleted=1 WHERE group_id=? AND id>=? AND deleted=0',(key,item['target_id']))
            for original in before:
                db.execute('UPDATE chat_group_messages SET run_id=?,role=?,speaker=?,name=?,content=?,status=?,created=?,deleted=? WHERE group_id=? AND id=?',tuple(original[k] for k in ('run_id','role','speaker','name','content','status','created','deleted'))+(key,original['id']))
            cursor=db.execute('INSERT INTO chat_group_changes(group_id,request_id,signature,before_payload,result,created) VALUES(?,?,?,?,?,?)',(key,receipt_id,signature,raw,'{}',now())).lastrowid
            result=dict(archive_id=cursor,action='restore',target_id=item['target_id'],restored=len(before),retired=len(active),source_archive_id=archive_id,history_revision=self.revision(db,key),replayed=False)
            db.execute('UPDATE chat_group_changes SET result=? WHERE id=?',(json.dumps(result),cursor))
            return result
