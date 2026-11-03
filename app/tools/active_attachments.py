"""Opt-in persistent image references; never copies an image on every turn."""
import hashlib
import json
import re
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict
from litechat.multimodal import MAX_IMAGES,MAX_TOTAL_BYTES


class ActiveAttachments:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS active_attachment_items(room TEXT,image_id TEXT,revision TEXT,PRIMARY KEY(room,image_id))')
            db.execute('CREATE TABLE IF NOT EXISTS active_attachment_versions(id INTEGER PRIMARY KEY,room TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('CREATE TABLE IF NOT EXISTS active_attachment_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def listing(self,room,db=None):
        room=self.store.room_path(room).name
        if db is None:
            with self.store.lock,self.store.connect() as conn:return self.listing(room,conn)
        rows=db.execute('SELECT image_id,revision FROM active_attachment_items WHERE room=? ORDER BY rowid',(room,)).fetchall()
        version=db.execute('SELECT COALESCE(MAX(id),0) FROM active_attachment_versions WHERE room=?',(room,)).fetchone()[0]
        entries=[]
        for row in rows:
            try:
                doc=self.app.attachments.snapshot(db,room,row[0])
                entries.append(dict(id=row[0],name=doc['name'],valid=doc['available'] and not doc['hidden'] and not doc['message_deleted'] and doc['revision']==row[1],revision=row[1]))
            except (OSError,ValueError):entries.append(dict(id=row[0],name='参照できない画像',valid=False,revision=row[1]))
        return dict(room=room,entries=entries,revision=fingerprint([version,[tuple(r) for r in rows]]))

    def remove(self,db,room,key):
        rows=[tuple(r) for r in db.execute('SELECT image_id,revision FROM active_attachment_items WHERE room=? ORDER BY rowid',(room,))]
        if db.execute('DELETE FROM active_attachment_items WHERE room=? AND image_id=?',(room,key)).rowcount:
            db.execute('INSERT INTO active_attachment_versions(room,payload) VALUES(?,?)',(room,json.dumps(rows)))

    def change(self,room,key,image_revision,revision,action,request_id,checkpoint=lambda:None):
        room=self.store.room_path(room).name
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        if action not in ('add','remove','clear'):raise ValueError('継続添付の操作が不正です。')
        signature=fingerprint([key,image_revision,revision,action])
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.job or self.app.closing or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから継続添付を変更してください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM active_attachment_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDで別の継続添付は設定できません。')
                return dict(json.loads(receipt[1]),replayed=True)
            old=self.listing(room,db)
            if old['revision']!=revision:raise EditConflict('継続添付が変更されています。読み直してください。')
            rows=[tuple(r) for r in db.execute('SELECT image_id,revision FROM active_attachment_items WHERE room=? ORDER BY rowid',(room,))]
            if action=='add':
                doc=self.app.attachments.snapshot(db,room,key)
                if image_revision!=doc['revision']:raise EditConflict('添付画像が変更されています。選び直してください。')
                if not doc['available'] or doc['hidden'] or doc['message_deleted']:raise ValueError('現在の会話で利用できる画像を選んでください。')
                existing=next((r for r in rows if r[0]==key),None)
                if existing!=(key,doc['revision']):
                    if not existing and len(rows)>=MAX_IMAGES:raise ValueError('継続添付は4枚までです。先に選択を解除してください。')
                    db.execute('INSERT INTO active_attachment_versions(room,payload) VALUES(?,?)',(room,json.dumps(rows)))
                    db.execute('INSERT OR REPLACE INTO active_attachment_items VALUES(?,?,?)',(room,key,doc['revision']))
            elif action=='remove':self.remove(db,room,key)
            elif rows:
                db.execute('INSERT INTO active_attachment_versions(room,payload) VALUES(?,?)',(room,json.dumps(rows)))
                db.execute('DELETE FROM active_attachment_items WHERE room=?',(room,))
            checkpoint();result=self.listing(room,db)
            db.execute('INSERT INTO active_attachment_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result,ensure_ascii=False)))
            return result

    def prepare(self,room,manual,vision):
        with self.store.lock,self.store.connect() as db:
            selected=self.listing(room,db)
            if not vision:return [],dict(ids=[],skipped=len(selected['entries']),reason='画像非対応の会話モデルでは継続添付を送信しません。')
            remaining=MAX_IMAGES-len(manual);budget=MAX_TOTAL_BYTES-sum(i['size'] for i in manual)
            seen={hashlib.sha256(i['raw']).hexdigest() for i in manual};images=[];skipped=[]
            for entry in selected['entries']:
                if not entry['valid']:raise EditConflict('継続添付の元画像または発言が変わりました。添付画像の管理で解除または選び直してください。')
                if not remaining:skipped.append(entry['id']);continue
                raw=self.store.read_image(room,entry['id']);sha=hashlib.sha256(raw['raw']).hexdigest()
                if self.app.attachments.snapshot(db,room,entry['id'])['revision']!=entry['revision']:raise EditConflict('継続添付の読込中に画像が変わりました。')
                if sha in seen or raw['size']>budget:skipped.append(entry['id']);continue
                images.append(dict(raw,source_id=entry['id'],sha256=sha));seen.add(sha);remaining-=1;budget-=raw['size']
            return images,dict(ids=[i['source_id'] for i in images],skipped=len(skipped),reason='手動添付を優先し、継続添付、直近履歴の順に最大4枚/合計12MiB。重複画像は再送しません。')
