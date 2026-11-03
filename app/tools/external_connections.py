"""Explicit provider/room grants and durable human-reviewed external outbox."""
import base64
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlencode
from litechat.file_editor import EditConflict
from . import external_providers as providers
from .external_secrets import protect,unprotect

PROVIDERS=('google','x','discord')
X_POST_DEFAULTS={'posting_summary':'','posting_guidelines':'','auto_post':False,
                 'approval_for_replies':True,'notify_discord_approval':False,'is_premium':False}
SECRET_FIELDS=('token','refresh_token','client_secret','browser_state')

def revision():return uuid.uuid4().hex
def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def x_post_length(body):
    # Match the draft limit approximately. X validates the actual weighted length.
    import re
    import unicodedata
    urls=re.compile(r'https?://\S+')
    return len(urls.findall(body))*23+sum(
        2 if unicodedata.east_asian_width(char) in ('W','F','A') else 1
        for char in urls.sub('',body)
    )


def text(value,limit=200,empty=True):
    if not isinstance(value,str) or len(value)>limit or (not empty and not value.strip()) or any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('入力の長さ・形式を確認してください。')
    return value.strip()
def instant(value):
    value=text(value,60,False)
    try:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('タイムゾーン付き日時を指定してください。')
    if dt.tzinfo is None:raise ValueError('タイムゾーン付き日時を指定してください。')
    return value

def date_range(args):
    start,end=instant(args.get('start')),instant(args.get('end'))
    if datetime.fromisoformat(start.replace('Z','+00:00'))>=datetime.fromisoformat(end.replace('Z','+00:00')):raise ValueError('終了は開始より後にしてください。')
    return {'start':start,'end':end}

class ExternalConnections:
    def __init__(self,app):
        self.app,self.store=app,app.store
        self.lock=threading.RLock();self.oauth={};self.fetch=providers.transport
        from .social_media import SocialMedia
        self.media=SocialMedia(app)
        with self.store.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS external_configs(scope TEXT,provider TEXT,revision TEXT,value TEXT,PRIMARY KEY(scope,provider));
            CREATE TABLE IF NOT EXISTS external_room_links(room TEXT,provider TEXT,id TEXT,PRIMARY KEY(room,provider),UNIQUE(id,provider));
            CREATE TABLE IF NOT EXISTS external_profiles(id TEXT,provider TEXT,revision TEXT,value TEXT,PRIMARY KEY(id,provider));
            CREATE TABLE IF NOT EXISTS external_profile_receipts(provider TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(provider,request_id));
            CREATE TABLE IF NOT EXISTS external_profile_versions(id INTEGER PRIMARY KEY,provider TEXT,profile TEXT,revision TEXT,value TEXT);
            CREATE TABLE IF NOT EXISTS external_outbox(id TEXT PRIMARY KEY,room TEXT,provider TEXT,action TEXT,revision TEXT,common_revision TEXT,room_revision TEXT,payload TEXT,status TEXT,result TEXT,request_id TEXT,signature TEXT,created REAL,UNIQUE(room,request_id));
            CREATE TABLE IF NOT EXISTS external_audit(id INTEGER PRIMARY KEY,room TEXT,provider TEXT,action TEXT,status TEXT,item TEXT,created REAL);
            """)
            from .discord_labels import initialize
            initialize(db)
            rows=db.execute("SELECT id,room,provider FROM external_outbox WHERE status='sending'").fetchall()
            db.execute("UPDATE external_outbox SET status='unknown',revision=?,result=? WHERE status='sending'",(revision(),packed({'message':'前回の送信結果は未確認です。再送せず接続先を確認してください。'})))
            for row in rows:self._audit(db,row['room'],row['provider'],'recover','unknown',row['id'])

    def _audit(self,db,room,provider,action,status,item=''):
        db.execute('INSERT INTO external_audit(room,provider,action,status,item,created) VALUES(?,?,?,?,?,?)',(room,provider,action,status,item,time.time()))

    def _config(self,scope,provider):
        if provider not in PROVIDERS:raise ValueError('接続先が不正です。')
        with self.store.connect() as db:row=db.execute('SELECT * FROM external_configs WHERE scope=? AND provider=?',(scope,provider)).fetchone()
        return {'revision':row['revision'] if row else '0','values':json.loads(row['value']) if row else {'enabled':False}}

    def connection(self,provider,key='default'):
        if provider not in PROVIDERS or not isinstance(key,str) or not 1<=len(key)<=64 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in key):raise ValueError('接続先が不正です。')
        if key=='default':return dict(self._config('*',provider),id='default')
        if provider=='google':raise ValueError('Googleの複数接続は今回の対象外です。')
        with self.store.connect() as db:row=db.execute('SELECT * FROM external_profiles WHERE provider=? AND id=?',(provider,key)).fetchone()
        if not row:raise ValueError('登録された接続を選択してください。')
        with self.store.connect() as db:owner=db.execute('SELECT room FROM external_room_links WHERE provider=? AND id=?',(provider,key)).fetchone()
        if owner:self.store.room_path(owner[0])
        return {'id':key,'revision':row['revision'],'values':json.loads(row['value']),**({'room':owner[0]} if owner else {})}

    def profiles(self,provider):
        raise ValueError('XとDiscordの登録はルーム設定内で行ってください。')

    def save_profile(self,*args,**kwargs):
        raise ValueError('共通アカウント登録・接続の選択は廃止しました。ルーム設定を使用してください。')

    def room_connection(self,room,provider):
        if provider not in ('x','discord'):raise ValueError('ルーム所有接続の対象外です。')
        self.store.room_path(room)
        if room not in {p.name for p in self.store.characters.iterdir() if p.is_dir()}:raise ValueError('一覧にあるルーム名を指定してください。')
        key='r_'+hashlib.sha256((room+':'+provider).encode()).hexdigest()[:40]
        with self.store.connect() as lookup:existing=lookup.execute('SELECT id FROM external_room_links WHERE room=? AND provider=?',(room,provider)).fetchone()
        if existing:return dict(self.connection(provider,existing[0]),room=room)
        with self.app.lock,self.lock,self.store.connect() as db:
            link=db.execute('SELECT id FROM external_room_links WHERE room=? AND provider=?',(room,provider)).fetchone()
            if link:return dict(self.connection(provider,link[0]),room=room)
            local=self._config(room,provider)
            if local['revision']=='0':return {'id':key,'room':room,'revision':'0','values':{'enabled':False}}
            # Only an existing room assignment is copied. Unassigned legacy registrations stay untouched.
            source=self.connection(provider,local['values'].get('connection_id','default'))
            value=copy.deepcopy(source['values']);value['enabled']=bool(value.get('enabled')) and not value.pop('archived',False);value.pop('name',None);version=revision();settings=dict(local['values'],connection_id=key);settings['enabled']=bool(settings.get('enabled')) and value['enabled'];value['enabled']=settings['enabled']
            db.execute('INSERT INTO external_profiles VALUES(?,?,?,?)',(key,provider,version,packed(value)))
            db.execute('INSERT INTO external_room_links VALUES(?,?,?)',(room,provider,key))
            db.execute('UPDATE external_configs SET revision=?,value=? WHERE scope=? AND provider=?',(version,packed(settings),room,provider))
            self._audit(db,room,provider,'room-owned-migration','ok',key)
            return {'id':key,'room':room,'revision':version,'values':value}

    @staticmethod
    def same_account(provider,a,b):
        va,vb=a['values'],b['values']
        def identities(v):
            fields=('verified_account','account') if provider=='x' and v.get('mode')!='browser' else ('verified_account',)
            return {str(v[f]).casefold() for f in fields if v.get(f)}
        if identities(va)&identities(vb):return 'アカウントID'
        if provider=='x' and va.get('username') and vb.get('username') and va['username'].lstrip('@').casefold()==vb['username'].lstrip('@').casefold():return 'ユーザー名'
        for field in ('token','refresh_token','browser_state'):
            if va.get(field) and vb.get(field) and unprotect(va[field])==unprotect(vb[field]):return '同じ認証情報'
        return ''

    def duplicates(self,room,provider,config=None):
        current=config or self.room_connection(room,provider);matches=[]
        with self.store.connect() as db:others=db.execute("SELECT scope FROM external_configs WHERE provider=? AND scope NOT IN ('*',?)",(provider,room)).fetchall()
        for row in others:
            other=row[0]
            with self.store.connect() as db:
                stored=db.execute('SELECT p.revision,p.value FROM external_room_links l JOIN external_profiles p ON p.id=l.id AND p.provider=l.provider WHERE l.room=? AND l.provider=?',(other,provider)).fetchone()
                archived=bool(db.execute('SELECT 1 FROM room_states WHERE room=? AND archived=1',(other,)).fetchone())
            try:
                if stored:target={'revision':stored['revision'],'values':json.loads(stored['value'])}
                else:
                    local=self._config(other,provider);target=self.connection(provider,local['values'].get('connection_id','default'))
            except ValueError:continue
            reason=self.same_account(provider,current,target)
            if reason:matches.append({'room':other,'reason':reason,'archived':archived})
        return matches

    def automatic_conflicts(self,room,config=None,settings=None):
        from .discord_access import channels
        current=config or self.room_connection(room,'discord');local=settings or self._config(room,'discord')['values']
        if not local.get('enabled') or not local.get('auto_reply'):return []
        return [m for m in self.duplicates(room,'discord',current) if not m.get('archived') and (lambda v:v.get('enabled') and v.get('auto_reply') and bool(set(channels(v))&set(channels(local))))(self._config(m['room'],'discord')['values'])]

    def room_public(self,room,provider):
        owned=self.room_connection(room,provider);local=self._config(room,provider)
        return self._public({'revision':local['revision'],'values':{**(X_POST_DEFAULTS if provider=='x' else {}),**owned['values'],**{k:v for k,v in local['values'].items() if k!='connection_id'}}})

    def editor_token(self,room,provider,expected):
        if not room or provider!='discord':raise ValueError('Botトークンの対象ルームを指定してください。')
        with self.app.lock,self.lock:
            local=self._config(room,provider)
            if local['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
            config=self.room_connection(room,provider)
            return dict(token=unprotect(config['values']['token']) if config['values'].get('token') else '')

    def _save_connection(self,db,provider,config,values,new_revision=None):
        key=config.get('id','default');version=new_revision or config['revision']
        if key=='default':db.execute("INSERT OR REPLACE INTO external_configs VALUES('*',?,?,?)",(provider,version,packed(values)))
        else:db.execute('INSERT OR REPLACE INTO external_profiles VALUES(?,?,?,?)',(key,provider,version,packed(values)))
        if config.get('room'):
            db.execute('INSERT OR IGNORE INTO external_room_links VALUES(?,?,?)',(config['room'],provider,key))
            if new_revision:db.execute('UPDATE external_configs SET revision=? WHERE scope=? AND provider=?',(version,config['room'],provider))

    def _public(self,config):
        value=copy.deepcopy(config)
        for key in SECRET_FIELDS:value['values'][key+'_saved']=bool(value['values'].pop(key,''))
        return value

    def listing(self,room=''):
        if room:self.store.room_path(room)
        with self.app.lock,self.lock:
            result={'common':{'google':self._public(self.connection('google'))}}
            if room:
                result['room']={p:self.room_public(room,p) if p in ('x','discord') else self._public(self._config(room,p)) for p in PROVIDERS}
                result['duplicates']={p:self.duplicates(room,p) for p in ('x','discord')}
                with self.store.connect() as db:
                    result['outbox']=[self._item(r) for r in db.execute('SELECT * FROM external_outbox WHERE room=? ORDER BY created DESC LIMIT 50',(room,))]
                    result['history']=[dict(r) for r in db.execute('SELECT * FROM external_audit WHERE room=? ORDER BY id DESC LIMIT 50',(room,))]
            return result

    def save(self,room,provider,values,expected,profile_id='default'):
        scope=room or '*'
        if not room and provider in ('x','discord'):raise ValueError('アカウントの登録・認証はルーム内で行ってください。')
        if profile_id!='default':raise ValueError('登録済み接続の選択はできません。')
        with self.app.lock,self.lock:
            if self.app.closing:raise ValueError('終了処理中です。')
            if room:self.store.room_path(room)
            owned=self.room_connection(room,provider) if room and provider in ('x','discord') else None
            if room:
                current=self._config(scope,provider)
                if owned:current=dict(current,values={**owned['values'],**current['values']})
            else:
                try:current=self.connection(provider,profile_id)
                except ValueError:
                    if expected!='0' or provider not in ('x','discord'):raise
                    current={'id':profile_id,'revision':'0','values':{'enabled':False}}
            if current['revision']!=expected:raise EditConflict('設定が変更されています。再読込してください。')
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM external_outbox WHERE status='sending' LIMIT 1").fetchone():raise ValueError('送信中の設定は変更できません。')
            allowed=({'enabled','calendar','channels','receive_mode','auto_reply','reply_mode','reply_max_per_day'} | ({'account','client_id','client_secret','token','refresh_token','clear_secrets','mode','username','browser_state'} if owned else set())) if room else {'enabled','account','client_id','client_secret','token','refresh_token','clear_secrets','name','archived','mode','username','browser_state'}
            if room and provider=='discord':allowed|={'default_channel','user_id','allowed_user_ids'}
            if room and provider=='x':allowed|=X_POST_DEFAULTS.keys()
            if not isinstance(values,dict) or set(values)-allowed:raise ValueError('未定義の設定です。')
            out=copy.deepcopy(current['values'])
            for key,value in values.items():
                if key in ('enabled','clear_secrets','archived','auto_reply','auto_post','approval_for_replies','notify_discord_approval','is_premium'):
                    if type(value) is not bool:raise ValueError('ON/OFF指定が不正です。')
                    if key!='clear_secrets':out[key]=value
                    elif value:
                        for secret in SECRET_FIELDS:out.pop(secret,None)
                elif key in ('receive_mode','reply_mode','mode'):
                    options={'receive_mode':('rest','gateway'),'reply_mode':('mentions','always'),'mode':('api','browser')}
                    if value not in options[key] or key=='mode' and provider!='x':raise ValueError('接続/返信方式が不正です。')
                    out[key]=value
                elif key=='reply_max_per_day':
                    if type(value) is not int or not 0<=value<=100:raise ValueError('自動返信上限は0〜100です（0は無制限）。')
                    out[key]=value
                elif key in ('channels','allowed_user_ids'):
                    if key=='allowed_user_ids':
                        if not isinstance(value,list):raise ValueError('許可するユーザIDは一覧で指定してください。')
                        out[key]=value;continue
                    if not isinstance(value,list) or len(value)>20 or any(not isinstance(v,str) or not v.isascii() or not v.isdigit() or not 1<=len(v)<=25 for v in value):raise ValueError('チャンネルIDを20件以内で指定してください。')
                    out[key]=list(dict.fromkeys(value))
                elif key in ('posting_summary','posting_guidelines'):
                    out[key]=text(value,1000 if key=='posting_summary' else 6000)
                elif key in SECRET_FIELDS:
                    value=text(value,8192)
                    if any(c.isspace() for c in value):raise ValueError('認証情報に空白や改行を含めないでください。')
                    if value:
                        out[key]=protect(value);out.pop('expires_at',None)
                else:out[key]=text(value,256)
            if provider=='discord':
                from .discord_access import checked
                out=checked(out)
            if provider=='x' and out.get('mode','api')=='api' and out.get('account') and (not out['account'].isascii() or not out['account'].isdigit()):raise ValueError('Xの対象アカウントは数値のユーザーIDです。')
            if owned and any(values.get(k) or values.get('clear_secrets') for k in SECRET_FIELDS):out.pop('verified_account',None)
            if owned and provider=='discord' and self.automatic_conflicts(room,dict(owned,values=out),out):raise ValueError('同じBot/チャンネルの自動返信は1つのルームだけに許可してください。')
            version=revision()
            with self.store.connect() as db:
                if room:
                    if owned:
                        db.execute('INSERT INTO external_profile_versions(provider,profile,revision,value) VALUES(?,?,?,?)',(provider,owned['id'],owned['revision'],packed(owned['values'])))
                        credentials={k:v for k,v in out.items() if k not in ('calendar','channels','default_channel','user_id','allowed_user_ids','connection_id','receive_mode','auto_reply','reply_mode','reply_max_per_day')}
                        self._save_connection(db,provider,owned,credentials,version)
                        settings={k:v for k,v in out.items() if k in ('enabled','channels','default_channel','user_id','allowed_user_ids','receive_mode','auto_reply','reply_mode','reply_max_per_day')};settings['connection_id']=owned['id']
                    else:settings=out
                    db.execute('INSERT OR REPLACE INTO external_configs VALUES(?,?,?,?)',(scope,provider,version,packed(settings)))
                else:
                    db.execute('INSERT INTO external_profile_versions(provider,profile,revision,value) VALUES(?,?,?,?)',(provider,profile_id,current['revision'],packed(current['values'])))
                    self._save_connection(db,provider,current,out,version)
                self._audit(db,room,provider,'settings','ok')
            return self.room_public(room,provider) if owned else self._public({'revision':version,'values':out})

    def enabled(self,room,provider):
        try:self._guard(room,provider);return True
        except ValueError:return False

    def _guard(self,room,provider):
        self.store.room_path(room)
        if self.app.closing:raise ValueError('終了処理中です。')
        common=self.room_connection(room,provider) if provider in ('x','discord') else self.connection(provider)
        local=self._config(room,provider)
        if common['values'].get('archived'):raise ValueError('この接続は保管されています。')
        if not common['values'].get('enabled') or not local['values'].get('enabled'):raise ValueError('このルームの連携を有効にしてください。')
        if provider=='discord':
            from .discord_access import checked
            checked({**common['values'],**local['values']})
        browser=provider=='x' and common['values'].get('mode')=='browser'
        if not (common['values'].get('browser_state') if browser else common['values'].get('token') or common['values'].get('refresh_token')):raise ValueError('未認証です。')
        return common,local

    def _token(self,provider,config):
        values=config['values']
        if values.get('token') and values.get('expires_at',0)>time.time()+60:return unprotect(values['token'])
        if values.get('refresh_token'):
            form={'grant_type':'refresh_token','refresh_token':unprotect(values['refresh_token']),'client_id':values.get('client_id','')}
            if values.get('client_secret'):form['client_secret']=unprotect(values['client_secret'])
            headers={}
            if provider=='x' and values.get('client_secret'):
                headers['Authorization']='Basic '+base64.b64encode((values['client_id']+':'+unprotect(values['client_secret'])).encode()).decode();form.pop('client_secret',None)
            result=self.fetch('google_oauth' if provider=='google' else 'x','POST','/token' if provider=='google' else '/2/oauth2/token',body=form,form=True,headers=headers)
            token=result.get('access_token')
            if not isinstance(token,str) or not token:raise ValueError('トークン更新に失敗しました。')
            values={**values,'token':protect(token),'expires_at':time.time()+min(86400,max(0,int(result.get('expires_in',3600))))}
            if result.get('refresh_token'):values['refresh_token']=protect(result['refresh_token'])
            with self.store.connect() as db:self._save_connection(db,provider,config,values)
            return token
        return unprotect(values.get('token',''))

    def _config_for(self,provider,common,local):
        conf={**common['values'],**local['values']}
        if provider=='google' and not conf.get('calendar'):raise ValueError('ルーム別にカレンダーを選んでください。')
        return conf

    def read(self,room,provider,action,args=None,checkpoint=lambda:None):
        with self.app.lock,self.lock:
            checkpoint();common,local=self._guard(room,provider);conf=self._config_for(provider,common,local) if action not in ('calendars','identity') else {**common['values'],**local['values']}
            args=dict(args or {})
            if provider=='discord' and action in ('messages','channel','members') and not args.get('channel'):args['channel']=conf.get('default_channel','')
            if provider=='discord' and action in ('guilds','channels'):
                from .discord_directory import read_directory
                result=read_directory(self,common,conf,action,args);checkpoint()
                with self.store.connect() as db:
                    from .discord_labels import remember
                    if action=='channels':
                        for channel in result['channels']:
                            if channel['allowed']:remember(db,room,common['id'],channel['id'],channel['name'])
                    self._audit(db,room,provider,action,'ok')
                return {'provider':provider,'action':action,'data':result,'retrieved_at':time.time(),'untrusted':True}
            if action in ('events','freebusy'):args=date_range(args)
            if provider=='discord' and action in ('messages','channel'):self._channel(conf,args.get('channel'))
            if provider=='x' and conf.get('mode')=='browser':
                result=self.app.xbrowser.execute(common,action,args);checkpoint()
                with self.store.connect() as db:self._audit(db,room,provider,action,'ok')
                return {'provider':provider,'action':action,'data':result,'retrieved_at':time.time(),'untrusted':True}
            if provider=='discord' and action=='members':
                from .discord_members import members
                checkpoint();token=self._token(provider,common);checkpoint()
                return {'provider':provider,'action':action,'data':members(self,conf,token,args.get('channel'),args.get('after',''),checkpoint),'retrieved_at':time.time(),'untrusted':True}
            method,path,body=providers.read_request(provider,conf,action,args)
            checkpoint();token=self._token(provider,common);checkpoint()
            result=self.fetch(provider,method,path,token,body)
            checkpoint()
            if provider=='discord' and action=='channel' and isinstance(result,dict):
                from .discord_labels import remember
                with self.store.connect() as db:remember(db,room,common['id'],args['channel'],result.get('name'))
            if action=='identity' and provider in ('x','discord'):
                identity=result.get('data',{}) if provider=='x' else result
                self.remember_identity(room,provider,identity.get('id'),common['revision'])
            with self.store.connect() as db:self._audit(db,room,provider,action,'ok')
            return {'provider':provider,'action':action,'data':result,'retrieved_at':time.time(),'untrusted':True}

    def remember_identity(self,room,provider,identity,expected):
        if provider not in ('x','discord'):return
        with self.app.lock,self.lock:
            config=self.room_connection(room,provider)
            if config['revision']!=expected:raise EditConflict('接続設定が変更されました。')
            value=str(identity or '')
            if not value.isascii() or not value.isdigit() or len(value)>30:return
            with self.store.connect() as db:self._save_connection(db,provider,config,dict(config['values'],verified_account=value))

    def _channel(self,conf,channel):
        from .discord_access import channels
        if not isinstance(channel,str) or channel not in channels(conf):raise ValueError('ルームで許可されたチャンネルを選んでください。')

    def _payload(self,provider,action,args,conf):
        if not isinstance(args,dict):raise ValueError('更新内容が不正です。')
        if provider=='google':
            if action not in ('create','update','delete'):raise ValueError('予定操作が不正です。')
            allowed={'summary','description','start','end'} if action=='create' else {'event_id','etag'} if action=='delete' else {'summary','description','start','end','event_id','etag'}
            if set(args)-allowed:raise ValueError('未定義の予定項目です。')
            out={}
            if action!='delete':
                out={'summary':text(args.get('summary'),300,False),'description':text(args.get('description',''),4000)}
                times=date_range(args)
                out.update({k:{'dateTime':v} for k,v in times.items()})
            if action!='create':
                out['event_id']=text(args.get('event_id'),1024,False);out['etag']=text(args.get('etag'),256,False)
                if any(c in out['etag'] for c in '\r\n'):raise ValueError('etagは1行で指定してください。')
            return out
        if provider=='x' and action=='post':
            if set(args)-{'text','image_ids','reply_to','quote_post'}:raise ValueError('投稿の引数が不正です。')
            targets={k:text(args[k],25,False) for k in ('reply_to','quote_post') if args.get(k)}
            if len(targets)>1 or any(not v.isascii() or not v.isdigit() for v in targets.values()):raise ValueError('引用または返信先の投稿IDを1つ指定してください。')
            if targets and conf.get('mode')=='browser':raise ValueError('Cookie方式では引用・返信の送信に対応していません。')
            body=text(args.get('text',''),25000 if conf.get('is_premium') else 280,bool(args.get('image_ids')))
            if not conf.get('is_premium') and x_post_length(body)>280:
                raise ValueError('通常投稿はX換算で280文字以内です（日本語のみなら目安140文字）。')
            return {'text':body,**targets,**({'image_ids':args['image_ids']} if args.get('image_ids') else {})}
        if provider=='discord' and action=='send':
            args=dict(args)
            if not args.get('channel'):args['channel']=conf.get('default_channel','')
            if set(args)-{'text','channel','image_ids','reply_to','recipient'} or 'channel' not in args:raise ValueError('本文とチャンネルを指定してください。')
            self._channel(conf,args.get('channel'))
            if args.get('recipient'):
                from .discord_directory import snowflake
                recipient=snowflake(args['recipient'])
                if len(str(args.get('text','')))+len(recipient)+4>2000:raise ValueError('宛先メンションを含めて2000文字以内にしてください。')
                return {'text':text(args.get('text',''),2000,bool(args.get('image_ids'))),'channel':args['channel'],'recipient':recipient,**({'image_ids':args['image_ids']} if args.get('image_ids') else {}),**({'reply_to':text(args['reply_to'],30,False)} if args.get('reply_to') else {})}
            return {'text':text(args.get('text',''),2000,bool(args.get('image_ids'))),'channel':args['channel'],**({'image_ids':args['image_ids']} if args.get('image_ids') else {}),**({'reply_to':text(args['reply_to'],30,False)} if args.get('reply_to') else {})}
        raise ValueError('更新操作が不正です。')

    def _item(self,row):
        item={k:(json.loads(row[k]) if k in ('payload','result') else row[k]) for k in ('id','room','provider','action','revision','payload','status','result','created')}
        item['result'].pop('discord_approval',None)
        return item

    def propose(self,room,provider,action,args,request_id,checkpoint=lambda:None,*,allow_auto=False):
        request_id=text(request_id,200,False);signature=hashlib.sha256(packed([provider,action,args]).encode()).hexdigest()
        with self.app.lock,self.lock:
            checkpoint();self.store.room_path(room)
            with self.store.connect() as db:previous=db.execute('SELECT * FROM external_outbox WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if previous:
                if previous['signature']!=signature:raise EditConflict('同じ呼出IDの内容が異なります。')
                return self._item(previous)
            common,local=self._guard(room,provider);conf=self._config_for(provider,common,local)
            payload=self._payload(provider,action,args,conf)
            if 'image_ids' in payload:payload['images']=self.media.snapshot(room,payload.pop('image_ids'),provider)
            payload['connection_id']=common.get('id','default');payload['connection_name']=common.get('room') or common['values'].get('name') or '既定の接続'
            key=revision();checkpoint()
            with self.store.connect() as db:
                db.execute('INSERT INTO external_outbox VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,room,provider,action,revision(),common['revision'],local['revision'],packed(payload),'pending','{}',request_id,signature,time.time()))
                self._audit(db,room,provider,action,'pending',key)
                item=self._item(db.execute('SELECT * FROM external_outbox WHERE id=?',(key,)).fetchone())
            if allow_auto and provider=='x' and conf.get('auto_post') and not (
                conf.get('approval_for_replies',True) and (payload.get('reply_to') or payload.get('quote_post'))
            ):
                checkpoint()
                return self.review(room,key,item['revision'],True)
            if provider=='x' and conf.get('notify_discord_approval'):
                from .x_discord_approval import notify
                notify(self,item)
            return item

    def review(self,room,key,expected,approve=False):
        # Serialize with room archival, app shutdown, config and policy edits. Network is bounded.
        with self.app.lock,self.lock:
            self.store.room_path(room)
            if self.app.closing:raise ValueError('終了処理中です。')
            with self.store.connect() as db:row=db.execute('SELECT * FROM external_outbox WHERE id=? AND room=?',(key,room)).fetchone()
            if not row:raise ValueError('下書きが見つかりません。')
            if row['status']!='pending':return self._item(row)
            if row['revision']!=expected:raise EditConflict('下書きの版が変わりました。')
            if not approve:return self._finish(row,'rejected',{})
            common,local=self._guard(room,row['provider'])
            if (row['common_revision'],row['room_revision'])!=(common['revision'],local['revision']):raise EditConflict('作成後に接続設定が変わりました。送信せず下書きを作り直してください。')
            conf=self._config_for(row['provider'],common,local)
            payload=json.loads(row['payload'])
            if payload.get('connection_id',common.get('id'))!=common.get('id'):raise EditConflict('接続先が変わりました。')
            images=self.media.load(room,payload.get('images',[]),row['provider']) if payload.get('images') else []
            method,path,body,headers=providers.write_request(row['provider'],conf,row['action'],payload,row['id'])
            # Authentication/preflight cannot have sent the requested mutation.
            browser=row['provider']=='x' and conf.get('mode')=='browser'
            token='' if browser else self._token(row['provider'],common)
            if row['provider']=='discord':
                from .discord_labels import resolve
                resolve(self,room,common['id'],payload['channel'])
            if row['provider']=='x' and not browser:
                identity=self.fetch('x','GET','/2/users/me',token)
                if identity.get('data',{}).get('id')!=conf.get('account'):raise ValueError('認証中のXアカウントが対象アカウントと一致しません。')
            with self.store.connect() as db:
                db.execute("UPDATE external_outbox SET status='sending',revision=? WHERE id=?",(revision(),key))
                self._audit(db,room,row['provider'],row['action'],'sending',key)
            try:
                def record(value):
                    with self.store.connect() as db:db.execute('UPDATE external_outbox SET result=? WHERE id=?',(packed(value),key))
                if browser:
                    result=self.app.xbrowser.execute(common,'post',{'text':payload['text']},images,record)
                    return self._finish(row,'sent',{'response':result})
                if images and row['provider']=='x':body['media']={'media_ids':self.media.x_upload(self,token,images,record)}
                if row['provider']=='discord':
                    if payload.get('reply_to'):body['message_reference']={'message_id':payload['reply_to'],'fail_if_not_exists':False}
                    if images:body,headers=self.media.discord_body(body,images)
                result=self.fetch(row['provider'],method,path,token,body,headers=headers)
                return self._finish(row,'sent',{'response':result})
            except providers.ProviderError as error:
                status='conflict' if error.status==412 else 'unknown' if error.status>=500 else 'error'
                return self._finish(row,status,{'message':str(error)})
            except Exception:
                # Do not expose provider bodies, URL query strings or tokens in diagnostics.
                if browser:
                    with self.store.connect() as db:
                        phase=json.loads(db.execute('SELECT result FROM external_outbox WHERE id=?',(key,)).fetchone()[0]).get('browser_phase')
                    if phase=='waiting_post_button':
                        return self._finish(row,'error',{'message':'投稿ボタンが操作可能にならず、押下前に停止しました。'})
                    if phase=='attaching':
                        return self._finish(row,'error',{'message':'画像の添付または投稿前のアカウント確認を完了できませんでした。'})
                    if phase=='clicked':
                        return self._finish(row,'unknown',{'message':'投稿ボタンの押下後、完了URLを確認できませんでした。自動再送せずXで確認してください。'})
                return self._finish(row,'unknown',{'message':'送信結果を確認できません。自動再送はしません。接続先で確認してください。'})

    def _finish(self,row,status,result):
        with self.store.connect() as db:
            previous=db.execute('SELECT result FROM external_outbox WHERE id=?',(row['id'],)).fetchone()
            result={**(json.loads(previous[0]) if previous else {}),**result}
            if row['provider']=='discord' and status=='sent':
                from .discord_chat import sent
                db.execute('SAVEPOINT discord_chat_projection')
                try:
                    job=getattr(self.app,'job',None) or {}
                    sent(db,row,result,job.get('display_model','Discord') if job.get('kind')=='external_reply' and job.get('room')==row['room'] else 'Discord')
                    db.execute('RELEASE discord_chat_projection')
                except (ValueError,sqlite3.Error,TypeError):
                    db.execute('ROLLBACK TO discord_chat_projection');db.execute('RELEASE discord_chat_projection')
                    result['chat_warning']='送信は成功しましたが、本体会話への反映を確認できません。再送せず受信履歴を確認してください。'
                    self._audit(db,row['room'],'discord','chat-mirror','failed',row['id'])
            db.execute('UPDATE external_outbox SET status=?,revision=?,result=? WHERE id=?',(status,revision(),packed(result),row['id']))
            self._audit(db,row['room'],row['provider'],row['action'],status,row['id'])
            return self._item(db.execute('SELECT * FROM external_outbox WHERE id=?',(row['id'],)).fetchone())

    def oauth_begin(self,provider,port,room=''):
        if provider not in ('google','x'):raise ValueError('OAuth対象が不正です。')
        with self.app.lock,self.lock:
            if self.app.closing:raise ValueError('終了処理中です。')
            config=self.room_connection(room,provider) if provider=='x' else self.connection(provider)
            if not config['values'].get('client_id'):raise ValueError('先にOAuthクライアントIDを保存してください。')
            state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
            redirect='http://127.0.0.1:'+str(port)+'/api/external-oauth-callback'
            self.oauth={s:v for s,v in self.oauth.items() if v['expires']>time.monotonic()}
            if len(self.oauth)>8:raise ValueError('認証待ちが多すぎます。少し待ってください。')
            self.oauth[state]={'provider':provider,'room':room,'revision':config['revision'],'verifier':verifier,'redirect':redirect,'expires':time.monotonic()+600}
            params={'response_type':'code','client_id':config['values']['client_id'],'redirect_uri':redirect,'state':state,'code_challenge_method':'S256','code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
            if provider=='google':params.update(scope='https://www.googleapis.com/auth/calendar',access_type='offline',prompt='consent');base='https://accounts.google.com/o/oauth2/v2/auth'
            else:params.update(scope='tweet.read tweet.write users.read media.write offline.access');base='https://x.com/i/oauth2/authorize'
            return {'url':base+'?'+urlencode(params),'redirect_uri':redirect}

    def oauth_finish(self,state,code):
        with self.app.lock,self.lock:
            pending=self.oauth.pop(state,None)
            if not pending or pending['expires']<time.monotonic():raise ValueError('認証要求が無効または期限切れです。')
            provider=pending['provider'];config=self.room_connection(pending.get('room',''),provider) if provider=='x' else self.connection(provider)
            if self.app.closing or config['revision']!=pending['revision']:raise EditConflict('認証中に接続設定が変わりました。認証をやり直してください。')
            values=config['values'];form={'grant_type':'authorization_code','code':text(code,8192,False),'redirect_uri':pending['redirect'],'client_id':values['client_id'],'code_verifier':pending['verifier']}
            if values.get('client_secret'):form['client_secret']=unprotect(values['client_secret'])
            headers={}
            if provider=='x' and values.get('client_secret'):
                headers['Authorization']='Basic '+base64.b64encode((values['client_id']+':'+unprotect(values['client_secret'])).encode()).decode();form.pop('client_secret',None)
            response=self.fetch('google_oauth' if provider=='google' else 'x','POST','/token' if provider=='google' else '/2/oauth2/token',body=form,form=True,headers=headers)
            if not isinstance(response.get('access_token'),str) or not response['access_token']:raise ValueError('アクセストークンを取得できませんでした。')
            updated={**values,'token':protect(response['access_token']),'expires_at':time.time()+min(86400,max(0,int(response.get('expires_in',3600))))}
            if response.get('refresh_token'):updated['refresh_token']=protect(response['refresh_token'])
            if provider=='x':
                identity=self.fetch('x','GET','/2/users/me',response['access_token'])
                updated['account']=text(identity.get('data',{}).get('id'),40,False)
            with self.store.connect() as db:
                self._save_connection(db,provider,config,updated,revision())
                self._audit(db,pending.get('room',''),provider,'oauth','ok')
            return {'message':'認証情報を保存しました。あかりの設定画面で再読込してください。'}
