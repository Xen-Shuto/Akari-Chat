"""Persistent local deadlines. No inference, network calls or per-timer threads."""
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from .file_editor import EditConflict


def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()
def integer(value,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('時間または回数が範囲外です。')
    return value


class Schedules:
    def __init__(self,store,wall=time.time,mono=time.monotonic):
        self.store,self.wall,self.mono=store,wall,mono
        self.deadlines={};self.stop=threading.Event();self.worker=None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS local_schedules(id TEXT PRIMARY KEY,room TEXT NOT NULL,request_id TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(room,request_id))")
            db.execute("CREATE TABLE IF NOT EXISTS local_notifications(id TEXT PRIMARY KEY,room TEXT NOT NULL,schedule_id TEXT NOT NULL,step INTEGER NOT NULL,payload TEXT NOT NULL,read INTEGER NOT NULL DEFAULT 0,UNIQUE(schedule_id,step))")
            db.execute('CREATE INDEX IF NOT EXISTS local_schedules_state ON local_schedules(state)')
            db.execute('CREATE INDEX IF NOT EXISTS local_notifications_room ON local_notifications(room,read)')
    def prepare(self,kind,title,message,seconds=300,at='',work_seconds=1500,break_seconds=300,cycles=4):
        if kind not in ('timer','alarm','pomodoro'):raise ValueError('予約の種類が不正です。')
        if not isinstance(title,str) or not title.strip() or len(title)>120:raise ValueError('タイトルは1～120文字です。')
        if not isinstance(message,str) or len(message)>2000:raise ValueError('通知本文は2000文字までです。')
        now=self.wall()
        if kind=='alarm':
            if not isinstance(at,str) or len(at)>64:raise ValueError('日時が不正です。')
            try:parsed=datetime.fromisoformat(at.replace('Z','+00:00'))
            except ValueError as error:raise ValueError('UTCオフセット付きのISO日時を指定してください。') from error
            if parsed.utcoffset() is None:raise ValueError('日時にはUTCオフセットが必要です。')
            due=parsed.timestamp()
            if not now<due<=now+31622400:raise ValueError('アラームは現在より後、366日以内です。')
        else:
            seconds=integer(seconds,1,31622400)
            if kind=='pomodoro':
                work_seconds=integer(work_seconds,1,86400);break_seconds=integer(break_seconds,1,86400);cycles=integer(cycles,1,20)
                seconds=work_seconds
            due=now+seconds
        signature=hashlib.sha256(json.dumps([kind,title.strip(),message,due if kind=='alarm' else seconds,work_seconds if kind=='pomodoro' else 0,break_seconds if kind=='pomodoro' else 0,cycles if kind=='pomodoro' else 1],ensure_ascii=False).encode()).hexdigest()
        return dict(signature=signature,kind=kind,title=title.strip(),message=message,due_at=stamp(due),due=due,
            work_seconds=work_seconds if kind=='pomodoro' else 0,break_seconds=break_seconds if kind=='pomodoro' else 0,
            cycles=cycles if kind=='pomodoro' else 1,phase='work' if kind=='pomodoro' else '',cycle=1,step=0,
            state='queued',revision=uuid.uuid4().hex,created_at=stamp(now))
    def create(self,room,prepared,request_id,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM local_schedules WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if row:
                cached=json.loads(row['payload'])
                if cached['signature']!=prepared['signature']:raise EditConflict('同じ操作IDに別の予約内容が指定されています。')
                return cached,False
            if db.execute("SELECT COUNT(*) FROM local_schedules WHERE state='queued' AND room=?",(room,)).fetchone()[0]>=100:raise ValueError('実行待ちの予約は1ルーム100件までです。')
            if db.execute("SELECT COUNT(*) FROM local_schedules WHERE state='queued'").fetchone()[0]>=500:raise ValueError('実行待ちの予約は全体500件までです。')
            item=dict(prepared,id=uuid.uuid4().hex,room=room)
            checkpoint()
            db.execute('INSERT INTO local_schedules VALUES(?,?,?,?,?)',(item['id'],room,request_id,item['state'],json.dumps(item,ensure_ascii=False)))
        self.deadlines[item['id']]=(item['due'],self.mono()+max(0,item['due']-self.wall()))
        return item,True
    def listing(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:
            rows=[json.loads(r[0]) for r in db.execute("SELECT payload FROM local_schedules WHERE room=? ORDER BY (state='queued') DESC,rowid DESC LIMIT 200",(room,))]
            notes=[]
            for r in db.execute('SELECT payload,read FROM local_notifications WHERE room=? ORDER BY read,rowid DESC LIMIT 100',(room,)):
                notes.append(dict(json.loads(r['payload']),read=bool(r['read'])))
            unread=db.execute('SELECT COUNT(*) FROM local_notifications WHERE room=? AND read=0',(room,)).fetchone()[0]
        return dict(schedules=rows,notifications=notes,unread=unread,schedule_limit=200,notification_limit=100)
    def cancel(self,room,key,revision,checkpoint=lambda:None):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM local_schedules WHERE room=? AND id=?',(room,key)).fetchone()
            if not row:raise ValueError('予約が見つかりません。')
            item=json.loads(row[0])
            if item['revision']!=revision:raise EditConflict('予約の状態が変わりました。読み直してください。')
            if item['state']!='queued':return item,False
            item.update(state='cancelled',revision=uuid.uuid4().hex)
            checkpoint()
            db.execute('UPDATE local_schedules SET state=?,payload=? WHERE room=? AND id=?',(item['state'],json.dumps(item,ensure_ascii=False),room,key))
        self.deadlines.pop(key,None)
        return item,True
    def acknowledge(self,room,key):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            row=db.execute('SELECT id FROM local_notifications WHERE room=? AND id=?',(room,key)).fetchone()
            if not row:raise ValueError('通知が見つかりません。')
            db.execute('UPDATE local_notifications SET read=1 WHERE room=? AND id=?',(room,key))
    def tick(self,checkpoint=lambda:None):
        """Called under the application lock only when conversational work is idle."""
        now,mono=self.wall(),self.mono();changed=[];new_deadlines={}
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT id,payload FROM local_schedules WHERE state='queued' AND room NOT IN (SELECT room FROM room_states WHERE archived=1) LIMIT 500").fetchall()
            for row in rows:
                item=json.loads(row['payload']);key=item['id']
                if item['state']!='queued':continue
                if item['kind']=='alarm':due=now>=item['due']
                else:
                    saved=self.deadlines.get(key)
                    deadline=saved[1] if saved and saved[0]==item['due'] else mono+max(0,item['due']-now)
                    new_deadlines[key]=(item['due'],deadline);due=mono>=deadline
                if not due:continue
                note=dict(id=uuid.uuid4().hex,schedule_id=key,room=item['room'],title=item['title'],message=item['message'],
                    due_at=item['due_at'],created_at=stamp(now),late_seconds=max(0,int(now-item['due'])) if item['kind']=='alarm' or not saved else max(0,int(mono-deadline)),
                    phase=item['phase'],cycle=item['cycle'],step=item['step'])
                db.execute('INSERT INTO local_notifications VALUES(?,?,?,?,?,0)',(note['id'],item['room'],key,item['step'],json.dumps(note,ensure_ascii=False)))
                item['step']+=1
                if item['kind']=='pomodoro' and (item['phase']=='break' or item['cycle']<item['cycles']):
                    if item['phase']=='work':item['phase']='break';duration=item['break_seconds']
                    else:item['phase']='work';item['cycle']+=1;duration=item['work_seconds']
                    item['due']=now+duration;item['due_at']=stamp(item['due']);new_deadlines[key]=(item['due'],mono+duration)
                else:item['state']='notified';new_deadlines.pop(key,None)
                item['revision']=uuid.uuid4().hex
                checkpoint()
                db.execute('UPDATE local_schedules SET state=?,payload=? WHERE id=?',(item['state'],json.dumps(item,ensure_ascii=False),key))
                changed.append(key)
        self.deadlines=new_deadlines
        return changed
    def start(self,app):
        def work():
            while not self.stop.wait(1):
                try:
                    with app.lock:
                        if not app.closing and app.job is None and not app.conversation_waiters:
                            self.tick()
                            app.inbox.dispatch()
                except Exception:logging.getLogger(__name__).exception('Local deadline dispatch failed; reservations retained')
        self.worker=threading.Thread(target=work,name='akari-deadlines',daemon=True);self.worker.start()
    def close(self):
        self.stop.set()
        if self.worker:self.worker.join(timeout=2)
