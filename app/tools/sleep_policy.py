"""One room sleep window, with read-only compatibility for older time settings."""
import hashlib
import json
import uuid
from datetime import datetime
from litechat.episodes import JST
from litechat.file_editor import EditConflict
from .sleep_schedule import clock_minute, night_key


class SleepPolicy:
    def __init__(self, app):
        self.app, self.store = app, app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sleep_policy_settings(room TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sleep_policy_versions(id INTEGER PRIMARY KEY,room TEXT,payload TEXT,changed TEXT DEFAULT CURRENT_TIMESTAMP)')

    def settings(self, room, db=None):
        self.store.room_path(room)
        if db is None:
            with self.store.connect() as conn:return self.settings(room, conn)
        row = db.execute('SELECT payload FROM sleep_policy_settings WHERE room=?', (room,)).fetchone()
        if row:return json.loads(row[0])
        notice = db.execute('SELECT payload FROM inbox_settings WHERE room=?', (room,)).fetchone()
        night = db.execute('SELECT payload FROM sleep_schedule_settings WHERE room=?', (room,)).fetchone()
        notice = json.loads(notice[0]) if notice else {}
        night = json.loads(night[0]) if night else {}
        if notice.get('quiet_enabled') and notice.get('quiet_start') != notice.get('quiet_end'):
            values = dict(enabled=True, start=notice['quiet_start'], end=notice['quiet_end'])
        elif night.get('enabled'):
            values = dict(enabled=True, start=night['start'], end=night['end'])
        else:
            values = dict(enabled=False, start=night.get('start', '00:00'), end=night.get('end', '07:00'))
        if values['start'] == values['end']:values.update(start='00:00', end='07:00')
        digest = hashlib.sha256(json.dumps([notice, night], sort_keys=True).encode()).hexdigest()
        return dict(values, revision='legacy:' + digest)

    def window(self, room, moment=None):
        config = self.settings(room)
        return night_key(moment or datetime.now(JST), config['start'], config['end']) if config['enabled'] else None

    def view(self, room):
        config = self.settings(room)
        return dict(settings=config, sleeping=self.window(room) is not None)

    def save(self, room, data, revision):
        self.store.room_path(room)
        if not isinstance(data, dict) or set(data) != {'enabled', 'start', 'end'} or type(data['enabled']) is not bool:
            raise ValueError('睡眠設定の項目が不正です。')
        clock_minute(data['start']);clock_minute(data['end'])
        if data['start'] == data['end']:raise ValueError('就寝時刻と起床時刻は異なる時刻にしてください。')
        with self.app.lock, self.store.lock, self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:
                raise ValueError('会話・実行中の処理が終わってから睡眠設定を変更してください。')
            db.execute('BEGIN IMMEDIATE')
            old = self.settings(room, db)
            if old['revision'] != revision:raise EditConflict('睡眠設定が変わりました。再読込してください。')
            saved = dict(data, revision=uuid.uuid4().hex)
            db.execute('INSERT INTO sleep_policy_versions(room,payload) VALUES(?,?)', (room, json.dumps(old)))
            db.execute('INSERT OR REPLACE INTO sleep_policy_settings VALUES(?,?)', (room, json.dumps(saved)))
            db.execute("UPDATE sleep_night_jobs SET state='cancelled',revision=?,error=? WHERE room=? AND state='queued'",
                       (uuid.uuid4().hex, '睡眠時間の変更により待機中の処理を取り消しました。', room))
        self.app.sleep_schedule.next_scan = 0
        return self.view(room)
