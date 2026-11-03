"""Bounded, opt-in semantic consolidation using existing cached vectors only."""
import json,re,uuid,time,hashlib,unicodedata
from datetime import datetime
from litechat.file_editor import EditConflict
from litechat.episodes import JST
from .sleep_schedule import night_key
from .background_guard import uncertain

def compatible(a,b):
 a,b=unicodedata.normalize('NFKC',a),unicodedata.normalize('NFKC',b)
 numbers=lambda s:set(re.findall(r'\d+(?:[./:-]\d+)*',s))
 negative=lambda s:set(re.findall(r'禁止|未解決|未完了|嫌い|ない|不可|拒否|不明|否定',s))
 return numbers(a)==numbers(b) and negative(a)==negative(b)

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
class SemanticConsolidation:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.next_scan=0;self.warning=''
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS semantic_consolidation_runs(id TEXT PRIMARY KEY,room TEXT,request_id TEXT,night TEXT,kind TEXT,state TEXT,revision TEXT,payload TEXT,UNIQUE(room,request_id))')
   db.execute("CREATE INDEX IF NOT EXISTS semantic_consolidation_sources ON semantic_consolidation_runs(room,kind,json_extract(payload,'$.source_signature'))")
   db.execute("UPDATE semantic_consolidation_runs SET state='unknown' WHERE state='applying'")
 def documents(self,room,kind):
  docs=[]
  if kind=='entity':
   entries=sorted(self.app.resources.inventory(room,'entity'),key=lambda x:x['name'])[:12];observations={}
   with self.store.connect() as db:
    for entry in entries:
     row=db.execute('SELECT payload FROM entity_observations WHERE room=? AND name=?',(room,entry['name'])).fetchone()
     if row:observations[entry['name']]=json.loads(row[0])
   for entry in entries:
    doc=self.app.entity_reviews.document(room,entry['name']);observation=observations.get(entry['name'],{})
    if observation.get('version')!=[doc['revision'],doc.get('state_version',0)]:observation={}
    if observation:observation=self.app.memory_resonance.entity_observation(room,entry['name'],doc,observation)
    if observation.get('importance',.5)>=.7:continue
    if doc.get('state')=='active' and not doc.get('merge_pending') and doc['editable'] and len(doc['text'])<=16000:docs.append(dict(id=entry['name'],text=doc['text'],path=doc['path'],revision=doc['revision'],state_revision=doc['state_revision'],importance=observation.get('importance',.5)))
  elif kind=='research':
   with self.store.connect() as db:rows=db.execute("SELECT payload FROM autonomy_documents WHERE room=? AND kind='thread' AND json_extract(payload,'$.state')='active' ORDER BY id LIMIT 12",(room,)).fetchall()
   for row in rows:
    item=json.loads(row[0]);details=item.get('details',{})
    if details.get('priority',50)>=80:continue
    text='\n'.join([item['title'],item['body'],details.get('findings',''),*details.get('open_questions',[]),item['next_action']])
    if len(text)<=16000:docs.append(dict(id=item['id'],text=text,path='research/'+item['id'],revision=item['revision']))
  else:raise ValueError('entity/researchを選択してください。')
  return docs
 def preview(self,room,kind):
  app=self.app;sem=app.semantic;self.store.room_path(room)
  with app.lock,sem.lock,self.store.lock:
   config=app.retrieval_policy.settings(room)
   if not sem.identity or not sem.loaded_settings or sem.engine.status()['state']!='ready' or sem.progress['state']=='building':raise ValueError('起動済みの記憶検索モデルと既存索引が必要です。自動起動・埋め込み生成はしません。')
   docs=self.documents(room,kind);vectors=[];total=0;missing=0
   def checkpoint():
    if app.closing or app.conversation_waiters:raise EditConflict('会話または終了を優先します。')
   with sem.connect() as db:
    for doc in docs:
     checkpoint();total+=len(doc['text'])
     if total>60000:break
     vector=app.entity_semantic._centroid(db,sem.identity,doc,checkpoint)
     if vector is None:missing+=1;continue
     vectors.append((doc,vector))
   best=None
   for i,(left,a) in enumerate(vectors):
    for right,z in vectors[i+1:]:
     checkpoint()
     if len(a)!=len(z):raise ValueError('索引の次元が一致しません。')
     score=sum(x*y for x,y in zip(a,z))
     if score<config['threshold'] or not compatible((left['id']+' ' if kind=='entity' else '')+left['text'],(right['id']+' ' if kind=='entity' else '')+right['text']):continue
     if not best or score>best['score']:best=dict(target=left,source=right,score=score)
   result=dict(room=room,kind=kind,candidate=best,model=sem.identity,config_revision=config['revision'],missing=missing,scanned=len(vectors),limits='各分類先頭12件/合計6万文字。既存索引だけ。数値・否定語の差は見送り。類似は同一性や事実の一致を保証しません。')
   result['revision']=fingerprint(result);return result
 def listing(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:return dict(runs=[dict(row,payload=json.loads(row['payload'])) for row in db.execute('SELECT * FROM semantic_consolidation_runs WHERE room=? ORDER BY rowid DESC LIMIT 50',(room,))])
 def propose(self,room,kind,revision,request_id,night='manual'):
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  app=self.app
  with app.lock,app.semantic.lock,self.store.lock:
   if app.closing or app.job or app.conversation_waiters:raise ValueError('会話・生成後に確認してください。')
   with self.store.connect() as db:old=db.execute('SELECT * FROM semantic_consolidation_runs WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    data=json.loads(old['payload'])
    if data['preview_revision']!=revision:raise EditConflict('同じ操作IDの資料が異なります。')
    return dict(old,payload=data)
   preview=self.preview(room,kind)
   if preview['revision']!=revision:raise EditConflict('候補・設定・索引が変わりました。再確認してください。')
   pair=preview['candidate']
   if not pair:raise ValueError('条件を満たす索引済み候補がありません。')
   left,right=pair['target'],pair['source'];key=uuid.uuid4().hex;payload=dict(preview_revision=revision,candidate=pair,model=preview['model'],config_revision=preview['config_revision'],source_signature=fingerprint([kind,preview['model'],pair]))
   if kind=='entity':
    text=left['text'].rstrip()+'\n\n---\n参照元：'+right['id']+'\n'+right['text'];payload['text']=text
   else:
    comparison=app.research_merges.preview(room,left['id'],right['id']);payload['comparison_revision']=comparison['revision'];payload['proposal']=comparison['proposal'];payload['warnings']=list(comparison['warnings'])
    if comparison['references']['source']['incoming']:payload['warnings'].append('統合元を参照する活動があります。自動適用せず、関連参照も確認してください。')
    if len(comparison['proposal']['body'])>4000:raise ValueError('統合本文が4000文字を超えるため見送ります。')
   with self.store.connect() as db:db.execute("INSERT INTO semantic_consolidation_runs VALUES(?,?,?,?,?,'pending',?,?)",(key,room,request_id,night,kind,uuid.uuid4().hex,json.dumps(payload,ensure_ascii=False)))
   return next(r for r in self.listing(room)['runs'] if r['id']==key)
 def approve(self,room,key,revision):
  app=self.app
  with app.lock,self.store.lock:
   self.store.room_path(room)
   if app.closing or app.job or app.conversation_waiters:raise ValueError('処理中です。')
   with self.store.connect() as db:row=db.execute('SELECT * FROM semantic_consolidation_runs WHERE room=? AND id=?',(room,key)).fetchone()
   if not row:raise ValueError('確認案が見つかりません。')
   if row['state']=='complete':return dict(row,payload=json.loads(row['payload']))
   if row['state']!='pending' or row['revision']!=revision:raise EditConflict('確認案の状態が変わりました。')
   p=json.loads(row['payload']);left,right=p['candidate']['target'],p['candidate']['source']
   def guard():
    if app.closing or app.conversation_waiters or app.retrieval_policy.settings(room)['revision']!=p['config_revision']:raise EditConflict('設定・会話待ちが変わりました。')
   guard()
   current={doc['id']:doc for doc in self.documents(room,row['kind'])}
   if any(doc['id'] not in current or current[doc['id']]!=doc for doc in (left,right)):raise EditConflict('本文・状態・重要度が変わりました。候補を再確認してください。')
   # Source revisions and cross-references are revalidated by the existing reversible managers.
   with self.store.connect() as db:db.execute("UPDATE semantic_consolidation_runs SET state='applying' WHERE id=?",(key,))
   try:
    if row['kind']=='entity':
     draft=app.entity_reviews.propose(room,left['id'],right['id'],left['revision'],right['revision'],p['text'],'semanticdraft_'+key,guard)
     result=app.entity_merges.merge(room,draft['draft_id'],p['text'],left['state_revision'],right['state_revision'],'semanticmerge_'+key,guard)
     p['result']=result
     if result.get('phase')!='applied':raise RuntimeError('統合結果が未確定です。統合履歴を確認してください。')
    else:result,_=app.research_merges.apply(room,left['id'],right['id'],p['comparison_revision'],p['proposal'],'semanticresearch_'+key,checkpoint=guard)
    state='complete';p['result']=result;p['index_refresh']=app.index_refresh.saved(room,{'changed':True})
   except (ValueError,EditConflict) as error:state='conflict';p['error']=str(error)
   except Exception as error:state='unknown';p['error']=str(error)[:500]
   with self.store.connect() as db:db.execute('UPDATE semantic_consolidation_runs SET state=?,revision=?,payload=? WHERE id=?',(state,uuid.uuid4().hex,json.dumps(p,ensure_ascii=False),key))
   return next(r for r in self.listing(room)['runs'] if r['id']==key)
 def cancel(self,room,key,revision):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   row=db.execute('SELECT state,revision FROM semantic_consolidation_runs WHERE room=? AND id=?',(room,key)).fetchone()
   if not row or row[0]!='pending' or row[1]!=revision:raise EditConflict('取消可能な確認案ではありません。')
   db.execute("UPDATE semantic_consolidation_runs SET state='cancelled',revision=? WHERE room=? AND id=?",(uuid.uuid4().hex,room,key));return dict(cancelled=True)
 def acknowledge(self,room,key,revision):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   row=db.execute('SELECT state,revision FROM semantic_consolidation_runs WHERE room=? AND id=?',(room,key)).fetchone()
   if not row or row[0]!='unknown' or row[1]!=revision:raise EditConflict('結果不明の最新の記録を指定してください。')
   db.execute("UPDATE semantic_consolidation_runs SET state='reviewed_unknown',revision=? WHERE room=? AND id=?",(uuid.uuid4().hex,room,key));return dict(reviewed=True,outcome='unknown',replayed=False)
 def tick(self,moment=None):
  if self.warning:return False
  if moment is None and time.monotonic()<self.next_scan:return False
  self.next_scan=time.monotonic()+60;moment=moment or datetime.now(JST);app=self.app
  with app.lock:
   if app.closing or app.job or app.conversation_waiters:return False
   with self.store.connect() as db:rows=db.execute("SELECT room,payload FROM retrieval_policy_settings WHERE room NOT IN(SELECT room FROM room_states WHERE archived=1) AND json_extract(payload,'$.semantic_enabled')=1 ORDER BY room LIMIT 200").fetchall()
   for row in rows:
    room=row['room'];config=json.loads(row['payload']);window=app.sleep_schedule.settings(room);night=night_key(moment,window['start'],window['end'])
    if night is None:continue
    with self.store.connect() as db:
     if uncertain(db,room):continue
    for kind in ('entity','research'):
     with self.store.connect() as db:
      if db.execute('SELECT 1 FROM semantic_consolidation_runs WHERE room=? AND night=? AND kind=?',(room,night,kind)).fetchone():continue
     try:
      preview=self.preview(room,kind)
      if not preview['candidate']:continue
      source_signature=fingerprint([kind,preview['model'],preview['candidate']])
      with self.store.connect() as db:
       if db.execute("SELECT 1 FROM semantic_consolidation_runs WHERE room=? AND kind=? AND json_extract(payload,'$.source_signature')=? LIMIT 1",(room,kind,source_signature)).fetchone():continue
      run=self.propose(room,kind,preview['revision'],'night:'+night+':'+kind,night)
      if config['auto_apply'] and not run['payload'].get('warnings'):self.approve(room,run['id'],run['revision'])
      return True
     except (ValueError,EditConflict):continue
  return False
