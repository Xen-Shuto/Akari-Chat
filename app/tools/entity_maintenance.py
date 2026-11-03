"""Opt-in exact duplicate consolidation and observed-age entity dormancy."""
import json,time,uuid
from datetime import datetime
from litechat.episodes import JST,fingerprint
from litechat.file_editor import EditConflict
from .sleep_schedule import night_key
from .background_guard import uncertain
from .entity_tools import normalized_name
from litechat.file_editor import normalize
from pathlib import PurePosixPath

DEFAULTS=dict(enabled=False,auto_dormant=False,days=90)
class EntityMaintenance:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.next_scan=0;self.warning=''
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS entity_observations(room TEXT,name TEXT,payload TEXT,PRIMARY KEY(room,name))')
   db.execute('CREATE TABLE IF NOT EXISTS entity_usage_receipts(room TEXT,event_id TEXT,PRIMARY KEY(room,event_id))')
   db.execute('CREATE TABLE IF NOT EXISTS entity_maintenance_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS entity_maintenance_runs(id TEXT PRIMARY KEY,room TEXT,night TEXT,state TEXT,revision TEXT,payload TEXT,UNIQUE(room,night))')
   db.execute('CREATE TABLE IF NOT EXISTS entity_maintenance_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
   db.execute("UPDATE entity_maintenance_runs SET state='unknown',revision=? WHERE state='applying'",(uuid.uuid4().hex,))
 def settings(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:row=db.execute('SELECT payload FROM entity_maintenance_settings WHERE room=?',(room,)).fetchone()
  return dict(DEFAULTS,revision='unconfigured') if not row else json.loads(row[0])
 def view(self,room):
  config=self.settings(room)
  with self.store.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM entity_maintenance_runs WHERE room=? ORDER BY rowid DESC LIMIT 30',(room,))]
  for row in rows:row['result']=json.loads(row.pop('payload'))
  return dict(settings=config,runs=rows,warning=self.warning,entries=self.inventory(room))
 def save(self,room,data,revision,request_id):
  self.store.room_path(room)
  if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or any(type(data[k]) is not bool for k in ('enabled','auto_dormant')) or type(data['days']) is not int or not 1<=data['days']<=365 or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('整理設定または操作IDが不正です。')
  signature=fingerprint([data,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成後に設定してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM entity_maintenance_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDに別の設定があります。')
    return json.loads(old[1])
   if self.settings(room)['revision']!=revision:raise EditConflict('整理設定が変わっています。再読込してください。')
   result=dict(data,since=time.time() if data['auto_dormant'] and not self.settings(room)['auto_dormant'] else self.settings(room).get('since'),revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO entity_maintenance_settings VALUES(?,?)',(room,json.dumps(result)))
   db.execute('INSERT INTO entity_maintenance_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
  self.next_scan=0;return result
 def acknowledge(self,room,key,revision):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('処理終了後に確認してください。')
   if not db.execute("UPDATE entity_maintenance_runs SET state='reviewed',revision=? WHERE room=? AND id=? AND revision=? AND state='unknown'",(uuid.uuid4().hex,room,key,revision)).rowcount:raise EditConflict('確認対象が変わっています。')
  return self.view(room)

 def observe(self,room,name,document=None,*,used=False,event_id=None,clock=None):
  clock=time.time() if clock is None else clock;document=document or self.app.resources.read(room,'entity',name)
  with self.store.lock,self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT payload FROM entity_observations WHERE room=? AND name=?',(room,name)).fetchone();old=json.loads(old[0]) if old else None
   version=[document['revision'],document.get('state_version',0)]
   data=old if old and old['version']==version else dict(name=name,version=version,observed=clock,last_used=None,importance=.5,revision=uuid.uuid4().hex)
   if used and event_id:
    if db.execute('SELECT 1 FROM entity_usage_receipts WHERE room=? AND event_id=?',(room,event_id)).fetchone():return data
    db.execute('INSERT INTO entity_usage_receipts VALUES(?,?)',(room,event_id));data.update(last_used=clock,use_count=data.get('use_count',0)+1,revision=uuid.uuid4().hex)
   db.execute('INSERT OR REPLACE INTO entity_observations VALUES(?,?,?)',(room,name,json.dumps(data)))
  return self.app.memory_resonance.entity_observation(room,name,document,data)
 def inventory(self,room):
  with self.store.connect() as db:rows={r[0]:json.loads(r[1]) for r in db.execute('SELECT name,payload FROM entity_observations WHERE room=?',(room,))}
  entries=[]
  for e in self.app.resources.inventory(room,'entity')[:100]:
   doc=self.app.resources.read(room,'entity',e['name']);observation=rows.get(e['name'])
   if observation and observation['version']!=[doc['revision'],doc.get('state_version',0)]:observation=None
   if observation:observation=self.app.memory_resonance.entity_observation(room,e['name'],doc,observation)
   entries.append(dict(name=e['name'],state=e.get('state','active'),observation=observation,revision=observation['revision'] if observation else doc['state_revision']))
  return entries
 def importance(self,room,name,value,revision,request_id):
  if type(value) not in (int,float) or not 0<=value<=1 or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('重要度は0～1で指定してください。')
  signature=fingerprint([name,value,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job:raise ValueError('処理完了後に変更してください。')
   old=db.execute('SELECT signature,result FROM entity_maintenance_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDに別の重要度があります。')
    return json.loads(old[1])
   doc=self.app.resources.read(room,'entity',name)
   if not doc['exists']:raise ValueError('既存のエンティティを指定してください。')
   observed=db.execute('SELECT payload FROM entity_observations WHERE room=? AND name=?',(room,name)).fetchone();previous=json.loads(observed[0]) if observed else None
   expected=previous['revision'] if previous and previous['version']==[doc['revision'],doc.get('state_version',0)] else doc['state_revision']
   if expected!=revision:raise EditConflict('本文・状態・使用記録が変わりました。再読込してください。')
   data=self.observe(room,name,doc)
   db.execute('BEGIN IMMEDIATE');data=json.loads(db.execute('SELECT payload FROM entity_observations WHERE room=? AND name=?',(room,name)).fetchone()[0]);data.update(importance=float(value),importance_revision=uuid.uuid4().hex,revision=uuid.uuid4().hex)
   db.execute('UPDATE entity_observations SET payload=? WHERE room=? AND name=?',(json.dumps(data),room,name));db.execute('INSERT INTO entity_maintenance_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(data)))
  return data
 def used(self,room,rows,event_id):
  try:self._used(room,rows,event_id)
  except Exception:self.warning='使用記録を保存できないため、自動エンティティ整理を停止しました。原本は保持しています。'
 def _used(self,room,rows,event_id):
  policy=self.settings(room)
  if not policy['enabled'] and not policy['auto_dormant']:return
  for row in rows:
   path=PurePosixPath(row.get('path','').replace('\\','/'))
   if path.parent.as_posix()!='memory/entities' or path.suffix!='.md':continue
   doc=self.app.resources.read(room,'entity',path.stem)
   if doc['exists'] and not doc.get('merge_pending'):self.observe(room,path.stem,doc,used=True,event_id=fingerprint([event_id,path.stem]))
 def tick(self,moment=None):
  if self.warning:return False
  explicit=moment is not None
  if not explicit and time.monotonic()<self.next_scan:return False
  self.next_scan=time.monotonic()+60;moment=moment or datetime.now(JST);clock=moment.timestamp()
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job or self.app.conversation_waiters:return False
   with self.store.connect() as db:configs=db.execute('SELECT room,payload FROM entity_maintenance_settings WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) ORDER BY room').fetchall()
   for room,raw in configs:
    config=json.loads(raw)
    if not config['enabled'] and not config['auto_dormant']:continue
    try:window=self.app.sleep_schedule.settings(room);night=night_key(moment,window['start'],window['end'])
    except ValueError:continue
    if night is None:continue
    with self.store.connect() as db:
     if uncertain(db,room):continue
     if db.execute("SELECT 1 FROM entity_maintenance_runs WHERE room=? AND (night=? OR state='unknown')",(room,night)).fetchone():continue
     if db.execute("SELECT 1 FROM autonomy_runs WHERE room=? AND state IN ('queued','running','unknown') AND COALESCE(json_extract(payload,'$.reviewed'),0)=0",(room,)).fetchone():continue
     history=db.execute('SELECT target,source FROM entity_merges WHERE room=? LIMIT 501',(room,)).fetchall()
    entries=self.app.resources.inventory(room,'entity');documents=[];reason='対象はありません。';pair=None;candidate=None;total=0
    if len(entries)<=100 and len(history)<=500:
     for entry in entries:
      doc=self.app.resources.read(room,'entity',entry['name']);total+=len(doc['text'])
      if total>2000000:documents=[];reason='本文の確認上限です。';break
      if not doc['exists'] or not doc['editable'] or doc.get('state')!='active' or doc.get('merge_pending'):continue
      data=self.observe(room,entry['name'],doc,clock=clock);documents.append((entry['name'],doc,data))
     groups={}
     if config['enabled']:
      for name,doc,data in documents:
       key=normalized_name(name)
       if key:groups.setdefault(key,[]).append((name,doc,data))
      for group in groups.values():
       if len(group)!=2:continue
       left,right=sorted(group,key=lambda v:v[0]);a,b=left[1],right[1]
       if len(a['text'])>32000 or not a['text'].strip() or normalize(a['text'])!=normalize(b['text']):continue
       if any({r[0],r[1]}=={left[0],right[0]} for r in history):continue
       pair=(left,right);break
     if config['auto_dormant'] and not pair:
      for name,doc,data in documents:
       anchor=max(data['observed'],data['last_used'] or 0,config.get('since') or clock)
       if data['importance']<=.4 and clock-anchor>=config['days']*86400:candidate=(name,doc);break
    else:reason='件数の確認上限です。'
    key=uuid.uuid4().hex;payload=dict(config_revision=config['revision'],reason=reason);action='merge' if pair else 'dormant' if candidate else None
    with self.store.connect() as db:db.execute('INSERT INTO entity_maintenance_runs VALUES(?,?,?,?,?,?)',(key,room,night,'applying' if action else 'skipped',uuid.uuid4().hex,json.dumps(payload)))
    if not action:return True
    try:
     def guard():
      current=self.settings(room);instant=moment if explicit else datetime.now(JST);window=self.app.sleep_schedule.settings(room)
      if current['revision']!=config['revision'] or night_key(instant,window['start'],window['end'])!=night or self.app.conversation_waiters or self.app.closing:raise EditConflict('設定・時間帯・会話待ちが変わりました。')
     guard()
     if pair:
      left,right=pair;a,b=left[1],right[1];draft=self.app.entity_reviews.propose(room,left[0],right[0],a['revision'],b['revision'],a['text'],'entitydraft_'+key,guard,_duplicate=True)
      result=self.app.entity_merges.merge(room,draft['draft_id'],a['text'],a['state_revision'],b['state_revision'],'entityauto_'+key,guard)
      payload.update(merge_id=result['id'],target=left[0],source=right[0],reason='同じ名前・本文の重複を退避しました。');state='complete' if result['phase']=='applied' else 'unknown' if result.get('recovery_required') else 'skipped'
      if state!='complete':payload['reason']=result.get('error') or '統合完了を確認できません。既存の統合履歴を確認してください。'
     else:
      name,doc=candidate;result=self.app.entity_states.change(room,name,'dormant',doc['state_revision'],'entitysleep_'+key,guard);payload.update(name=name,reason='低重要度で指定日数以上使用されていないため休眠しました。');state='complete'
    except (ValueError,EditConflict) as error:payload['reason']=str(error);state='skipped'
    except Exception as error:payload['reason']='結果未確認。統合・状態履歴を確認してください。 '+str(error)[:200];state='unknown'
    with self.store.connect() as db:db.execute('UPDATE entity_maintenance_runs SET state=?,revision=?,payload=? WHERE id=?',(state,uuid.uuid4().hex,json.dumps(payload),key))
    return True
  return False
