"""Named text presets. Loading one never changes the active appearance template."""
import json
import re
import uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict
from .appearance_templates import validate


class AppearancePresets:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS appearance_presets(room TEXT,scope TEXT,id TEXT,payload TEXT,PRIMARY KEY(room,scope,id))')
            db.execute('CREATE TABLE IF NOT EXISTS appearance_preset_versions(id INTEGER PRIMARY KEY,room TEXT,scope TEXT,preset_id TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS appearance_preset_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def listing(self,room,scope):
        room=self.app.appearance_templates.target(room,scope)
        with self.store.connect() as db:
            rows=db.execute('SELECT payload FROM appearance_presets WHERE room=? AND scope=? ORDER BY rowid DESC',(room,scope)).fetchall()
        return dict(room=room,scope=scope,entries=[json.loads(row[0]) for row in rows])

    def save(self,room,scope,id,name,body,archived,revision,request_id,checkpoint=lambda:None):
        room=self.app.appearance_templates.target(room,scope);validate(body)
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('書式名は1～80文字です。')
        name=name.strip()
        if type(archived) is not bool:raise ValueError('保管状態が不正です。')
        if not isinstance(id,str) or (id and not re.fullmatch('[0-9a-f]{32}',id)):raise ValueError('書式IDが不正です。')
        if not isinstance(revision,str):raise ValueError('書式のrevisionが不正です。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        signature=fingerprint([scope,id,name,body,archived,revision])
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから書式集を変更してください。')
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT signature,result FROM appearance_preset_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if old:
                if old[0]!=signature:raise EditConflict('同じ操作IDで別の書式は保存できません。')
                return dict(json.loads(old[1]),replayed=True)
            previous=None
            if id:
                row=db.execute('SELECT payload FROM appearance_presets WHERE room=? AND scope=? AND id=?',(room,scope,id)).fetchone()
                if row is None:raise ValueError('選択した書式が見つかりません。')
                previous=json.loads(row[0])
                if revision!=previous['revision']:raise EditConflict('名前付き書式が変わりました。書式集を読み直してください。')
            else:
                if revision:raise EditConflict('新規書式にrevisionは指定できません。')
                if db.execute('SELECT COUNT(*) FROM appearance_presets WHERE room=? AND scope=?',(room,scope)).fetchone()[0]>=100:raise ValueError('書式集は対象ごとに保管分も含め100件までです。')
                id=uuid.uuid4().hex
            # Ambiguous names are rejected, including archived presets.
            if db.execute("SELECT 1 FROM appearance_presets WHERE room=? AND scope=? AND id!=? AND json_extract(payload,'$.name')=?",(room,scope,id,name)).fetchone():raise ValueError('同じ名前の書式があります。別の名前にしてください。')
            changed=not previous or any(previous[k]!=v for k,v in dict(name=name,body=body,archived=archived).items())
            result=dict(id=id,name=name,body=body,archived=archived,revision=uuid.uuid4().hex) if changed else previous
            checkpoint()
            if changed:
                if previous:db.execute('INSERT INTO appearance_preset_versions(room,scope,preset_id,payload) VALUES(?,?,?,?)',(room,scope,id,json.dumps(previous,ensure_ascii=False)))
                db.execute('INSERT OR REPLACE INTO appearance_presets VALUES(?,?,?,?)',(room,scope,id,json.dumps(result,ensure_ascii=False)))
            checkpoint()
            receipt=dict(preset=result,changed=changed,replayed=False)
            db.execute('INSERT INTO appearance_preset_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(receipt,ensure_ascii=False)))
            return receipt
