"""Explicit local subscription moves within a reviewed research merge transaction."""
import json
import re
import uuid
from litechat.autonomy import encoded,now
from litechat.file_editor import EditConflict


def selected_ids(values):
    if values is None:return []
    if not isinstance(values,list) or len(values)>100 or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{32}',v) for v in values) or len(set(values))!=len(values):
        raise ValueError('移管する購読IDは重複のない100件以内の一覧で指定してください。')
    return sorted(values)


def move(db,room,source,target,ids,checkpoint):
    changes=[]
    for key in ids:
        row=db.execute('SELECT payload FROM research_subscriptions WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームの購読を指定してください。')
        old=json.loads(row[0])
        if old['thread_id']!=source or old['source']!='local' or old['archived']:
            raise ValueError('統合元にある保管されていないローカル購読だけを移管できます。')
        pending=db.execute("SELECT 1 FROM autonomy_runs WHERE room=? AND state IN ('queued','running','unknown') AND json_extract(payload,'$.research.id')=? AND COALESCE(json_extract(payload,'$.reviewed'),0)=0",(room,key)).fetchone()
        if pending:raise EditConflict('移管する購読に待機・実行中・結果未確認の処理があります。先に確認してください。')
        updated=dict(old,thread_id=target,enabled=False,auto_apply=False,next_due=None,revision=uuid.uuid4().hex,updated_at=now(),last_error='')
        changes.append(dict(before=old,after=updated))
    for change in changes:
        checkpoint();old=change['before'];updated=change['after']
        db.execute('INSERT INTO research_subscription_versions(room,subscription_id,payload,changed_at) VALUES(?,?,?,?)',(room,old['id'],encoded(old),now()))
        db.execute('UPDATE research_subscriptions SET payload=? WHERE room=? AND id=?',(encoded(updated),room,old['id']))
    return changes


def restore(db,room,changes,checkpoint):
    restored=[]
    for change in changes:
        after=change['after'];row=db.execute('SELECT payload FROM research_subscriptions WHERE room=? AND id=?',(room,after['id'])).fetchone()
        if row is None or json.loads(row[0])!=after:raise EditConflict('移管後の購読が変更・実行されています。統合と一緒には戻せません。')
        old=change['before']
        # Keep stopped on undo too; never restart a scheduler as a side effect.
        restored.append(dict(old,enabled=False,auto_apply=False,next_due=None,revision=uuid.uuid4().hex,updated_at=now()))
    for change,item in zip(changes,restored):
        checkpoint()
        db.execute('INSERT INTO research_subscription_versions(room,subscription_id,payload,changed_at) VALUES(?,?,?,?)',(room,item['id'],encoded(change['after']),now()))
        db.execute('UPDATE research_subscriptions SET payload=? WHERE room=? AND id=?',(encoded(item),room,item['id']))
    return restored
