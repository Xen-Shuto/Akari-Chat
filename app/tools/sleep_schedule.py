"""Persisted nightly draft-only maintenance, dispatched by the shared worker."""
import json
import re
import time
import uuid
from datetime import datetime,timedelta,date

from litechat.episodes import JST, fingerprint
from litechat import text_connection
from litechat.working_slots import slot_name
from litechat.file_editor import EditConflict
from tools.sleep_review import SleepBusy
from tools.background_guard import queue_full, ready_sql
from tools.episode_compression import CompressionNoCandidates
from .sleep_diary import SleepDiary, NoDiarySource, DiaryCancelled

DEFAULTS=dict(enabled=False,start='00:00',end='07:00',kinds=['diary','episode','dream'],compression_targets=[],auto_save=False)


def clock_minute(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
        raise ValueError('時刻はHH:MM（00:00～23:59）で指定してください。')
    h,m=map(int,value.split(':'));return h*60+m


def night_key(moment,start,end):
    moment=moment.astimezone(JST)
    first,last=clock_minute(start),clock_minute(end)
    if first==last:raise ValueError('開始と終了は異なる時刻にしてください。')
    minute=moment.hour*60+moment.minute
    if first<last:return moment.date().isoformat() if first<=minute<last else None
    if minute>=first:return moment.date().isoformat()
    if minute<last:return (moment.date()-timedelta(days=1)).isoformat()
    return None


class SleepSchedule:
    def __init__(self,app):
        self.app,self.store=app,app.store
        self.next_scan=0
        self.pending=True
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sleep_schedule_settings(room TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute("""CREATE TABLE IF NOT EXISTS sleep_night_jobs(
                id TEXT PRIMARY KEY,room TEXT NOT NULL,night TEXT NOT NULL,kind TEXT NOT NULL,
                config_revision TEXT NOT NULL,state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',
                draft_id INTEGER,revision TEXT NOT NULL,reviewed INTEGER NOT NULL DEFAULT 0,created TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(room,night,kind))""")
            if 'input_snapshot' not in {r[1] for r in db.execute('PRAGMA table_info(sleep_night_jobs)')}:db.execute("ALTER TABLE sleep_night_jobs ADD COLUMN input_snapshot TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE sleep_night_jobs SET state='unknown',revision=?,error=? WHERE state='running'",
                       (uuid.uuid4().hex,'前回の結果を確認できません。この夜の同じ対象を自動で再実行しません。'))
        from .night_autosave import NightAutosave
        self.autosave=NightAutosave(self)
        self.diary=SleepDiary(app)
        from .manual_memory import ManualMemory
        self.manual=ManualMemory(self)

    def settings(self,room):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:row=db.execute('SELECT payload FROM sleep_schedule_settings WHERE room=?',(room,)).fetchone()
        result={**DEFAULTS,'compression_targets':[],**json.loads(row[0])} if row else {**DEFAULTS,'kinds':list(DEFAULTS['kinds']),'compression_targets':[],'revision':'unconfigured'}
        if getattr(self.app,'sleep_policy',None):
            sleep=self.app.sleep_policy.settings(room)
            result.update(start=sleep['start'],end=sleep['end'],sleep_enabled=sleep['enabled'],revision=result['revision']+':sleep:'+sleep['revision'])
        return result

    def save(self,room,data,revision):
        room=self.store.room_path(room).resolve().name
        if not isinstance(data,dict) or (set(data)-set(DEFAULTS) or not {'enabled','start','end','kinds'}<=set(data)) or type(data['enabled']) is not bool:
            raise ValueError('夜間設定の項目が不正です。')
        clock_minute(data['start']);clock_minute(data['end'])
        if data['start']==data['end']:raise ValueError('開始と終了は異なる時刻にしてください。')
        data={**data,'auto_save':data.get('auto_save',self.settings(room)['auto_save']),'compression_targets':data.get('compression_targets',self.settings(room)['compression_targets'])}
        if type(data['auto_save']) is not bool:raise ValueError('自動保存はtrue/falseで指定してください。')
        targets=data['compression_targets']
        if not isinstance(targets,list) or len(targets)>2 or any(not isinstance(t,dict) or set(t) not in ({'mode','name'},{'mode','name','sources'}) or t['mode'] not in ('weekly','monthly') or not isinstance(t['name'],str) for t in targets) or len({t['mode'] for t in targets})!=len(targets):raise ValueError('圧縮するファイルは週次/月次それぞれ1件まで指定してください。')
        for target in targets:
            if 'sources' in target:
                slot_name(target['name'])
                if len(target['name'])>50:raise ValueError('横断集約の保存名の先頭は50文字までです。')
                with self.app.crossfile_compression.context(target['sources']):pass
                for name in target['sources']:
                    if not self.app.resources.read(room,'episode',name)['exists'] and data['enabled']:raise ValueError('横断集約の入力元を確認してください。')
            elif not self.app.resources.read(room,'episode',target['name'])['exists'] and data['enabled']:raise ValueError('圧縮する既存のエピソードファイルを選んでください。')
        kinds=data['kinds']
        if not isinstance(kinds,list) or not (0 if targets else 1)<=len(kinds)<=5 or any(k not in (*self.app.sleep_review.KINDS,'diary') for k in kinds) or len(set(kinds))!=len(kinds):
            raise ValueError('生成する対象を重複なく選択してください（通常整理5種類・圧縮2種類まで）。')
        with self.app.lock,self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self.settings(room)
            if old['revision']!=revision:raise EditConflict('夜間設定が変更されています。読み直してください。')
            saved={**data,'revision':uuid.uuid4().hex}
            db.execute('INSERT OR REPLACE INTO sleep_schedule_settings VALUES(?,?)',(room,json.dumps(saved)))
            db.execute("UPDATE sleep_night_jobs SET state='cancelled',revision=?,error=? WHERE room=? AND state='queued'",
                       (uuid.uuid4().hex,'夜間設定の変更により待機中の処理を取り消しました。',room))
        self.next_scan=0
        return self.settings(room)

    def listing(self,room):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute("SELECT n.*,d.status AS draft_status,t.id AS timeline_id,a.state AS auto_save_state,a.error AS auto_save_error,a.revision AS auto_save_revision FROM sleep_night_jobs n LEFT JOIN resource_drafts d ON d.id=n.draft_id LEFT JOIN night_autosaves a ON a.job_id=n.id LEFT JOIN activity_timelines t ON t.room=n.room AND t.id=n.id WHERE n.room=? ORDER BY (n.state='unknown' AND n.reviewed=0) DESC,(n.state IN ('queued','running')) DESC,n.rowid DESC LIMIT 50",(room,))]
        return dict(settings=self.settings(room),runs=rows,manual=self.manual.listing(room))

    def _finish(self,key,state,error='',draft_id=None):
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE sleep_night_jobs SET state=?,error=?,draft_id=?,revision=? WHERE id=?',
                       (state,error[:1000],draft_id,uuid.uuid4().hex,key))
            if state!='queued':
                row=db.execute('SELECT * FROM sleep_night_jobs WHERE id=?',(key,)).fetchone()
                self.app.timelines.finish_sleep(db,row['room'],dict(row))

    def import_timeline(self,room,key,revision):
        room=self.store.room_path(room).resolve().name
        with self.app.lock,self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM sleep_night_jobs WHERE room=? AND id=?',(room,key)).fetchone()
            if row is None or row['revision']!=revision:raise EditConflict('夜間履歴が変更されています。再読込してください。')
            if row['state'] in ('queued','running'):raise ValueError('終了済みの履歴を選んでください。')
            run=dict(row);self.app.timelines.start_sleep(db,room,run,imported=True);self.app.timelines.finish_sleep(db,room,run)
        return dict(timeline_id=key)

    def acknowledge(self,room,key,revision):
        room=self.store.room_path(room).resolve().name
        with self.app.lock,self.store.lock,self.store.connect() as db:
            changed=db.execute("UPDATE sleep_night_jobs SET reviewed=1,revision=? WHERE room=? AND id=? AND revision=? AND state='unknown' AND reviewed=0",
                               (uuid.uuid4().hex,room,key,revision)).rowcount
            if not changed:raise EditConflict('確認対象が変更されています。履歴を再読込してください。')
        self.next_scan=0
        return self.listing(room)

    def tick(self,moment=None):
        if self.manual.tick():return True
        if self.autosave.process_one(moment or datetime.now(JST)):return True
        explicit=moment is not None
        if not explicit and not self.pending and time.monotonic()<self.next_scan:return False
        moment=moment or datetime.now(JST)
        app=self.app
        with app.lock:
            if app.closing or app.job or app.conversation_waiters:return False
            if explicit or time.monotonic()>=self.next_scan:
                self.next_scan=time.monotonic()+30
                with self.store.lock,self.store.connect() as db:
                    settings=db.execute('SELECT room,payload FROM sleep_schedule_settings WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) ORDER BY room').fetchall()
                    for row in settings:
                        if db.execute("SELECT 1 FROM manual_memory_runs WHERE room=? AND state IN ('preparing','queued','running','indexing','unknown')",(row['room'],)).fetchone():continue
                        config=json.loads(row['payload'])
                        if not config['enabled'] or not text_connection.ready(app,row['room']):continue
                        effective=self.settings(row['room'])
                        if not effective.get('sleep_enabled',True):continue
                        config=effective
                        key=night_key(moment,config['start'],config['end'])
                        if key is None:continue
                        for kind in sorted(config['kinds'],key=lambda kind:kind!='diary')+[t['mode']+'_compression' for t in config.get('compression_targets',[])]:
                            if queue_full(db):
                                break
                            db.execute('INSERT OR IGNORE INTO sleep_night_jobs(id,room,night,kind,config_revision,state,revision) VALUES(?,?,?,?,?,?,?)',
                                       (uuid.uuid4().hex,row['room'],key,kind,config['revision'],'queued',uuid.uuid4().hex))
            with self.store.connect() as db:
                row = db.execute(
                    "SELECT * FROM sleep_night_jobs n WHERE state='queued' AND "
                    + ready_sql('n.room') + " ORDER BY n.rowid LIMIT 20"
                ).fetchall()
                row=next((r for r in row if text_connection.ready(app,r['room']) and not db.execute("SELECT 1 FROM manual_memory_runs WHERE room=? AND state IN ('preparing','queued','running','indexing','unknown')",(r['room'],)).fetchone()),None)
            self.pending=row is not None
            if row is None:return False
            item=dict(row)
            try:config=self.settings(item['room'])
            except ValueError:
                self._finish(item['id'],'skipped','ルームが見つかりません。');return True
            if not config['enabled'] or not config.get('sleep_enabled',True) or config['revision']!=item['config_revision']:
                self._finish(item['id'],'cancelled','夜間設定が変更されています。');return True
            if night_key(moment,config['start'],config['end'])!=item['night']:
                self._finish(item['id'],'expired','指定の時間帯を過ぎたため実行しませんでした。');return True
            with self.store.connect() as db:
                db.execute("UPDATE sleep_night_jobs SET state='running',revision=? WHERE id=?",(uuid.uuid4().hex,item['id']))
        try:
            conversation=None
            if item['kind']=='episode' and 'diary' in config['kinds']:
                conversation=self.diary.episode_source(item['room'],item['night'])
            compression=item['kind'] in ('weekly_compression','monthly_compression')
            if compression:
                mode=item['kind'].split('_')[0]
                target=next(t for t in config.get('compression_targets',[]) if t['mode']==mode)
                day=date.fromisoformat(item['night'])
                period=(day-timedelta(days=day.weekday()+7)) if mode=='weekly' else (day.replace(day=1)-timedelta(days=1)).replace(day=1)
                item['input_snapshot']=json.dumps(dict(name=target['name'],week=period.isoformat(),compression_mode=mode))
                with self.store.connect() as db:db.execute('UPDATE sleep_night_jobs SET input_snapshot=? WHERE id=?',(item['input_snapshot'],item['id']))
                if 'sources' in target:
                    originals=[app.resources.read(item['room'],'episode',n) for n in target['sources']]
                    name=target['name']+'_'+period.strftime('%Y%m%d')+'_'+fingerprint([(n,d['revision']) for n,d in zip(target['sources'],originals)])[:12]
                    if app.resources.read(item['room'],'episode',name)['exists']:raise CompressionNoCandidates('同じ入力の集約先が既にあります。再生成せず内容を確認してください。')
                    with app.crossfile_compression.context(target['sources']):
                        try:source,document,_,_=app.crossfile_compression.preview(item['room'],name,period.isoformat(),mode)
                        except ValueError as error:raise CompressionNoCandidates(str(error)) from error
                else:source,document,_,_=app.episode_compression.preview(item['room'],target['name'],period.isoformat(),mode)
                with self.store.connect() as db:
                    prior=db.execute("SELECT c.draft_id FROM episode_compressions c JOIN resource_drafts d ON d.id=c.draft_id WHERE c.room=? AND c.name=? AND c.week=? AND c.mode=? AND c.state='complete' AND d.status='pending' AND d.revision=? LIMIT 1",(item['room'],source['name'],period.isoformat(),mode,document['revision'])).fetchone()
                if prior:
                    self._finish(item['id'],'skipped','同じ期間・本文に確認待ち案があるため再生成しません。',prior[0]);return True
                item['input_snapshot']=json.dumps({k:source[k] for k in ('name','week','compression_mode','revision')+ (('names',) if 'names' in source else ())})
                with self.store.connect() as db:db.execute('UPDATE sleep_night_jobs SET input_snapshot=? WHERE id=?',(item['input_snapshot'],item['id']))
            elif item['kind']=='diary':source=None
            else:source=app.sleep_review.preview(item['room'],item['kind'],**({'conversation':conversation} if conversation is not None else {}))
            def guard():
                current=self.settings(item['room'])
                instant=moment if explicit else datetime.now(JST)
                if current['revision']!=item['config_revision'] or not current['enabled'] or night_key(instant,current['start'],current['end'])!=item['night']:
                    raise SleepBusy('夜間設定または実行時間帯が変わりました。')
                with self.store.lock,self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE');app.timelines.start_sleep(db,item['room'],item)
            if item['kind']=='diary':
                draft=self.diary.generate(item,config,guard)
            elif compression and 'names' in source:
                draft=app.crossfile_compression.generate_bundle(source['room'],source['names'],source['name'],source['week'],source['revision'],'night-'+item['id'],source['compression_mode'],background=True,before_start=guard)
            elif compression:
                draft=app.episode_compression.generate(source['room'],source['name'],source['week'],source['revision'],'night-'+item['id'],source['compression_mode'],background=True,before_start=guard)
            else:
                draft=app.sleep_review.generate(*(source[k] for k in ('room','kind','name','revision','source_hash')),
                                                'night-'+item['id'],background=True,before_start=guard,**({'conversation':conversation} if conversation is not None else {}))
            if config.get('auto_save'):self.autosave.enqueue(item,draft['id'])
            self._finish(item['id'],'complete',draft_id=draft['id'])
        except (CompressionNoCandidates,NoDiarySource) as error:
            self._finish(item['id'],'skipped',str(error))
        except DiaryCancelled as error:
            self._finish(item['id'],'cancelled',str(error))
        except SleepBusy:
            self._finish(item['id'],'queued')
        except Exception as error:
            # No originals were changed. Failed attempts are not retried nightly.
            with self.store.connect() as db:
                if item['kind']=='diary':
                    receipt=db.execute('SELECT state,draft_id FROM sleep_diary_batches WHERE room=? AND job_id=?',(item['room'],item['id'])).fetchone()
                else:
                    table='episode_compressions' if item['kind'] in ('weekly_compression','monthly_compression') else 'sleep_reviews'
                    receipt=db.execute('SELECT state,draft_id FROM '+table+' WHERE room=? AND request_id=?',(item['room'],'night-'+item['id'])).fetchone()
            state='unknown' if receipt and receipt['state']=='complete' else 'cancelled' if receipt and receipt['state']=='cancelled' else 'failed'
            self._finish(item['id'],state,str(error),receipt['draft_id'] if receipt else None)
        return True
