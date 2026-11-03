"""Per-room REST subscriptions and shared-per-Bot Gateway sessions with inert input storage."""
import json,queue,threading,time
from .external_connections import packed
from .discord_gateway import DiscordGateway
from .discord_replies import DiscordReplies
from .discord_access import channels

class DiscordReceiver:
 def start_saved(self):
  """One startup pass; REST, archived, disabled rooms never auto-connect."""
  for room in self.app.store.rooms():
   if self.app.closing:return
   room=room['id']
   local=self.app.external._config(room,'discord')
   if not local['values'].get('enabled') or local['values'].get('receive_mode','rest')!='gateway':continue
   try:self.start(room)
   except Exception:
    with self.lock:self.startup_errors[room]='自動受信の開始に失敗しました。Bot設定・許可チャンネル・通信を確認してください。'
    with self.app.store.connect() as db:self.app.external._audit(db,room,'discord','receiver-autostart','failed')
 def __init__(self,app):
  self.startup_errors={}
  self.app=app;self.lock=threading.RLock();self.stop_event=threading.Event();self.sessions={};self.gateways={};self.owned_workers=[];self.gateway_factory=DiscordGateway
  with app.store.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS discord_received(room TEXT,channel TEXT,id TEXT,content TEXT,created REAL,PRIMARY KEY(room,channel,id));
   CREATE TABLE IF NOT EXISTS discord_cursors(room TEXT,channel TEXT,id TEXT,PRIMARY KEY(room,channel));
   CREATE TABLE IF NOT EXISTS discord_received_v2(room TEXT,connection TEXT,channel TEXT,id TEXT,content TEXT,created REAL,PRIMARY KEY(room,connection,channel,id));
   CREATE TABLE IF NOT EXISTS discord_cursors_v2(room TEXT,connection TEXT,channel TEXT,id TEXT,PRIMARY KEY(room,connection,channel));""")
   db.execute("INSERT OR IGNORE INTO discord_received_v2 SELECT room,'default',channel,id,content,created FROM discord_received")
   db.execute("INSERT OR IGNORE INTO discord_cursors_v2 SELECT room,'default',channel,id FROM discord_cursors")
  from .discord_chat import initialize
  with self.app.store.connect() as db:initialize(db)
  self.replies=DiscordReplies(app)
 def active(self,room):
  with self.lock:session=self.sessions.get(room)
  if not session or session['stop'].is_set():return False
  try:
   common,local=self.app.external._guard(room,'discord')
   if (common['id'],common['revision'],local['revision'])!=(session['profile'],session['common_revision'],session['room_revision']):return False
   worker=session.get('worker') if session['mode']=='rest' else self.gateways.get(session['profile'],{}).get('worker')
   if session['mode']=='gateway' and self.gateways.get(session['profile'],{}).get('revision')!=session['common_revision']:return False
   return bool(worker and worker.is_alive())
  except ValueError:return False
 def status(self,room):
  if room:self.app.store.room_path(room)
  local=self.app.external._config(room,'discord');profile=local['values'].get('connection_id','default')
  with self.lock:
   session=self.sessions.get(room);state=session.get('state','stopped') if session else 'stopped';message=session.get('message','') if session else '';mode=session.get('mode',local['values'].get('receive_mode','rest')) if session else local['values'].get('receive_mode','rest')
   if session and session['mode']=='gateway':
    shared=self.gateways.get(session['profile'],{});state=shared.get('state',state);message=shared.get('message',message)
   if not self.active(room):state='stopped' if state not in ('failed','stopping') else state
  with self.app.store.connect() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT content FROM discord_received_v2 WHERE room=? AND connection=? ORDER BY created DESC,rowid DESC LIMIT 50',(room,profile))]
  if room in self.startup_errors and not self.active(room):state='failed';message=self.startup_errors[room]
  return {'state':state,'room':room,'connection_id':profile,'message':message,'messages':rows,'mode':'Gateway v10' if mode=='gateway' else 'REST polling / 60 seconds','replies':self.replies.listing(room)}
 def start(self,room):
  with self.app.lock:
   common,local=self.app.external._guard(room,'discord')
   self.startup_errors.pop(room,None)
   if not channels(local['values']):raise ValueError('許可チャンネルを先に指定してください。')
   with self.lock:
    if self.active(room):return self.status(room)
    if self.stop_event.is_set():self.stop_event=threading.Event()
    old=self.sessions.get(room)
    if old:old['stop'].set()
    profile=common['id'];mode=local['values'].get('receive_mode','rest');session={'room':room,'profile':profile,'mode':mode,'stop':threading.Event(),'common_revision':common['revision'],'room_revision':local['revision'],'state':'connecting','message':''};self.sessions[room]=session
    if mode=='gateway':
     shared=self.gateways.get(profile)
     if shared and shared['worker'].is_alive() and shared['revision']!=common['revision']:raise ValueError('旧Bot接続の停止完了を待ってから開始してください。')
     if not shared or not shared['worker'].is_alive():
      token=self.app.external._token('discord',common)
      shared={'state':'connecting','message':'','revision':common['revision'],'events':queue.Queue(256),'dispatch_stop':threading.Event()};self.gateways[profile]=shared
      def valid():
       if self.app.closing or self.stop_event.is_set():return False
       try:
        if self.app.external.connection('discord',profile)['revision']!=shared['revision']:return False
       except ValueError:return False
       with self.lock:subscribers=[s for s in self.sessions.values() if s['profile']==profile and s['mode']=='gateway' and not s['stop'].is_set()]
       return any(self._valid_session(s) for s in subscribers)
      def status(state,message):
       with self.lock:
        if shared['state']!='failed':shared['state']=state;shared['message']=message
      def enqueue(kind,data,bot):
       try:shared['events'].put_nowait((kind,data,bot))
       except queue.Full:
        shared['state']='failed';shared['message']='受信処理の待機上限に達しました。受信履歴を確認して開始し直してください。';shared['dispatch_stop'].set();shared['client'].stop()
      client=self.gateway_factory(token,enqueue,status,valid)
      shared['client']=client
      def dispatch():
       while not shared['dispatch_stop'].is_set():
        try:event=shared['events'].get(timeout=.25)
        except queue.Empty:
         if not shared['worker'].is_alive():break
         continue
        try:self._gateway_event(profile,*event,expected_revision=shared['revision'])
        except Exception:
         shared['state']='failed';shared['message']='受信内容を保存できなかったため停止しました。';shared['dispatch_stop'].set();client.stop()
      shared['worker']=threading.Thread(target=client.run,daemon=True,name='akari-discord-gateway');shared['worker'].start();self.owned_workers.append(shared['worker']);shared['dispatch']=threading.Thread(target=dispatch,daemon=True,name='akari-discord-dispatch');shared['dispatch'].start();self.owned_workers.append(shared['dispatch'])
    else:
     session['worker']=threading.Thread(target=self._rest,args=(session,),daemon=True,name='akari-discord-rest');session['worker'].start();self.owned_workers.append(session['worker'])
   with self.app.store.connect() as db:self.app.external._audit(db,room,'discord','receiver-start',mode,profile)
   return self.status(room)
 def _valid_session(self,session):
  try:
   common,local=self.app.external._guard(session['room'],'discord')
   return (common['id'],common['revision'],local['revision'])==(session['profile'],session['common_revision'],session['room_revision']) and not session['stop'].is_set()
  except ValueError:return False
 def stop(self,room=None):
  if room is not None:self.app.store.room_path(room)
  with self.lock:
   if room is None:self.stop_event.set()
   selected=[s for key,s in self.sessions.items() if room is None or key==room]
   for session in selected:session['stop'].set();session['state']='stopped'
   workers=list(self.owned_workers) if room is None else [s['worker'] for s in selected if s.get('worker')]
   for profile,shared in self.gateways.items():
    live=any(s['profile']==profile and s['mode']=='gateway' and not s['stop'].is_set() for s in self.sessions.values())
    if not live:
     shared['dispatch_stop'].set();shared['client'].stop();workers.extend([shared['worker'],shared['dispatch']])
  with self.app.store.connect() as db:
   if room is None:db.execute("UPDATE discord_reply_jobs SET state='cancelled' WHERE state='queued'")
   else:db.execute("UPDATE discord_reply_jobs SET state='cancelled' WHERE state='queued' AND room=?",(room,))
   for session in selected:self.app.external._audit(db,session['room'],'discord','receiver-stop','requested',session['profile'])
  deadline=time.monotonic()+15
  for worker in workers:
   if worker is not threading.current_thread():worker.join(max(0,deadline-time.monotonic()))
  return not any(w.is_alive() for w in workers)
 def ingest(self,room,profile,message,bot_id='',live=False):
  with self.app.lock:
   common,local=self.app.external._guard(room,'discord')
   if common['id']!=profile:return False
   channel=message.get('channel_id') or message.get('channel');self.app.external._channel(local['values'],channel)
   key=message.get('id')
   if not isinstance(key,str) or not key.isascii() or not key.isdigit() or len(key)>25:return False
   safe={k:message[k] for k in ('id','content','timestamp','author','mentions','referenced_message') if k in message};safe['channel']=channel
   attachments=message.get('attachments')
   if isinstance(attachments,list):safe['attachments']=[{'filename':str(a.get('filename','添付ファイル'))[:200]} for a in attachments[:10] if isinstance(a,dict)]
   if not isinstance(safe.get('content',''),str):return False
   safe['content']=safe.get('content','')[:4000]
   from .discord_labels import resolve
   resolve(self.app.external,room,profile,channel)
   with self.app.store.connect() as db:
    db.execute('BEGIN IMMEDIATE');changed=db.execute('INSERT OR IGNORE INTO discord_received_v2 VALUES(?,?,?,?,?,?)',(room,profile,channel,key,packed(safe),time.time())).rowcount
    if changed:
     from .discord_chat import received
     received(db,room,profile,safe,bot_id or str(common['values'].get('verified_account','')))
    if changed and live:self.replies.receive(db,room,profile,common,local,safe,bot_id)
   return bool(changed)
 def _gateway_event(self,profile,kind,data,bot,expected_revision=None):
  if kind=='READY':
   with self.lock:owners=[s for s in self.sessions.values() if s['profile']==profile and s['mode']=='gateway' and not s['stop'].is_set()]
   for owner in owners:
    if expected_revision is None or owner['common_revision']==expected_revision:
     try:self.app.external.remember_identity(owner['room'],'discord',bot,owner['common_revision'])
     except ValueError:pass
   return
  if kind in ('GUILD_CREATE','CHANNEL_CREATE','CHANNEL_UPDATE','THREAD_CREATE','THREAD_UPDATE'):
   from .discord_labels import remember
   items=data.get('channels',[])+data.get('threads',[]) if kind=='GUILD_CREATE' else [data]
   with self.lock:owners=[s for s in self.sessions.values() if s['profile']==profile and s['mode']=='gateway' and not s['stop'].is_set()]
   for session in owners:
    if expected_revision is not None and session['common_revision']!=expected_revision:continue
    if not self._valid_session(session):continue
    local=self.app.external._config(session['room'],'discord')['values']
    with self.app.store.connect() as db:
     for item in items:
      if item.get('id') in channels(local):remember(db,session['room'],profile,item['id'],item.get('name'))
   return
  if kind=='INTERACTION_CREATE':
   from .x_discord_approval import handle
   try:handle(self.app.external,profile,data,expected_revision)
   except (ValueError,OSError):pass
   return
  if kind!='MESSAGE_CREATE':return
  with self.lock:sessions=[s for s in self.sessions.values() if s['profile']==profile and s['mode']=='gateway' and not s['stop'].is_set()]
  for session in sessions:
   try:
    if expected_revision is not None and session['common_revision']!=expected_revision:continue
    if not self._valid_session(session):continue
    local=self.app.external._config(session['room'],'discord')
    if data.get('channel_id') in channels(local['values']):self.ingest(session['room'],profile,data,bot,True)
   except ValueError:continue
 def poll(self,room):
  with self.app.lock:
   if self.app.closing or self.app.job or self.app.conversation_waiters:return False
  common,local=self.app.external._guard(room,'discord');profile=common['id'];session=self.sessions.get(room)
  if session and not self._valid_session(session):raise ValueError('接続設定が変わりました。')
  bot=''
  if local['values'].get('auto_reply'):
   identity=self.app.external.read(room,'discord','identity')['data'];bot=str(identity.get('id',''))
  for channel in channels(local['values']):
   if self.stop_event.is_set() or session and session['stop'].is_set():return False
   with self.app.lock:
    if self.app.closing or self.app.job or self.app.conversation_waiters:return False
    with self.app.store.connect() as db:cursor=db.execute('SELECT id FROM discord_cursors_v2 WHERE room=? AND connection=? AND channel=?',(room,profile,channel)).fetchone()
    response=self.app.external.read(room,'discord','messages',{'channel':channel,'after':cursor[0] if cursor else ''});messages=response['data']
    if not isinstance(messages,list):raise ValueError('Discord応答が不正です。')
    valid=[m for m in messages if isinstance(m,dict) and isinstance(m.get('id'),str) and m['id'].isascii() and m['id'].isdigit()]
    if cursor and len(valid)>=100:raise ValueError('取得上限のためカーソルを進めず停止しました。')
    for item in sorted(valid,key=lambda m:int(m['id'])):self.ingest(room,profile,dict(item,channel_id=channel),bot,bool(cursor))
    if valid:
     latest=max([m['id'] for m in valid]+([cursor[0]] if cursor else []),key=int)
     with self.app.store.connect() as db:db.execute('INSERT OR REPLACE INTO discord_cursors_v2 VALUES(?,?,?,?)',(room,profile,channel,latest))
  return True
 def _rest(self,session):
  try:
   session['state']='running'
   while not self.stop_event.is_set() and not session['stop'].is_set():
    try:self.poll(session['room'])
    except Exception:session['state']='failed';session['message']='受信を停止しました。設定・権限・通信を確認してください。';break
    if session['stop'].wait(60):break
  finally:
   if session['state']!='failed':session['state']='stopped'
