"""Room/target scoped plain-text templates, without executing expressions."""
import hashlib
import json
import re
import string
import uuid
from litechat.file_editor import EditConflict

FIELDS={'appearance','note','outfit','extra'}
DEFAULT='Full-body portrait.\nBase appearance: {appearance}\nCurrent outfit note: {note}\nWorn items:\n{outfit}\nAdditional request: {extra}'


def validate(body):
    if not isinstance(body,str) or not body.strip() or len(body)>6000:raise ValueError('書式は1～6000文字です。')
    used=set()
    try:
        for _,field,spec,conversion in string.Formatter().parse(body):
            if field is not None:
                if field not in FIELDS or spec or conversion:raise ValueError('使える差し込みは {appearance}、{note}、{outfit}、{extra} です。式や書式指定は使えません。')
                used.add(field)
    except ValueError as exc:raise ValueError('書式の差し込みが不正です。'+str(exc)) from exc
    if not used & {'appearance','note','outfit'}:raise ValueError('外見・装い・衣装の差し込みを最低1つ含めてください。')
    return used


class AppearanceTemplates:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS appearance_templates(room TEXT,scope TEXT,payload TEXT,PRIMARY KEY(room,scope))')
            db.execute('CREATE TABLE IF NOT EXISTS appearance_template_versions(id INTEGER PRIMARY KEY,room TEXT,scope TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS appearance_template_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def target(self,room,scope):
        if scope not in ('user','persona'):raise ValueError('対象はユーザーかキャラクターです。')
        return self.store.room_path(room).resolve().name

    def settings(self,room,scope,db=None):
        room=self.target(room,scope)
        if db is None:
            with self.store.connect() as conn:return self.settings(room,scope,conn)
        row=db.execute('SELECT payload FROM appearance_templates WHERE room=? AND scope=?',(room,scope)).fetchone()
        return json.loads(row[0]) if row else dict(room=room,scope=scope,enabled=False,body=DEFAULT,revision='unconfigured')

    def view(self,room,scope):
        room=self.target(room,scope)
        with self.store.connect() as db:
            db.execute('BEGIN')
            return dict(settings=self.settings(room,scope,db),history=[dict(id=row[0],settings=json.loads(row[1]),created=row[2]) for row in db.execute('SELECT id,payload,created FROM appearance_template_versions WHERE room=? AND scope=? ORDER BY id DESC LIMIT 10',(room,scope))])

    def save(self,room,scope,enabled,body,revision,request_id,checkpoint=lambda:None):
        room=self.target(room,scope);validate(body)
        if type(enabled) is not bool:raise ValueError('使用設定にはtrue/falseを指定してください。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        signature=hashlib.sha256(json.dumps([scope,enabled,body,revision],ensure_ascii=False).encode()).hexdigest()
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job:raise ValueError('生成中または終了処理中です。')
            db.execute('BEGIN IMMEDIATE');checkpoint()
            row=db.execute('SELECT signature,result FROM appearance_template_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if row:
                if row[0]!=signature:raise EditConflict('同じ操作IDで別の書式は保存できません。')
                return dict(json.loads(row[1]),replayed=True)
            old=self.settings(room,scope,db)
            if old['revision']!=revision:raise EditConflict('書式が変更されました。保存済みを読み直してください。')
            changed=(old['body'],old['enabled'])!=(body,enabled)
            result=dict(old,enabled=enabled,body=body,revision=uuid.uuid4().hex) if changed else old
            if changed:
                db.execute('INSERT INTO appearance_template_versions(room,scope,payload) VALUES(?,?,?)',(room,scope,json.dumps(old,ensure_ascii=False)))
                db.execute('INSERT OR REPLACE INTO appearance_templates VALUES(?,?,?)',(room,scope,json.dumps(result,ensure_ascii=False)))
            checkpoint()
            receipt=dict(settings=result,changed=changed,replayed=False)
            db.execute('INSERT INTO appearance_template_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(receipt,ensure_ascii=False)))
            return receipt

    def render(self,settings,values):
        used=validate(settings['body'])
        if not any(values[key].strip() for key in used & {'appearance','note','outfit'}):
            raise ValueError('この書式で使う外見・装い・衣装の情報が空です。書式か外見設定を確認してください。')
        size=sum(len(literal)+(len(values[field]) if field is not None else 0) for literal,field,_,_ in string.Formatter().parse(settings['body']))
        if not 1<=size<=6000:raise ValueError('書式を反映した生成内容は1～6000文字にしてください。')
        result=settings['body'].format_map(values).strip()
        if not result or len(result)>6000:raise ValueError('書式を反映した生成内容は1～6000文字にしてください。')
        return result
