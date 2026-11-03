"""Opt-in independent bearer/TLS gateway; never shares the UI token or changes OS networking."""
import hashlib
import hmac
import json
import secrets
import socket
import ssl
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,unquote,parse_qs
from .external_connections import packed,text,revision
from litechat.file_editor import EditConflict

class GatewayServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(5);return connection,address

class ExternalGateway:
    def __init__(self,app):
        self.app=app;self.lock=threading.RLock();self.server=None;self.worker=None;self.events={};self.tasks={};self.limits=threading.BoundedSemaphore(8)
        with app.store.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS gateway_config(id INTEGER PRIMARY KEY,revision TEXT,value TEXT);
            CREATE TABLE IF NOT EXISTS gateway_requests(id TEXT PRIMARY KEY,room TEXT,kind TEXT,signature TEXT,status TEXT,result TEXT,created REAL);""")
            db.execute("UPDATE gateway_requests SET status='unknown',result=? WHERE status IN ('queued','running')",(packed({'message':'前回の実行結果は未確認です。自動再実行しません。'}),))

        from .gateway_access import GatewayAccess
        self.access=GatewayAccess(app)

    def _config(self):
        with self.app.store.connect() as db:row=db.execute('SELECT * FROM gateway_config WHERE id=1').fetchone()
        return {'revision':row['revision'] if row else '0','values':json.loads(row['value']) if row else {'host':'127.0.0.1','port':0,'certificate':'','private_key':'','token_hash':''}}

    def status(self):
        with self.lock:
            result=self._config();result['values']['token_saved']=bool(result['values'].pop('token_hash',''));result['running']=self.server is not None
            result['address']=('https' if result['values']['certificate'] else 'http')+'://'+result['values']['host']+':'+str(self.server.server_port if self.server else result['values']['port'])
            return result

    def save(self,values,expected):
        with self.app.lock,self.lock:
            if self.app.closing:raise ValueError('終了処理中です。')
            if self.server:raise ValueError('Gatewayを停止してから設定を変更してください。')
            current=self._config()
            if current['revision']!=expected:raise EditConflict('Gateway設定が変わりました。')
            if not isinstance(values,dict) or set(values)-{'host','port','certificate','private_key','rotate_token'}:raise ValueError('Gateway設定が不正です。')
            out={**current['values'],**{k:v for k,v in values.items() if k!='rotate_token'}}
            if out['host'] not in ('127.0.0.1','0.0.0.0'):raise ValueError('待受先はこのPCのみ、または全インターフェースを選択してください。')
            if type(out['port']) is not int or not 0<=out['port']<=65535:raise ValueError('ポートは0〜65535です。0は空きポートを自動選択します。')
            for key in ('certificate','private_key'):out[key]=text(out[key],2048)
            if bool(out['certificate'])!=bool(out['private_key']):raise ValueError('証明書と秘密鍵の両方を指定してください。')
            if out['host']!='127.0.0.1' and not out['certificate']:raise ValueError('このPC以外からの接続にはHTTPSの証明書と秘密鍵が必要です。')
            if type(values.get('rotate_token',False)) is not bool:raise ValueError('トークン再発行指定が不正です。')
            token=None
            if values.get('rotate_token') or not out['token_hash']:
                token=secrets.token_urlsafe(40);out['token_hash']=hashlib.sha256(token.encode()).hexdigest()
            with self.app.store.connect() as db:db.execute('INSERT OR REPLACE INTO gateway_config VALUES(1,?,?)',(revision(),packed(out)))
            result=self.status()
            if token:result['new_token']=token
            return result

    def start(self):
        with self.app.lock,self.lock:
            if self.app.closing:raise ValueError('終了処理中です。')
            if self.server:return self.status()
            conf=self._config()['values']
            if not conf['token_hash']:raise ValueError('先にGatewayの接続トークンを設定してください。ルーム別の許可はルーム設定で指定します。')
            context=None
            if conf['certificate']:
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
                context.load_cert_chain(conf['certificate'],conf['private_key'])
            if conf['host']!='127.0.0.1' and context is None:raise ValueError('外部待受にはHTTPSが必要です。')
            server=GatewayServer((conf['host'],conf['port']),gateway_handler(self))
            try:
                if context:server.socket=context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
                self.server=server;self.worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True,name='akari-gateway');self.worker.start()
            except Exception:server.server_close();self.server=None;raise
            return self.status()

    def stop(self):
        with self.lock:server=self.server;self.server=None
        if server:server.shutdown();server.server_close()
        return self.status()

    def authorized(self,token):
        with self.lock:
            saved=self._config()['values']['token_hash']
            return bool(saved) and len(token)<=256 and hmac.compare_digest(saved,hashlib.sha256(token.encode()).hexdigest())

    def grant(self,room,scope):
        try:scopes=self.access.scopes(room)
        except ValueError:raise PermissionError('このルーム・操作はGatewayに許可されていません。')
        if scope not in scopes:raise PermissionError('このルーム・操作はGatewayに許可されていません。')

    def request(self,room,kind,data):
        with self.app.lock,self.lock:
            self.grant(room,kind)
            key=text(data.get('request_id'),100,False);message=text(data.get('message'),100000,False)
            signature=hashlib.sha256(packed([room,kind,message]).encode()).hexdigest()
            with self.app.store.connect() as db:row=db.execute('SELECT * FROM gateway_requests WHERE id=?',(key,)).fetchone()
            if row:
                if row['signature']!=signature:raise EditConflict('同じrequest_idの内容が異なります。')
                return self.result(room,key)
            if self.server is None or self.app.closing:raise ValueError('Gatewayは停止中です。')
            if len(self.tasks)>=8:raise ValueError('Gatewayの処理上限です。完了を待ってください。')
            # Events are inert, explicitly submitted source material; no hidden prompt injection.
            if kind=='events':
                with self.app.store.connect() as db:db.execute('INSERT INTO gateway_requests VALUES(?,?,?,?,?,?,?)',(key,room,kind,signature,'received',packed({'message':message,'untrusted':True}),time.time()))
                return self.result(room,key)
            with self.app.store.connect() as db:db.execute('INSERT INTO gateway_requests VALUES(?,?,?,?,?,?,?)',(key,room,kind,signature,'queued','{}',time.time()))
            self.events[key]=[];self.tasks[key]=None
            worker=threading.Thread(target=self._chat,args=(room,key,message),daemon=True,name='akari-gateway-chat');worker.start()
            return self.result(room,key)

    def _chat(self,room,key,message):
        job=None
        try:
            with self.app.conversation_gate():
                with self.lock:
                    self.grant(room,'chat')
                    if self.server is None or self.app.closing:raise ValueError('開始前にGatewayが停止しました。')
                # Existing turn gate waits for a running autonomous operation; normal chat stays serialized.
                job=self.app.begin(room,message,tools=False)
                with self.lock:self.tasks[key]=job['id']
            self._complete(key,'running',{})
            self.app.execute(job,lambda event:self._event(key,event))
            with self.lock:events=list(self.events.get(key,[]))
            done=next((e for e in reversed(events) if e['type']=='done'),{})
            self._complete(key,done.get('status','unknown'),{'events':events})
        except Exception:
            self._complete(key,'unknown' if job else 'error',{'message':'会話を開始または完了できませんでした。アプリのモデル状態と履歴を確認してください。'})
        finally:
            with self.lock:self.tasks.pop(key,None);self.events.pop(key,None)

    def _event(self,key,event):
        safe={k:v for k,v in event.items() if k in ('type','content','text','status','message','id')}
        with self.lock:
            current=self.events.setdefault(key,[]);current.append(safe)
            # Keep bounded incremental events; full answer remains in the room's existing history.
            if len(current)>256:del current[:-256]

    def _complete(self,key,status,value):
        with self.app.store.connect() as db:db.execute('UPDATE gateway_requests SET status=?,result=? WHERE id=?',(status,packed(value),key))

    def result(self,room,key):
        self.grant(room,'read')
        with self.lock,self.app.store.connect() as db:
            row=db.execute('SELECT * FROM gateway_requests WHERE id=? AND room=?',(key,room)).fetchone()
            if not row:raise ValueError('要求が見つかりません。')
            result={'request_id':key,'status':row['status'],'result':json.loads(row['result'])}
            if key in self.events:result['events']=list(self.events[key])
            return result

    def cancel(self,room,key):
        self.grant(room,'chat')
        with self.app.store.connect() as db:
            if not db.execute('SELECT 1 FROM gateway_requests WHERE room=? AND id=?',(room,key)).fetchone():raise ValueError('同じルームの要求を指定してください。')
        with self.lock:
            job=self.tasks.get(key);waiting=key in self.tasks and not job
        if job:self.app.cancel(job)
        elif waiting:raise ValueError('開始待ちです。Gateway停止で待機中の開始を防げます。')
        return self.result(room,key)


def gateway_handler(gateway):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,status=200):
            raw=packed(value).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
        def dispatch(self,post=False):
            self.close_connection=True
            if not post and self.path in ('/lite','/lite.js'):
                file='gateway_lite.html' if self.path=='/lite' else 'gateway_lite.js'
                raw=(Path(__file__).resolve().parents[1]/'web'/file).read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if file.endswith('html') else 'text/javascript; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(raw);return
            origin=self.headers.get('Origin')
            same_origin=('https' if gateway._config()['values']['certificate'] else 'http')+'://'+self.headers.get('Host','')
            if origin and origin!=same_origin or not self.headers.get('Authorization','').startswith('Bearer ') or not gateway.authorized(self.headers.get('Authorization','')[7:]):return self.reply({'error':'認証が必要です。'},401)
            if not gateway.limits.acquire(blocking=False):return self.reply({'error':'同時要求数の上限です。'},429)
            try:
                parsed=urlsplit(self.path);parts=[unquote(p) for p in parsed.path.split('/') if p];args=parse_qs(parsed.query)
                if parts==['api','v1','rooms'] and not post:
                    rooms=[]
                    for room in gateway.access.rooms():
                        try:gateway.grant(room,'read');rooms.append(room)
                        except (ValueError,PermissionError):pass
                    return self.reply({'rooms':rooms})
                if len(parts)!=5 or parts[:3]!=['api','v1','rooms']:raise ValueError('APIが見つかりません。')
                room,action=parts[3:]
                if post:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=200000:raise ValueError('本文サイズが不正です。')
                    data=json.loads(self.rfile.read(size))
                    if not isinstance(data,dict):raise ValueError('JSONオブジェクトを指定してください。')
                    if action in ('chat','events'):return self.reply(gateway.request(room,action,data),202)
                    if action=='cancel':return self.reply(gateway.cancel(room,data.get('request_id','')))
                else:
                    gateway.grant(room,'read')
                    if action=='status':return self.reply({'room':room,'busy':gateway.app.job is not None,'engine':gateway.app.engine.status()['state']})
                    if action=='requests':return self.reply(gateway.result(room,args.get('request_id',[''])[0]))
                    if action=='events':
                        with gateway.app.store.connect() as db:rows=db.execute("SELECT id,status,result FROM gateway_requests WHERE room=? AND kind='events' ORDER BY created DESC LIMIT 50",(room,)).fetchall()
                        return self.reply({'events':[{'request_id':r['id'],'status':r['status'],**json.loads(r['result'])} for r in rows]})
                    if action=='history':return self.reply({'messages':gateway.app.store.history(room,20)})
                    if action=='capabilities':return self.reply({'scopes':gateway.access.scopes(room),'chat_tools':False,'input':['text'],'events_trigger_generation':False})
                raise ValueError('APIが見つかりません。')
            except PermissionError as error:self.reply({'error':str(error)},403)
            except (ValueError,OSError):self.reply({'error':'要求を処理できません。許可・ルーム・入力・履歴を確認してください。'},400)
            finally:gateway.limits.release()
        def do_GET(self):self.dispatch()
        def do_POST(self):self.dispatch(True)
    return Handler
