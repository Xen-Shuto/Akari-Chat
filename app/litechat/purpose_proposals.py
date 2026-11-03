"""Purpose changes remain proposals until a human review commits atomically."""
import hashlib
import json
import uuid
from .autonomy import encoded,now,text
from .activity_details import PURPOSE_FIELDS,normalize
from .file_editor import EditConflict


class PurposeProposals:
    def __init__(self,store,autonomy):
        self.store,self.autonomy=store,autonomy
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS purpose_proposals(id TEXT PRIMARY KEY,room TEXT,state TEXT,request_id TEXT,signature TEXT,payload TEXT,UNIQUE(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS purpose_review_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def _current(self,db,room):
        rows=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND kind="purpose" LIMIT 2',(room,)).fetchall()
        if len(rows)>1:raise ValueError('活動目的が複数あります。状態を確認してください。')
        return json.loads(rows[0][0]) if rows else None

    def profile(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:item=self._current(db,room)
        return dict(document=item,revision=item['revision'] if item else 'absent')

    def listing(self,room,offset=0,limit=10,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=1000 or type(limit) is not int or not 1<=limit<=20:raise ValueError('提案一覧の範囲が不正です。')
        with self.store.connect() as db:
            rows=db.execute("SELECT json_remove(payload,'$.before','$.after') FROM purpose_proposals WHERE room=? ORDER BY rowid DESC LIMIT 1000",(room,)).fetchall()
        items=[json.loads(row[0]) for row in rows]
        current=hashlib.sha256(encoded([(item['id'],item['revision']) for item in items]).encode()).hexdigest()
        if revision and current!=revision:raise EditConflict('提案一覧が変わりました。先頭から読み直してください。')
        return dict(entries=items[offset:offset+limit],more=len(items)>offset+limit,offset=offset,revision=current)

    def document(self,room,key):
        self.store.room_path(room)
        with self.store.connect() as db:row=db.execute('SELECT payload FROM purpose_proposals WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('このルームの提案が見つかりません。')
        return json.loads(row[0])

    def propose(self,room,title,body,reason,revision,request_id,details=None,checkpoint=lambda:None):
        self.store.room_path(room)
        title=text(title,120,True);body=text(body,4000);reason=text(reason,600,True)
        if details is None:details={}
        if not isinstance(details,dict) or set(details)-PURPOSE_FIELDS:raise ValueError('提案できる詳細は活動目的のプロフィール項目です。')
        details=normalize('purpose',details)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        signature=hashlib.sha256(encoded([title,body,reason,revision,details]).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT signature,payload FROM purpose_proposals WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if previous:
                if previous[0]!=signature:raise EditConflict('同じ操作IDに異なる提案が指定されました。')
                return json.loads(previous[1]),False
            before=self._current(db,room)
            if revision!=(before['revision'] if before else 'absent'):raise EditConflict('活動目的が変更されています。現在の目的を読み直してください。')
            if db.execute('SELECT COUNT(*) FROM purpose_proposals WHERE room=?',(room,)).fetchone()[0]>=1000:raise ValueError('提案は1ルーム1000件までです。')
            after=dict(title=title,body=body,details=dict(before.get('details',{}) if before else {},**details),next_action=before.get('next_action','') if before else '',state=before['state'] if before else 'active')
            item=dict(id=uuid.uuid4().hex,room=room,state='pending',title=title,reason=reason,before=before,after=after,revision=uuid.uuid4().hex,created_at=now())
            checkpoint()
            db.execute('INSERT INTO purpose_proposals VALUES(?,?,?,?,?,?)',(item['id'],room,'pending',request_id,signature,encoded(item)))
        return item,True

    def review(self,room,key,revision,action,request_id,edited=None,checkpoint=lambda:None):
        self.store.room_path(room)
        if action not in ('approve','reject') or not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('確認操作が不正です。')
        if edited is not None and (not isinstance(edited,dict) or set(edited)-{'title','body','details','state','next_action'}):raise ValueError('編集項目が不正です。')
        signature=hashlib.sha256(encoded([key,revision,action,edited]).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM purpose_review_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに異なる確認内容が指定されています。')
                return json.loads(receipt[1])
            row=db.execute('SELECT payload FROM purpose_proposals WHERE room=? AND id=?',(room,key)).fetchone()
            if row is None:raise ValueError('このルームの提案が見つかりません。')
            proposal=json.loads(row[0])
            if proposal['revision']!=revision:raise EditConflict('提案が変更されています。再読込してください。')
            if proposal['state']!='pending':raise EditConflict('この提案は確認済みです。')
            document=None
            if action=='approve':
                current=self._current(db,room);before=proposal['before']
                if (current is None)!=(before is None) or current is not None and (current['id']!=before['id'] or current['revision']!=before['revision']):raise EditConflict('提案後に活動目的が変更されました。上書きせず、現在の目的に対する新しい提案を作成してください。')
                values=dict(proposal['after'],**(edited or {}))
                token=hashlib.sha256(encoded(['purpose-review',room,key,request_id]).encode()).hexdigest()
                document,_=self.autonomy.save(room,'purpose',key=current['id'] if current else '',revision=current['revision'] if current else '',request_id=token,
                    allow_purpose=True,checkpoint=checkpoint,_db=db,**values)
            else:checkpoint()
            proposal.update(state='approved' if action=='approve' else 'rejected',revision=uuid.uuid4().hex,reviewed_at=now(),approved_revision=document['revision'] if document else None)
            db.execute('UPDATE purpose_proposals SET state=?,payload=? WHERE room=? AND id=?',(proposal['state'],encoded(proposal),room,key))
            result=dict(proposal=proposal,document=document)
            db.execute('INSERT INTO purpose_review_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(result)))
        return result
