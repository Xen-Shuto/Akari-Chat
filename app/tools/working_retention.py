"""Opt-in idle-topic dormancy, using recorded generation attempts, never mtimes."""
from tools.background_guard import uncertain
import json
import os
import time
import uuid
from datetime import datetime, timezone
from litechat.file_editor import EditConflict
from tools.working_usage import instant
from tools.sleep_schedule import night_key


def parsed(value, sqlite=False):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            if not sqlite:return None
            result = result.replace(tzinfo=timezone.utc)  # SQLite state timestamps are UTC.
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


class WorkingRetention:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.next_scan = 0
        self.warning = ''
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS working_retention_settings(room TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS working_retention_runs(id TEXT PRIMARY KEY,room TEXT,day TEXT,policy_revision TEXT,payload TEXT,UNIQUE(room,day,policy_revision))')

    def settings(self, room, db=None):
        room = self.store.room_path(room).resolve().name
        if db is None:
            with self.store.connect() as conn:return self.settings(room, conn)
        row = db.execute('SELECT payload FROM working_retention_settings WHERE room=?', (room,)).fetchone()
        return json.loads(row[0]) if row else dict(enabled=False, days=30, since=None, revision='unconfigured')

    def save(self, room, enabled, days, revision, clock=None):
        room = self.store.room_path(room).resolve().name
        if type(enabled) is not bool or type(days) is not int or not 1 <= days <= 365:
            raise ValueError('自動休眠はtrue/false、未使用日数は1～365日で指定してください。')
        with self.app.lock, self.store.lock, self.store.connect() as db:
            if self.app.job or self.app.closing:
                raise ValueError('生成中または終了処理中です。')
            db.execute('BEGIN IMMEDIATE')
            previous = self.settings(room, db)
            if previous['revision'] != revision:
                raise EditConflict('自動休眠の設定が変わりました。再読込してください。')
            if (previous['enabled'], previous['days']) == (enabled, days):
                return previous
            policy = dict(enabled=enabled, days=days, since=instant(clock).isoformat() if enabled else None, revision=uuid.uuid4().hex)
            db.execute('INSERT OR REPLACE INTO working_retention_settings VALUES(?,?)', (room, json.dumps(policy)))
        self.next_scan = 0
        return policy

    def snapshot(self, room, db, clock, policy):
        inventory = self.app.slots.inventory(room)
        if inventory['warnings'] or len(inventory['slots']) > 100:
            raise ValueError('話題一覧に警告があるか100件を超えるため、自動休眠の確認を中止します。')
        rows = db.execute('SELECT payload FROM autonomy_documents WHERE room=? ORDER BY id LIMIT 1001', (room,)).fetchall()
        if len(rows) > 1000:
            raise ValueError('活動が1000件を超えるため、自動休眠の確認を中止します。')
        linked = set()
        if policy['enabled'] and parsed(policy['since']) is None:
            raise ValueError('有効化時刻を確認できないため自動休眠を中止します。')
        for row in rows:
            item = json.loads(row[0])
            if item['state'] == 'active':
                linked.update(os.path.normcase(name) for name in item.get('details', {}).get('working_slots', []))
        entries = []; size = 0
        for slot in inventory['slots']:
            doc = self.app.slots.preview(room, slot['name']);size += len(doc['text'])
            if size > 2000000:
                raise ValueError('話題本文が200万字を超えるため、自動休眠の確認を中止します。')
            path = self.app.working_states.key(doc['path'])
            use = db.execute('SELECT * FROM working_usage WHERE room=? AND path=?', (room, path)).fetchone()
            last = parsed(use['last_used_at']) if use else None
            enabled_at = parsed(policy['since'])
            restored = parsed(doc['state_updated'], sqlite=True)
            anchor = max(value for value in (last, enabled_at, restored) if value is not None) if last else None
            idle = max(0., (clock - anchor).total_seconds() / 86400) if anchor else None
            reason = ''
            if not doc['exists']:reason = '本文がありません'
            elif doc['state'] == 'dormant':reason = '休眠中です'
            elif doc['is_selected'] or os.path.normcase(doc['slot']) == 'main':reason = '手動選択中または既定のmainです'
            elif os.path.normcase(doc['slot']) in linked:reason = '進行中の活動に関連しています'
            elif not last:reason = '送信試行の時刻が不明です'
            elif use['body_revision'] != doc['revision']:reason = '最終記録の後で本文が変更されています'
            elif not policy['enabled']:reason = '自動休眠はオフです'
            elif idle < policy['days']:reason = '未使用日数または有効化/復帰後の猶予期間内です'
            entries.append(dict(slot=doc['slot'], path=doc['path'], revision=doc['revision'], state_revision=doc['state_revision'],
                                eligible=not reason, reason=reason or '未使用日数を超えています', idle_days=idle,
                                last_used_at=use['last_used_at'] if use else None, protected_since=anchor.isoformat() if anchor else None))
        return dict(config_revision=inventory['config_revision'], entries=entries)

    def preview(self, room, clock=None):
        room = self.store.room_path(room).resolve().name
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN')
            policy = self.settings(room, db)
            warning = self.warning
            try:result = self.snapshot(room, db, instant(clock), policy)
            except (ValueError,OSError) as exc:
                result = dict(config_revision=None,entries=[])
                warning = str(exc)
            history = db.execute('SELECT payload FROM working_retention_runs WHERE room=? ORDER BY rowid DESC LIMIT 10', (room,)).fetchall()
        return dict(policy=policy, **result, history=[json.loads(row[0]) for row in history], warning=warning)

    def sweep(self, room, revision, clock=None, checkpoint=lambda:None):
        room = self.store.room_path(room).resolve().name
        moment = instant(clock)
        with self.app.lock, self.store.lock, self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:
                return None
            db.execute('BEGIN IMMEDIATE');checkpoint()
            policy = self.settings(room, db)
            if uncertain(db,room):return None
            if policy['revision'] != revision:
                raise EditConflict('自動休眠の設定が変わりました。再確認してください。')
            if not policy['enabled']:
                return None
            schedule = self.app.sleep_schedule.settings(room)
            day = night_key(moment,schedule['start'],schedule['end'])
            if day is None:return None
            previous = db.execute('SELECT payload FROM working_retention_runs WHERE room=? AND day=? AND policy_revision=?', (room, day, revision)).fetchone()
            if previous:return dict(json.loads(previous[0]), replayed=True)
            snapshot = self.snapshot(room, db, moment, policy)
            key = uuid.uuid4().hex
            results = []
            for index, entry in enumerate(snapshot['entries']):
                if entry['eligible']:
                    result = self.app.working_states.change(room, entry['slot'], 'dormant', entry['state_revision'], key+'-'+str(index), checkpoint, _db=db)
                    results.append(dict(slot=entry['slot'],path=entry['path'],revision=entry['revision'],last_used_at=entry['last_used_at'],idle_days=entry['idle_days'],state_revision=result['current']['state_revision']))
            checkpoint()
            if self.app.slots.config(room)[0]['revision'] != snapshot['config_revision']:
                raise EditConflict('休眠処理中に手動の選択設定が変わりました。')
            # Detect external edits after any earlier candidate was changed. All state
            # metadata and receipts roll back together; source bytes are never written.
            for entry in snapshot['entries']:
                if self.app.slots.preview(room,entry['slot'])['revision'] != entry['revision']:
                    raise EditConflict('休眠処理中に話題本文が変わりました。')
            result = dict(id=key,day=day,policy_revision=revision,days=policy['days'],evaluated_at=moment.isoformat(),changed=results,scanned=len(snapshot['entries']),replayed=False)
            db.execute('INSERT INTO working_retention_runs VALUES(?,?,?,?,?)', (key,room,day,revision,json.dumps(result,ensure_ascii=False)))
            return result

    def tick(self, clock=None):
        if self.warning or time.monotonic() < self.next_scan:
            return False
        self.next_scan = time.monotonic()+60
        with self.app.lock:
            if self.app.closing or self.app.job or self.app.conversation_waiters:
                return False
            with self.store.connect() as db:
                rows = db.execute('SELECT room,payload FROM working_retention_settings WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) ORDER BY room LIMIT 1001').fetchall()
            if len(rows) > 1000:
                raise ValueError('自動休眠のルーム設定が1000件を超えています。')
            for row in rows:
                policy=json.loads(row['payload'])
                if policy['enabled']:
                    result=self.sweep(row['room'],policy['revision'],clock)
                    if result and not result['replayed']:
                        return True
        return False
