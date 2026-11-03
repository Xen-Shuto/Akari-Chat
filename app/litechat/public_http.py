"""Bounded HTTP transport with address validation and a pinned DNS result."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit,urljoin,urlunsplit,quote

class RemoteError(ValueError):pass
class RemoteTimeout(RemoteError):pass

def parsed_url(url):
    if not isinstance(url,str) or len(url)>2048 or any(ord(c)<33 or ord(c)==127 for c in url):raise RemoteError('URLの形式を確認してください。')
    try:
        p=urlsplit(url);port=p.port
        if p.scheme not in ('http','https') or not p.hostname or '%' in p.hostname or p.username is not None or p.password is not None:raise ValueError()
        p.hostname.encode('idna')
    except (ValueError,UnicodeError) as error:raise RemoteError('HTTP/HTTPSのURLを指定してください。認証情報を含むURLは使えません。') from error
    return p

def addresses(url,allow_local=False):
    p=parsed_url(url);port=p.port or (443 if p.scheme=='https' else 80)
    if not allow_local and port not in (80,443):raise RemoteError('Web参照は標準のHTTP/HTTPSポートのみです。')
    try:infos=socket.getaddrinfo(p.hostname.encode('idna').decode(),port,type=socket.SOCK_STREAM)
    except OSError as error:raise RemoteError('接続先の名前解決に失敗しました。') from error
    ips=list(dict.fromkeys(row[4][0] for row in infos))
    if not ips or not allow_local and any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast or ipaddress.ip_address(ip).is_reserved for ip in ips):
        raise RemoteError('Web参照では公開インターネットのアドレスだけを取得します。')
    return p,port,ips

def request(url,method='GET',body=None,headers=None,timeout=12,limit=1048576,checkpoint=lambda:None,allow_local=False,redirects=3):
    deadline=time.monotonic()+timeout
    for _ in range(redirects+1):
        checkpoint();p,port,ips=addresses(url,allow_local);checkpoint()
        remaining=deadline-time.monotonic()
        if remaining<=0:raise RemoteTimeout('接続先の応答待ちがタイムアウトしました。')
        connection=http.client.HTTPConnection(p.hostname,port,timeout=remaining)
        try:
            connection.sock=socket.create_connection((ips[0],port),timeout=remaining)
            if p.scheme=='https':connection.sock=ssl.create_default_context().wrap_socket(connection.sock,server_hostname=p.hostname.encode('idna').decode())
            sock=connection.sock
            path=urlunsplit(('', '',quote(p.path or '/',safe="/%:@!$&'()*+,;=-._~"),quote(p.query,safe="/%:@!$&'()*+,;=?-._~"),''))
            connection.request(method,path,body,headers or {'User-Agent':'Akari-Chat/1.0','Accept-Encoding':'identity'})
            response=connection.getresponse();checkpoint()
            if response.status in (301,302,303,307,308):
                if method!='GET' or not response.getheader('Location'):raise RemoteError('この要求のリダイレクトは実行しません。')
                url=urljoin(url,response.getheader('Location'));continue
            if not 200<=response.status<300:raise RemoteError('接続先がHTTP '+str(response.status)+'を返しました。')
            if response.getheader('Content-Encoding','identity').lower() not in ('identity',''):raise RemoteError('圧縮応答には対応していません。')
            raw=bytearray()
            while True:
                checkpoint();remaining=deadline-time.monotonic()
                if remaining<=0:raise RemoteTimeout('接続先の応答待ちがタイムアウトしました。')
                sock.settimeout(remaining)
                block=response.read1(min(65536,limit+1-len(raw)))
                if not block:break
                raw.extend(block)
                if len(raw)>limit:raise RemoteError('取得サイズの上限を超えました。')
            return bytes(raw),response.getheader('Content-Type',''),url
        except TimeoutError as error:raise RemoteTimeout('接続先の応答待ちがタイムアウトしました。生成先の処理が続いている可能性があります。') from error
        except (OSError,http.client.HTTPException) as error:raise RemoteError('接続先との通信を完了できませんでした（'+type(error).__name__+'）。') from error
        finally:connection.close()
    raise RemoteError('リダイレクト回数の上限を超えました。')
