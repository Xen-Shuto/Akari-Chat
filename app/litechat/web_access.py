"""DuckDuckGo HTML search and immutable room-scoped URL snapshots."""
import hashlib
import json
import re
import time
import uuid
from datetime import datetime,timezone
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit,parse_qs
from .public_http import request,parsed_url,RemoteError
from .file_editor import EditConflict
from .ddg_transport import search_response

class SearchError(RemoteError):
    def __init__(self,message,reason,http_status):
        super().__init__(message);self.reason=reason;self.http_status=http_status

class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.skip=0;self.parts=[];self.title=[];self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg','nav','footer','header'):self.skip+=1
        if tag=='title':self.in_title=True
        if tag in ('p','div','br','li','h1','h2','h3','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg','nav','footer','header'):self.skip=max(0,self.skip-1)
        if tag=='title':self.in_title=False
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        if not self.skip:self.parts.append(data)
    def text(self):return '\n'.join(re.sub(r'\s+',' ',line).strip() for line in ''.join(self.parts).splitlines() if line.strip())[:200000]

class Results(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.rows=[];self.current=None;self.capture='';self.depth=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);classes=a.get('class','').split()
        if tag=='a' and 'result__a' in classes:
            self.current={'title':'','url':a.get('href',''),'snippet':''};self.rows.append(self.current);self.capture='title';self.depth=1
        elif self.current and 'result__snippet' in classes:self.capture='snippet';self.depth=1
        elif self.capture and tag not in ('br','img','input','meta','link'):self.depth+=1
    def handle_endtag(self,tag):
        if self.capture:self.depth-=1
        if self.depth<=0:self.capture=''
    def handle_data(self,data):
        if self.capture and self.current:self.current[self.capture]+=data

def decoded(raw,content_type):
    match=re.search(r'charset=["\']?([^;"\' ]+)',content_type,re.I)
    encoding=match[1] if match else 'utf-8'
    try:return raw.decode(encoding,errors='replace')
    except LookupError:return raw.decode('utf8',errors='replace')

class WebAccess:
    def __init__(self,store,fetch=request,clock=time.time):
        self.store,self.fetch,self.clock=store,fetch,clock
        with store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS web_documents(id TEXT PRIMARY KEY,room TEXT,url TEXT,title TEXT,body TEXT,fetched REAL,revision TEXT)')
    def search(self,query,limit,checkpoint):
        status,html,final=search_response(query,checkpoint)
        if status==202 or any(word in html.lower() for word in ('anomaly.js','anomaly-modal','bots use duckduckgo','challenge-form')):raise SearchError('DuckDuckGoが追加確認を要求しています。自動再試行はしません。','challenge',status)
        if status!=200:raise SearchError('DuckDuckGoがHTTP '+str(status)+'を返しました。自動再試行はしません。','http_error',status)
        parser=Results();parser.feed(html);rows=[]
        for row in parser.rows:
            target=urljoin(final,row['url']);p=urlsplit(target)
            if p.hostname in ('duckduckgo.com','www.duckduckgo.com') and p.path.startswith('/l/'):
                target=parse_qs(p.query).get('uddg',[''])[0]
            try:parsed_url(target)
            except ValueError:continue
            if target in [r['url'] for r in rows]:continue
            rows.append({'title':re.sub(r'\s+',' ',row['title']).strip()[:200],'url':target,'snippet':re.sub(r'\s+',' ',row['snippet']).strip()[:500]})
            if len(rows)>=limit:break
        if not rows and not any(marker in html.lower() for marker in ('class="no-results','class="result--no-result',"class='no-results","class='result--no-result")):raise SearchError('DuckDuckGoの検索結果を解析できませんでした。','parse_error',status)
        return {'provider':'DuckDuckGo','backend':'duckduckgo-html-only','query':query,'results':rows,'fetched_at':datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),'limit':limit,'source_url':final}
    def test_search(self,query,checkpoint=lambda:None):
        if not isinstance(query,str) or not query.strip() or len(query)>200:raise ValueError('検索語は1〜200文字で指定してください。')
        started=time.monotonic()
        try:
            data=self.search(query.strip(),5,checkpoint)
            return dict(ok=True,reason='results' if data['results'] else 'empty',http_status=200,
                        duration_ms=round((time.monotonic()-started)*1000),**data)
        except RemoteError as error:
            return dict(ok=False,reason=getattr(error,'reason','transport_error'),http_status=getattr(error,'http_status',None),
                        message=str(error),duration_ms=round((time.monotonic()-started)*1000),provider='DuckDuckGo',backend='duckduckgo-html-only',query=query.strip(),results=[])
    def read(self,room,url='',document_id='',offset=0,limit=4000,checkpoint=lambda:None):
        self.store.room_path(room);checkpoint()
        if document_id:
            if url:raise ValueError('続きの取得ではurlを省略してください。')
            with self.store.connect() as db:row=db.execute('SELECT * FROM web_documents WHERE room=? AND id=?',(room,document_id)).fetchone()
            if row is None or self.clock()-row['fetched']>3600:raise EditConflict('取得結果がないか期限切れです。URLから取得し直してください。')
            doc=dict(row)
        else:
            if offset:raise ValueError('続きはdocument_idで取得してください。')
            raw,mime,final=self.fetch(url,checkpoint=checkpoint)
            kind=mime.split(';')[0].strip().lower()
            if kind not in ('text/html','application/xhtml+xml','text/plain','application/pdf'):raise RemoteError('HTML・プレーンテキスト・PDFのURLに対応しています。')
            text=decoded(raw,mime);title=final
            if kind=='application/pdf':
                from tools.pdf_text import extract_pdf
                text=extract_pdf(raw,checkpoint)['text']
            elif kind!='text/plain':
                parser=Text();parser.feed(text);text=parser.text();title=''.join(parser.title).strip()[:200] or final
            if not text.strip():raise RemoteError('本文を取得できませんでした。JavaScript実行や認証が必要なページには対応していません。')
            doc=dict(id=uuid.uuid4().hex,room=room,url=final,title=title,body=text[:200000],fetched=self.clock(),revision=hashlib.sha256(text.encode()).hexdigest())
            checkpoint()
            with self.store.connect() as db:
                db.execute('DELETE FROM web_documents WHERE fetched<?',(self.clock()-3600,))
                db.execute('INSERT INTO web_documents VALUES(:id,:room,:url,:title,:body,:fetched,:revision)',doc)
                db.execute('DELETE FROM web_documents WHERE room=? AND id NOT IN (SELECT id FROM web_documents WHERE room=? ORDER BY fetched DESC,rowid DESC LIMIT 30)',(room,room))
        if offset>len(doc['body']):raise ValueError('offsetが本文の長さを超えています。')
        end=min(len(doc['body']),offset+limit)
        return dict(document_id=doc['id'],url=doc['url'],title=doc['title'],text=doc['body'][offset:end],offset=offset,total_chars=len(doc['body']),revision=doc['revision'],stored_char_limit=200000,source_may_be_truncated=len(doc['body'])>=200000,fetched_at=datetime.fromtimestamp(doc['fetched'],timezone.utc).isoformat()),end<len(doc['body'])
