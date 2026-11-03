"""Common conversation endpoint with explicit room overrides; encrypted keys, no automatic paid requests."""
import hashlib,json,threading,uuid
from dataclasses import dataclass,field
from urllib.parse import urlsplit
from .external_secrets import protect,unprotect
from .conversation_capabilities import detect
KEY_MASK="••••••••••••"
from litechat.file_editor import EditConflict

PROVIDERS={'local':'','openai':'https://api.openai.com/v1','gemini':'https://generativelanguage.googleapis.com/v1beta','anthropic':'https://api.anthropic.com/v1','compatible':''}
@dataclass(frozen=True)
class APITarget:
 provider:str
 endpoint:str
 model:str
 key:str=field(repr=False)
 vision:bool=False
 tools:bool=False
 sampling:bool=False
 revision:str=''
 max_output_tokens:int=0

def endpoint(value):
 if not isinstance(value,str) or len(value)>2048:raise ValueError('接続先URLが不正です。')
 p=urlsplit(value)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or any(c.isspace() for c in value) or '..' in p.path.split('/'):raise ValueError('認証情報・クエリを含まないAPIベースURLを指定してください。')
 if p.scheme=='http' and p.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('外部APIにはHTTPSを使用してください。')
 try:p.port
 except ValueError:raise ValueError('ポート番号が不正です。') from None
 return value.rstrip('/')

class ConversationAPI:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.lock=threading.RLock()
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS conversation_api_audit(id INTEGER PRIMARY KEY,room TEXT,job TEXT,action TEXT,state TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
   db.execute('CREATE TABLE IF NOT EXISTS conversation_api(room TEXT PRIMARY KEY,revision TEXT,value TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS conversation_api_profiles(room TEXT,provider TEXT,value TEXT,PRIMARY KEY(room,provider))')
   db.execute('CREATE TABLE IF NOT EXISTS conversation_api_versions(id INTEGER PRIMARY KEY,room TEXT,revision TEXT,value TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS conversation_model_catalog(scope TEXT,provider TEXT,endpoint TEXT,model TEXT,capabilities TEXT,PRIMARY KEY(scope,provider,endpoint,model))')
   db.execute('CREATE TABLE IF NOT EXISTS conversation_api_receipts(id TEXT PRIMARY KEY,signature TEXT,result TEXT)')
 def raw(self,room):
  if not isinstance(room,str):raise ValueError('設定の対象が不正です。')
  if room:
   self.validate_room(room)
  with self.store.connect() as db:row=db.execute('SELECT * FROM conversation_api WHERE room=?',(room,)).fetchone()
  return {'revision':row['revision'],'values':json.loads(row['value'])} if row else {'revision':'0','values':dict(provider='local',model='',endpoint='',vision=False,tools=False,sampling=False,inherit=bool(room))}
 def validate_room(self,room):
  root=self.store.room_path(room)
  if root.is_symlink() or getattr(root.lstat(),'st_file_attributes',0)&0x400:raise ValueError('リンク/ジャンクションのルームにはAPI認証を登録できません。')
  if room not in {p.name for p in self.store.characters.iterdir()}:raise ValueError('一覧のルームを指定してください。')
 def capabilities(self,scope,values):
  found=detect(values['provider'],values.get('model',''))
  with self.store.connect() as db:row=db.execute('SELECT capabilities FROM conversation_model_catalog WHERE scope=? AND provider=? AND endpoint=? AND model=?',(scope,values['provider'],values.get('endpoint',''),values.get('model',''))).fetchone()
  if row:found=json.loads(row['capabilities'])
  # Preserve existing explicit settings only when neither metadata nor known IDs decides.
  found=dict(found)
  for name in ('vision','tools'):
   if found[name] is None and not values.get('capabilities_auto') and values.get(name) is True:found[name]=True;found['source']='previous-setting'
  return found
 def automatic(self,scope,values):
  v=dict(values);caps=self.capabilities(scope,v)
  v.update(vision=caps['vision'] is True,tools=caps['tools'] is True,capabilities=caps)
  return v
 def resolved(self,room):
  doc=self.raw(room);scope=room;source='room' if room else 'common'
  if room and doc['values'].get('inherit',False):
   common=self.raw('');doc=dict(revision='common:'+doc['revision']+':'+common['revision'],values=common['values']);scope='';source='common'
  v=self.automatic(scope,doc['values']);revision=doc['revision']
  if v['provider']!='local':revision+=':caps:'+hashlib.sha256(json.dumps(v['capabilities'],sort_keys=True).encode()).hexdigest()[:16]
  return dict(revision=revision,values=v,source=source)
 def revision(self,room):return self.resolved(room)['revision']
 def public(self,room):
  doc=self.raw(room);v=self.automatic(room,doc['values']);secret=v.pop('key','');v['key_saved']=bool(secret);matches=[]
  with self.store.connect() as db:rows=db.execute('SELECT room,value FROM conversation_api WHERE room!=? UNION SELECT room,value FROM conversation_api_profiles WHERE room!=? ORDER BY room',(room,room)).fetchall()
  for row in rows:
   other=json.loads(row['value'])
   if row['room'] not in matches and secret and other.get('key') and unprotect(secret)==unprotect(other['key']):matches.append(row['room'])
  effective=self.resolved(room);ev={k:v for k,v in effective['values'].items() if k!='key'};ev['key_saved']=bool(effective['values'].get('key'));v.setdefault('inherit',False)
  profiles={provider:{**{k:value for k,value in config.items() if k!='key'},'key_saved':bool(config.get('key'))} for provider,config in self.profiles(room,doc['values']).items()}
  return dict(room=room,revision=doc['revision'],values=v,profiles=profiles,duplicates=matches,effective=dict(revision=effective['revision'],values=ev,source=effective['source']))
 def profiles(self,room,active):
  with self.store.connect() as db:
   rows=db.execute('SELECT provider,value FROM conversation_api_profiles WHERE room=?',(room,)).fetchall()
  profiles={row['provider']:json.loads(row['value']) for row in rows}
  profiles[active['provider']]=active
  return profiles
 def editor_key(self,room,expected,provider=None):
  """Authenticated settings editor only; never include this in public()."""
  if provider is not None and (not isinstance(provider,str) or provider not in PROVIDERS):raise ValueError('プロバイダーが不正です。')
  with self.app.lock,self.lock:
   doc=self.raw(room)
   if doc['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
   config=doc['values'] if provider is None else self.profiles(room,doc['values']).get(provider,{})
   return dict(key=unprotect(config['key']) if config.get('key') else '')
 def save(self,room,values,expected,request_id):
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  if not isinstance(values,dict) or set(values)-{'provider','endpoint','model','key','clear_key','vision','tools','sampling','inherit'}:raise ValueError('設定項目が不正です。')
  signature=hashlib.sha256(json.dumps([room,values,expected],sort_keys=True).encode()).hexdigest()
  with self.app.lock,self.store.lock,self.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話の処理完了後に設定してください。')
   receipt=db.execute('SELECT * FROM conversation_api_receipts WHERE id=?',(request_id,)).fetchone()
   if receipt:
    if receipt['signature']!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
    return json.loads(receipt['result'])
   old=self.raw(room)
   if old['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
   provider=values.get('provider',old['values']['provider'])
   if not isinstance(provider,str) or provider not in PROVIDERS:raise ValueError('プロバイダーが不正です。')
   previous=self.profiles(room,old['values']).get(provider,old['values'])
   out=self.normalize(previous,values,room)
   version=uuid.uuid4().hex;db.execute('BEGIN IMMEDIATE');db.execute('INSERT INTO conversation_api_versions(room,revision,value) VALUES(?,?,?)',(room,old['revision'],json.dumps(old['values'])))
   if old['revision']!='0':
    db.execute('INSERT OR REPLACE INTO conversation_api_profiles VALUES(?,?,?)',(room,old['values']['provider'],json.dumps(old['values'])))
   db.execute('INSERT OR REPLACE INTO conversation_api_profiles VALUES(?,?,?)',(room,provider,json.dumps(out)))
   db.execute('INSERT OR REPLACE INTO conversation_api VALUES(?,?,?)',(room,version,json.dumps(out)))
   result=dict(room=room,revision=version,values={k:v for k,v in out.items() if k!='key'})
   result['values']['key_saved']=bool(out.get('key'));result['duplicates']=[]
   db.execute('INSERT INTO conversation_api_receipts VALUES(?,?,?)',(request_id,signature,json.dumps(result)));db.execute('INSERT INTO conversation_api_audit(room,job,action,state) VALUES(?,?,?,?)',(room,request_id,'settings','saved'));return result
 def normalize(self,old,values,room,require_model=True):
   out=dict(old);provider=values.get('provider',out['provider'])
   if not isinstance(provider,str) or provider not in PROVIDERS:raise ValueError('プロバイダーが不正です。')
   inherit=values.get('inherit',False if 'provider' in values else out.get('inherit',False))
   if type(inherit) is not bool or not room and inherit:raise ValueError('共通設定に従う指定が不正です。')
   # Switching providers never carries a credential to a different service.
   if provider!=out['provider']:
    out=dict(provider=provider,model='',endpoint=PROVIDERS[provider],vision=False,tools=False,sampling=False)
   out['provider']=provider;out['inherit']=inherit
   for name in ('vision','tools','sampling','clear_key'):
    if name in values and type(values[name]) is not bool:raise ValueError('ON/OFF指定が不正です。')
    if name in values and name!='clear_key':out[name]=values[name]
   if values.get('clear_key'):out.pop('key',None)
   for name,maximum in (('model',200),('key',8192)):
    v=values.get(name,'')
    if not isinstance(v,str) or len(v)>maximum or any(ord(c)<32 for c in v):raise ValueError('モデルID/認証情報の形式が不正です。')
    if name=='model' and name in values:out[name]=v.strip()
    elif name=='key' and v:
     if v==KEY_MASK:raise ValueError('伏せ字はAPIキーとして登録できません。新しいキーを入力してください。')
     if any(c.isspace() for c in v):raise ValueError('APIキーに空白を含めないでください。')
     out['key']=protect(v)
   if provider!='local':
    out['endpoint']=endpoint(values.get('endpoint',out.get('endpoint')) or PROVIDERS[provider])
    if provider!='compatible' and out['endpoint']!=PROVIDERS[provider]:raise ValueError('公式プロバイダーの接続先は固定です。任意URLは互換APIで設定してください。')
    if require_model and not inherit and not out.get('model'):raise ValueError('利用するモデルIDを指定してください。')
    if provider=='gemini' and any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for c in out['model']):raise ValueError('GeminiのモデルIDが不正です。')
    previous=old.get('endpoint','')
    if provider=='compatible' and previous and previous!=out['endpoint'] and not values.get('key'):out.pop('key',None)
   if any(old.get(k)!=out.get(k) for k in ('provider','endpoint','model')):
    for name in ('vision','tools'):
     if name not in values:out[name]=False
   caps=self.capabilities(room,out)
   for name in ('vision','tools'):
    if caps[name] is not None:out[name]=caps[name]
   out['capabilities_auto']=caps['source']!='previous-setting'
   return out
 def preview_capabilities(self,room,values,expected):
  if not isinstance(values,dict) or set(values)-{'provider','endpoint','model','inherit'}:raise ValueError('設定項目が不正です。')
  old=self.raw(room)
  if old['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
  config=self.normalize(old['values'],values,room,require_model=False)
  return self.capabilities(room,config)
 def models(self,room,values,expected):
  from .conversation_models import list_models
  if not isinstance(values,dict) or set(values)-{'provider','endpoint','model','key','clear_key','vision','tools','sampling','inherit'}:raise ValueError('設定項目が不正です。')
  with self.app.lock,self.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   old=self.raw(room)
   if old['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
   if room and values.get('inherit',old['values'].get('inherit',False)):raise ValueError('共通設定のモデル一覧は共通設定から取得してください。')
   if self.app.job or self.app.conversation_waiters:raise ValueError('会話の処理完了後にモデル一覧を取得してください。')
   config=self.normalize(old['values'],values,room,require_model=False)
   if config['provider']=='local':raise ValueError('ローカルモデルは会話モデル設定で選択してください。')
   if not config.get('key'):raise ValueError('APIキーを入力してください。')
   target=APITarget(config['provider'],config['endpoint'],'',unprotect(config['key']))
  details={}
  try:models=list_models(target,lambda:self.app.closing,details=details)
  except Exception:
   self.record(room,'','models','failed');raise
  with self.app.lock,self.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   if self.raw(room)['revision']!=expected:raise EditConflict('取得中に設定が変更されました。再読込してください。')
   if self.app.job or self.app.conversation_waiters:raise ValueError('取得中に会話が始まりました。会話完了後に取得してください。')
   with self.store.connect() as db:
    for model,cap in details.items():db.execute('INSERT OR REPLACE INTO conversation_model_catalog VALUES(?,?,?,?,?)',(room,target.provider,target.endpoint,model,json.dumps(cap)))
   self.record(room,'','models','complete')
   return dict(room=room,revision=expected,models=models,capabilities=details)
 def record(self,room,job,action,state):
  with self.store.connect() as db:db.execute('INSERT INTO conversation_api_audit(room,job,action,state) VALUES(?,?,?,?)',(room,job,action,state))
 def target(self,room):
  doc=self.resolved(room);v=doc['values']
  if v['provider']=='local':return None
  if not v.get('key') or not v.get('model'):raise ValueError('このルームの会話APIのモデルとキーを保存してください。')
  return APITarget(v['provider'],v['endpoint'],v['model'],unprotect(v['key']),v['vision'],v['tools'],v['sampling'],doc['revision'],v['capabilities'].get('max_output_tokens',0))
 def status(self,room):
  if not room:return self.app.engine.status()
  revision=''
  try:
   revision=self.revision(room);target=self.target(room)
   if target is None:return self.app.engine.status()
   return dict(state='ready',vision=target.vision,error='',pid=None,remote=True,provider=target.provider,model=target.model,revision=target.revision)
  except ValueError:return dict(state='error',vision=False,error='このルームの会話API設定を確認してください。',pid=None,remote=True,revision=revision,provider=self.resolved(room)['values']['provider'])
