"""Explicit shared user appearance; private room data and originals are retained."""
import copy
import json
import re
import uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict

KEYS=('user_profile','user_closet')


class SharedCloset:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS shared_closet(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS shared_closet_modes(room TEXT PRIMARY KEY,enabled INTEGER,revision TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS shared_closet_versions(id INTEGER PRIMARY KEY,payload TEXT,action TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS shared_closet_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def snapshot(self,room,db):
        mode=db.execute('SELECT enabled,revision FROM shared_closet_modes WHERE room=?',(room,)).fetchone()
        row=db.execute('SELECT payload FROM shared_closet WHERE id=1').fetchone()
        data=json.loads(row[0]) if row else {'user_profile':{'enabled':False,'base':{'description':''},'current':{'note':'','worn':[]}},'user_closet':{'items':[]}}
        return dict(enabled=bool(mode and mode[0]),mode_revision=mode[1] if mode else 'unconfigured',data=data,revision=fingerprint(data),exists=bool(row))

    def view(self,room):
        room=self.store.room_path(room).name
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN')
            return dict(self.snapshot(room,db),room=room,history=[dict(r) for r in db.execute('SELECT id,action,created FROM shared_closet_versions ORDER BY id DESC LIMIT 20')])

    def write(self,db,old,data,action):
        if old==data and db.execute('SELECT 1 FROM shared_closet WHERE id=1').fetchone():return
        # Save only portable text fields. Room-local asset paths and inventory links stay private.
        self.validate(data)
        db.execute('INSERT INTO shared_closet_versions(payload,action) VALUES(?,?)',(json.dumps(old,ensure_ascii=False),action))
        db.execute('INSERT OR REPLACE INTO shared_closet VALUES(1,?)',(json.dumps(data,ensure_ascii=False),))

    def portable(self,data):
        profile=data['user_profile'];current=profile.get('current',{})
        return dict(user_profile=dict(enabled=profile.get('enabled') is True,base=dict(description=profile.get('base',{}).get('description','')),current=dict(note=current.get('note',''),worn=list(current.get('worn',[])))),
                    user_closet=dict(items=[{key:item.get(key,'') for key in ('id','name','part','description')} for item in data['user_closet']['items']]))

    def validate(self,data):
        from litechat.lifestyle import text
        if set(data)!=set(KEYS):raise ValueError('共通衣装の形式が不正です。')
        p=data['user_profile'];items=data['user_closet']['items'];ids=set()
        if type(p.get('enabled')) is not bool or not isinstance(items,list) or len(items)>1000:raise ValueError('共通衣装の形式が不正です。')
        text(p['base']['description']);text(p['current']['note'])
        for i in items:
            if not isinstance(i.get('id'),str) or not i['id'] or i['id'] in ids:raise ValueError('共通衣装IDが不正です。')
            ids.add(i['id'])
            if not text(i.get('name'),200).strip():raise ValueError('衣装名が必要です。')
            text(i.get('part',''),100);text(i.get('description',''))
        worn=p['current']['worn']
        if not isinstance(worn,list) or any(not isinstance(k,str) or k not in ids for k in worn) or len(set(worn))!=len(worn):raise ValueError('着用する共通衣装が見つかりません。')
        if len(json.dumps(data,ensure_ascii=False).encode())>1048576:raise ValueError('共通衣装は合計1MiBまでです。')

    def change(self,app,room,action,revision,mode_revision,life_revision,request_id,version_id=None,checkpoint=lambda:None):
        room=self.store.room_path(room).name
        if action not in ('enable','disable','copy-from-room','restore'):raise ValueError('共通衣装の操作が不正です。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        if action=='restore' and (type(version_id) is not int or version_id<1):raise ValueError('復元対象を選択してください。')
        signature=fingerprint([action,revision,mode_revision,life_revision,version_id])
        with app.lock,self.store.lock,self.store.connect() as db:
            if app.closing or app.job or getattr(app,'conversation_waiters',0):raise ValueError('会話・生成待機中または終了処理中です。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM shared_closet_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDで別の更新はできません。')
                return dict(json.loads(receipt[1]),replayed=True)
            old=self.snapshot(room,db);life=self.store.lifestyle.state(room,db=db)
            if old['revision']!=revision or old['mode_revision']!=mode_revision or life['revision']!=life_revision:raise EditConflict('共通衣装または生活データが変更されています。再読込してください。')
            if action=='copy-from-room':
                private=self.store.lifestyle.private(room,db)[0]
                self.write(db,old['data'],self.portable(private),'copy-from-room:'+room)
            elif action=='restore':
                row=db.execute('SELECT payload FROM shared_closet_versions WHERE id=?',(version_id,)).fetchone()
                if row is None:raise ValueError('復元対象がありません。')
                self.write(db,old['data'],json.loads(row[0]),'restore:'+room)
            else:
                enabled=action=='enable'
                if enabled and not old['exists']:raise ValueError('先にルーム専用の外見・衣装を共通へコピーしてください。')
                if enabled!=old['enabled']:db.execute('INSERT OR REPLACE INTO shared_closet_modes VALUES(?,?,?)',(room,enabled,uuid.uuid4().hex))
            checkpoint()
            result=dict(ok=True,replayed=False)
            db.execute('INSERT INTO shared_closet_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
            return result
