"""Discord Gateway v10: heartbeat/ACK, sequence, resume, bounded reconnect. No SDK side effects."""
import json,random,struct,sys,threading,time
from pathlib import Path
from urllib.parse import urlsplit

class GatewayFatal(ValueError):pass
class Reconnect(Exception):pass

def gateway_url(url):
 p=urlsplit(url)
 if p.scheme!='wss' or not p.hostname or not (p.hostname=='gateway.discord.gg' or p.hostname.endswith('.discord.gg')) or p.username or p.password or p.port not in (None,443) or p.query or p.fragment or p.path not in ('','/'):raise GatewayFatal('Gateway URLを確認できません。')
 return url.rstrip('/')+'/?v=10&encoding=json'

class Socket:
 def __init__(self,url):
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/social_packages'))
  import websocket
  self.module=websocket;self.socket=websocket.create_connection(gateway_url(url),timeout=1,suppress_origin=True);self.socket.settimeout(.5)
 def send(self,value):self.socket.send(json.dumps(value))
 def receive(self):
  try:
   opcode,data=self.socket.recv_data(control_frame=True)
   if opcode==8:
    code=struct.unpack('!H',data[:2])[0] if len(data)>=2 else 1000
    if code in (4004,4010,4011,4012,4013,4014):raise GatewayFatal('Bot認証・Gateway Intent・権限を確認してください。')
    raise Reconnect()
   if opcode!=1:return None
   if len(data)>1048576:raise GatewayFatal('Gateway応答の上限です。')
   return json.loads(data)
  except self.module.WebSocketTimeoutException:return None
 def close(self):self.socket.close(timeout=1)

class DiscordGateway:
 def __init__(self,token,on_event,on_status=lambda state,message:None,valid=lambda:True,factory=Socket,clock=time.monotonic):
  self.token=token;self.on_event=on_event;self.on_status=on_status;self.valid=valid;self.factory=factory;self.clock=clock;self.stop_event=threading.Event();self.socket=None;self.session=None;self.sequence=None;self.resume_url='wss://gateway.discord.gg';self.own_id=''
 def stop(self):
  self.stop_event.set()
  if self.socket:
   try:self.socket.close()
   except Exception:pass
 def identify(self):return {'op':2,'d':{'token':self.token,'intents':1+512+4096+32768,'properties':{'os':'windows','browser':'Akari-Chat','device':'Akari-Chat'},'compress':False}}
 def connected(self):
  self.socket=self.factory(self.resume_url);hello_until=self.clock()+15;interval=None;next_beat=0;ack=True
  while not self.stop_event.is_set():
   if not self.valid():raise GatewayFatal('接続設定が変わったため受信を停止しました。')
   now=self.clock()
   if interval is None and now>hello_until:raise Reconnect()
   if interval and now>=next_beat:
    if not ack:raise Reconnect()
    self.socket.send({'op':1,'d':self.sequence});ack=False;next_beat=now+interval
   event=self.socket.receive()
   if event is None:continue
   if not isinstance(event,dict):raise GatewayFatal('Gateway応答が不正です。')
   op=event.get('op')
   if isinstance(event.get('s'),int):self.sequence=event['s']
   if op==10:
    interval=event['d'].get('heartbeat_interval',0)/1000
    if not 1<=interval<=120:raise GatewayFatal('Heartbeat設定が不正です。')
    next_beat=self.clock()+interval*random.random()
    if self.session and self.sequence is not None:self.socket.send({'op':6,'d':{'token':self.token,'session_id':self.session,'seq':self.sequence}})
    else:self.socket.send(self.identify())
   elif op==11:ack=True
   elif op==1:self.socket.send({'op':1,'d':self.sequence});ack=False;next_beat=self.clock()+(interval or 30)
   elif op==7:raise Reconnect()
   elif op==9:
    if not event.get('d'):self.session=None;self.sequence=None;self.resume_url='wss://gateway.discord.gg'
    raise Reconnect()
   elif op==0:
    kind=event.get('t');data=event.get('d',{})
    if kind=='READY':
     self.session=data['session_id'];self.resume_url=data.get('resume_gateway_url','wss://gateway.discord.gg');gateway_url(self.resume_url);self.own_id=str(data.get('user',{}).get('id',''));self.on_status('running','')
    elif kind=='RESUMED':self.on_status('running','')
    if kind in ('READY','MESSAGE_CREATE','INTERACTION_CREATE','GUILD_CREATE','CHANNEL_CREATE','CHANNEL_UPDATE','THREAD_CREATE','THREAD_UPDATE'):self.on_event(kind,data,self.own_id)
 def run(self):
  failures=0
  try:
   while not self.stop_event.is_set() and failures<10:
    self.on_status('connecting' if not self.session else 'reconnecting','')
    try:self.connected()
    except GatewayFatal as error:self.on_status('failed',str(error));return
    except Exception:
     failures+=1
     if not self.stop_event.is_set():self.on_status('reconnecting','接続を再開しています。')
    finally:
     if self.socket:
      try:self.socket.close()
      except Exception:pass
      self.socket=None
    if self.stop_event.wait(min(30,2**min(failures,5))):break
   if failures>=10:self.on_status('failed','再接続の上限に達しました。設定を確認して開始し直してください。')
  finally:
   self.token=''
   if self.stop_event.is_set():self.on_status('stopped','')
