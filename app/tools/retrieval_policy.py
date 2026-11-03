"""Opt-in bounded retrieval weighting and semantic-consolidation policy."""
import json,math,re,uuid,hashlib
from datetime import datetime,timedelta,timezone,date
from litechat.file_editor import EditConflict
DEFAULTS=dict(rag_enabled=False,half_life_days=90,semantic_enabled=False,auto_apply=False,threshold=.97)
JST=timezone(timedelta(hours=9))

def analyze(query):
 if re.search(r'API|GGUF|llama|LLM|CUDA|コード|仕様|技術|実装|エラー',query,re.I):intent='technical'
 elif re.search(r'最近|昨日|今日|先週|直近',query):intent='recent'
 elif re.search(r'思い出|約束|嬉し|悲し|寂し|気持ち|感情',query):intent='personal'
 else:intent='general'
 return dict(intent=intent,dates=re.findall(r'\d{4}-\d{2}-\d{2}',query)[:5],method='local_rules',extra_llm_calls=0)

class RetrievalPolicy:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS retrieval_policy_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS retrieval_policy_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
 def settings(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:row=db.execute('SELECT payload FROM retrieval_policy_settings WHERE room=?',(room,)).fetchone()
  return json.loads(row[0]) if row else dict(DEFAULTS,revision='unconfigured')
 def save(self,room,data,revision,request_id):
  self.store.room_path(room)
  if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or any(type(data[k]) is not bool for k in ('rag_enabled','semantic_enabled','auto_apply')) or type(data['half_life_days']) is not int or not 1<=data['half_life_days']<=3650 or type(data['threshold']) not in (int,float) or not math.isfinite(data['threshold']) or not .9<=data['threshold']<=1:raise ValueError('検索/統合設定が不正です。')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  signature=hashlib.sha256(json.dumps([data,revision],sort_keys=True).encode()).hexdigest()
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job:raise ValueError('生成後に設定してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM retrieval_policy_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDの設定が異なります。')
    return json.loads(old[1])
   if self.settings(room)['revision']!=revision:raise EditConflict('設定が変更されました。再読込してください。')
   result=dict(data,revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO retrieval_policy_settings VALUES(?,?)',(room,json.dumps(result)));db.execute('INSERT INTO retrieval_policy_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)));return result
 def query(self,room,text):
  if not self.settings(room)['rag_enabled'] or len(text)<=200:return text[:200]
  # Keep words from both ends; long prefatory prose must not hide the actual question.
  words=list(dict.fromkeys(re.findall(r'[A-Za-z][A-Za-z0-9_.-]{1,}|[一-龥々]{2,}|[ァ-ヶー]{2,}|\d{4}-\d{2}-\d{2}',text[-4000:])))
  return (text[-100:]+' '+' '.join(words[-20:]))[:200]
 def scorer(self,room,query,config=None):
  config=self.settings(room) if config is None else config;affect=self.app.memory_affect.scorer(room)
  resonance=self.app.memory_resonance.settings(room)['memory']
  def base(rows):
   if resonance:self.app.memory_resonance.overlay(room,rows)
   if affect:affect(rows)
  if not config['rag_enabled']:return base if resonance or affect else None
  analysis=analyze(query);today=datetime.now(JST).date()
  def adjust(rows):
   if base:base(rows)
   for row in rows:
    original=row['score'];category=row.get('category');weight=1.
    if analysis['intent']=='technical':weight={'knowledge':1.2,'memory':.95,'conversation':.85}.get(category,1.)
    elif analysis['intent']=='personal':weight={'memory':1.15,'conversation':1.1,'knowledge':.9}.get(category,1.)
    day=str(row.get('period_end',row.get('date',row.get('created',''))))[:10];decay=1.
    try:
     parsed=date.fromisoformat(day)
     if analysis['intent']=='recent':decay=.5+.5*2**(-max(0,(today-parsed).days)/config['half_life_days'])
     if analysis['dates'] and day in analysis['dates']:weight*=1.2
    except ValueError:pass
    row.update(retrieval_base_score=original,intent=analysis['intent'],intent_weight=weight,temporal_weight=round(decay,8),score=original*weight*decay)
  return adjust
 def threshold(self,room,query,config=None):
  config=self.settings(room) if config is None else config
  if not config['rag_enabled'] or analyze(query)['intent']!='personal':return None
  if not self.app.memory_affect.settings(room)['arousal_search']:return None
  def adjust(document,value):
   effective_document=dict(document);self.app.memory_resonance.overlay(room,[effective_document])
   score=effective_document.get('arousal',0)
   return max(0.,value-.03*score) if type(score) in (int,float) and math.isfinite(score) and 0<=score<=1 else value
  return adjust
 def annotate(self,room,query,result,config=None):
  config=self.settings(room) if config is None else config
  if config['rag_enabled']:
   result['analysis']=dict(analyze(query),policy_revision=config['revision']);result['revision']=hashlib.sha256((result['revision']+config['revision']).encode()).hexdigest()
  return result
