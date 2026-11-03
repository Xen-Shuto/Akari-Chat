"""Bounded input references: preparation evidence, never proof of task completion."""
import json
from litechat.file_editor import EditConflict


def capture(db, room, activity_id, working_reference, expected_revision=''):
    result = {'activity': None, 'working': None}
    if activity_id:
        row = db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=?', (room, activity_id)).fetchone()
        if row is None:
            raise EditConflict('実行対象の活動が見つかりません。再実行してください。')
        item = json.loads(row[0])
        if expected_revision and item['revision'] != expected_revision:
            raise EditConflict('実行対象の活動が変更されました。再実行してください。')
        result['activity'] = {key: item[key] for key in ('id', 'kind', 'title', 'state', 'revision')}
    if working_reference:
        # Paths, private bodies, and unrelated slot candidates are not copied.
        result['working'] = {'slot': working_reference['slot'], 'revision': working_reference['body_revision']}
    return result


def describe(db, room, references):
    result = dict(references)
    source = references.get('activity')
    if source:
        row = db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=?', (room, source['id'])).fetchone()
        current = json.loads(row[0]) if row else None
        result['activity'] = dict(source, exists=current is not None,
            changed=current is None or current['revision'] != source['revision'])
    result['notice'] = '開始準備時の入力の参照です。モデルの理解、対象の進捗、行動の成功を示す証拠ではありません。'
    return result
