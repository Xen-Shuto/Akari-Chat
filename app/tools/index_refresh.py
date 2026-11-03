"""Opt-in post-save index maintenance; never starts an embedding server."""
import json
import logging
import time
import uuid
import struct
from litechat.file_editor import EditConflict
from litechat.semantic import digest,unit,cached_cosine


class IndexRefresh:
    def __init__(self,app):
        self.app,self.store=app,app.store;self.warning='';self.enqueue_warnings={};self.worker=None;self.active=None;self.next_scan=0
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS index_refresh_settings(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS index_refresh_setting_receipts(room TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,signature))')
            db.execute('CREATE TABLE IF NOT EXISTS index_refresh_jobs(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS index_refresh_runs(id TEXT PRIMARY KEY,room TEXT,state TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
            for row in db.execute("SELECT id,room,payload FROM index_refresh_runs WHERE state='running'").fetchall():
                p=json.loads(row[2]);p['error']='再起動前の索引更新は未確認です。自動再実行しません。'
                db.execute("UPDATE index_refresh_runs SET state='unknown',payload=? WHERE id=?",(json.dumps(p),row[0]))
                old=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(row[1],)).fetchone()
                if old:
                    job=json.loads(old[0]);job.update(state='unknown',revision=uuid.uuid4().hex,error=p['error']);db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),row[1]))

    def settings(self,room,db=None):
        room=self.store.room_path(room).name
        if db is None:
            with self.store.connect() as conn:return self.settings(room,conn)
        row=db.execute('SELECT payload FROM index_refresh_settings WHERE room=?',(room,)).fetchone()
        return json.loads(row[0]) if row else dict(enabled=False,revision='unconfigured')

    def listing(self,room):
        room=self.store.room_path(room).name
        with self.store.connect() as db:
            row=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(room,)).fetchone()
            return dict(settings=self.settings(room,db),job=json.loads(row[0]) if row else None,history=[{**json.loads(r[2]),'id':r[0],'state':r[1]} for r in db.execute('SELECT id,state,payload FROM index_refresh_runs WHERE room=? ORDER BY rowid DESC LIMIT 10',(room,))],warning=self.warning or self.enqueue_warnings.get(room,''))

    def save(self,room,enabled,revision):
        room=self.store.room_path(room).name
        if type(enabled) is not bool:raise ValueError('自動索引更新はtrue/falseを指定してください。')
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成の完了後に設定してください。')
            db.execute('BEGIN IMMEDIATE')
            signature=digest(json.dumps([enabled,revision],sort_keys=True))
            receipt=db.execute('SELECT result FROM index_refresh_setting_receipts WHERE room=? AND signature=?',(room,signature)).fetchone()
            if receipt:return json.loads(receipt[0])
            old=self.settings(room,db)
            if old['revision']!=revision:raise EditConflict('自動索引設定が変更されています。再読込してください。')
            result=dict(enabled=enabled,revision=uuid.uuid4().hex) if old['enabled']!=enabled else old
            db.execute('INSERT OR REPLACE INTO index_refresh_settings VALUES(?,?)',(room,json.dumps(result)))
            if not enabled:
                row=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(room,)).fetchone()
                if row:
                    job=json.loads(row[0]);job.update(state='cancelled',revision=uuid.uuid4().hex);db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),room))
                if self.active and self.active['room']==room and self.worker is self.app.semantic.worker:self.app.semantic.cancel.set()
            db.execute('INSERT INTO index_refresh_setting_receipts VALUES(?,?,?)',(room,signature,json.dumps(result)))
        return result

    def saved(self,room,result):
        """Called after a successful UI write, never from inside its file/DB transaction."""
        if not result.get('changed',True):return result
        try:
            room=self.store.room_path(room).name
            with self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');config=self.settings(room,db)
                if not config['enabled']:return result
                row=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(room,)).fetchone();old=json.loads(row[0]) if row else None
                if old and old['state'] in ('unknown','failed','incomplete'):
                    return dict(result,index_warning='本文は保存しました。前回の自動索引更新を確認し、明示的に再試行してください。')
                job=dict(room=room,state='queued',revision=uuid.uuid4().hex,due=time.time()+5,error='')
                if self.active and self.active['room']==room:job['state']='running'
                db.execute('INSERT OR REPLACE INTO index_refresh_jobs VALUES(?,?)',(room,json.dumps(job)))
            self.enqueue_warnings.pop(room,None)
            return dict(result,index_queued=True)
        except Exception:
            logging.getLogger(__name__).exception('Saved source retained; index refresh enqueue failed')
            self.enqueue_warnings[room]='本文は保存しましたが、索引の自動更新を予約できませんでした。手動で索引更新してください。'
            return dict(result,index_warning=self.enqueue_warnings[room])

    def retry(self,room,revision):
        room=self.store.room_path(room).name
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.job or self.app.closing:raise ValueError('会話・生成が終わってから再試行してください。')
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(room,)).fetchone()
            if not row or json.loads(row[0])['revision']!=revision:raise EditConflict('更新記録が変更されています。再読込してください。')
            if not self.settings(room,db)['enabled']:raise ValueError('先に自動索引更新を有効にしてください。')
            job=json.loads(row[0])
            if job['state'] in ('queued','running'):raise ValueError('既に待機または実行中です。')
            job.update(state='queued',revision=uuid.uuid4().hex,due=time.time(),error='');db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),room))
        return self.listing(room)

    def coverage(self,room,identity):
        sem=self.app.semantic;documents,warnings=sem.corpus(room)
        if sem.additional_corpus:
            extra,notes=sem.additional_corpus(room);documents.extend(extra);warnings.extend(notes)
        keys=set(digest(doc['text']) for doc in documents)
        missing=0;probe=None;invalid=False
        with sem.connect() as db:
            for k in keys:
                row=db.execute('SELECT vector FROM vectors WHERE model=? AND body=?',(identity,k)).fetchone()
                if row is None:missing+=1;continue
                raw=row[0]
                try:
                    if not isinstance(raw,bytes) or not raw or len(raw)%4 or len(raw)>4096*4:raise ValueError('invalid cache')
                    if probe is None:probe=unit(list(struct.unpack('<%sf'%(len(raw)//4),raw)))
                    cached_cosine(raw,probe)
                except ValueError:missing+=1;invalid=True
        if invalid:warnings.append('検索キャッシュに不正な値/形式/次元があります。自動で原本やキャッシュを削除しません。')
        return dict(documents=len(keys),missing=missing,warnings=warnings[:10])

    def tick(self):
        if self.warning:return False
        app=self.app;sem=app.semantic
        # Semantic search acquires sem.lock before reading room data. Keep the
        # same order so a concurrent search cannot deadlock with this worker.
        with app.lock,sem.lock,self.store.lock:
            if app.closing or app.job or app.conversation_waiters:return False
            if self.active:
                if self.worker and self.worker.is_alive():return False
                run=self.active;progress=sem.status();status='failed';error='索引作成が完了していません。';coverage=None
                config=self.settings(run['room'])
                if not config['enabled']:status='cancelled';error='自動更新設定をOFFにしました。'
                elif sem.worker is not self.worker:status='unknown';error='別の索引処理へ切り替わったため結果を断定しません。'
                elif progress['state']=='ready' and sem.identity==run['model']:
                    coverage=self.coverage(run['room'],run['model']);status='complete' if not coverage['missing'] and not coverage['warnings'] else 'incomplete';error='' if status=='complete' else '現在の資料に未索引または警告があります。'
                else:error=progress.get('error') or error
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE');payload=dict(run,error=error,coverage=coverage)
                    db.execute('UPDATE index_refresh_runs SET state=?,payload=? WHERE id=?',(status,json.dumps(payload,ensure_ascii=False),run['id']))
                    row=db.execute('SELECT payload FROM index_refresh_jobs WHERE room=?',(run['room'],)).fetchone();job=json.loads(row[0])
                    pending=job['revision']!=run['source_revision'] and config['enabled'] and status in ('complete','incomplete')
                    job.update(state='queued' if pending else status,revision=uuid.uuid4().hex,error='' if pending else error,due=time.time()+5)
                    db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),run['room']))
                self.active=None;self.worker=None;return True
            if time.monotonic()<self.next_scan:return False
            self.next_scan=time.monotonic()+2
            if sem.worker and sem.worker.is_alive() or sem.engine.status()['state']!='ready' or not sem.identity:return False
            config=sem.settings()
            if any((sem.loaded_settings or {}).get(k)!=config.get(k) for k in ('model','profile')):return False
            with self.store.connect() as db:
                row=db.execute("SELECT room,payload FROM index_refresh_jobs WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) AND json_extract(payload,'$.state')='queued' AND json_extract(payload,'$.due')<=? ORDER BY rowid LIMIT 1",(time.time(),)).fetchone()
                if row is None:return False
                room=row[0];job=json.loads(row[1])
                if not self.settings(room,db)['enabled']:return False
                run=dict(id=uuid.uuid4().hex,room=room,source_revision=job['revision'],model=sem.identity,error='')
                job['state']='running'
                db.execute("INSERT INTO index_refresh_runs(id,room,state,payload) VALUES(?,?,'running',?)",(run['id'],room,json.dumps(run)))
                db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),room))
            self.active=run
            try:
                sem.build(room,'',existing_only=True,idle_check=lambda:not app.closing and not app.job and not app.conversation_waiters)
                self.worker=sem.worker
            except Exception as exc:
                with self.store.connect() as db:
                    run['error']=str(exc)[:1000];db.execute("UPDATE index_refresh_runs SET state='failed',payload=? WHERE id=?",(json.dumps(run),run['id']))
                    job.update(state='failed',error=run['error'],revision=uuid.uuid4().hex);db.execute('UPDATE index_refresh_jobs SET payload=? WHERE room=?',(json.dumps(job),room))
                self.active=None;self.worker=None
            return True
