"""Wait for the requested operation in its original tool turn, never a new chat."""
import json
import threading
import time
import uuid
from datetime import datetime,timezone
from litechat.engine import Generation
from .contracts import ToolOutput,ToolFailure


TABLES={'image':'image_jobs','watch':'url_watch_checks','research':'autonomy_runs'}
PENDING={'queued','running','cancelling','waiting'}


def recover_inline_jobs(store):
    """Never start orphaned tool requests as background work after restart."""
    with store.lock,store.connect() as db:
        for table in TABLES.values():
            for row in db.execute(f"SELECT room,id,payload FROM {table} WHERE state='queued' AND json_extract(payload,'$.execution')='inline'").fetchall():
                item=json.loads(row['payload']);item.update(state='cancelled',error='前回の会話の完了待機が中断されたため、未開始の処理は実行しませんでした。',revision=uuid.uuid4().hex)
                db.execute(f'UPDATE {table} SET state=?,payload=? WHERE room=? AND id=?',('cancelled',json.dumps(item,ensure_ascii=False),row['room'],row['id']))


def wait_for_result(app,context,kind,key,changed=True):
    table=TABLES[kind]
    def read():
        with app.store.connect() as db:
            row=db.execute(f'SELECT payload FROM {table} WHERE room=? AND id=?',(context.room,key)).fetchone()
        if not row:raise ToolFailure('result_unconfirmed','処理記録が見つかりません。再実行しません。','unknown')
        return json.loads(row[0])
    def cancel_queued(reason):
        with app.store.lock,app.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute(f'SELECT payload FROM {table} WHERE room=? AND id=?',(context.room,key)).fetchone()
            if row:
                item=json.loads(row[0])
                if item['state']=='queued':
                    item.update(state='cancelled',error=reason,revision=uuid.uuid4().hex,completed_at=datetime.now(timezone.utc).isoformat())
                    db.execute(f'UPDATE {table} SET state=?,payload=? WHERE room=? AND id=?',('cancelled',json.dumps(item,ensure_ascii=False),context.room,key))
    standalone=False
    with app.lock:
        owner=app.job
        if owner is None:
            generation=Generation()
            if context.cancelled is not None:generation.cancelled=context.cancelled
            owner=dict(id=context.job_id,room=context.room,generation=generation,finished=threading.Event(),kind='tool_wait')
            app.job=owner;standalone=True
        if owner.get('room')!=context.room or context.job_id not in [owner['id'],*owner.get('nested_job_ids',[])]:
            cancel_queued('別の処理が進行中のため開始しませんでした。')
            raise ToolFailure('result_unconfirmed','別の処理が進行中のため、この操作は開始しません。','unknown')
    interrupted='';done=threading.Event();errors=[]
    def perform():
        try:
            context.checkpoint()
            if read()['state']=='queued':
                if kind=='image':app.images.process_one(claim=lambda:not owner['generation'].cancelled.is_set(),gate=app.lock,room=context.room,key=key)
                elif kind=='watch':app.watches.tick(app,owner=owner,room=context.room,key=key)
                else:app.autonomous.tick(owner=owner,room=context.room,key=key)
        except Exception as error:errors.append(type(error).__name__)
        finally:done.set()
    try:
        with app.store.lock,app.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute(f'SELECT payload FROM {table} WHERE room=? AND id=?',(context.room,key)).fetchone()
            if not row:raise ToolFailure('result_unconfirmed','処理記録を確認できません。','unknown')
            item=json.loads(row[0]);item['execution']='inline'
            db.execute(f'UPDATE {table} SET payload=? WHERE room=? AND id=?',(json.dumps(item,ensure_ascii=False),context.room,key))
        app.diagnostics.trace('tool.waiting',context.room,context.job_id,call_id=context.call_id,kind=kind,target=key)
        worker=threading.Thread(target=perform,daemon=True,name='akari-tool-result');worker.start()
        while True:
            if done.wait(.1) and (errors or read()['state'] not in PENDING or read()['state']=='queued'):break
            try:context.checkpoint()
            except ToolFailure as error:
                interrupted=str(error);break
            if done.is_set():time.sleep(.1)
        if interrupted or errors or read()['state']=='queued':
            cancel_queued(interrupted or '今回の処理を開始できませんでした。設定・許可・未確認の処理を確認してください。')
        item=read()
        if kind=='image':item=app.images.describe_job(item)
        data={k:item[k] for k in ('id','revision','state','error_code','error','completed_at','duration_ms','artifact_id','result','diff','summary','summary_error','text','sources') if k in item}
        data.update(kind=kind+'_job',completed=item['state']=='succeeded',phase='finished' if item['state'] not in PENDING else 'unconfirmed')
        artifact=None
        if kind=='image' and item['state']=='succeeded' and item.get('artifact_id'):
            doc,_=app.images.artifact(context.room,item['artifact_id'])
            artifact={k:doc[k] for k in ('id','path','mime','width','height','provider','model')}
            data.update(artifact=artifact,path=doc['path'],visual_content='not_supplied')
        status=('ok' if changed else 'no_change') if item['state']=='succeeded' else 'cancelled' if item['state']=='cancelled' else 'unknown' if item['state'] in PENDING|{'unknown'} else 'error'
        message=('処理が完了しました。保存済みの結果を踏まえて応答できます。' if item['state']=='succeeded' else '処理結果: '+item['state']+'。'+item.get('error','')+'結果未確認の処理を自動再実行しません。')
        if artifact:message+='画像の保存先（このルームからの相対パス）: '+artifact['path']+'。画像データが渡された場合だけ内容を確認できます。'
        if interrupted:message+='待機を中止しました。開始済みの外部処理が続いている可能性があります。'
        app.diagnostics.trace('tool.result_ready',context.room,context.job_id,call_id=context.call_id,kind=kind,target=key,state=item['state'],status=status)
        effect={'image':'image_job','watch':'url_check_queued','research':'research_queued'}[kind]
        error=None if status in ('ok','no_change') else dict(code=item.get('error_code') or status,message=message)
        return ToolOutput(data=data,status=status,message=message,error=error,changes=[{'kind':effect,'id':key,'state':item['state']}] if changed else [])
    finally:
        if standalone:
            with app.lock:
                if app.job is owner:app.job=None
                owner['finished'].set()
