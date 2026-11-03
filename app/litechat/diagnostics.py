"""Bounded local diagnostics. Viewing/clearing never deletes durable records."""
import hashlib
import json
import logging
import math
import re
import threading
import traceback
import uuid
from collections import deque
from datetime import datetime,timezone
from .file_editor import safe_path


def redact(value,limit=1000):
    text=str(value)[:20000]
    text=re.sub(r'(?i)\bBearer\s+[^\s"\',;}]+','Bearer [redacted]',text)
    text=re.sub(r'(?i)(\b(?:api[_-]?key|access[_-]?token|token|password|authorization|x-app-token)\b["\']?\s*[:=]\s*["\']?)([^\s"\'&,;}]+)',r'\1[redacted]',text)
    text=re.sub(r'(?i)([?&]key=)[^\s&"\']+',r'\1[redacted]',text)
    return text[:limit]+('…（省略）' if len(text)>limit else '')


class Capture(logging.Handler):
    def __init__(self,owner):super().__init__(logging.INFO);self.owner=owner
    def emit(self,record):
        try:
            self.owner.record(record.levelname,record.name,self.format(record),getattr(record,'room',''),getattr(record,'job',''))
            exception={}
            if record.exc_info and record.exc_info[0]:
                exception=dict(error_type=record.exc_info[0].__name__,error=str(record.exc_info[1]),
                    stack=[dict(file=frame.filename,line=frame.lineno,function=frame.name)
                           for frame in traceback.extract_tb(record.exc_info[2])])
            self.owner.trace('python.log',getattr(record,'room',''),getattr(record,'job',''),
                             level=record.levelname,category=record.name,log_message=record.getMessage(),exception=exception)
        except Exception:pass  # Diagnostic failures must never interrupt the caller.


class Diagnostics:
    def __init__(self,app,debug=False):
        from .debug_trace import DebugTrace
        self.debug_trace=DebugTrace(app.app_dir,enabled=debug)
        self.app,self.store=app,app.store;self.session=uuid.uuid4().hex;self.lock=threading.RLock();self.rows=deque(maxlen=200);self.console_rows=deque(maxlen=200);self.console=None;self.evicted={'runtime':0,'console':0};self.seq=0;self.closed=False
        with self.store.connect() as db:
            db.execute('CREATE INDEX IF NOT EXISTS diagnostic_tool_room ON tool_events(room,id)')
            db.execute('CREATE INDEX IF NOT EXISTS diagnostic_metric_room ON generation_metrics(room)')
        self.handler=Capture(self)
        for namespace in ('litechat','tools'):logging.getLogger(namespace).addHandler(self.handler)
        self.record('INFO','lifecycle','アプリの初期化が完了しました。')

    def trace(self,event,room='',job='',**fields):
        self.debug_trace.record(event,room,job,**fields)

    def record(self,level,category,message,room='',job='',source='runtime'):
        with self.lock:
            if self.closed:return
            buffer=self.console_rows if source=='console' else self.rows
            if len(buffer)==buffer.maxlen:self.evicted[source]=buffer[0]['id']
            self.seq+=1;buffer.append(dict(id=self.seq,created=datetime.now(timezone.utc).isoformat(),level=level,category=redact(category,120),message=redact(message),room=redact(room,120),job=redact(job,80)))

    def capture_console(self):
        from .console_capture import ConsoleCapture
        with self.lock:
            if self.closed:raise ValueError('終了済みの診断記録です。')
            if self.console is None:self.console=ConsoleCapture(self).install()
            return self.console

    def close(self):
        if self.console:self.console.close()
        with self.lock:
            if self.closed:return
            self.closed=True
        for namespace in ('litechat','tools'):logging.getLogger(namespace).removeHandler(self.handler)
        self.handler.close()
        self.debug_trace.close()

    def read(self,source,room='',before=0,after=0,epoch='',query='',level='all'):
        if source not in ('runtime','console','engine','tools','metrics') or type(before) is not int or type(after) is not int or not 0<=before<2**63 or not 0<=after<2**63:
            raise ValueError('診断ログの種類または範囲が不正です。')
        if not isinstance(query,str) or len(query)>100 or level not in ('all','INFO','WARNING','ERROR'):raise ValueError('診断ログの絞り込み条件が不正です。')
        if not isinstance(epoch,str) or len(epoch)>100:raise ValueError('診断ログの表示位置が不正です。')
        if source in ('tools','metrics'):self.store.room_path(room)
        if source=='engine':return self.engine(after,epoch)
        reset=bool(epoch and epoch!=self.session)
        if reset:after=before=0
        if source in ('runtime','console'):
            with self.lock:
                buffer=self.console_rows if source=='console' else self.rows
                rows=[dict(row) for row in reversed(buffer) if row['id']>after and (not before or row['id']<before)]
                watermark=self.seq;evicted=bool(after and after<self.evicted[source])
            rows=[row for row in rows if (level=='all' or row['level']==level or level=='ERROR' and row['level']=='CRITICAL') and (not query or query.casefold() in json.dumps(row,ensure_ascii=False).casefold())]
        else:
            with self.store.connect() as db:
                if source=='tools':
                    rows=[dict(row) for row in db.execute('''SELECT id,created,substr(job,1,80) AS job,substr(name,1,120) AS name,substr(call_id,1,80) AS call_id,status,duration_ms,input_chars,result_chars,over_budget
                        FROM tool_events WHERE room=? AND id>? AND (?=0 OR id<?) ORDER BY id DESC LIMIT 51''',(room,after,before,before))]
                    watermark=db.execute('SELECT COALESCE(MAX(id),0) FROM tool_events WHERE room=?',(room,)).fetchone()[0]
                else:
                    rows=[dict(row) for row in db.execute('''SELECT rowid AS id,created,substr(job,1,80) AS job,substr(metrics,1,32768) AS metrics FROM generation_metrics
                        WHERE room=? AND rowid>? AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT 51''',(room,after,before,before))]
                    watermark=db.execute('SELECT COALESCE(MAX(rowid),0) FROM generation_metrics WHERE room=?',(room,)).fetchone()[0]
            for row in rows:
                if source=='metrics':
                    try:
                        data=json.loads(row.pop('metrics'))
                        if not isinstance(data,dict):raise ValueError('invalid metrics')
                    except ValueError:data={};row['note']='計測データを読み取れませんでした。'
                    row['metrics']={key:value for key,value in data.items() if key in ('preprocess_seconds','model_calls','model_seconds','tool_calls','tool_seconds','first_text_seconds','first_after_tools_seconds','tool_result_chars','persistence') and (type(value) is int and abs(value)<10**15 or type(value) is float and math.isfinite(value) or key=='persistence' and value in ('saved','failed') or value is None)}
                for key,value in list(row.items()):
                    if isinstance(value,str):row[key]=redact(value,200)
            # DB pages are bounded before display filtering; never silently skip an unbounded history.
        scanned=rows[:50];more=len(rows)>50
        next_before=scanned[-1]['id'] if more else 0
        if source not in ('runtime','console') and query:scanned=[row for row in scanned if query.casefold() in json.dumps(row,ensure_ascii=False).casefold()]
        return dict(source=source,scope='アプリ全体' if source in ('runtime','console') else room,entries=scanned,watermark=watermark,next_before=next_before,epoch=self.session,reset=reset,evicted=evicted if source in ('runtime','console') else False)

    def engine(self,after,epoch):
        path=safe_path(self.app.app_dir/'data','llama-server.log')
        try:
            with path.open('rb') as stream:
                import os
                stat=os.fstat(stream.fileno());identity=hashlib.sha256(str((stat.st_dev,stat.st_ino)).encode()).hexdigest()[:12]
                saved=re.fullmatch(r'([a-f0-9]{12}):(\d{1,2}):([a-f0-9]{64})',epoch)
                reset=bool(epoch and (not saved or saved[1]!=identity or after>stat.st_size or int(saved[2])>min(64,stat.st_size)))
                prefix_size=int(saved[2]) if saved and not reset else min(64,stat.st_size)
                stream.seek(0);prefix=stream.read(prefix_size)
                def signature(position):
                    stream.seek(max(0,position-64));tail=stream.read(min(64,position))
                    return hashlib.sha256(prefix+b'\0'+tail).hexdigest()
                if epoch and not reset and signature(after)!=saved[3]:reset=True
                if reset:
                    after=0;prefix_size=min(64,stat.st_size);stream.seek(0);prefix=stream.read(prefix_size)
                start=max(after,stat.st_size-16000);stream.seek(start);raw=stream.read(min(16000,max(0,stat.st_size-start)))
                watermark=start+len(raw);current=identity+':'+str(prefix_size)+':'+signature(watermark)
                return dict(source='engine',scope='推論サーバー全体',text=redact(raw.decode('utf-8',errors='replace'),16000),watermark=watermark,epoch=current,reset=reset,truncated=start>after,next_before=0)
        except FileNotFoundError:return dict(source='engine',scope='推論サーバー全体',text='',watermark=0,epoch='',reset=bool(epoch),truncated=False,next_before=0)
