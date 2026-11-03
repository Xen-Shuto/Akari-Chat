"""Normalize explicit user-provided X Cookie exports; never read browser profiles."""
import json,math,time

MAX_COOKIE_JSON=262144

def parse_cookies(raw):
 if not isinstance(raw,str) or not raw.strip() or len(raw.encode('utf-8'))>MAX_COOKIE_JSON:raise ValueError('Cookie JSONは空欄にせず、256KiB以内で指定してください。')
 try:data=json.loads(raw.lstrip("\ufeff"))
 except (ValueError,RecursionError):raise ValueError('Cookie JSONの形式を確認してください。') from None
 rows=data.get('cookies') if isinstance(data,dict) else data
 if not isinstance(rows,list) or not 1<=len(rows)<=300:raise ValueError('Cookie配列、またはcookies配列を含むJSONを指定してください。')
 cookies=[];seen=set()
 for row in rows:
  if not isinstance(row,dict):raise ValueError('Cookieの各項目はオブジェクトで指定してください。')
  domain=row.get('domain','')
  if not isinstance(domain,str) or domain.lstrip('.').lower() not in ('x.com','twitter.com'):continue
  cookie={'domain':domain.lower(),'path':row.get('path','/')}
  if not isinstance(cookie['path'],str) or not cookie['path'].startswith('/') or len(cookie['path'])>1024:raise ValueError('Cookieのpathが不正です。')
  for key,limit in [('name',256),('value',16384)]:
   v=row.get(key)
   if not isinstance(v,str) or len(v)>limit or (not v and key=='name') or any(ord(c)<32 or ord(c)==127 for c in v):raise ValueError('Cookieのname/valueが不正です。')
   cookie[key]=v
  for key in ('httpOnly','secure'):
   v=row.get(key,False)
   if type(v) is not bool:raise ValueError('Cookieの属性が不正です。')
   cookie[key]=v
  ss=row.get('sameSite')
  if ss is not None:
   if not isinstance(ss,str):raise ValueError('CookieのsameSiteが不正です。')
   if ss.lower() not in ('unspecified',''):
    value={'strict':'Strict','lax':'Lax','none':'None','no_restriction':'None'}.get(ss.lower())
    if not value:raise ValueError('CookieのsameSiteが不正です。')
    cookie['sameSite']=value
  expiry=row.get('expires',row.get('expirationDate'))
  if expiry is not None and not row.get('session'):
   if type(expiry) not in (int,float) or not math.isfinite(expiry) or expiry < -1:raise ValueError('Cookieの有効期限が不正です。')
   if expiry!=-1 and expiry<=time.time():continue
   cookie['expires']=expiry
  identity=(cookie['domain'],cookie['path'],cookie['name'])
  if identity in seen:raise ValueError('同じCookieが重複しています。')
  seen.add(identity);cookies.append(cookie)
 if not any(c['name']=='auth_token' and c['value'] for c in cookies):raise ValueError('有効なXのログインCookie（auth_token）が見つかりません。')
 return {'cookies':cookies,'origins':[]}
