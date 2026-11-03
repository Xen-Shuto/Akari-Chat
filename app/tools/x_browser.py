"""Account-bound browser sessions. Auth state stays DPAPI-protected, never on a cookie file."""
import base64,json,queue,subprocess,threading,time,re
from pathlib import Path
from .managed_process import ProcessGroup
from .external_secrets import protect,unprotect
from .external_connections import revision
from litechat.file_editor import EditConflict

class BrowserRun:
 def __init__(self,args):
  app=Path(__file__).resolve().parents[1]
  if not (app/'runtime/social_packages/playwright').is_dir():raise ValueError('tools/setup_social.pyでブラウザ依存を導入してください。')
  self.queue=queue.Queue();self.process=subprocess.Popen([str(app/'runtime/python.exe'),'-I','-B',str(Path(__file__).with_name('x_browser_worker.py'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  self.group=ProcessGroup(self.process)
  def read():
   try:
    for line in self.process.stdout:
     if len(line)>2*1048576:self.queue.put({'error':'応答サイズの上限です。'});break
     self.queue.put(json.loads(line))
   except Exception:self.queue.put({'error':'ブラウザ応答を読み取れません。'})
   finally:self.queue.put({'closed':True})
  self.reader=threading.Thread(target=read,daemon=True);self.reader.start();self.send(args)
 def send(self,value):self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
 def receive(self,timeout=45):
  try:return self.queue.get(timeout=timeout)
  except queue.Empty:raise ValueError('ブラウザ応答が時間内に返りませんでした。')
 def close(self):
  try:
   if self.process.poll() is None:self.process.stdin.close();self.process.wait(timeout=3)
  except Exception:
   if self.process.poll() is None:self.process.kill();self.process.wait(timeout=3)
  self.group.close()
  for stream in (self.process.stdout,self.process.stderr):
   if stream:stream.close()

class XBrowser:
 def __init__(self,app):self.app=app;self.lock=threading.RLock();self.logins={};self.factory=BrowserRun
 def begin(self,key):
  with self.app.lock,self.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   config=self.app.external.room_connection(key,'x')
   if config['values'].get('mode')!='browser':raise ValueError('接続方式をブラウザにして保存してください。')
   if key in self.logins:raise ValueError('この接続でログイン待機中です。')
   run=self.factory({'action':'login','state':json.loads(unprotect(config['values']['browser_state'])) if config['values'].get('browser_state') else None})
   try:
    first=run.receive(30)
    if not first.get('ready'):raise ValueError('ログイン用ブラウザを起動できませんでした。')
   except Exception:run.close();raise
   self.logins[key]={'run':run,'revision':config['revision'],'created':time.monotonic()}
   return {'message':'専用ブラウザで手動ログイン後、あかりで「ログイン完了を保存」を押してください。'}
 def finish(self,key):
  with self.app.lock,self.lock:
   pending=self.logins.pop(key,None)
   if not pending:raise ValueError('ログイン待機がありません。')
   run=pending['run']
   try:
    config=self.app.external.room_connection(key,'x')
    if self.app.closing or config['revision']!=pending['revision']:raise EditConflict('ログイン中に接続設定が変わりました。')
    run.send({'action':'complete'});reply=run.receive(30)
    if not reply.get('result'):raise ValueError('ログインを確認できませんでした。')
    result=reply['result'];values={**config['values'],'browser_state':protect(json.dumps(result['state'],separators=(',',':'))),'username':result['username']};values.pop('verified_account',None)
    with self.app.store.connect() as db:self.app.external._save_connection(db,'x',config,values,revision());self.app.external._audit(db,key,'x','browser-login','ok')
    return {'message':'ブラウザ接続を保存しました。','username':result['username']}
   finally:run.close()
 def import_cookies(self,key,raw,expected):
  from .x_cookies import parse_cookies
  state=parse_cookies(raw)
  with self.app.lock,self.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   self.app.store.room_path(key)
   config=self.app.external.room_connection(key,'x')
   local=self.app.external._config(key,'x')
   if local['revision']!=expected:raise EditConflict('設定が変わりました。再読込してください。')
   if config['values'].get('mode')!='browser':raise ValueError('接続方式をCookieにして保存してください。')
   if key in self.logins:raise ValueError('ログイン待機を終えてから取り込んでください。')
   protected=protect(json.dumps(state,separators=(',',':')))
  # A read-only identity check; no posting and no user/browser profile extraction.
  try:result=self.execute(dict(config,values={**config['values'],'browser_state':protected,'username':''}),'identity',{})
  except Exception:raise ValueError('Cookieのログイン状態を確認できませんでした。期限切れ・X側の制限・通信を確認してください。以前の設定は保持しています。') from None
  username=result.get('username') if isinstance(result,dict) else None
  if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_]{1,15}',username):raise ValueError('Cookieのアカウントを確認できませんでした。以前の設定は保持しています。')
  with self.app.lock,self.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   self.app.store.room_path(key)
   current=self.app.external.room_connection(key,'x');local=self.app.external._config(key,'x')
   if current['revision']!=config['revision'] or local['revision']!=expected:raise EditConflict('確認中に設定が変わりました。保存せず停止しました。')
   with self.app.store.connect() as db:
    if db.execute("SELECT 1 FROM external_outbox WHERE room=? AND provider='x' AND status='sending'",(key,)).fetchone():raise ValueError('送信中はCookieを変更できません。')
    values={**current['values'],'browser_state':protected,'username':username};values.pop('verified_account',None)
    db.execute('INSERT INTO external_profile_versions(provider,profile,revision,value) VALUES(?,?,?,?)',('x',current['id'],current['revision'],json.dumps(current['values'])))
    self.app.external._save_connection(db,'x',current,values,revision());self.app.external._audit(db,key,'x','cookie-import','ok')
  return {'message':'Cookieを取り込みました。','username':username}
 def execute(self,config,action,payload,images=(),record=lambda value:None):
  value=config['values'];args={'action':action,'username':value.get('username',''),'state':json.loads(unprotect(value['browser_state'])),**payload,'images':[{'mime':item['mime'],'data':base64.b64encode(raw).decode()} for item,raw in images]}
  run=self.factory(args)
  try:
   deadline=time.monotonic()+60
   while time.monotonic()<deadline:
    reply=run.receive(max(1,deadline-time.monotonic()))
    if reply.get('phase'):record({'browser_phase':reply['phase']});continue
    if 'result' in reply:return reply['result']
    raise ValueError('ブラウザ操作の完了を確認できません。')
   raise ValueError('ブラウザ操作の待機上限です。')
  finally:run.close()
 def close(self):
  with self.lock:
   for item in self.logins.values():item['run'].close()
   self.logins.clear()
