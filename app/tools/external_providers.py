"""Bounded official API adapters. No arbitrary credential destinations or redirects."""
import http.client
import json
import ssl
import time
from urllib.parse import urlencode, quote

class ProviderError(ValueError):
    def __init__(self,status):
        self.status=status
        super().__init__('接続先がHTTP '+str(status)+'を返しました。認証・権限・利用枠を確認してください。')

HOSTS={'google':'www.googleapis.com','x':'api.x.com','discord':'discord.com','google_oauth':'oauth2.googleapis.com'}

def transport(provider,method,path,token='',body=None,headers=None,form=False):
    host=HOSTS[provider]
    if not path.startswith('/') or any(ord(c)<32 for c in path):raise ValueError('APIパスが不正です。')
    request_headers={'Accept':'application/json','Accept-Encoding':'identity','User-Agent':'Akari-Chat/1.0'}
    if token:request_headers['Authorization']=('Bot ' if provider=='discord' else 'Bearer ')+token
    if headers:request_headers.update(headers)
    payload=None
    if isinstance(body,bytes):
        if len(body)>9*1048576:raise ValueError('送信サイズの上限です。')
        payload=body
    elif body is not None:
        payload=(urlencode(body) if form else json.dumps(body,ensure_ascii=False)).encode('utf-8')
        request_headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
    deadline=time.monotonic()+12
    conn=http.client.HTTPSConnection(host,timeout=12,context=ssl.create_default_context())
    try:
        conn.request(method,path,payload,request_headers)
        sock=conn.sock
        response=conn.getresponse()
        raw=bytearray()
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('API request deadline')
            sock.settimeout(remaining)
            chunk=response.read1(min(65536,1024*1024+1-len(raw)))
            if not chunk:break
            raw.extend(chunk)
            if len(raw)>1024*1024:raise ValueError('応答サイズの上限を超えました。')
        if not 200<=response.status<300:raise ProviderError(response.status)
        if not raw:return {}
        value=json.loads(raw)
        if not isinstance(value,(dict,list)):raise ValueError('接続先の応答形式が不正です。')
        return value
    finally:conn.close()

def segment(value):return quote(str(value),safe='')


def read_request(provider,config,action,args):
    if provider=='google':
        if action=='calendars':return 'GET','/calendar/v3/users/me/calendarList?maxResults=100',None
        base='/calendar/v3/calendars/'+segment(config['calendar'])
        if action=='events':return 'GET',base+'/events?'+urlencode({'timeMin':args['start'],'timeMax':args['end'],'singleEvents':'true','orderBy':'startTime','maxResults':50}),None
        if action=='freebusy':return 'POST','/calendar/v3/freeBusy',{'timeMin':args['start'],'timeMax':args['end'],'items':[{'id':config['calendar']}]}
    if provider=='x':
        if action=='identity':return 'GET','/2/users/me',None
        if action=='timeline':return 'GET','/2/users/'+segment(config['account'])+'/tweets?max_results=10&tweet.fields=created_at,author_id',None
        if action=='mentions':return 'GET','/2/users/'+segment(config['account'])+'/mentions?max_results=10&tweet.fields=created_at,author_id',None
    if provider=='discord':
        if action=='identity':return 'GET','/api/v10/users/@me',None
        if action=='channel':return 'GET','/api/v10/channels/'+segment(args['channel']),None
        if action=='messages':return 'GET','/api/v10/channels/'+segment(args['channel'])+'/messages?'+urlencode({'limit':100,**({'after':args['after']} if args.get('after') else {})}),None
    raise ValueError('対応していない参照操作です。')


def write_request(provider,config,action,args,key):
    if provider=='google':
        base='/calendar/v3/calendars/'+segment(config['calendar'])+'/events'
        event={k:args[k] for k in ('summary','description','start','end') if k in args}
        # Google accepts caller IDs in base32hex. A stable ID permits manual reconciliation.
        if action=='create':return 'POST',base,{**event,'id':key},{}
        if action in ('update','delete'):
            return ('PATCH' if action=='update' else 'DELETE'),base+'/'+segment(args['event_id']),event if action=='update' else None,{'If-Match':args['etag']}
    if provider=='x' and action=='post':
        body={'text':args['text']}
        if args.get('reply_to'):body['reply']={'in_reply_to_tweet_id':args['reply_to']}
        if args.get('quote_post'):body['quote_tweet_id']=args['quote_post']
        return 'POST','/2/tweets',body,{}
    if provider=='discord' and action=='send':return 'POST','/api/v10/channels/'+segment(args['channel'])+'/messages',{'content':('<@'+args['recipient']+'> ' if args.get('recipient') else '')+args['text'],'allowed_mentions':{'parse':[],**({'users':[args['recipient']]} if args.get('recipient') else {})},'nonce':key[:25],'enforce_nonce':True},{}
    raise ValueError('対応していない更新操作です。')
