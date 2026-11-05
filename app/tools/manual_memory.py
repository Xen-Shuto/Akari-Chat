"""Owner-requested, durable memory maintenance outside the sleep window."""
import hashlib
import json
import uuid
from datetime import datetime

from litechat import settings_io, text_connection
from litechat.episodes import JST
from litechat.file_editor import EditConflict, safe_path, decode
from .background_guard import uncertain
from .sleep_diary import NoDiarySource
from .sleep_review import SleepBusy

KINDS = ('diary', 'episode', 'dream', 'identity', 'notepad')
ACTIVE = ('preparing', 'queued', 'running', 'indexing')


class ManualMemory:
    def __init__(self, scheduler):
        self.scheduler, self.app, self.store = scheduler, scheduler.app, scheduler.store
        self.active = None
        self.index_worker = None
        with self.store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS manual_memory_runs(
                id TEXT PRIMARY KEY,room TEXT,request_id TEXT,mode TEXT,state TEXT,
                revision TEXT,payload TEXT,UNIQUE(room,request_id))""")
            db.execute("UPDATE manual_memory_runs SET state='unknown',revision=? WHERE state IN ('preparing','running')",
                       (uuid.uuid4().hex,))
            self.pending=bool(db.execute("SELECT 1 FROM manual_memory_runs WHERE state IN ('queued','indexing') LIMIT 1").fetchone())

    def records(self, room):
        self.store.room_path(room)
        with self.store.connect() as db:
            return [dict(row, payload=json.loads(row['payload'])) for row in db.execute(
                'SELECT * FROM manual_memory_runs WHERE room=? ORDER BY rowid DESC LIMIT 20', (room,))]

    def listing(self,room):
        return [dict(run,payload={key:value for key,value in run['payload'].items()
                                 if key in ('cursor','maximum','batch','step','saved','error','archive','kinds','date','prepared','operation')})
                for run in self.records(room)]

    def update(self, run, state, **fields):
        payload={**run['payload'],**fields}
        revision=uuid.uuid4().hex
        with self.app.lock,self.store.lock,self.store.connect() as db:
            changed=db.execute('UPDATE manual_memory_runs SET state=?,revision=?,payload=? WHERE id=? AND revision=?',
                       (state, revision, json.dumps(payload, ensure_ascii=False), run['id'],run['revision']))
            if changed.rowcount!=1:raise EditConflict('手動整理の状態が変更されています。履歴を確認してください。')
            self.pending=bool(db.execute("SELECT 1 FROM manual_memory_runs WHERE state IN ('queued','indexing') LIMIT 1").fetchone())
        run['payload'].update(fields)
        run.update(state=state,revision=revision)

    def idle(self, room, *, ignore_manual=False):
        if self.app.closing or self.app.job or self.app.conversation_waiters:
            raise SleepBusy('会話・生成の完了を待ってください。')
        with self.store.connect() as db:
            if uncertain(db, room,ignore_manual=ignore_manual):
                raise ValueError('保管状態または結果未確認の処理を確認してください。')
        worker = self.app.semantic.worker
        if worker and worker.is_alive():
            raise SleepBusy('記憶索引の作成完了を待ってください。')

    def enable_rag(self, room, key):
        app = self.app
        document = app.editor.read_snapshot(room, 'manual_memory', 'ルーム設定', 'room_config.json')
        config = json.loads(document['text'])
        overrides = config.setdefault('override_settings', {})
        overrides.update(enable_auto_retrieval=True, include_knowledge_in_auto_retrieval=True)
        app.editor.save_snapshot(room, 'manual_memory', json.dumps(config, ensure_ascii=False, indent=2),
                                 document['revision'], lambda: app.editor.read_snapshot(
                                     room, 'manual_memory', 'ルーム設定', 'room_config.json'))
        policy = app.retrieval_policy.settings(room)
        app.retrieval_policy.save(room, {k: (True if k in ('rag_enabled', 'semantic_enabled', 'auto_apply') else v)
                                        for k, v in policy.items() if k != 'revision'},
                                  policy['revision'], key)
        app.index_refresh.save(room, True, app.index_refresh.settings(room)['revision'])
        # These flags do not change the running model or its embedding identity.
        with app.semantic.lock:
            settings = app.semantic.settings()
            settings.update(auto_start=True, auto_semantic=True)
            settings_io.save(app.semantic.config_file, settings)

    def rebuild_plan(self, room):
        """Restore generated targets to their earliest backed-up input, keeping source logs."""
        with self.store.connect() as db:
            drafts = [dict(row) for row in db.execute("""
                SELECT d.* FROM resource_drafts d WHERE d.room=? AND d.status='approved'
                AND d.id IN (
                  SELECT draft_id FROM sleep_reviews WHERE room=? AND state='complete'
                  UNION SELECT draft_id FROM sleep_diary_batches WHERE room=? AND state='complete'
                  UNION SELECT draft_id FROM episode_compressions WHERE room=? AND state='complete')
                ORDER BY d.id""", (room, room, room, room))]
        targets = {}
        for draft in drafts:
            targets.setdefault((draft['kind'], draft['name']), []).append(draft)
        plan = []
        backups={}
        folder = self.store.root / self.app.editor.backup_root(room, 'resource')
        for info in folder.glob('*/info.json'):
            metadata=json.loads(info.read_text('utf-8'))
            backups[(metadata['path'],metadata['revision'])]=(info,metadata)
        for (kind, name), history in targets.items():
            if kind not in KINDS:
                continue
            current = self.app.resources.read(room, kind, name)
            if current['text'].replace('\r\n', '\n').strip() != history[-1]['text'].replace('\r\n', '\n').strip():
                raise EditConflict('整理後に編集された記憶があります。全再整理前に確認してください: ' + current['path'])
            original = None
            for position,draft in enumerate(history):
                backup=backups.get((current['path'],draft['revision']))
                if backup is None:
                    raise ValueError('再整理用の原本バックアップがありません: '+current['path'])
                info,metadata=backup
                raw=info.with_name('before.bin').read_bytes()
                if hashlib.sha256(raw).hexdigest()!=metadata['sha256']:
                    raise ValueError('記憶のバックアップを検証できません。')
                before=decode(raw)['text']
                if position==0:original=before
                elif before.replace('\r\n','\n').strip()!=history[position-1]['text'].replace('\r\n','\n').strip():
                    raise EditConflict('整理の間に編集された記憶があります。全再整理前に確認してください: '+current['path'])
            if original is None:
                raise ValueError('再整理用の原本バックアップがありません: ' + current['path'])
            # New JSON documents are kept as empty arrays, so paths and readers remain valid.
            if not original.strip() and kind in ('episode', 'dream'):
                original = '[]'
            plan.append(dict(kind=kind, name=name, document=current, original=original))
        return plan

    def start(self, room, mode, request_id, revision):
        if mode not in ('incremental', 'rebuild') or not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            raise ValueError('実行方法または操作IDが不正です。')
        room = self.store.room_path(room).name
        with self.app.lock, self.store.lock:
            with self.store.connect() as db:
                previous = db.execute('SELECT * FROM manual_memory_runs WHERE room=? AND request_id=?',
                                      (room, request_id)).fetchone()
                if previous:
                    if previous['mode'] != mode:
                        raise EditConflict('同じ操作IDの内容が異なります。')
                    return dict(previous, payload=json.loads(previous['payload']))
            self.idle(room)
            if self.scheduler.settings(room)['revision'] != revision:
                raise EditConflict('夜間設定が変更されています。再読込してください。')
            if any(r['state'] in ACTIVE or r['state'] == 'unknown' for r in self.listing(room)):
                raise ValueError('実行中または結果未確認の手動整理があります。')
            if any(r['state'] in ('failed','cancelled') and (r['payload'].get('operation') or r['payload'].get('step')) for r in self.records(room)):
                raise ValueError('途中の手動整理があります。履歴を確認し「結果を確認して続行」を使ってください。')
            if not text_connection.ready(self.app, room):
                raise ValueError('会話モデル・APIを準備してください。')
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM resource_drafts WHERE room=? AND status='pending' AND kind IN ('diary','episode','dream','identity','notepad') LIMIT 1", (room,)).fetchone():
                    raise ValueError('記憶の確認待ち案を保存または破棄してから実行してください。')
                maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE room=? AND deleted=0 AND status='complete' AND role='assistant'", (room,)).fetchone()[0]
                cursor = db.execute('SELECT last_id FROM sleep_diary_cursors WHERE room=?', (room,)).fetchone()
            plan = self.rebuild_plan(room) if mode == 'rebuild' else []
            key = uuid.uuid4().hex
            archive = self.store.root / 'manual_memory_backups' / key
            archive.mkdir(parents=True)
            manifest = []
            for target in plan:
                doc = target['document']
                raw = safe_path(self.store.room_path(room), doc['path']).read_bytes()
                snapshot = archive / ('file-' + str(len(manifest)) + '.bin')
                snapshot.write_bytes(raw)
                if hashlib.sha256(snapshot.read_bytes()).digest()!=hashlib.sha256(raw).digest():
                    raise OSError('退避ファイルの照合に失敗しました。原本は変更していません。')
                manifest.append(dict(path=doc['path'],snapshot=snapshot.name,sha256=hashlib.sha256(raw).hexdigest(),
                                     kind=target['kind'],name=target['name'],revision=doc['revision']))
            (archive / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            config = self.scheduler.settings(room)
            payload = dict(cursor=0 if mode == 'rebuild' else (cursor[0] if cursor else 0),
                           maximum=maximum, batch=0, step=0, saved=0, error='',
                           archive=archive.relative_to(self.store.root).as_posix(),
                           kinds=list(KINDS), config_revision=config['revision'], date=datetime.now(JST).date().isoformat())
            run = dict(id=key, room=room, mode=mode, request_id=request_id, state='preparing',
                       revision=uuid.uuid4().hex, payload=payload)
            with self.store.connect() as db:
                db.execute('INSERT INTO manual_memory_runs VALUES(?,?,?,?,?,?,?)',
                           (key, room, request_id, mode, run['state'], run['revision'], json.dumps(payload)))
            try:
                self.enable_rag(room, key)
                for target in plan:
                    self.app.resources.save(room, target['kind'], target['name'], target['original'], target['document']['revision'])
                if mode == 'rebuild':
                    with self.store.connect() as db:
                        # Old drafts remain for auditing, but no longer hide replayed source messages.
                        db.execute("""UPDATE resource_drafts SET status='archived' WHERE room=? AND id IN (
                            SELECT draft_id FROM sleep_reviews WHERE room=?
                            UNION SELECT draft_id FROM sleep_diary_batches WHERE room=?
                            UNION SELECT draft_id FROM episode_compressions WHERE room=?)""", (room,room,room,room))
                        db.execute('INSERT OR REPLACE INTO sleep_diary_cursors VALUES(?,0)', (room,))
                self.update(run, 'queued',prepared=True)
            except Exception:
                self.update(run, 'unknown', error='準備の完了を確認できません。退避先と設定・記憶を確認してください。')
                raise
            return run

    def cancel(self, room, key, revision):
        with self.app.lock:
            run = next((r for r in self.records(room) if r['id'] == key), None)
            if not run or run['revision'] != revision or run['state'] not in ACTIVE:
                raise EditConflict('手動整理の状態が変更されています。')
            self.update(run, 'cancelled', error='手動で中止しました。保存済みの記憶とバックアップを保持しています。')
            if self.active == key and self.app.job and self.app.job.get('room') == room:
                self.app.job['generation'].cancel()
            if run['payload'].get('index_started') and self.index_worker is not None and self.app.semantic.worker is self.index_worker:
                self.app.semantic.cancel.set()
            return run

    def resume(self, room, key, revision):
        with self.app.lock, self.store.lock:
            run=next((r for r in self.records(room) if r['id']==key),None)
            if not run or run['revision']!=revision or run['state'] not in ('failed','unknown','cancelled'):
                raise EditConflict('続行する整理の状態が変更されています。')
            if not run['payload'].get('prepared'):
                raise ValueError('準備途中の結果は自動復旧できません。退避先と記憶の原本を確認してください。')
            if self.app.job or self.app.conversation_waiters or self.app.closing:
                raise SleepBusy('会話・生成の完了を待ってください。')
            with self.store.connect() as db:
                db.execute("UPDATE sleep_night_jobs SET reviewed=1 WHERE room=? AND night LIKE ? AND state='unknown'",
                           (room,run['payload']['date']+':manual-'+key+'-%'))
            self.idle(room,ignore_manual=True)
            # Continuing is an explicit owner action: bind the next steps to the
            # current settings without losing an already-saved partial batch.
            self.update(run,'queued',error='',config_revision=self.scheduler.settings(room)['revision'])
            return run

    def acknowledge(self, room, key, revision):
        with self.app.lock:
            run=next((r for r in self.records(room) if r['id']==key),None)
            if not run or run['revision']!=revision or run['state']!='unknown' or run['payload'].get('prepared'):
                raise EditConflict('確認する準備失敗の状態が変更されています。')
            self.update(run,'reviewed',error='準備途中の設定・退避先・記憶を利用者が確認しました。自動復旧・再送はしていません。')
            return run

    def tick(self):
        if not self.pending:return False
        with self.app.lock:
            if self.app.closing or self.app.job or self.app.conversation_waiters:
                return False
            with self.store.connect() as db:
                row = db.execute("SELECT * FROM manual_memory_runs WHERE state IN ('queued','indexing') ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return False
            run = dict(row, payload=json.loads(row['payload']))
            room, p = run['room'], run['payload']
            try:
                self.idle(room)
            except SleepBusy:
                return False
            except ValueError as error:
                self.update(run, 'failed', error=str(error))
                return True
            if run['state'] == 'indexing':
                status = self.app.semantic.status()
                state = status['state']
                current=hashlib.sha256(json.dumps(self.app.corpus(room),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                success=state=='ready' and status.get('room')==room and not status.get('warnings') and current==p.get('index_source')
                self.update(run, 'complete' if success else 'failed',
                            error='' if success else '索引更新の完了を確認できません。')
                return True
            if not text_connection.ready(self.app, room):
                return False
            if self.scheduler.settings(room)['revision'] != p['config_revision']:
                self.update(run, 'failed', error='夜間設定が変更されました。保存済みの結果を確認してください。')
                return True
            self.update(run, 'running')
            self.active = run['id']
        try:
            config = self.scheduler.settings(room)
            night = p['date'] + ':manual-' + run['id'] + '-' + str(p['batch'])
            kind = KINDS[p['step']]
            if p.get('operation') and not p.get('current_draft'):
                with self.store.connect() as db:
                    if kind=='diary':
                        receipt=db.execute('SELECT state,draft_id,payload FROM sleep_diary_batches WHERE room=? AND job_id=?',
                                           (room,p['operation'])).fetchone()
                    else:
                        receipt=db.execute('SELECT state,draft_id FROM sleep_reviews WHERE room=? AND request_id=?',
                                           (room,'manual-'+p['operation'])).fetchone()
                if receipt and receipt['state']=='complete':
                    p['current_draft']=receipt['draft_id']
                    if kind=='diary':p['diary_source']=json.loads(receipt['payload'])
                    self.update(run,'running')
            if p.get('current_draft'):
                with self.store.connect() as db:
                    old=db.execute('SELECT status FROM resource_drafts WHERE room=? AND id=?',(room,p['current_draft'])).fetchone()
                if old and old[0]=='approved':
                    with self.app.lock:
                        self.guard(run)
                        self.advance(run,kind,p.get('diary_source'))
                    return True
                if old and old[0]=='pending':
                    self.guard(run)
                    review=self.app.resources.review(room,p['current_draft'])
                    if review['conflict']:raise EditConflict('確認待ち案の資料が変更されています。AI更新案を確認してください。')
                    with self.app.lock:
                        self.guard(run)
                        self.app.resources.approve(room,p['current_draft'],review['text'])
                        self.advance(run,kind,p.get('diary_source'))
                    return True
                p.pop('current_draft',None)
            p['operation']=uuid.uuid4().hex
            self.update(run,'running')
            if kind == 'diary':
                diary, source = self.scheduler.diary.source(room, p['date'], config,
                    cursor=p['cursor'], maximum=p['maximum'], replay=run['mode']=='rebuild')
                night+='-'+p['operation']
                p['batch_night']=night
                self.update(run,'running')
                item = dict(id=p['operation'], room=room, night=night, kind='diary')
                with self.store.connect() as db:
                    db.execute('INSERT INTO sleep_night_jobs(id,room,night,kind,config_revision,state,revision) VALUES(?,?,?,?,?,?,?)',
                               (item['id'],room,night,kind,p['config_revision'],'running',uuid.uuid4().hex))
                self.update(run,'running',diary_source=source)
                draft = self.scheduler.diary.generate(item, config, lambda: self.guard(run), source_override=(diary,source))
                self.scheduler._finish(item['id'],'complete',draft_id=draft['id'])
            else:
                conversation = self.scheduler.diary.episode_source(room, p['batch_night'],replay=run['mode']=='rebuild') if kind == 'episode' else p['source']
                source = self.app.sleep_review.preview(room, kind, conversation=conversation)
                draft = self.app.sleep_review.generate(*(source[k] for k in ('room','kind','name','revision','source_hash')),
                    'manual-'+p['operation'],
                    background=True, before_start=lambda: self.guard(run), conversation=conversation)
            with self.app.lock:
                self.guard(run)
                self.update(run,'running',current_draft=draft['id'])
            review = self.app.resources.review(room, draft['id'])
            if review['conflict']:
                raise EditConflict('記憶または会話資料が変更されました。草案を保持しました。')
            with self.app.lock:
                self.guard(run)
                self.app.resources.approve(room, draft['id'], review['text'])
                self.advance(run, kind, source)
        except NoDiarySource:
            try:
                with self.app.lock:
                    self.guard(run)
                    if kind!='diary':
                        self.advance(run,kind,None)
                    else:
                        digest=hashlib.sha256(json.dumps(self.app.corpus(room),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                        self.app.semantic.build(room, self.app.resolved_settings()['executable'])
                        self.index_worker=self.app.semantic.worker
                        self.update(run,'indexing',index_started=True,index_source=digest)
            except (ValueError,OSError) as error:
                with self.app.lock,self.store.connect() as db:
                    current=db.execute('SELECT state FROM manual_memory_runs WHERE id=?',(run['id'],)).fetchone()
                    if current and current[0]!='cancelled':
                        self.update(run,'failed',error='記憶の保存は終了しましたが、索引更新を開始できません: '+str(error))
        except SleepBusy:
            with self.store.connect() as db:
                current=db.execute('SELECT state FROM manual_memory_runs WHERE id=?',(run['id'],)).fetchone()
            if current and current[0]!='cancelled':self.update(run, 'queued')
        except Exception as error:
            if str(error)=='整理による変更がありません。' and kind in ('identity','notepad'):
                with self.app.lock:
                    self.guard(run)
                    self.advance(run,kind,None)
                return True
            with self.store.connect() as db:
                current=db.execute('SELECT state FROM manual_memory_runs WHERE id=?',(run['id'],)).fetchone()
            if current and current[0] != 'cancelled':
                self.update(run,'failed',error=str(error)[:500])
        finally:
            with self.store.connect() as db:
                db.execute("UPDATE sleep_night_jobs SET state='failed',error='手動整理の履歴を確認してください。',revision=? WHERE room=? AND night LIKE ? AND state='running'",
                           (uuid.uuid4().hex,room,p['date']+':manual-'+run['id']+'-%'))
            self.active = None
        return True

    def advance(self,run,kind,source):
        p=run['payload']
        p['saved']+=1
        if kind=='diary':
            p['cursor']=source['last_id']
            p['source']={k:source[k] for k in ('messages','message_revisions')}
        p.pop('current_draft',None)
        p.pop('operation',None)
        p.pop('diary_source',None)
        p['step']+=1
        if p['step']==len(KINDS):
            p.update(step=0,batch=p['batch']+1)
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO sleep_diary_cursors VALUES(?,?)',(run['room'],p['cursor']))
        self.update(run,'queued')

    def guard(self, run):
        with self.app.lock, self.store.connect() as db:
            current = db.execute('SELECT state FROM manual_memory_runs WHERE id=?', (run['id'],)).fetchone()
            if not current or current[0] != 'running' or self.app.closing:
                raise ValueError('手動整理は中止されています。')
            if uncertain(db,run['room']):
                raise ValueError('ルーム状態または結果未確認の処理が変わりました。')
            if self.scheduler.settings(run['room'])['revision']!=run['payload']['config_revision']:
                raise EditConflict('整理中に夜間設定が変更されました。保存済みの結果を確認してください。')
