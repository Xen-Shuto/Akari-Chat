"""Revisioned conversation edits with transactional, reversible tail snapshots."""
import json
from .episodes import fingerprint
from .file_editor import EditConflict

class ChatOps:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            if 'deleted' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            db.execute("CREATE TABLE IF NOT EXISTS chat_revisions(id INTEGER PRIMARY KEY,room TEXT,action TEXT,snapshot TEXT,after_hash TEXT,restored INTEGER DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP)")
    def rows(self,db,room):
        rows=[dict(r) for r in db.execute('SELECT * FROM messages WHERE room=? ORDER BY id',(room,))]
        states={}
        for item in db.execute('SELECT i.message_id,i.id,s.hidden,s.revision FROM images i JOIN attachment_states s ON s.image_id=i.id WHERE i.room=? ORDER BY i.id',(room,)):
            states.setdefault(item[0],[]).append(tuple(item)[1:])
        for row in rows:
            if row['id'] in states:row['attachment_state']=fingerprint(states[row['id']])
        return rows
    def conversation(self,room):
        with self.store.lock:
            messages=self.store.history(room)
            with self.store.connect() as db:revision=fingerprint(self.rows(db,room))
        return {'messages':messages,'revision':revision}
    def state(self,room,query='',before=0,focus=0):
        self.store.room_path(room);self.store.index_room(room)
        if not isinstance(query,str) or len(query)>200:raise ValueError('検索語は200文字までです。')
        with self.store.lock:
            with self.store.connect() as db:
                rows=self.rows(db,room)
                visible=[r for r in rows if not r['deleted']]
                matches=[r for r in visible if query.casefold() in r['content'].casefold()][-100:] if query else []
                selected=focus or (matches[-1]['id'] if query and not before and matches else 0)
                if selected:
                    index=next((i for i,r in enumerate(visible) if r['id']==selected),None)
                    if index is None:raise ValueError('発言が見つかりません。再読込してください。')
                    messages=visible[max(0,index-20):index+21]
                else:
                    messages=[r for r in visible if not before or r['id']<before][-100:] if not query or before else []
                from tools.discord_labels import present
                matches=present(db,room,matches)
                revisions=[dict(r) for r in db.execute('SELECT id,action,created,restored FROM chat_revisions WHERE room=? ORDER BY id DESC LIMIT 30',(room,))]
            if messages:messages=self.store.history(room,len(messages),before=messages[-1]['id']+1)
        return {'messages':messages,'matches':[dict(id=r['id'],role=r['role'],content=r['content'][:120],created=r['created'],speaker=r.get('speaker','')) for r in matches],
                'focus_id':selected,'revision':fingerprint(rows),'revisions':revisions,
                'next_before':messages[0]['id'] if messages and any(r['id']<messages[0]['id'] for r in visible) else 0}
    def change(self,room,key,revision,action,text=''):
        if action not in ('edit','remove','regenerate'):raise ValueError('操作が不正です。')
        if not isinstance(text,str) or len(text)>100000:raise ValueError('本文は100000文字までです。')
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=self.rows(db,room)
            if fingerprint(rows)!=revision:raise EditConflict('履歴が変わりました。再読込してください。')
            visible=[r for r in rows if not r['deleted']]
            target=next((r for r in visible if r['id']==key),None)
            if target is None:raise ValueError('発言が見つかりません。')
            if action=='regenerate' and target['role']!='assistant':
                raise ValueError('キャラクターの発言を選んでください。')
            if action=='regenerate' and target.get('origin') in ('autonomy','opening','tool_result'):
                raise ValueError('自律行動・最初のメッセージ・ツール結果報告は通常会話として再生成できません。')
            if action=='edit' and not text.strip():raise ValueError('空の発言には変更できません。')
            snapshot=[r for r in rows if r['id']>=key]
            db.execute('UPDATE messages SET deleted=1 WHERE room=? AND id>=?',(room,key))
            if action=='edit':db.execute('UPDATE messages SET content=?,deleted=0 WHERE room=? AND id=?',(text,room,key))
            after=fingerprint(self.rows(db,room))
            db.execute('INSERT INTO chat_revisions(room,action,snapshot,after_hash) VALUES(?,?,?,?)',(room,action,json.dumps(snapshot,ensure_ascii=False),after))
        return self.state(room)
    def restore(self,room,key,revision):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=self.rows(db,room)
            item=db.execute('SELECT * FROM chat_revisions WHERE room=? AND id=? AND restored=0',(room,key)).fetchone()
            if item is None:raise ValueError('復元可能な変更がありません。')
            if fingerprint(rows)!=revision:
                raise EditConflict('確認後に会話が進んでいます。再読込してください。')
            snapshot=json.loads(item['snapshot'])
            start=snapshot[0]['id']
            displaced=[r for r in rows if r['id']>=start]
            db.execute('UPDATE messages SET deleted=1 WHERE room=? AND id>=?',(room,start))
            for r in snapshot:
                db.execute('UPDATE messages SET content=?,status=?,deleted=? WHERE room=? AND id=?',(r['content'],r['status'],r['deleted'],room,r['id']))
            db.execute('UPDATE chat_revisions SET restored=1 WHERE id=?',(key,))
            db.execute('INSERT INTO chat_revisions(room,action,snapshot,after_hash) VALUES(?,?,?,?)',(room,'restore',json.dumps(displaced,ensure_ascii=False),fingerprint(self.rows(db,room))))
        return self.state(room)
