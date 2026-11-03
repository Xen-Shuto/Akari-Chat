"""Persistent image jobs and room-scoped artifacts. Never replay an uncertain request."""
from contextlib import nullcontext
import base64
import hashlib
import json
from itertools import islice
import os
import re
import threading
import time
import uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlencode
from . import settings_io,image_backends
from .file_editor import safe_path,FileEditor,EditConflict
from .public_http import RemoteError,RemoteTimeout


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True)
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()

class ImageJobs:
    def __init__(self,store,data_dir):
        self.store=store;self.linker=None;self.path=Path(data_dir)/'image_settings.json';self.stop=threading.Event();self.worker=None;self.error='';self.backend=image_backends.generate;self.diagnostics=None
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS image_jobs(id TEXT PRIMARY KEY,room TEXT,request_id TEXT,state TEXT,payload TEXT,UNIQUE(room,request_id))')
            db.execute('CREATE INDEX IF NOT EXISTS image_jobs_state ON image_jobs(state)')
            db.execute('CREATE TABLE IF NOT EXISTS image_artifacts(id TEXT PRIMARY KEY,room TEXT,path TEXT,payload TEXT)')
            for row in db.execute("SELECT id,payload FROM image_jobs WHERE state IN ('running','cancelling')").fetchall():
                item=json.loads(row['payload']);item.update(state='unknown',error='前回の実行結果を確認できません。自動再実行しません。',revision=uuid.uuid4().hex)
                db.execute('UPDATE image_jobs SET state=?,payload=? WHERE id=?',('unknown',encoded(item),row['id']))
    def settings(self,public=False):
        def validate(value):
            provider=value.get('provider','disabled')
            if provider not in image_backends.PROVIDERS:raise ValueError('プロバイダが不正です。')
            profiles=value.get('profiles',{})
            if not isinstance(profiles,dict) or set(profiles)-set(image_backends.DEFAULTS):raise ValueError('設定の形式が不正です。')
            return dict(provider=provider,profiles={k:image_backends.checked_profile(k,profiles.get(k,{})) for k in image_backends.DEFAULTS})
        data,warning=settings_io.load(self.path,dict(provider='disabled',profiles={}),validate)
        data=validate({'provider':'disabled','profiles':{}} if warning else data)
        revision=digest(data)
        if public:
            data=json.loads(encoded(data))
            for profile in data['profiles'].values():
                if 'api_key' in profile:profile['key_set']=bool(profile.pop('api_key'))
        return dict(data,revision=revision,warning='画像生成設定を読めません。元ファイルを保持して無効化しています。' if warning else '')
    def save_settings(self,provider,profiles,revision):
        with self.store.lock:
            old=self.settings()
            if old['revision']!=revision:raise EditConflict('画像生成設定が変わりました。読み直してください。')
            if provider not in image_backends.PROVIDERS or not isinstance(profiles,dict) or set(profiles)-set(image_backends.DEFAULTS):raise ValueError('設定の形式が不正です。')
            merged=dict(old['profiles'])
            for name,changes in profiles.items():
                if not isinstance(changes,dict):raise ValueError('設定はオブジェクトが必要です。')
                updates=dict(changes)
                if 'base_url' in updates and not isinstance(updates['base_url'],str):raise ValueError('接続先URLは文字列で指定してください。')
                if 'base_url' in updates and updates['base_url'].strip().rstrip('/')!=merged[name].get('base_url','').rstrip('/') and not updates.get('api_key'):
                    updates['api_key']=''
                merged[name]=image_backends.checked_profile(name,{**merged[name],**updates})
            settings_io.save(self.path,dict(provider=provider,profiles=merged))
            return self.settings(public=True)
    def configured(self):
        settings=self.settings();provider=settings['provider']
        return provider!='disabled' and image_backends.ready(provider,settings['profiles'][provider])
    def references(self,room):
        root=self.store.room_path(room);paths=['profile.png'];count=0
        for folder in ('images','closet','backgrounds','spaces/images'):
            directory=safe_path(root,folder)
            if not directory.is_dir():continue
            pending=[directory]
            while pending and len(paths)<200:
                current=pending.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        count+=1
                        if count>5000:raise ValueError('参照画像の走査対象が5000件を超えました。')
                        path=Path(entry.path)
                        try:target=safe_path(root,path.relative_to(root))
                        except ValueError:continue
                        if target.is_dir():pending.append(target)
                        elif target.is_file() and target.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):paths.append(target.relative_to(root).as_posix())
                        if len(paths)>=200:break
        result=[]
        for relative in dict.fromkeys(paths):
            try:
                target=safe_path(root,relative)
                if target.is_file() and target.stat().st_size<=5*1048576:result.append(dict(id='ref_'+hashlib.sha256(relative.encode()).hexdigest()[:24],name=relative))
            except ValueError:continue
        return result[:200]
    def legacy_images(self,room):
        root=self.store.room_path(room);directory=safe_path(root,'generated_images');result=[];count=0
        if not directory.is_dir():return result
        pending=[directory]
        while pending and len(result)<200:
            current=pending.pop()
            with os.scandir(current) as entries:paths=[Path(e.path) for e in islice(entries,5001-count)]
            if len(paths)+count>5000:raise ValueError('画像一覧の走査対象が5000件を超えました。')
            paths.sort(reverse=True)
            for path in paths:
                count+=1
                if count>5000:raise ValueError('画像一覧の走査対象が5000件を超えました。')
                relative=path.relative_to(root).as_posix()
                try:target=safe_path(root,relative)
                except ValueError:continue
                if target.is_dir():pending.append(target)
                elif target.is_file() and target.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                    result.append(dict(id='legacy_'+hashlib.sha256(relative.encode()).hexdigest()[:32],path=relative,name=target.name,provider='imported',created_at=datetime.fromtimestamp(target.stat().st_mtime,timezone.utc).isoformat()))
                if len(result)>=200:break
        return result
    def reference(self,room,key):
        if not isinstance(key,str):raise ValueError('参照画像IDが不正です。')
        if not key.startswith('ref_'):
            doc,raw=self.artifact(room,key)
            if len(raw)>5*1048576:raise ValueError('参照画像は5MiBまでです。')
            return dict(raw=raw,mime=doc['mime'],sha256=hashlib.sha256(raw).hexdigest(),id=key)
        row=next((i for i in self.references(room) if i['id']==key),None)
        if not row:raise ValueError('参照画像がありません。ルームの一覧から選んでください。')
        path=safe_path(self.store.room_path(room),row['name'])
        with path.open('rb') as stream:raw=stream.read(5*1048576+1)
        if len(raw)>5*1048576:raise ValueError('参照画像は5MiBまでです。')
        raw,mime=image_backends.image_bytes(raw)
        return dict(raw=raw,mime=mime,sha256=hashlib.sha256(raw).hexdigest(),id=key)
    def prepare(self,room,prompt,aspect,reference_ids):
        self.store.room_path(room);settings=self.settings();provider=settings['provider']
        if provider=='disabled' or not image_backends.ready(provider,settings['profiles'][provider]):raise ValueError('画像生成の接続先・モデル・キーを設定してください。')
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000 or aspect not in ('square','portrait','landscape'):raise ValueError('指示または縦横比が不正です。')
        if not isinstance(reference_ids,list) or len(reference_ids)>4 or any(not isinstance(k,str) for k in reference_ids) or len(set(reference_ids))!=len(reference_ids):raise ValueError('参照画像は重複なしの4枚までです。')
        if reference_ids and not image_backends.supports_reference(provider,settings['profiles'][provider].get('model',''),settings['profiles'][provider].get('base_url','')):raise ValueError('選択中の方式は参照画像を直接受け取れません。')
        refs=[self.reference(room,k) for k in reference_ids]
        if sum(len(i['raw']) for i in refs)>12*1048576:raise ValueError('参照画像の合計は12MiBまでです。')
        return dict(prompt=prompt.strip(),aspect=aspect,references=[{'id':i['id'],'sha256':i['sha256']} for i in refs],config_revision=settings['revision'],provider=provider,model=settings['profiles'][provider].get('model',''))
    def enqueue(self,room,prepared,request_id,checkpoint=lambda:None):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM image_jobs WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if row:
                item=json.loads(row[0])
                if item['signature']!=digest(prepared):raise EditConflict('同じ操作IDの内容が変わっています。')
                return item,False
            if self.settings()['revision']!=prepared['config_revision']:raise EditConflict('画像生成設定が変わりました。')
            link_label=''
            if prepared.get('link_plan') is not None:
                if self.linker is None:raise ValueError('生成後の関連付けはこの起動方法では利用できません。')
                link_label=self.linker.validate_plan(room,prepared['link_plan'],db)['target']['label']
            if db.execute("SELECT COUNT(*) FROM image_jobs WHERE state IN ('queued','running','cancelling')").fetchone()[0]>=20:raise ValueError('待機/実行中の画像生成は全体20件までです。')
            item=dict(prepared,link_label=link_label,id=uuid.uuid4().hex,room=room,state='queued',revision=uuid.uuid4().hex,signature=digest(prepared),created_at=now(),cancel_requested=False,artifact_id=None,error='',provider_attempts=0,duration_ms=None,started_at=None,timeout_seconds=self.settings()['profiles'][prepared['provider']]['timeout_seconds'],error_code='')
            checkpoint();db.execute('INSERT INTO image_jobs VALUES(?,?,?,?,?)',(item['id'],room,request_id,'queued',encoded(item)))
        return item,True
    def _job(self,db,room,key):
        row=db.execute('SELECT payload FROM image_jobs WHERE room=? AND id=?',(room,key)).fetchone()
        if not row:raise ValueError('画像生成ジョブがありません。')
        return json.loads(row[0])
    def _write(self,db,item):
        item['revision']=uuid.uuid4().hex
        db.execute('UPDATE image_jobs SET state=?,payload=? WHERE id=?',(item['state'],encoded(item),item['id']))
    def cancel(self,room,key,revision,checkpoint=lambda:None):
        self.store.room_path(room)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');item=self._job(db,room,key)
            if item['revision']!=revision:raise EditConflict('画像生成の状態が変わりました。')
            if item['state'] not in ('queued','running'):return item,False
            item['cancel_requested']=True;item['state']='cancelled' if item['state']=='queued' else 'cancelling'
            checkpoint();self._write(db,item)
            return item,True
    def listing(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:
            jobs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM image_jobs WHERE room=? ORDER BY (state IN ('queued','running','cancelling')) DESC,rowid DESC LIMIT 50",(room,))]
            images=[json.loads(r[0]) for r in db.execute('SELECT payload FROM image_artifacts WHERE room=? ORDER BY rowid DESC LIMIT 100',(room,))]
        return dict(jobs=[self.describe_job(item) for item in jobs],images=images,warning=self.error)
    @staticmethod
    def describe_job(item):
        # Also classify historical records without rewriting their receipts.
        code=item.get('error_code','')
        if not code and item.get('state')=='unknown' and any(s in item.get('error','') for s in ('TimeoutError','タイムアウト','待機時間を超え')):code='timeout'
        return dict(item,error_code=code,completed=item.get('state')=='succeeded')
    def conversation_status(self,room):
        if not room:return []
        self.store.room_path(room)
        cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.store.connect() as db:
            rows=db.execute("SELECT payload FROM image_jobs WHERE room=? AND json_extract(payload,'$.source_job_id') IS NOT NULL AND json_extract(payload,'$.created_at')>=? ORDER BY rowid DESC LIMIT 3",(room,cutoff)).fetchall()
        keys=('id','state','error_code','completed','created_at','duration_ms','timeout_seconds','artifact_id')
        result=[]
        for row in rows:
            item=self.describe_job(json.loads(row[0]));record={key:item.get(key) for key in keys};record['error']=item.get('error','')[:220];result.append(record)
        return result
    def artifact(self,room,key):
        self.store.room_path(room)
        with self.store.connect() as db:row=db.execute('SELECT payload FROM image_artifacts WHERE room=? AND id=?',(room,key)).fetchone()
        if not row and isinstance(key,str) and key.startswith('legacy_'):
            legacy=next((i for i in self.legacy_images(room) if i['id']==key),None)
            if not legacy:raise ValueError('既存画像がありません。')
            path=safe_path(self.store.room_path(room),legacy['path'])
            with path.open('rb') as stream:raw=stream.read(8*1048576+1)
            raw,mime=image_backends.image_bytes(raw);width,height=image_backends.dimensions(raw,mime)
            return dict(legacy,mime=mime,width=width,height=height,sha256=hashlib.sha256(raw).hexdigest(),prompt='',model='',reference_used=False,comment='既存ファイルの現在の内容です。'),raw
        if not row:raise ValueError('画像がありません。')
        doc=json.loads(row[0]);path=safe_path(self.store.room_path(room),doc['path'])
        with path.open('rb') as stream:raw=stream.read(8*1048576+1)
        if hashlib.sha256(raw).hexdigest()!=doc['sha256']:raise EditConflict('画像ファイルが変更されています。')
        return doc,raw
    def completed_for_call(self,room,job,call):
        """Only app-recorded outcomes, never a filename supplied by model text."""
        with self.store.connect() as db:
            rows=db.execute("SELECT a.payload FROM image_artifacts a JOIN image_jobs j ON a.id=json_extract(j.payload,'$.artifact_id') AND a.room=j.room WHERE j.room=? AND j.state='succeeded' AND json_extract(j.payload,'$.source_job_id')=? AND json_extract(j.payload,'$.source_call_id')=?",(room,job,call)).fetchall()
        result=[]
        for row in rows:
            doc=json.loads(row[0]);result.append(dict(id=doc['id'],name=Path(doc['path']).name,mime=doc['mime'],width=doc['width'],height=doc['height'],generated=True,url='/api/generated-image?'+urlencode(dict(room=room,id=doc['id']))))
        return result
    def process_one(self,claim=lambda:True,gate=None,*,room=None,key=None):
        with (gate or nullcontext()),self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT payload FROM image_jobs WHERE state='queued' AND room NOT IN (SELECT room FROM room_states WHERE archived=1) AND (? IS NULL OR room=?) AND (? IS NULL OR id=?) AND (? IS NOT NULL OR COALESCE(json_extract(payload,'$.execution'),'')!='inline') ORDER BY rowid LIMIT 1",(room,room,key,key,key)).fetchone()
            if not row or not claim():return False
            item=json.loads(row[0]);settings=self.settings()
            if settings['revision']!=item['config_revision']:
                item.update(state='failed',error='設定が変更されたため実行しませんでした。');self._write(db,item);return True
            try:
                refs=[self.reference(item['room'],r['id']) for r in item['references']]
                if any(a['sha256']!=b['sha256'] for a,b in zip(refs,item['references'])):raise ValueError('参照画像が変更されています。')
            except (ValueError,OSError):
                item.update(state='failed',error='参照画像を確認できないため実行しませんでした。');self._write(db,item);return True
            item.update(state='running',provider_attempts=1,started_at=now());self._write(db,item)
        started_clock=time.monotonic()
        failure_phase='生成先の通信・応答'
        try:
            raw,mime,comment=self.backend(item['provider'],settings['profiles'][item['provider']],item['prompt'],item['aspect'],refs)
            raw,mime=image_backends.image_bytes(raw);width,height=image_backends.dimensions(raw,mime)
            artifact_id=uuid.uuid4().hex;month=datetime.now().strftime('%Y-%m')
            provider={'local':'Local','gemini':'Gemini','openai':'DALL-E-3' if 'dall-e-3' in item['model'].lower() else 'OpenAI','pollinations':'PollinationsAI','huggingface':'HuggingFace'}[item['provider']]
            component=lambda value:re.sub(r'[^\w.-]','_',str(value)).strip('.') or 'image'
            filename=component(provider)+'_'+component(item['room'].lower())+'_'+str(int(time.time()))+'_'+artifact_id+image_backends.EXTENSIONS[mime]
            path='generated_images/'+month+'/'+filename
            with self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');current=self._job(db,item['room'],item['id']);current['artifact_path']=path
                # Keep the intended filename in the durable running record before saving below.
                self._write(db,current)
            failure_phase='成果物の保存'
            target=safe_path(self.store.room_path(item['room']),path,create=True)
            with target.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            doc=dict(id=artifact_id,room=item['room'],path=path,mime=mime,width=width,height=height,sha256=hashlib.sha256(raw).hexdigest(),prompt=item['prompt'],provider=item['provider'],model=item['model'],created_at=now(),job_id=item['id'],reference_used=bool(refs),comment=comment[:2000])
            with self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');current=self._job(db,item['room'],item['id'])
                db.execute('INSERT INTO image_artifacts VALUES(?,?,?,?)',(artifact_id,item['room'],path,encoded(doc)))
                if current.get('link_plan') is not None:
                    db.execute('SAVEPOINT generated_link')
                    try:
                        if self.linker is None:raise ValueError('linker unavailable')
                        current['link_result']=self.linker.link_generated(db,current,doc)
                        db.execute('RELEASE generated_link')
                    except Exception:
                        db.execute('ROLLBACK TO generated_link');db.execute('RELEASE generated_link')
                        current['link_result']=dict(state='review',message='関連付けの保存を確認できません。生成画像を保持しました。手動で確認してください。')
                current.update(state='succeeded',artifact_id=artifact_id,completed_at=now(),error='',duration_ms=round((time.monotonic()-started_clock)*1000,2));self._write(db,current)
        except Exception as error:
            # Upstream generation or charging might have happened. Never log keys or response bodies.
            with self.store.lock,self.store.connect() as db:
                timed_out=failure_phase=='生成先の通信・応答' and isinstance(error,(RemoteTimeout,TimeoutError))
                current=self._job(db,item['room'],item['id']);current.update(state='unknown',completed_at=now(),error_code='timeout' if timed_out else 'unconfirmed',duration_ms=round((time.monotonic()-started_clock)*1000,2),error=('生成先の応答待ちがタイムアウトしました。処理が続いている可能性があり、完成/失敗は未確認です。自動再実行しません。' if timed_out else failure_phase+'の結果を確認できません。自動再実行しません。 '+(str(error) if isinstance(error,RemoteError) else type(error).__name__)));self._write(db,current)
        if self.diagnostics is not None:
            self.diagnostics.record('WARNING' if current['state']=='unknown' else 'INFO','image_generation','画像生成結果: '+current['state']+(' / タイムアウト' if current.get('error_code')=='timeout' else ''),current['room'],current.get('source_job_id') or current['id'])
            self.diagnostics.trace('image_job.finished',current['room'],current.get('source_job_id') or current['id'],image_job_id=current['id'],state=current['state'],error_code=current.get('error_code',''),duration_ms=current.get('duration_ms'),timeout_seconds=current.get('timeout_seconds'),provider_attempts=current.get('provider_attempts'))
        return True
    def start(self,app):
        self.diagnostics=getattr(app,'diagnostics',None)
        def work():
            while not self.stop.wait(.5):
                try:
                    # Claim and validate while holding app.lock; perform network calls outside it.
                    self.process_one(lambda:not self.stop.is_set() and not app.closing and app.job is None,app.lock)
                except Exception:
                    # DB failures keep the durable state for inspection; no automatic retry of running jobs.
                    self.error='画像生成状態の記録に失敗しました。保存状態を確認し、自動再実行しないでください。'
        self.worker=threading.Thread(target=work,name='akari-image-jobs',daemon=True);self.worker.start()
    def close(self):
        self.stop.set()
        if self.worker:self.worker.join(timeout=2)
        return not (self.worker and self.worker.is_alive())
