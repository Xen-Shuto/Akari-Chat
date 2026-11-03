"""Human-controlled entity dormancy; source Markdown and Nexus indices stay intact."""
import hashlib
import json
import os
import re
from litechat.file_editor import EditConflict
from litechat.working_slots import slot_name


class EntityStates:
    def __init__(self, store, editor):
        self.store,self.editor=store,editor
        self.merges=None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS entity_states(room TEXT, path TEXT, state TEXT NOT NULL, version INTEGER NOT NULL, body_revision TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(room,path))")
            db.execute("CREATE TABLE IF NOT EXISTS entity_state_events(id INTEGER PRIMARY KEY,room TEXT,path TEXT,request_id TEXT,fingerprint TEXT,result TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(room,request_id))")
            db.execute('CREATE INDEX IF NOT EXISTS entity_state_history ON entity_state_events(room,path,id DESC)')

    def path(self, name):return 'memory/entities/'+slot_name(name)+'.md'

    def key(self, path):return os.path.normcase(path).replace('\\','/')

    def describe(self, room, name, document=None, db=None):
        room=self.store.room_path(room).resolve().name
        path=self.path(name)
        document=document if document is not None else self.editor.read_snapshot(room,'resource','エンティティ記憶',path)
        if db is None:
            self.store.room_path(room)
            with self.store.connect() as connection:return self.describe(room,name,document,connection)
        row=db.execute('SELECT * FROM entity_states WHERE room=? AND path=?',(room,self.key(path))).fetchone()
        state,version=(row['state'],row['version']) if row else ('active',0)
        revision=hashlib.sha256(json.dumps([room,self.key(path),document['revision'],state,version]).encode()).hexdigest()
        extra=self.merges.annotations(room,db).get(self.key(path),{}) if self.merges else {}
        return {'state':state,'state_version':version,'state_revision':revision,**extra,
                'state_updated':row['updated'] if row else None,
                'state_source_changed':bool(row and row['body_revision']!=document['revision'])}

    def annotate(self, room, entries):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:
            states={row['path']:(row['state'],row['version']) for row in db.execute('SELECT path,state,version FROM entity_states WHERE room=?',(room,))}
        extra=self.merges.annotations(room) if self.merges else {}
        return [dict(entry,state=states.get(self.key(entry['path']),('active',0))[0],state_version=states.get(self.key(entry['path']),('active',0))[1],**extra.get(self.key(entry['path']),{})) for entry in entries]

    def inactive(self, room):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:result={row[0] for row in db.execute("SELECT path FROM entity_states WHERE room=? AND state IN ('dormant','merged')",(room,))}
        if self.merges:result.update(path for path,item in self.merges.annotations(room).items() if item.get('merge_pending'))
        return result

    def change(self, room, name, state, revision, request_id, checkpoint=lambda:None):
        room=self.store.room_path(room).resolve().name
        path=self.path(name)
        if state not in ('active','dormant'):raise ValueError('状態はactiveまたはdormantです。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',request_id):raise ValueError('要求IDが不正です。')
        fingerprint=hashlib.sha256(json.dumps([self.key(path),state,revision],ensure_ascii=False).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            checkpoint();self.store.room_path(room)
            previous=db.execute('SELECT fingerprint,result FROM entity_state_events WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise EditConflict('同じ要求IDに異なる操作が指定されました。')
                return {**json.loads(previous['result']),'replayed':True}
            if self.merges:self.merges.guard(room,path)
            document=self.editor.read_snapshot(room,'resource','エンティティ記憶',path)
            if not document['exists']:raise ValueError('既存のエンティティを選択してください。')
            before=self.describe(room,name,document,db)
            if revision!=before['state_revision']:raise EditConflict('本文または状態が変わりました。読み直してください。')
            changed=before['state']!=state
            if changed:
                db.execute("INSERT INTO entity_states(room,path,state,version,body_revision) VALUES(?,?,?,?,?) ON CONFLICT(room,path) DO UPDATE SET state=excluded.state,version=excluded.version,body_revision=excluded.body_revision,updated=CURRENT_TIMESTAMP",
                           (room,self.key(path),state,before['state_version']+1,document['revision']))
            checkpoint()
            result={'name':name,'path':path,'previous':before['state'],'changed':changed,
                    'current':self.describe(room,name,document,db),'replayed':False}
            db.execute('INSERT INTO entity_state_events(room,path,request_id,fingerprint,result) VALUES(?,?,?,?,?)',
                       (room,self.key(path),request_id,fingerprint,json.dumps(result,ensure_ascii=False)))
            return result

    def history(self, room, name):
        room=self.store.room_path(room).resolve().name
        current=self.describe(room,name)
        with self.store.connect() as db:
            rows=db.execute('SELECT id,result,created FROM entity_state_events WHERE room=? AND path=? ORDER BY id DESC LIMIT 20',(room,self.key(self.path(name)))).fetchall()
        return {'name':name,'current':current,'entries':[dict(id=row['id'],created=row['created'],**json.loads(row['result'])) for row in rows]}
