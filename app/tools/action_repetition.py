"""Opt-in report semantics and verified unchanged read-result repetition."""
import json,time,uuid,math
import http.client,sqlite3
from litechat.report_repetition import evaluate as report_comparison,DRIVES
from litechat.semantic import unit
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict
DEFAULTS=dict(semantic_reports=False,tool_results=False)
class ActionRepetition:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.cache={}
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS action_repetition_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS action_repetition_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
 def settings(self,room,db=None):
  self.store.room_path(room)
  if db is None:
   with self.store.connect() as db:return self.settings(room,db)
  row=db.execute('SELECT payload FROM action_repetition_settings WHERE room=?',(room,)).fetchone()
  return json.loads(row[0]) if row else dict(DEFAULTS,revision='unconfigured')
 def save(self,room,data,revision,request_id):
  self.store.room_path(room)
  if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or any(type(v) is not bool for v in data.values()) or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('反復評価の設定または操作IDが不正です。')
  signature=fingerprint([data,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成後に変更してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM action_repetition_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDに別の設定があります。')
    return json.loads(old[1])
   if self.settings(room,db)['revision']!=revision:raise EditConflict('反復評価の設定が変更されています。')
   result=dict(data,revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO action_repetition_settings VALUES(?,?)',(room,json.dumps(result)))
   db.execute('INSERT INTO action_repetition_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
  return result

 def semantic(self,first,second,stats):
  sem=self.app.semantic;a=first.get('text','').strip();b=second.get('text','').strip()
  if min(len(a),len(b))<80 or max(len(a),len(b))>4000:return None
  if not self.app.lock.acquire(blocking=False):stats['reason']='会話の開始処理を優先します';return None
  if self.app.job or self.app.conversation_waiters or not sem.identity or not sem.lock.acquire(blocking=False):self.app.lock.release();stats['reason']='会話または検索モデルの使用中です';return None
  try:
   if sem.progress['state']=='building' or sem.engine.status()['state']!='ready':stats['reason']='起動済みの検索モデルが必要です';return None
   key=fingerprint([sem.identity,a,b])
   if key in self.cache:stats['cache_hits']+=1;return self.cache[key]
   stats['embedding_calls']+=1;started=time.perf_counter()
   try:vectors=sem.embed([a,b]);left,right=unit(vectors[0]),unit(vectors[1])
   finally:stats['embedding_seconds']+=time.perf_counter()-started
   if len(left)!=len(right):return None
   score=max(-1.,min(1.,sum(x*y for x,y in zip(left,right))))
   if len(self.cache)>=16:self.cache.pop(next(iter(self.cache)))
   self.cache[key]=score;return score
  except (OSError,ValueError,sqlite3.Error,http.client.HTTPException,IndexError,TypeError):stats['reason']='意味比較を利用できないため反復と判定しません';return None
  finally:sem.lock.release();self.app.lock.release()
 def current(self,room,name,data,stats):
  key=fingerprint([name,data.get('kind'),data.get('name'),data.get('revision'),data.get('state_revision')])
  if key in stats['source_checks']:return stats['source_checks'][key]
  if len(stats['source_checks'])>=12:return False
  valid=False
  try:
   if name=='read_notepad':doc=self.app.resources.read(room,'notepad')
   elif name=='read_memory_document' and data.get('kind') not in ('working','secret'):doc=self.app.resources.read(room,data['kind'],data.get('name',''))
   else:return False
   valid=doc['revision']==data.get('revision') and doc['exists']==data.get('exists') and not doc.get('merge_pending') and ('state_revision' not in data or doc.get('state_revision')==data['state_revision'])
  except (ValueError,OSError,KeyError):pass
  stats['source_checks'][key]=valid;return valid
 def tools(self,db,room,first,second,stats):
  def signatures(item):
   count=item.get('metrics',{}).get('tool_calls')
   if type(count) is not int or not 1<=count<=12:return None
   rows=db.execute("SELECT e.name,e.result,i.fingerprint,e.status,e.over_budget FROM tool_events e JOIN tool_invocations i ON i.event_id=e.id AND i.room=e.room AND i.job=e.job WHERE e.room=? AND e.job=? AND i.state='complete' ORDER BY e.id LIMIT 13",(room,item['id'])).fetchall()
   if len(rows)!=count:return None
   result=[]
   for name,raw,input_hash,status,over_budget in rows:
    spec=self.app.local_tools.registry.get(name)
    if spec is None or spec.operation!='read' or status not in ('ok','no_change') or over_budget or len(raw)>16000:return None
    try:value=json.loads(raw)
    except (ValueError,TypeError):return None
    if value.get('truncated') or value.get('error') or value.get('continuation') or value.get('changes') or not self.current(room,name,value.get('data',{}),stats):return None
    result.append((name,input_hash,fingerprint([value.get('status'),value.get('data')])))
   return result
  a,b=signatures(first),signatures(second)
  if a and a==b:stats['evidence'].append(dict(newer_id=first['id'],older_id=second['id'],signatures=[fingerprint(s) for s in a]));return 1.
  return None
 def evaluate(self,db,room,clock,config,activity_plan,last_interaction):
  policy=self.settings(room,db);result=dict(settings=policy,penalties={k:0. for k in DRIVES},matches=[],embedding_calls=0,embedding_seconds=0.,cache_hits=0,evidence=[],source_checks={},reason='追加の反復評価はオフです')
  if not config['enabled'] or not any(policy.get(k) for k in DEFAULTS):return result
  result['reason']='報告や参照結果の反復候補です。実作業の重複を示す証明ではありません'
  if policy['semantic_reports']:
   report=report_comparison(db,room,clock,config,activity_plan,last_interaction,comparator=lambda a,b:self.semantic(a,b,result),threshold=.92)
   for match in report['matches']:result['matches'].append(dict(match,method='semantic_report'))
   result['penalties']=report['penalties']
  if policy['tool_results']:
   tools=report_comparison(db,room,clock,config,activity_plan,last_interaction,comparator=lambda a,b:self.tools(db,room,a,b,result),threshold=1.,require_tools=True)
   for match in tools['matches']:result['matches'].append(dict(match,method='identical_read_results'))
   result['penalties']={k:max(result['penalties'][k],tools['penalties'][k]) for k in DRIVES}
  return result
