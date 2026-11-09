"""Room-scoped autonomy records and bounded, opt-in execution receipts."""
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime,timezone
from contextlib import nullcontext
from .file_editor import EditConflict
from .activity_details import normalize as normalize_details

KINDS=('purpose','goal','procedure','thread','reflection','question')
STATES=('active','paused','completed','archived')

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
def now():return datetime.now(timezone.utc).isoformat()
def text(value,limit,required=False):
    if not isinstance(value,str) or len(value)>limit or required and not value.strip():raise ValueError('本文またはタイトルの長さを確認してください。')
    return value.strip()

def inactive_purpose(item):
    return bool(item and item.get('kind')=='purpose' and item.get('state') in ('paused','completed','archived'))

def purpose_status_only(item):
    if not inactive_purpose(item):return item
    return {key:item[key] for key in ('id','kind','state','revision') if key in item}

class Autonomy:
    def __init__(self,store,slots=None):
        self.store,self.slots=store,slots
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS autonomy_documents(id TEXT PRIMARY KEY,room TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS autonomy_documents_room ON autonomy_documents(room,kind)')
            db.execute('CREATE TABLE IF NOT EXISTS autonomy_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS autonomy_versions(id INTEGER PRIMARY KEY,room TEXT,document_id TEXT,payload TEXT,changed_at TEXT)')
        from .initial_purpose import apply
        apply(self)
    def documents(self,room,kind='',offset=0,limit=10,revision=''):
        self.store.room_path(room)
        if kind and kind not in KINDS:raise ValueError('種類が不正です。')
        if type(offset) is not int or not 0<=offset<=1000 or type(limit) is not int or not 1<=limit<=20:raise ValueError('一覧の範囲が不正です。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            versions=db.execute('SELECT id,json_extract(payload,"$.revision") FROM autonomy_documents WHERE room=? AND (?="" OR kind=?) ORDER BY rowid DESC',(room,kind,kind)).fetchall()
            current=hashlib.sha256(encoded([tuple(row) for row in versions]).encode()).hexdigest()
            if revision and revision!=current:raise EditConflict('活動記録の一覧が変わりました。先頭から読み直してください。')
            rows=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND (?="" OR kind=?) ORDER BY rowid DESC LIMIT ? OFFSET ?',(room,kind,kind,limit+1,offset)).fetchall()
        return dict(entries=[{k:v for k,v in json.loads(row[0]).items() if k not in ('body','details')} for row in rows[:limit]],more=len(rows)>limit,offset=offset,revision=current)
    def document(self,room,key):
        self.store.room_path(room)
        with self.store.connect() as db:row=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('記録が見つかりません。')
        return json.loads(row[0])
    def save(self,room,kind,title,body,state='active',next_action='',key='',revision='',request_id='',checkpoint=lambda:None,allow_purpose=False,details=None,_db=None):
        self.store.room_path(room)
        if kind not in KINDS or kind=='purpose' and not allow_purpose:raise ValueError('目的の変更は画面から行ってください。')
        if state not in STATES:raise ValueError('状態が不正です。')
        title=text(title,120,True);body=text(body,4000);next_action=text(next_action,600)
        if not isinstance(key,str) or not isinstance(revision,str) or not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        if details is not None:details=normalize_details(kind,details)
        signature=hashlib.sha256(encoded([kind,title,body,state,next_action,key,revision]+([details] if details is not None else [])).encode()).hexdigest()
        with self.store.lock,(self.store.connect() if _db is None else nullcontext(_db)) as db:
            if _db is None:db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM autonomy_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに別の内容が指定されています。')
                return json.loads(receipt[1]),False
            old=None
            if key:
                row=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=?',(room,key)).fetchone()
                if row is None:raise ValueError('更新対象がありません。')
                old=json.loads(row[0])
                if old['kind']!=kind:raise ValueError('記録の種類を変更できません。')
                if old['revision']!=revision:raise EditConflict('記録が変更されました。再読込してください。')
            else:
                if revision:raise EditConflict('新規作成にはrevisionを指定しないでください。')
                if kind=='purpose' and db.execute('SELECT 1 FROM autonomy_documents WHERE room=? AND kind="purpose"',(room,)).fetchone():raise EditConflict('目的は既存の記録を読み込んで更新してください。')
                if db.execute('SELECT COUNT(*) FROM autonomy_documents WHERE room=?',(room,)).fetchone()[0]>=1000:raise ValueError('記録は1ルーム1000件までです。')
                key=uuid.uuid4().hex
            merged=dict(old.get('details',{}) if old else {})
            if details is not None:merged.update(details)
            merged=normalize_details(kind,merged)
            if merged.get('source_timeline'):
                source=db.execute('SELECT payload FROM activity_timelines WHERE room=? AND id=?',(room,merged['source_timeline'])).fetchone()
                if source is None:raise ValueError('手順の元タイムラインが見つかりません。')
                source=json.loads(source[0])
                if source['revision']!=merged.get('source_revision'):raise EditConflict('元タイムラインが変更されました。')
                if source['state']!='completed':raise ValueError('完了が確認されたタイムラインを指定してください。')
            for linked in merged.get('links',[]):
                if linked==key:raise ValueError('自分自身を関連先にできません。')
                if db.execute('SELECT 1 FROM autonomy_documents WHERE room=? AND id=?',(room,linked)).fetchone() is None:
                    raise ValueError('同じルームにある記録を関連先に指定してください。')
            for slot in merged.get('working_slots',[]):
                if self.slots is None or not self.slots.preview(room,slot)['exists']:raise ValueError('本文のある作業メモを指定してください。')
            item=dict(id=key,room=room,kind=kind,title=title,body=body,state=state,next_action=next_action,details=merged,
                      revision=uuid.uuid4().hex,created_at=old['created_at'] if old else now(),updated_at=now())
            if kind=='goal':
                if old is None:item['progress_updated_at']=item['created_at']
                elif old.get('details',{}).get('progress',0)!=merged.get('progress',0):item['progress_updated_at']=item['updated_at']
                elif 'progress_updated_at' in old:item['progress_updated_at']=old['progress_updated_at']
            checkpoint()
            if old:db.execute('INSERT INTO autonomy_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,key,encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO autonomy_documents VALUES(?,?,?,?)',(key,room,kind,encoded(item)))
            db.execute('INSERT INTO autonomy_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(item)))
        return item,True
    def context(self,room,*,include_purpose_state=False):
        self.store.room_path(room)
        with self.store.connect() as db:
            rows=db.execute('SELECT payload FROM autonomy_documents WHERE room=? ORDER BY rowid DESC LIMIT 1000',(room,)).fetchall()
        entries=[];counts={};purpose_state=None
        for row in rows:
            item=json.loads(row[0]);kind=item['kind']
            if kind=='purpose' and purpose_state is None:
                purpose_state=item['state']
            if item['state']!='active' or kind=='procedure' or counts.get(kind,0)>=2:continue
            counts[kind]=counts.get(kind,0)+1
            # Context is a small prompt summary, not a dump of linked records.
            entries.append(dict({k:v for k,v in item.items() if k!='details'},body=item['body'][:600],body_truncated=len(item['body'])>600,
                                progress=item.get('details',{}).get('progress'),due_at=item.get('details',{}).get('due_at','')))
            if kind=='purpose':
                fields=('core_values','stable_interests','preferred_behaviors','avoid_behaviors','active_interests','open_questions')
                entries[-1]['profile']={key:item.get('details',{}).get(key,[])[:3] for key in fields}
                entries[-1]['profile_truncated']=any(len(item.get('details',{}).get(key,[]))>3 for key in fields)
        result = dict(entries=entries,notice='現在の活動目的・目標・研究・問い・振返り。本文は最大600字、各種類2件。詳細・手順は一覧と本文から参照してください。')
        if include_purpose_state:result['purpose_state']=purpose_state
        return result

    def references(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:rows=db.execute("SELECT json_remove(payload,'$.body','$.details') FROM autonomy_documents WHERE room=? ORDER BY rowid DESC LIMIT 1000",(room,)).fetchall()
        docs=[json.loads(row[0]) for row in rows]
        slots=self.slots.inventory(room)['slots'] if self.slots else []
        return dict(documents=[{k:item[k] for k in ('id','kind','title','state','revision')} for item in docs],
                    slots=[item['name'] for item in slots if item['exists']])

    def related(self,room,key,*,hide_inactive_purpose=False):
        item=self.document(room,key)
        if hide_inactive_purpose and inactive_purpose(item):return None
        refs=self.references(room)
        outgoing=set(item.get('details',{}).get('links',[]))
        with self.store.connect() as db:
            rows=db.execute("SELECT id,kind,json_extract(payload,'$.title'),json_extract(payload,'$.state') FROM autonomy_documents WHERE room=? AND EXISTS (SELECT 1 FROM json_each(json_extract(payload,'$.details.links')) WHERE value=?) ORDER BY rowid DESC LIMIT 51",(room,key)).fetchall()
        incoming=[dict(zip(('id','kind','title','state'),tuple(row))) for row in rows]
        slots=[]
        for name in item.get('details',{}).get('working_slots',[]):
            try:doc=self.slots.preview(room,name) if self.slots else {};slots.append(dict(name=name,exists=doc.get('exists',False),revision=doc.get('revision','')))
            except (ValueError,OSError):slots.append(dict(name=name,exists=False,unavailable=True))
        with self.store.connect() as db:
            rows=db.execute("SELECT id,json_extract(payload,'$.title'),json_extract(payload,'$.state'),json_extract(payload,'$.revision'),json_extract(payload,'$.input_references.activity.revision') FROM activity_timelines WHERE room=? AND json_extract(payload,'$.related_id')=? ORDER BY rowid DESC LIMIT 11",(room,key)).fetchall()
        timelines=[dict(id=row[0],title=row[1],state=row[2],revision=row[3],source_changed=bool(row[4] and row[4]!=item['revision'])) for row in rows]
        outgoing=[doc for doc in refs['documents'] if doc['id'] in outgoing]
        if hide_inactive_purpose:
            outgoing=[purpose_status_only(doc) for doc in outgoing]
            incoming=[purpose_status_only(doc) for doc in incoming]
        return dict(outgoing=outgoing,incoming=incoming[:50],incoming_more=len(incoming)>50,working_slots=slots,timelines=timelines[:10],timelines_more=len(timelines)>10)
