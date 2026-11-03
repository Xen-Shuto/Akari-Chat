"""Generation-attempt references for working topics, separate from source files."""
import hashlib
import json
from datetime import datetime, timezone
from litechat.file_editor import EditConflict


def instant(clock=None):
    value = clock if clock is not None else datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('時刻にはタイムゾーンが必要です。')
    return value.astimezone(timezone.utc)


class WorkingUsage:
    def __init__(self, app):
        self.app, self.store = app, app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS working_usage(room TEXT,path TEXT,slot TEXT,body_revision TEXT,last_used_at TEXT,run_id TEXT,PRIMARY KEY(room,path))')
            db.execute('CREATE TABLE IF NOT EXISTS working_usage_events(room TEXT,run_id TEXT,fingerprint TEXT,payload TEXT,PRIMARY KEY(room,run_id))')

    def capture(self, character, report):
        source = character['memory_sources']['working']
        if not any(row['key'] == 'working' and row['status'] == 'included' for row in report['sources']):
            return None
        doc = self.app.slots.preview(character['id'], source['slot'])
        if not doc['exists'] or doc['path'] != source['path'] or doc['text'].strip() != source['text'].strip():
            raise EditConflict('送信する話題の本文が変更されました。再実行してください。')
        return dict(slot=doc['slot'], path=doc['path'], body_revision=doc['revision'])

    def record(self, db, room, run_id, reference, status, model_calls, clock=None):
        if not reference or model_calls < 1:
            return False
        room = self.store.room_path(room).resolve().name
        signature = hashlib.sha256(json.dumps([reference, status], sort_keys=True).encode()).hexdigest()
        previous = db.execute('SELECT fingerprint FROM working_usage_events WHERE room=? AND run_id=?', (room, run_id)).fetchone()
        if previous:
            if previous[0] != signature:
                raise EditConflict('同じ実行IDで別の話題利用記録は保存できません。')
            return False
        moment = instant(clock).isoformat()
        path = self.app.working_states.key(reference['path'])
        payload = dict(reference, last_used_at=moment, status=status, kind='generation_attempt')
        db.execute('INSERT INTO working_usage_events VALUES(?,?,?,?)', (room, run_id, signature, json.dumps(payload, ensure_ascii=False)))
        # A late completion or replay must not move the last-reference clock back.
        prior = db.execute('SELECT last_used_at FROM working_usage WHERE room=? AND path=?', (room, path)).fetchone()
        if prior is None or prior[0] < moment:
            db.execute('INSERT OR REPLACE INTO working_usage VALUES(?,?,?,?,?,?)', (room, path, reference['slot'], reference['body_revision'], moment, run_id))
        return True

    def describe(self, room, name):
        room = self.store.room_path(room).resolve().name
        doc = self.app.slots.preview(room, name)
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM working_usage WHERE room=? AND path=?', (room, self.app.working_states.key(doc['path']))).fetchone()
            count = db.execute('SELECT COUNT(*) FROM working_usage_events WHERE room=? AND json_extract(payload,\'$.path\')=?', (room, doc['path'])).fetchone()[0]
        return dict(last_use=dict(row) if row else None, attempts=count,
                    source_changed=bool(row and row['body_revision'] != doc['revision']))
