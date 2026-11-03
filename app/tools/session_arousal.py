"""Source-bound daily arousal ledger, processed only by successful episode saves."""
import json,time,uuid
from datetime import datetime
from litechat.episodes import JST,fingerprint,episode_date
from litechat.file_editor import EditConflict
class SessionArousal:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS session_arousal_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS session_arousal_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
   db.execute('CREATE TABLE IF NOT EXISTS session_arousal(room TEXT,event_id TEXT,day TEXT,assistant_id INTEGER,payload TEXT,processed_by TEXT,PRIMARY KEY(room,event_id))')
   db.execute('CREATE INDEX IF NOT EXISTS session_arousal_day ON session_arousal(room,day)')
 def settings(self,room,db=None):
  self.store.room_path(room)
  if db is None:
   with self.store.connect() as db:return self.settings(room,db)
  row=db.execute('SELECT payload FROM session_arousal_settings WHERE room=?',(room,)).fetchone();return json.loads(row[0]) if row else dict(enabled=True,revision='unconfigured')
 def save(self,room,enabled,revision,request_id):
  if type(enabled) is not bool or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('日別記録の設定・操作IDが不正です。')
  signature=fingerprint([enabled,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成後に変更してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM session_arousal_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDの設定が異なります。')
    return json.loads(old[1])
   if self.settings(room,db)['revision']!=revision:raise EditConflict('日別記録の設定が変わりました。')
   result=dict(enabled=enabled,revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO session_arousal_settings VALUES(?,?)',(room,json.dumps(result)));db.execute('INSERT INTO session_arousal_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)));return result
 def record(self,db,room,job,assistant_id,body):
  config=job.get('session_arousal_settings')
  if not config or not config['enabled'] or config!=self.settings(room,db):return
  row=db.execute('SELECT payload FROM motivation_events WHERE room=? AND event_id=?',(room,job['id']+':conversation')).fetchone()
  if not row:return
  event=json.loads(row[0]);score=event.get('arousal')
  if event.get('emotion_source')!='回答の明示タグ（自己申告）' or type(score) not in (float,int) or not 0<=score<=1:return
  user=db.execute("SELECT content FROM messages WHERE room=? AND id=? AND role='user' AND deleted=0 AND status='complete'",(room,job.get('user_id'))).fetchone()
  if not user:return
  created=time.time();day=datetime.fromtimestamp(created,JST).date().isoformat();item=dict(user_id=job['user_id'],assistant_id=assistant_id,user_hash=fingerprint(user[0]),assistant_hash=fingerprint(body),created=created,value=score)
  db.execute('INSERT OR IGNORE INTO session_arousal VALUES(?,?,?,?,?,NULL)',(room,job['id'],day,assistant_id,json.dumps(item)))
 def source(self,room,messages,db):
  ids=[m['id'] for m in messages if m.get('role')=='assistant'][:80]
  if not ids:return None
  rows=db.execute('SELECT event_id,payload FROM session_arousal WHERE room=? AND assistant_id IN ('+','.join('?' for _ in ids)+') ORDER BY rowid',(room,*ids)).fetchall();entries=[]
  for row in rows:
   item=json.loads(row['payload'])
   if self.app.memory_affect.valid(db,room,item):entries.append(dict(id=row['event_id'],hash=fingerprint(item),value=item['value']))
  return dict(version=1,entries=entries,average=sum(e['value'] for e in entries)/len(entries),maximum=max(e['value'] for e in entries)) if entries else None
 def enrich(self,room,source,db):
  source=self.app.memory_affect.enrich(room,source,db);value=self.source(room,source['messages'],db)
  if value:
   source=dict(source,akari_session_arousal=value)
   if not source.get('akari_arousal'):source['akari_arousal']=dict(version=1,value=value['maximum'],source='session_arousal',message_ids=[m['id'] for m in source['messages'] if m.get('role')=='assistant'])
  return source
 def processed(self,db,room,value,saved_id):
  if not isinstance(value,dict) or value.get('version')!=1:return
  entries=value.get('entries',[])
  if not isinstance(entries,list) or len(entries)>80:raise ValueError('日別記録の参照が不正です。')
  for entry in entries:
   row=db.execute('SELECT payload FROM session_arousal WHERE room=? AND event_id=?',(room,entry['id'])).fetchone()
   if not row or fingerprint(json.loads(row[0]))!=entry['hash'] or not self.app.memory_affect.valid(db,room,json.loads(row[0])):raise EditConflict('元のArousal記録・会話が変わりました。')
   db.execute('UPDATE session_arousal SET processed_by=COALESCE(processed_by,?) WHERE room=? AND event_id=?',(saved_id,room,entry['id']))
 def view(self,room,day=''):
  day=episode_date(day) if day else datetime.now(JST).date().isoformat();config=self.settings(room);entries=[]
  with self.store.connect() as db:
   rows=db.execute('SELECT event_id,payload,processed_by FROM session_arousal WHERE room=? AND day=? ORDER BY rowid DESC LIMIT 1001',(room,day)).fetchall()
   for row in rows[:1000]:
    item=json.loads(row['payload']);entries.append(dict(item,id=row['event_id'],processed_by=row['processed_by'],source_current=self.app.memory_affect.valid(db,room,item)))
  scores=[e['value'] for e in entries if e['source_current']]
  return dict(settings=config,day=day,entries=entries[:100],count=len(scores),unprocessed=sum(e['source_current'] and not e['processed_by'] for e in entries),average=sum(scores)/len(scores) if scores else None,maximum=max(scores) if scores else None,truncated=len(rows)>1000)
