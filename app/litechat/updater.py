"""Owner-triggered public GitHub updates; no Git client or account credentials."""
import json
import re
import stat
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from .file_editor import safe_path
from .update_files import digest, managed, update_root, write_json, load_plan

REPOSITORY='Xen-Shuto/Akari-Chat'
BRANCH='main'
HOSTS={'api.github.com','raw.githubusercontent.com','codeload.github.com','github.com'}
MAX_ARCHIVE=1024*1024*1024


def version(raw):
    value=raw.decode('utf-8-sig').strip()
    if not value or len(value)>80 or any(ord(c)<32 for c in value):raise ValueError('バージョンテキストが不正です。')
    return value


def allowed_url(url):
    from urllib.parse import urlsplit
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError('更新元のURLを確認できません。')


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        allowed_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def open_public(url):
    allowed_url(url)
    return build_opener(Redirects()).open(Request(url,headers={'User-Agent':'Akari-Chat-Updater','Accept':'application/vnd.github+json'}),timeout=30)


class Updater:
    def __init__(self,install,transport=open_public):
        self.install=Path(install);self.transport=transport
        self.lock=threading.RLock();self.worker=None
        self.value=dict(state='idle',revision=uuid.uuid4().hex,current=self.current(),remote='',commit='',error='',bytes=0)

    def current(self):return version((self.install/'app/VERSION.txt').read_bytes()) if (self.install/'app/VERSION.txt').is_file() else ''

    def status(self):
        with self.lock:
            result=dict(self.value,current=self.current(),repository=REPOSITORY,branch=BRANCH)
            last=update_root(self.install)/'last.json'
            result['last']=json.loads(last.read_text('utf8')) if last.is_file() else None
            return result

    def set(self,**values):
        with self.lock:self.value.update(values,revision=uuid.uuid4().hex)

    def start(self,action,revision=None):
        with self.lock:
            if self.worker and self.worker.is_alive():raise ValueError('更新の確認・取得中です。')
            if (update_root(self.install)/'pending.json').exists():raise ValueError('適用待ち、または結果未確認の更新があります。再起動後の結果を確認してください。')
            if action=='prepare':
                if self.value['state']!='available' or revision!=self.value['revision'] or self.current()!=self.value['current']:
                    raise ValueError('更新候補が変更されています。もう一度確認してください。')
            elif action!='check':raise ValueError('更新操作が不正です。')
            self.set(state='checking' if action=='check' else 'downloading',error='',current=self.current(),bytes=0)
            self.worker=threading.Thread(target=self.run,args=(action,),daemon=True,name='akari-update')
            self.worker.start()
            return self.status()

    def small(self,url):
        with self.transport(url) as response:raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('更新情報が大きすぎます。')
        return raw

    def run(self,action):
        try:
            if action=='check':
                info=json.loads(self.small(f'https://api.github.com/repos/{REPOSITORY}/commits/{BRANCH}'))
                commit=info['sha']
                if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('公開コミットを確認できません。')
                remote=version(self.small(f'https://raw.githubusercontent.com/{REPOSITORY}/{commit}/app/VERSION.txt'))
                self.set(state='available' if remote!=self.current() else 'current',remote=remote,commit=commit)
            else:self.prepare()
        except Exception as error:
            message=('GitHubへのアクセスに失敗しました（HTTP '+str(error.code)+'）。' if isinstance(error,HTTPError)
                     else '更新元へ接続できませんでした。時間をおいて確認してください。' if isinstance(error,(URLError,TimeoutError))
                     else str(error)[:400])
            self.set(state='failed',error=message)

    def prepare(self):
        commit=self.value['commit'];remote=self.value['remote']
        key=uuid.uuid4().hex;root=update_root(self.install)
        folder=safe_path(root,key+'/.anchor',create=True).parent
        archive=folder/'download.zip'
        start=time.monotonic();size=0
        with self.transport(f'https://api.github.com/repos/{REPOSITORY}/zipball/{commit}') as response,archive.open('xb') as output:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_ARCHIVE or time.monotonic()-start>600:raise ValueError('更新の取得上限を超えました。')
                output.write(chunk)
                with self.lock:self.value['bytes']=size
        self.set(state='staging')
        files=[];seen=set();total=0;prefix=None
        with zipfile.ZipFile(archive) as zip:
            if len(zip.infolist())>100000:raise ValueError('更新ファイルが多すぎます。')
            for entry in zip.infolist():
                name=entry.filename
                parts=PurePosixPath(name).parts
                if len(parts)<1 or name.startswith('/') or '\\' in name or any(p=='..' or ':' in p for p in parts):
                    raise ValueError('取得ZIPのパスが不正です。')
                if prefix is None:prefix=parts[0]
                if prefix!=parts[0]:raise ValueError('取得ZIPの構造が不正です。')
                if entry.is_dir():continue
                rel='/'.join(parts[1:])
                if rel.lower() in seen:raise ValueError('大文字小文字だけが異なる更新ファイルがあります。')
                seen.add(rel.lower())
                if not rel or not managed(rel):continue
                if stat.S_ISLNK(entry.external_attr>>16):raise ValueError('リンクの更新ファイルがあります。')
                if entry.file_size>64*1024*1024:raise ValueError('更新ファイルが大きすぎます。')
                total+=entry.file_size
                if total>512*1024*1024 or len(files)>20000:raise ValueError('更新の展開上限を超えました。')
                target=safe_path(self.install,rel)
                if target.exists() and not target.is_file():raise ValueError('更新先にファイル以外の項目があります。')
                stage=safe_path(folder,'stage/'+rel,create=True)
                with zip.open(entry) as src,stage.open('xb') as dst:
                    import shutil
                    shutil.copyfileobj(src,dst)
                files.append(dict(path=rel,hash=digest(stage),before=digest(target)))
        required={'app/VERSION.txt','app/app.py','app/launch.py','app/litechat/server.py','app/web/index.html'}
        if not required<={e['path'] for e in files}:raise ValueError('更新に必要なファイルが不足しています。')
        if version((folder/'stage/app/VERSION.txt').read_bytes())!=remote:raise ValueError('取得したバージョンが一致しません。')
        if self.current()!=self.value['current']:raise ValueError('準備中に現在のバージョンが変わりました。再確認してください。')
        plan=dict(version=remote,commit=commit,files=files)
        write_json(folder/'plan.json',plan)
        archive.unlink()
        self.set(state='prepared',id=key,plan_hash=digest(folder/'plan.json'),count=len(files))

    def arm(self,revision):
        with self.lock:
            if self.value['state']!='prepared' or self.value['revision']!=revision:raise ValueError('更新内容を再確認してください。')
            root=update_root(self.install)
            pointer=dict(id=self.value['id'],hash=self.value['plan_hash'])
            load_plan(root,pointer)
            if (root/'pending.json').exists():raise ValueError('適用待ちの更新があります。')
            write_json(root/'pending.json',pointer)
            self.set(state='applying')
