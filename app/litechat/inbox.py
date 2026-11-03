"""Local letters and deferred in-app notices. No external delivery or model calls."""
import difflib
import hashlib
import json
import re
import time
import unicodedata
import uuid
from datetime import datetime
from .autonomy import encoded,now,text
from .research_subscriptions import JST
from .file_editor import EditConflict

DEFAULTS=dict(autonomous_letters=False,autonomous_notifications=False,quiet_enabled=False,quiet_start='00:00',quiet_end='07:00')


def normalized(value):return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold()


def quiet(config,clock):
    if not config['quiet_enabled'] or config['quiet_start']==config['quiet_end']:return False
    current=datetime.fromtimestamp(clock,JST).strftime('%H:%M');start,end=config['quiet_start'],config['quiet_end']
    return start<=current<end if start<end else current>=start or current<end


class Inbox:
    def __init__(self,store,clock=time.time):
        self.store,self.clock=store,clock
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS inbox_messages(id TEXT PRIMARY KEY,room TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS inbox_room_kind ON inbox_messages(room,kind,state)')
            db.execute('CREATE INDEX IF NOT EXISTS inbox_pending ON inbox_messages(kind,state)')
            db.execute('CREATE TABLE IF NOT EXISTS inbox_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS inbox_versions(id INTEGER PRIMARY KEY,room TEXT,document_id TEXT,payload TEXT,changed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS inbox_settings(room TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def _settings(self,db,room):
        row=db.execute('SELECT payload FROM inbox_settings WHERE room=?',(room,)).fetchone()
        result=json.loads(row[0]) if row else dict(DEFAULTS,revision='unconfigured')
        if getattr(self,'sleep_policy',None):
            sleep=self.sleep_policy.settings(room,db)
            result.update(quiet_enabled=sleep['enabled'],quiet_start=sleep['start'],quiet_end=sleep['end'],revision=result['revision']+':sleep:'+sleep['revision'])
        return result

    def settings(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:return self._settings(db,room)

    def save_settings(self,room,config,revision):
        self.store.room_path(room)
        if isinstance(config,dict) and set(config)=={'autonomous_letters','autonomous_notifications'}:
            old=self.settings(room)
            config={**{key:old[key] for key in DEFAULTS},**config}
        if not isinstance(config,dict) or set(config)!=set(DEFAULTS):raise ValueError('お知らせ設定の項目が不正です。')
        if any(type(config[key]) is not bool for key in ('autonomous_letters','autonomous_notifications','quiet_enabled')):raise ValueError('許可項目はtrue/falseです。')
        for key in ('quiet_start','quiet_end'):
            if not isinstance(config[key],str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',config[key]):raise ValueError('時刻は日本時間のHH:MM形式です。')
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._settings(db,room)
            if old['revision']!=revision:raise EditConflict('お知らせ設定が変わりました。')
            item=dict(config,revision=uuid.uuid4().hex)
            db.execute('INSERT INTO inbox_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,'settings',encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO inbox_settings VALUES(?,?)',(room,encoded(item)))
        return self.settings(room)

    def _receipt(self,db,room,request,signature):
        text(request,128,True)
        row=db.execute('SELECT signature,result FROM inbox_receipts WHERE room=? AND request_id=?',(room,request)).fetchone()
        if row:
            if row[0]!=signature:raise EditConflict('同じ操作IDに別の内容が指定されました。')
            return json.loads(row[1])

    def create(self,room,kind,title,body,request_id,job_id='',allow_similar=False,checkpoint=lambda:None):
        self.store.room_path(room)
        if kind not in ('letter','notice') or type(allow_similar) is not bool:raise ValueError('保存する種類が不正です。')
        title=text(title,120,True);body=text(body,8000 if kind=='letter' else 2000,True)
        signature=encoded(['create',kind,title,body,job_id,allow_similar]);clock=self.clock()
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            run=db.execute('SELECT state FROM autonomy_runs WHERE room=? AND id=?',(room,job_id)).fetchone()
            origin='autonomy' if run else 'conversation'
            policy=self._settings(db,room)
            if run and (run[0]!='running' or not policy['autonomous_letters' if kind=='letter' else 'autonomous_notifications']):raise ValueError('この自律実行では手紙/お知らせの保存が許可されていません。')
            rows=db.execute('SELECT payload FROM inbox_messages WHERE room=? AND kind=? ORDER BY rowid DESC LIMIT 50',(room,kind)).fetchall()
            body_key,title_key=normalized(body),normalized(title)
            similar=None
            if not allow_similar:
                for row in rows:
                    candidate=json.loads(row[0])
                    if candidate['state']=='cancelled' or clock-candidate['created_epoch']>(604800 if kind=='letter' else 86400):continue
                    candidate_body=normalized(candidate['body'])
                    matched=body_key==candidate_body
                    if kind=='letter' and not matched:
                        # Bounded heuristic, not a semantic or whole-history guarantee.
                        matched=difflib.SequenceMatcher(None,title_key,normalized(candidate['title'])).ratio()>=.9 and difflib.SequenceMatcher(None,body_key[:1500],candidate_body[:1500]).ratio()>=.9
                    if matched:similar=candidate;break
            if similar:
                result=dict(similar,deduplicated=True)
                db.execute('INSERT INTO inbox_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(result)))
                return result,False
            day=datetime.fromtimestamp(clock,JST).date().isoformat()
            used=db.execute("SELECT COUNT(*) FROM inbox_messages WHERE room=? AND kind=? AND json_extract(payload,'$.day_jst')=?",(room,kind,day)).fetchone()[0]
            if used>=(10 if kind=='letter' else 24):raise ValueError('このルームの1日の保存上限に達しました（手紙10通、お知らせ24件）。')
            if db.execute('SELECT COUNT(*) FROM inbox_messages WHERE room=?',(room,)).fetchone()[0]>=2000:raise ValueError('手紙/お知らせの保存上限は1ルーム2000件です。自動削除はしません。')
            if kind=='notice' and db.execute("SELECT COUNT(*) FROM inbox_messages WHERE kind='notice' AND state='queued'").fetchone()[0]>=100:raise ValueError('表示待ちのお知らせは全体100件までです。')
            item=dict(id=uuid.uuid4().hex,room=room,kind=kind,title=title,body=body,state='stored' if kind=='letter' else 'queued',revision=uuid.uuid4().hex,created_at=now(),created_epoch=clock,day_jst=day,origin=origin,job_id=job_id,read_at=None,archived=False)
            checkpoint()
            db.execute('INSERT INTO inbox_messages VALUES(?,?,?,?,?)',(item['id'],room,kind,item['state'],encoded(item)))
            db.execute('INSERT INTO inbox_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(item)))
        return item,True

    def _get(self,db,room,key):
        row=db.execute('SELECT payload FROM inbox_messages WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームの手紙/お知らせを指定してください。')
        return json.loads(row[0])

    def unread(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:return db.execute("SELECT COUNT(*) FROM inbox_messages WHERE room=? AND kind='letter' AND json_extract(payload,'$.read_at') IS NULL AND json_extract(payload,'$.archived')=0",(room,)).fetchone()[0]

    def listing(self,room,kind='letter',filter='all',offset=0,limit=20,revision=''):
        self.store.room_path(room)
        if kind not in ('letter','notice') or filter not in ('all','unread','archived'):raise ValueError('一覧の種類が不正です。')
        if kind=='notice' and filter=='unread':raise ValueError('お知らせの既読状態は通知一覧で確認してください。')
        if type(offset) is not int or not 0<=offset<=2000 or type(limit) is not int or not 1<=limit<=20:raise ValueError('一覧の範囲が不正です。')
        condition="room=? AND kind=? AND json_extract(payload,'$.archived')=?"
        if filter=='unread':condition+=" AND json_extract(payload,'$.read_at') IS NULL"
        params=(room,kind,int(filter=='archived'))
        with self.store.connect() as db:
            db.execute('BEGIN')
            versions=db.execute('SELECT id,json_extract(payload,"$.revision") FROM inbox_messages WHERE '+condition+' ORDER BY rowid DESC',params).fetchall()
            current=hashlib.sha256(encoded([tuple(r) for r in versions]).encode()).hexdigest()
            if revision and revision!=current:raise EditConflict('手紙/お知らせの一覧が変わりました。先頭から読み直してください。')
            rows=db.execute("SELECT json_remove(payload,'$.body') FROM inbox_messages WHERE "+condition+' ORDER BY rowid DESC LIMIT ? OFFSET ?',(*params,limit+1,offset)).fetchall()
        return dict(entries=[json.loads(r[0]) for r in rows[:limit]],more=len(rows)>limit,revision=current,offset=offset)

    def document(self,room,key,offset=0,limit=4000,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=8000 or type(limit) is not int or not 1<=limit<=4000:raise ValueError('本文の参照範囲が不正です。')
        with self.store.connect() as db:item=self._get(db,room,key)
        if revision and item['revision']!=revision:raise EditConflict('手紙/お知らせが変わりました。')
        if offset>len(item['body']):raise ValueError('本文の文字数を超えています。')
        body=item['body'];end=min(len(body),offset+limit)
        return dict(item,body=body[offset:end],offset=offset,total_chars=len(body),more=end<len(body))

    def change(self,room,key,revision,action,request_id):
        self.store.room_path(room)
        if action not in ('read','unread','archive','restore','cancel'):raise ValueError('操作の種類が不正です。')
        signature=encoded(['change',key,revision,action])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            old=self._get(db,room,key)
            if old['revision']!=revision:raise EditConflict('手紙/お知らせが変わりました。')
            item=dict(old)
            if action in ('read','unread'):
                if item['kind']!='letter':raise ValueError('お知らせの既読は通知一覧から操作してください。')
                item['read_at']=now() if action=='read' else None
            elif action=='cancel':
                if item['kind']!='notice' or item['state']!='queued':raise ValueError('表示待ちのお知らせだけを取り消せます。')
                item['state']='cancelled'
            else:
                if item['kind']=='notice' and item['state']=='queued':raise ValueError('表示待ちは先に取り消してください。')
                item['archived']=action=='archive'
            item['revision']=uuid.uuid4().hex
            db.execute('INSERT INTO inbox_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,key,encoded(old),now()))
            db.execute('UPDATE inbox_messages SET state=?,payload=? WHERE room=? AND id=?',(item['state'],encoded(item),room,key))
            db.execute('INSERT INTO inbox_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(item)))
        return item,True

    def dispatch(self,checkpoint=lambda:None):
        """Called by the existing deadline worker under the application idle lock."""
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');rows=db.execute("SELECT payload FROM inbox_messages WHERE kind='notice' AND state='queued' ORDER BY rowid LIMIT 100").fetchall()
            policies={};changed=[]
            for row in rows:
                item=json.loads(row[0]);room=item['room']
                if room not in policies:policies[room]=self._settings(db,room)
                policy=policies[room]
                if item['origin']=='autonomy' and not policy['autonomous_notifications']:item.update(state='cancelled',reason='自律からのお知らせ許可が取り消されました。')
                elif quiet(policy,self.clock()):continue
                else:
                    note=dict(id=uuid.uuid4().hex,schedule_id='inbox:'+item['id'],room=room,title=item['title'],message=item['body'],created_at=now(),phase='',cycle=0,step=0,late_seconds=0,inbox_id=item['id'])
                    db.execute('INSERT INTO local_notifications VALUES(?,?,?,?,?,0)',(note['id'],room,note['schedule_id'],0,encoded(note)))
                    item.update(state='delivered',notification_id=note['id'],delivered_at=now())
                item['revision']=uuid.uuid4().hex;checkpoint()
                db.execute('UPDATE inbox_messages SET state=?,payload=? WHERE id=?',(item['state'],encoded(item),item['id']));changed.append(item['id'])
        return changed
