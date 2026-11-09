"""Shared bounded tool execution, durable receipts and backwards-compatible audit."""
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from .resources import strict_json
from .file_editor import EditConflict
from tools import REGISTRY, TOOLS
from tools.contracts import ToolContext, ToolOutput, ToolFailure, failure, validate
from tools.permissions import ToolPermissions

log=logging.getLogger(__name__)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def bounded_result(result,operation,limit):
    if len(encoded(result))<=limit:return result
    if operation=='read':return failure('limit_exceeded','結果が表示上限を超えました。対象を絞ってください。')
    # Keep a confirmed status and a bounded receipt, never invite a duplicate write.
    ids={}
    for key,value in result['data'].items():
        if key in ('draft_id','task_id','artifact_id','reservation_id') and type(value) in (str,int) and len(str(value))<64:
            ids[key]=value;break
    ids['details_omitted']=True
    return dict(result,message='結果の詳細を省略しました。statusとIDを確認してください。',data=ids,
                changes=[{'kind':'details_omitted'}] if result['changes'] else [],
                error=None if result['error'] is None else {'code':result['error']['code'][:64],'message':'結果の詳細を省略しました。'},
                truncated=True,continuation=None)


class LocalTools:
    def __init__(self,app,registry=REGISTRY):
        self.app,self.registry=app,registry
        self.permissions=ToolPermissions(app,registry)
        self.lock=threading.RLock()
        with app.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS tool_events(id INTEGER PRIMARY KEY,room TEXT,job TEXT,name TEXT,result TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)")
            columns={r[1] for r in db.execute('PRAGMA table_info(tool_events)')}
            for name,kind in [('call_id','TEXT'),('status','TEXT'),('started','TEXT'),('duration_ms','REAL'),
                              ('input_chars','INTEGER'),('result_chars','INTEGER'),('over_budget','INTEGER')]:
                if name not in columns:db.execute('ALTER TABLE tool_events ADD COLUMN '+name+' '+kind)
            db.execute("""CREATE TABLE IF NOT EXISTS tool_invocations(
                room TEXT NOT NULL,job TEXT NOT NULL,call_id TEXT NOT NULL,fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,event_id INTEGER,PRIMARY KEY(room,job,call_id))""")
            receipt_columns={r[1] for r in db.execute('PRAGMA table_info(tool_invocations)')}
            if 'event_id' not in receipt_columns:db.execute('ALTER TABLE tool_invocations ADD COLUMN event_id INTEGER')
            db.execute("""CREATE TABLE IF NOT EXISTS generation_metrics(
                job TEXT PRIMARY KEY,room TEXT,created TEXT,metrics TEXT)""")

    def definitions(self,context,*,categories=None):
        self.app.store.room_path(context.room)
        settings=self.permissions.settings(context.room)
        approved=self.permissions.approved(context,settings) if 'ask' in settings['levels'].values() else []
        result=[]
        for definition in self.registry.definitions(self.app,context,categories=categories):
            spec=self.registry.get(definition['function']['name']);grants=[item for item in approved if item['tool']==spec.name]
            if self.permissions.permits(spec,settings) or grants:
                if grants:definition['function']['description']+=' 承認済みの引数と完全一致する1回のみ実行可。例（最新1件）: '+encoded(grants[0]['arguments'])
                result.append(definition)
        return result

    def _trace(self,event,context,**fields):
        diagnostics=getattr(self.app,'diagnostics',None)
        if diagnostics is not None:diagnostics.trace(event,context.room,context.job_id,call_id=context.call_id,**fields)

    def _record(self,context,name,result,started,elapsed,input_chars,over_budget=False,receipt=False,delivery_result=None):
        raw=encoded(result)
        with self.app.store.connect() as db:
            event=db.execute('INSERT INTO tool_events(room,job,name,result,call_id,status,started,duration_ms,input_chars,result_chars,over_budget)\n                VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (context.room,context.job_id,name,raw,context.call_id,result['status'],started,elapsed,input_chars,len(raw),int(over_budget)))
            delivery=getattr(self.app,'tool_results',None)
            if delivery is not None:delivery.track(db,context,name,delivery_result if delivery_result is not None else result)
            if receipt:
                db.execute("UPDATE tool_invocations SET state='complete',event_id=? WHERE room=? AND job=? AND call_id=?",
                           (event.lastrowid,context.room,context.job_id,context.call_id))
        return result

    def _claim(self,context,fingerprint):
        with self.lock,self.app.store.connect() as db:
            row=db.execute('SELECT i.*,e.result AS event_result FROM tool_invocations i LEFT JOIN tool_events e ON e.id=i.event_id AND e.room=i.room AND e.job=i.job AND e.call_id=i.call_id WHERE i.room=? AND i.job=? AND i.call_id=?',
                           (context.room,context.job_id,context.call_id)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint:
                    raise ToolFailure('conflict','同じ呼出IDに異なる引数が指定されました。','conflict')
                saved=row['event_result'] or (row['result'] if 'result' in row.keys() else None)
                if row['state']=='complete' and saved:return json.loads(saved)
                return failure('result_unconfirmed','同じ呼出しが処理中、または前回の完了を確認できません。再実行しません。','unknown')
            db.execute("INSERT INTO tool_invocations(room,job,call_id,fingerprint,state) VALUES(?,?,?,?,'running')",
                       (context.room,context.job_id,context.call_id,fingerprint))
        return None

    def run(self,room,job,call,*,before=None,cancelled=None,allowed=None,result_budget=None,interactive=False,capability_scope=None,deduplicate_effects=False,autonomous=False):
        # These identifiers come from the application, never from tool arguments.
        self.app.store.room_path(room)
        if not isinstance(job,str) or not job:raise ValueError('ジョブIDが必要です。')
        if before is not None and (type(before) is not int or before<1):raise ValueError('履歴の参照境界が不正です。')
        clock=time.monotonic();started=datetime.now(timezone.utc).isoformat()
        fn=call.get('function',{}) if isinstance(call,dict) else {}
        fn=fn if isinstance(fn,dict) else {}
        name=fn.get('name','');name=name if isinstance(name,str) else ''
        call_id=call.get('id','') if isinstance(call,dict) else ''
        valid_id=isinstance(call_id,str) and 0<len(call_id)<=256
        context=ToolContext(room,job,call_id if valid_id and call_id else uuid.uuid4().hex,before,cancelled,interactive=interactive,capability_scope=capability_scope,autonomous=autonomous)
        spec=self.registry.get(name);input_chars=0;claimed=False;policy_lease=False;approval_used=False
        stage='validation';delivery_result=None
        self._trace('tool.received',context,name=name,arguments_raw=fn.get('arguments','{}'))
        output_limit=spec.max_result_chars if spec else 16000
        if result_budget is not None:
            if type(result_budget) is not int:raise ValueError("結果の予算が不正です。")
            output_limit=min(output_limit,max(0,result_budget))
        try:
            if not valid_id:raise ToolFailure('invalid_arguments','呼出IDが不正です。')
            if not isinstance(call,dict) or call.get('type','function')!='function':
                raise ToolFailure('invalid_arguments','ツール呼出形式が不正です。')
            if spec is None or allowed is not None and name not in allowed:
                raise ToolFailure('unavailable','この応答では利用できないツールです。')
            if not self.registry.usable(spec,self.app,context):
                raise ToolFailure('unavailable','ツールの利用条件が満たされていません。')
            if output_limit<512:raise ToolFailure('limit_exceeded','応答全体のツール結果上限に達しました。')
            raw=fn.get('arguments','{}')
            if not isinstance(raw,str):raise ToolFailure('invalid_arguments','引数はJSON文字列で指定してください。')
            input_chars=len(raw)
            if input_chars>16000:raise ToolFailure('limit_exceeded','ツール引数が長すぎます。')
            try:args=strict_json(raw)
            except (ValueError,TypeError,RecursionError):raise ToolFailure('invalid_arguments','引数のJSON形式が不正です。') from None
            self._trace('tool.arguments',context,name=name,arguments=args)
            args=validate(args,spec.definition['function']['parameters'])
            self._trace('tool.validated',context,name=name,arguments=args)
            stage='permission'
            approval_used=self.permissions.acquire(room,spec,context,args);policy_lease=True
            self._trace('tool.permission',context,name=name,approved_once=bool(approval_used),operation=spec.operation)
            stage='receipt'
            fingerprint=hashlib.sha256(encoded([name,args,before]).encode('utf-8')).hexdigest()
            cached=self._claim(context,fingerprint)
            if cached is not None:
                self._trace('tool.replayed',context,name=name,status=cached.get('status'))
                return bounded_result(cached,spec.operation,output_limit)
            claimed=True
            if deduplicate_effects and spec.operation!='read':
                # A stage rebuild must not turn an already attempted write into a
                # fresh operation under a new call ID. Keep its original outcome.
                with self.app.store.connect() as db:
                    previous=db.execute('SELECT i.call_id,e.result FROM tool_invocations i LEFT JOIN tool_events e ON e.id=i.event_id WHERE i.room=? AND i.job=? AND i.fingerprint=? AND i.call_id<>? ORDER BY i.rowid LIMIT 1',
                        (room,job,fingerprint,context.call_id)).fetchone()
                if previous:
                    result=json.loads(previous['result']) if previous['result'] else failure('result_unconfirmed','先行する同じ更新の結果が未確認です。再実行せず確認してください。','unknown')
                    result=dict(result,data=dict(result['data'],replayed_from_call_id=previous['call_id']))
                    result=bounded_result(result,spec.operation,output_limit)
                    self._trace('tool.effect_replayed',context,name=name,status=result['status'],previous_call_id=previous['call_id'])
                    return self._record(context,name,result,started,(time.monotonic()-clock)*1000,input_chars,receipt=True)
            context=replace(context,deadline=clock+spec.timeout_s)
            context.checkpoint()
            stage='handler'
            self._trace('tool.handler',context,name=name,handler=getattr(spec.handler,'__module__','')+'.'+getattr(spec.handler,'__qualname__',type(spec.handler).__name__))
            output=spec.handler(self.app,context,args)
            if not isinstance(output,ToolOutput):raise TypeError('ToolOutputが必要です。')
            result=output.as_dict()
            if spec.operation=='read' and (result['changes'] or result['status'] in ('pending','partial')):
                raise ValueError('参照ツールが変更結果を返しました。')
            if spec.operation=='propose' and result['changes']:
                raise ValueError('提案ツールが反映済みの変更を返しました。')
            delivery_result=result
            result=bounded_result(result,spec.operation,output_limit)
        except ToolFailure as error:
            self._trace('tool.rejected',context,name=name,stage=stage,error_type=type(error).__name__,code=error.code,error=str(error))
            result=failure(error.code,str(error),error.status)
        except EditConflict:
            self._trace('tool.rejected',context,name=name,stage=stage,error_type='EditConflict',code='conflict')
            result=failure('conflict','対象が変更されています。最新の内容を確認してください。','conflict')
        except Exception:
            log.exception('Tool execution failed: %s',name[:128],extra={'room':room,'job':job})
            self._trace('tool.exception',context,name=name,stage=stage)
            uncertain=claimed and spec is not None and spec.operation!='read'
            result=failure('result_unconfirmed' if uncertain else 'execution_failed',
                           '反映状況を確認できません。自動再実行せず履歴・草案を確認してください。' if uncertain else 'ツールの処理に失敗しました。',
                           'unknown' if uncertain else 'error')
        finally:
            if policy_lease:self.permissions.release()
        elapsed=(time.monotonic()-clock)*1000
        try:
            result=self._record(context,name[:256],result,started,elapsed,input_chars,
                                spec is not None and elapsed>spec.timeout_s*1000,claimed,delivery_result)
            if approval_used:self.permissions.finish(context,result)
            self._trace('tool.finished',context,name=name,status=result['status'],error=result.get('error'),
                        duration_ms=elapsed,result_chars=len(encoded(result)),truncated=result.get('truncated',False),
                        changes_count=len(result.get('changes',[])))
            return result
        except (sqlite3.Error,OSError):
            log.exception('Tool result recording failed',extra={'room':room,'job':job})
            self._trace('tool.record_failed',context,name=name,status='unknown')
            return failure('result_unconfirmed','実行履歴の保存を確認できません。自動再実行せず状態を確認してください。','unknown')

    def record_generation(self,room,job,metrics):
        with self.app.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO generation_metrics VALUES(?,?,?,?)',
                       (job,room,datetime.now(timezone.utc).isoformat(),encoded(metrics)))

    def listing(self,room):
        self.app.store.room_path(room)
        with self.app.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM tool_events WHERE room=? ORDER BY id DESC LIMIT 30',(room,))]
        # Existing rows retain their original result text and receive no invented timings.
        for row in rows:row['format_version']=1 if row['status'] else 0
        return rows
