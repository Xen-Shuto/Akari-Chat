"""Opt-in source-bound pipeline saves, behind conversations and classification."""
import json,time,uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict
from .background_guard import ready_sql
DEFAULTS=dict(episodes=False,classified=False)
class MemorySaves:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.pending=True;self.next_scan=0;self.warning=''
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS memory_save_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS memory_save_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
   db.execute('CREATE TABLE IF NOT EXISTS memory_save_jobs(id TEXT PRIMARY KEY,room TEXT,run_id TEXT,kind TEXT,draft_id TEXT,state TEXT,revision TEXT,payload TEXT)')
   db.execute('CREATE INDEX IF NOT EXISTS memory_save_state ON memory_save_jobs(state,room)')
   db.execute("UPDATE memory_save_jobs SET state='unknown',revision=? WHERE state='applying'",(uuid.uuid4().hex,))
 def settings(self,room,db=None):
  self.store.room_path(room)
  if db is None:
   with self.store.connect() as db:return self.settings(room,db)
  row=db.execute('SELECT payload FROM memory_save_settings WHERE room=?',(room,)).fetchone()
  return json.loads(row[0]) if row else dict(DEFAULTS,revision='unconfigured')
 def save(self,room,data,revision,request_id):
  self.store.room_path(room)
  if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or any(type(v) is not bool for v in data.values()) or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('記憶の自動保存の設定または操作IDが不正です。')
  signature=fingerprint([data,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成後に変更してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM memory_save_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDに別の設定があります。')
    return json.loads(old[1])
   if self.settings(room,db)['revision']!=revision:raise EditConflict('記憶の自動保存の設定が変更されています。')
   result=dict(data,revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO memory_save_settings VALUES(?,?)',(room,json.dumps(result)))
   db.execute('INSERT INTO memory_save_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
  self.next_scan=0;self.pending=True;return result

 def enqueue(self,db,room,run_id,kind,draft_id,policy,blockers=None):
  if not policy['episodes' if kind=='episode' else 'classified']:return
  if self.settings(room,db)['revision']!=policy['revision']:return
  table='episode_drafts' if kind=='episode' else 'resource_drafts'
  row=db.execute('SELECT * FROM '+table+' WHERE room=? AND id=?',(room,draft_id)).fetchone()
  if not row or row['status']!='pending':return
  key=fingerprint([run_id,kind,str(draft_id)])
  payload=dict(policy_revision=policy['revision'],draft_hash=fingerprint(dict(row)),blockers=blockers or [],error='')
  existing=db.execute('SELECT payload FROM memory_save_jobs WHERE id=?',(key,)).fetchone()
  if existing:
   previous=json.loads(existing[0])
   if previous['policy_revision']!=payload['policy_revision'] or previous['draft_hash']!=payload['draft_hash']:raise EditConflict('同じ実行の自動保存対象が変わりました。')
   return
  if db.execute("SELECT count(*) FROM memory_save_jobs WHERE state IN ('queued','applying')").fetchone()[0]>=100:raise ValueError('記憶の自動保存待機は全体100件までです。')
  db.execute('INSERT INTO memory_save_jobs VALUES(?,?,?,?,?,?,?,?)',(key,room,run_id,kind,str(draft_id),'queued',uuid.uuid4().hex,json.dumps(payload)))
  self.pending=True
 def finished_routing(self,db,item):
  row=db.execute('SELECT payload FROM autonomy_runs WHERE room=? AND id=?',(item['room'],item['routing']['parent_id'])).fetchone()
  parent=json.loads(row[0]) if row else {};policy=parent.get('memory_save_policy')
  if not policy:return
  drafts=item['routing_result']['drafts']
  for draft in drafts:
   if not draft['reused']:self.enqueue(db,item['room'],item['id'],'resource',draft['id'],policy)
  self.enqueue(db,item['room'],parent['id'],'episode',parent['episode_draft_id'],policy,[d['id'] for d in drafts])
 def view(self,room):
  settings=self.settings(room)
  with self.store.connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM memory_save_jobs WHERE room=? ORDER BY (state='unknown') DESC,(state='queued') DESC,rowid DESC LIMIT 50",(room,))]
  for row in rows:row['result']=json.loads(row.pop('payload'))
  return dict(settings=settings,runs=rows,warning=self.warning)
 def finish(self,item,state,error=''):
  payload=json.loads(item['payload']);payload['error']=error[:500]
  with self.store.lock,self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');db.execute('UPDATE memory_save_jobs SET state=?,revision=?,payload=? WHERE id=?',(state,uuid.uuid4().hex,json.dumps(payload),item['id']))
   if state!='applying':self.app.timelines.record_memory_save(db,item['room'],item['run_id'],item['id'],state,item['kind'],item['draft_id'])
 def acknowledge(self,room,key,revision):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('処理完了後に確認してください。')
   if not db.execute("UPDATE memory_save_jobs SET state='review',revision=? WHERE room=? AND id=? AND revision=? AND state='unknown'",(uuid.uuid4().hex,room,key,revision)).rowcount:raise EditConflict('確認対象が変わっています。')
  self.pending=True;return self.view(room)
 def process(self):
  if self.warning:return False
  if not self.pending and time.monotonic()<self.next_scan:return False
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job or self.app.conversation_waiters:return False
   with self.store.connect() as db:rows=db.execute("SELECT * FROM memory_save_jobs j WHERE state='queued' AND "+ready_sql("j.room")+" ORDER BY (kind='episode'),rowid LIMIT 100").fetchall()
   for row in rows:
    item=dict(row);room=item['room'];payload=json.loads(item['payload']);policy=self.settings(room)
    if policy['revision']!=payload['policy_revision'] or not policy['episodes' if item['kind']=='episode' else 'classified']:
     self.finish(item,'review','自動保存設定が変わったため草案を維持しました。');return True
    with self.store.connect() as db:
     parent=db.execute('SELECT state FROM autonomy_runs WHERE room=? AND id=?',(room,item['run_id'])).fetchone()
     if not parent or parent[0]!='succeeded':self.finish(item,'review','元の実行の正常完了を確認できません。');return True
     if item['kind']=='episode' and any(db.execute("SELECT 1 FROM resource_drafts WHERE room=? AND id=? AND status='pending'",(room,key)).fetchone() for key in payload['blockers']):continue
     table='episode_drafts' if item['kind']=='episode' else 'resource_drafts';draft=db.execute('SELECT * FROM '+table+' WHERE room=? AND id=?',(room,item['draft_id'])).fetchone()
    if not draft or draft['status']!='pending' or fingerprint(dict(draft))!=payload['draft_hash']:
     self.finish(item,'review','草案は変更/確認済みです。自動保存は行いません。');return True
    if item['kind']=='resource':
     try:
      review=self.app.resources.review(room,int(item['draft_id']))
      if review['conflict']:raise EditConflict('分類元・本文・会話が変更されています。')
     except (ValueError,OSError):self.finish(item,'review','参照元を再確認してください。分類案を維持しました。');return True
    self.finish(item,'applying')
    try:
     if item['kind']=='episode':result=self.app.episodes.approve(room,item['draft_id'],draft['day'],draft['summary'])
     else:result=self.app.resources.approve(room,int(item['draft_id']),draft['text'])
    except (ValueError,EditConflict):self.finish(item,'review','保存直前の資料変更等により、草案と原本を維持しました。');return True
    except Exception:self.finish(item,'unknown','保存結果を確認できません。原本・バックアップ・草案の状態を確認してください。自動再試行しません。');return True
    indexed=self.app.index_refresh.saved(room,result)
    self.finish(item,'saved',indexed.get('index_warning',''));return True
   self.pending=False;self.next_scan=time.monotonic()+30
  return False
