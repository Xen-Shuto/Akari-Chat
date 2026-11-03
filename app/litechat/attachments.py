"""Reversible attachment maintenance; never deletes bytes or original messages."""
import hashlib
import json
import os
import stat
from itertools import islice
import re
import uuid
from urllib.parse import urlencode
from .autonomy import encoded,now
from .file_editor import EditConflict
from .multimodal import MAX_IMAGE_BYTES,dimensions


class Attachments:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE INDEX IF NOT EXISTS attachment_link_image ON message_attachment_links(image_id)')
            db.execute('CREATE TABLE IF NOT EXISTS attachment_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS attachment_events(id INTEGER PRIMARY KEY,room TEXT,image_id TEXT,action TEXT,before_revision TEXT,after_revision TEXT,created TEXT)')

    def listing(self,room,before=0,filter='all'):
        self.store.room_path(room)
        if type(before) is not int or not 0<=before<2**63 or filter not in ('all','active','hidden','retired'):raise ValueError('添付画像の一覧条件が不正です。')
        where={'all':'1','active':'COALESCE(s.hidden,0)=0 AND m.deleted=0','hidden':'COALESCE(s.hidden,0)=1','retired':'m.deleted=1'}[filter]
        with self.store.connect() as db:
            rows=db.execute(f'''SELECT i.*,m.created,m.deleted AS message_deleted,m.role,substr(m.content,1,200) AS excerpt,COALESCE(s.hidden,0) AS hidden
                FROM images i JOIN messages m ON m.id=i.message_id LEFT JOIN attachment_states s ON s.image_id=i.id
                WHERE i.room=? AND m.room=i.room AND (?=0 OR i.rowid<?) AND {where} ORDER BY i.rowid DESC LIMIT 21''',(room,before,before)).fetchall()
            items=[dict(row) for row in rows[:20]]
            next_before=db.execute('SELECT rowid FROM images WHERE id=?',(items[-1]['id'],)).fetchone()[0] if len(rows)>20 else 0
            counts=db.execute('''SELECT COUNT(*),COALESCE(SUM(i.size),0),COALESCE(SUM(CASE WHEN s.hidden=1 THEN 1 ELSE 0 END),0)
                FROM images i LEFT JOIN attachment_states s ON s.image_id=i.id WHERE i.room=?''',(room,)).fetchone()
        return dict(entries=items,next_before=next_before,count=counts[0],bytes=counts[1],hidden=counts[2])

    def snapshot(self,db,room,key):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('添付画像のIDが不正です。')
        row=db.execute('''SELECT i.*,m.created,m.deleted AS message_deleted,m.role,m.content,COALESCE(s.hidden,0) AS hidden,COALESCE(s.revision,'original') AS state_revision
            FROM images i JOIN messages m ON m.id=i.message_id LEFT JOIN attachment_states s ON s.image_id=i.id WHERE i.room=? AND m.room=i.room AND i.id=?''',(room,key)).fetchone()
        if row is None:raise ValueError('このルームの添付画像が見つかりません。')
        item=dict(row);path=self.store.image_path(room,key,item['mime'])
        try:
            with path.open('rb') as stream:raw=stream.read(MAX_IMAGE_BYTES+1)
            file_hash=hashlib.sha256(raw).hexdigest()
            available=len(raw)==item['size'] and len(raw)<=MAX_IMAGE_BYTES
            issue='' if available else '保存した画像のサイズが一致しません。'
            if available:
                try:available=dimensions(raw,item['mime'])==(item['width'],item['height'])
                except ValueError:available=False
                if not available:issue='保存した画像の形式・寸法が一致しません。'
        except FileNotFoundError:file_hash='missing';available=False;issue='画像ファイルが見つかりません。'
        if file_hash=='missing' and hasattr(self.app,'attachment_disposal'):
            row=db.execute("SELECT json_extract(payload,'$.state') FROM attachment_disposals WHERE room=? AND image_id=? ORDER BY rowid DESC LIMIT 1",(room,key)).fetchone()
            if row:
                file_hash+=':'+row[0]
                if row[0]=='quarantined':issue='画像ファイルは退避中です。退避一覧から戻せます。'
                elif row[0]=='purged':issue='画像ファイルは完全削除済みです。復元できません。'
        revision=hashlib.sha256(encoded([item,file_hash]).encode()).hexdigest()
        item.update(revision=revision,available=available,issue=issue,url='/api/attachment-image?'+urlencode({'room':room,'id':key,'revision':revision}))
        item['excerpt']=item.pop('content')[:500]
        return item

    def document(self,room,key):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:return self.snapshot(db,room,key)

    def image(self,room,key,revision):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            doc=self.snapshot(db,room,key)
            if revision!=doc['revision']:raise EditConflict('添付画像が変更されています。一覧から選び直してください。')
            if not doc['available']:raise ValueError(doc['issue'])
            return self.store.read_image(room,key,include_hidden=True)

    def change(self,room,key,revision,action,request_id,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(revision,str) or not re.fullmatch('[0-9a-f]{64}',revision):raise ValueError('添付画像のrevisionが不正です。')
        if action not in ('hide','restore') or not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('添付画像の操作または操作IDが不正です。')
        signature=hashlib.sha256(encoded([key,revision,action]).encode()).hexdigest()
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから添付画像を整理してください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM attachment_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに別の内容が指定されています。')
                return json.loads(receipt[1]),False
            before=self.snapshot(db,room,key)
            if revision!=before['revision']:raise EditConflict('添付画像または元の発言が変更されています。選び直してください。')
            if action=='restore' and not before['available']:raise ValueError(before['issue'])
            hidden=action=='hide';changed=bool(before['hidden'])!=hidden
            checkpoint()
            if changed:
                db.execute('INSERT OR REPLACE INTO attachment_states VALUES(?,?,?)',(key,int(hidden),uuid.uuid4().hex))
                if hidden:self.app.active_attachments.remove(db,room,key)
                after=self.snapshot(db,room,key)
                db.execute('INSERT INTO attachment_events(room,image_id,action,before_revision,after_revision,created) VALUES(?,?,?,?,?,?)',(room,key,action,revision,after['revision'],now()))
            else:after=before
            db.execute('INSERT INTO attachment_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(after)))
        return after,changed


    def usage(self,room):
        """Explicit bounded size inspection; no pixel reads, moves, or deletes."""
        room=self.store.room_path(room).name
        with self.store.lock,self.store.connect() as db:
            rows=db.execute("""SELECT i.*,COALESCE(s.hidden,0) AS hidden,COALESCE(m.deleted,1) AS retired,
                EXISTS(SELECT 1 FROM active_attachment_items a WHERE a.room=i.room AND a.image_id=i.id) AS persistent,
                (SELECT COUNT(*) FROM message_attachment_links l WHERE l.image_id=i.id) AS links
                FROM images i LEFT JOIN messages m ON m.id=i.message_id AND m.room=i.room
                LEFT JOIN attachment_states s ON s.image_id=i.id WHERE i.room=? LIMIT 5001""",(room,)).fetchall()
            if len(rows)>5000:raise ValueError('容量確認は1ルーム5000枚以内です。')
            if any(type(r['size']) is not int or r['size']<0 for r in rows):raise ValueError('登録時サイズの記録が不正です。')
            folder=self.store.image_path(room,'0'*32,'image/png').parent
            result=dict(room=room,checked_at=now(),registered=len(rows),registered_bytes=sum(r['size'] for r in rows),present=0,file_bytes=0,missing=0,blocked=0,size_changed=0,persistent=0,linked=0,unregistered=0,unregistered_bytes=0,unclassified=0,categories={k:dict(count=0,bytes=0) for k in ('active','hidden','retired')})
            expected=set()
            for row in rows:
                path=self.store.image_path(room,row['id'],row['mime']);expected.add(path.name)
                result['persistent']+=int(bool(row['persistent']));result['linked']+=int(bool(row['links']))
                try:
                    info=path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink>1 or getattr(info,'st_file_attributes',0)&0x400:result['blocked']+=1;continue
                except FileNotFoundError:result['missing']+=1;continue
                except OSError:result['blocked']+=1;continue
                result['present']+=1;result['file_bytes']+=info.st_size;result['size_changed']+=int(info.st_size!=row['size'])
                category='hidden' if row['hidden'] else 'retired' if row['retired'] else 'active'
                result['categories'][category]['count']+=1;result['categories'][category]['bytes']+=info.st_size
            try:
                with os.scandir(folder) as entries:files=list(islice(entries,5001))
            except FileNotFoundError:files=[]
            if len(files)>5000:raise ValueError('添付フォルダの確認対象が5000件を超えました。')
            for entry in files:
                if entry.name in expected:continue
                if not re.fullmatch('[0-9a-f]{32}\.(png|jpg)',entry.name):result['unclassified']+=1;continue
                try:
                    info=entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink>1 or getattr(info,'st_file_attributes',0)&0x400:result['unclassified']+=1;continue
                    result['unregistered']+=1;result['unregistered_bytes']+=info.st_size
                except OSError:result['unclassified']+=1
            return result
