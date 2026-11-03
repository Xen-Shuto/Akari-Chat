"""Explicit, bounded model discovery. No redirects, credential logging or fallback."""
import http.client,json,socket,threading,time
from urllib.parse import urlsplit,urlencode


def get_page(target,query,remaining):
    url=urlsplit(target.endpoint)
    connection=(http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection)(url.hostname,url.port,timeout=min(10,remaining))
    headers={'Accept':'application/json'}
    if target.provider=='gemini':headers['x-goog-api-key']=target.key
    elif target.provider=='anthropic':headers.update({'x-api-key':target.key,'anthropic-version':'2023-06-01'})
    else:headers['Authorization']='Bearer '+target.key
    def expire():
        if connection.sock:
            try:connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        connection.close()
    timer=threading.Timer(remaining,expire);timer.daemon=True;timer.start()
    try:
        connection.request('GET',url.path.rstrip('/')+'/models'+('?' + urlencode(query) if query else ''),headers=headers)
        response=connection.getresponse()
        if response.status!=200:raise ValueError('モデル一覧を取得できませんでした（HTTP '+str(response.status)+'）。キー・接続先・利用制限を確認してください。')
        raw=response.read(2*1048576+1)
        if len(raw)>2*1048576:raise ValueError('モデル一覧の応答が大きすぎます。')
        return json.loads(raw)
    except (OSError,http.client.HTTPException,UnicodeError,json.JSONDecodeError):
        raise ValueError('モデル一覧の通信または応答形式を確認できませんでした。自動再試行はしません。') from None
    finally:timer.cancel();connection.close()


def list_models(target,closing=lambda:False,details=None):
    models=set();cursor='';seen=set();deadline=time.monotonic()+45
    for page in range(10):
        if closing():raise ValueError('終了処理のためモデル一覧の取得を中止しました。')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ValueError('モデル一覧の取得時間を超えました。')
        query={}
        if target.provider=='gemini':query={'pageSize':1000,**({'pageToken':cursor} if cursor else {})}
        elif target.provider=='anthropic':query={'limit':1000,**({'after_id':cursor} if cursor else {})}
        data=get_page(target,query,remaining)
        if closing():raise ValueError('終了処理のためモデル一覧の取得を中止しました。')
        if time.monotonic()>deadline:raise ValueError('モデル一覧の取得時間を超えました。')
        if not isinstance(data,dict) or not isinstance(data.get('models' if target.provider=='gemini' else 'data'),list):raise ValueError('モデル一覧の応答形式が不正です。')
        rows=data['models' if target.provider=='gemini' else 'data']
        for row in rows:
            if not isinstance(row,dict):raise ValueError('モデル一覧の項目形式が不正です。')
            if target.provider=='gemini':
                if 'generateContent' not in row.get('supportedGenerationMethods',[]):continue
                model=row.get('name','')
                if not isinstance(model,str) or not model.startswith('models/'):raise ValueError('モデルIDの形式が不正です。')
                model=model[len('models/'):]
            else:model=row.get('id','')
            if not isinstance(model,str) or not model or len(model)>200 or any(ord(c)<32 for c in model) or target.key in model:raise ValueError('モデルIDの形式が不正です。')
            models.add(model)
            if details is not None:
                from .conversation_capabilities import detect
                details[model]=detect(target.provider,model,row)
        if len(models)>5000:raise ValueError('モデル一覧の件数上限を超えました。')
        cursor=data.get('nextPageToken','') if target.provider=='gemini' else data.get('last_id','') if target.provider=='anthropic' and data.get('has_more') else ''
        if target.provider=='anthropic' and data.get('has_more') and not cursor:raise ValueError('モデル一覧のページ情報が不正です。')
        if not cursor:return sorted(models)
        if not isinstance(cursor,str) or len(cursor)>4096 or cursor in seen:raise ValueError('モデル一覧のページ情報が不正です。')
        seen.add(cursor)
    raise ValueError('モデル一覧のページ上限を超えました。')
