"""Working topic dormancy metadata; never moves or rewrites source documents."""
import hashlib
import json
import os
import re
from contextlib import nullcontext

from litechat.file_editor import EditConflict
from litechat.memory_context import working_location
from litechat.working_slots import slot_name


class WorkingStates:
    def __init__(self,slots):
        self.slots,self.store=slots,slots.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS working_states(room TEXT,path TEXT,state TEXT NOT NULL,version INTEGER NOT NULL,body_revision TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(room,path))')
            db.execute('CREATE TABLE IF NOT EXISTS working_state_events(id INTEGER PRIMARY KEY,room TEXT,path TEXT,request_id TEXT,fingerprint TEXT,result TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(room,request_id))')

    @staticmethod
    def key(path):return os.path.normcase(path).replace('\\','/')

    def document(self,room,name):
        name=slot_name(name)
        return self.slots.editor.read_snapshot(room,'working_slot','ワーキングメモリ',self.slots.location(room,name),name)

    def describe(self,room,name,document=None,db=None):
        room=self.store.room_path(room).resolve().name
        doc=document if document is not None else self.document(room,name)
        if db is None:
            with self.store.connect() as conn:return self.describe(room,name,doc,conn)
        config,data=self.slots.config(room)
        _,path=working_location(self.store.room_path(room),data.get('override_settings',{}))
        row=db.execute('SELECT * FROM working_states WHERE room=? AND path=?',(room,self.key(doc['path']))).fetchone()
        state,version=(row['state'],row['version']) if row else ('active',0)
        revision=hashlib.sha256(json.dumps([room,self.key(doc['path']),doc['revision'],config['revision'],state,version]).encode()).hexdigest()
        return dict(state=state,state_version=version,state_revision=revision,state_updated=row['updated'] if row else None,
                    config_revision=config['revision'],is_selected=self.key(path)==self.key(doc['path']),state_source_changed=bool(row and row['body_revision']!=doc['revision']))

    def annotate(self,room,entries):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:
            states={r['path']:(r['state'],r['version']) for r in db.execute('SELECT path,state,version FROM working_states WHERE room=?',(room,))}
        return [dict(e,state=states.get(self.key(e['path']),('active',0))[0],
                     state_version=states.get(self.key(e['path']),('active',0))[1],
                     selectable=e['selectable'] and states.get(self.key(e['path']),('active',0))[0]=='active') for e in entries]

    def change(self,room,name,state,revision,request_id,checkpoint=lambda:None,_db=None):
        room=self.store.room_path(room).resolve().name;name=slot_name(name)
        if state not in ('active','dormant'):raise ValueError('状態はactiveまたはdormantです。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        path=self.key(self.slots.location(room,name))
        signature=hashlib.sha256(json.dumps([path,state,revision]).encode()).hexdigest()
        with self.store.lock,self.store.connect() if _db is None else nullcontext(_db) as db:
            if _db is None:db.execute('BEGIN IMMEDIATE')
            checkpoint()
            receipt=db.execute('SELECT fingerprint,result FROM working_state_events WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt['fingerprint']!=signature:raise EditConflict('同じ操作IDで別の状態変更はできません。')
                return {**json.loads(receipt['result']),'replayed':True}
            doc=self.document(room,name);before=self.describe(room,name,doc,db)
            if self.key(doc['path'])!=path:raise EditConflict('話題の保存先が変わりました。再読込してください。')
            if not doc['exists']:raise ValueError('本文がある話題を選んでください。')
            if before['state_revision']!=revision:raise EditConflict('本文・状態・選択設定が変わりました。再読込してください。')
            if state=='dormant' and before['is_selected']:raise ValueError('選択中の話題は休眠できません。先に別の話題へ切り替えてください。')
            changed=state!=before['state']
            if changed:
                db.execute('INSERT INTO working_states(room,path,state,version,body_revision) VALUES(?,?,?,?,?) ON CONFLICT(room,path) DO UPDATE SET state=excluded.state,version=excluded.version,body_revision=excluded.body_revision,updated=CURRENT_TIMESTAMP',
                           (room,path,state,before['state_version']+1,doc['revision']))
            checkpoint()
            latest=self.document(room,name);current=self.describe(room,name,latest,db)
            if latest['revision']!=doc['revision'] or current['config_revision']!=before['config_revision']:
                raise EditConflict('状態変更中に本文または選択設定が変わりました。再読込してください。')
            result=dict(name=name,path=doc['path'],previous=before['state'],changed=changed,current=current,replayed=False)
            db.execute('INSERT INTO working_state_events(room,path,request_id,fingerprint,result) VALUES(?,?,?,?,?)',
                       (room,path,request_id,signature,json.dumps(result,ensure_ascii=False)))
            return result

    def history(self,room,name):
        room=self.store.room_path(room).resolve().name;doc=self.document(room,name)
        with self.store.connect() as db:
            rows=db.execute('SELECT result,created FROM working_state_events WHERE room=? AND path=? ORDER BY id DESC LIMIT 20',(room,self.key(doc['path']))).fetchall()
        return dict(current=self.describe(room,name,doc),entries=[dict(json.loads(r['result']),created=r['created']) for r in rows])
