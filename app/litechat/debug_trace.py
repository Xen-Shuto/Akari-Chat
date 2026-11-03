"""Opt-in, bounded JSONL tracing. Never serializes complete prompts or credentials."""
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from .diagnostics import redact
from .file_editor import safe_path

SECRET=re.compile(r'api.?key|token|password|secret|authorization|cookie|credential|client.?secret',re.I)
CONTENT={'text','body','prompt','message','summary','description','intent','details','evidence','quote',
         'old_text','reason','findings','lesson','outcome','resolution','note','steps','arguments_raw'}


def enabled_from_environment(environment=None):
    environment=os.environ if environment is None else environment
    return environment.get('DEBUG_MODE','').casefold()=='y'


def clean(value,key='',depth=0):
    if SECRET.search(key):return '[redacted]'
    if depth>8:return '[depth limit]'
    if key in CONTENT:
        raw=json.dumps(value,ensure_ascii=False,default=lambda v:type(v).__name__)
        return {'omitted_content':True,'characters':len(raw),'sha256':hashlib.sha256(raw.encode()).hexdigest()}
    if key=='arguments_json':
        try:return clean(json.loads(value),'arguments',depth+1)
        except (ValueError,TypeError):return '[invalid JSON omitted]'
    if isinstance(value,dict):return {redact(k,80):clean(v,str(k),depth+1) for k,v in list(value.items())[:80]}
    if isinstance(value,(list,tuple)):return [clean(v,'',depth+1) for v in value[:80]]
    if isinstance(value,str):
        value=redact(value,1200)
        value=re.sub(r'(?i)\b(?:sk-[a-z0-9_-]{8,}|AIza[a-z0-9_-]{12,})','[redacted]',value)
        # URL userinfo and arbitrary query values can carry credentials.
        value=re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@',r'\1[redacted]@',value)
        value=re.sub(r'([?&][^\s=&]+)=([^\s&#]*)',r'\1=[redacted]',value)
        return value
    if value is None or type(value) in (int,float,bool):return value
    return '['+type(value).__name__+']'


class DebugTrace:
    def __init__(self,app_dir,enabled=False):
        self.enabled=bool(enabled);self.lock=threading.RLock();self.handler=None
        self.session=uuid.uuid4().hex;self.sequence=0;self.path=None;self.closed=False
        if not self.enabled:return
        # A private handler: no root/library logging level changes or HTTP wire dumps.
        data=safe_path(app_dir,'data');data.mkdir(exist_ok=True)
        directory=safe_path(data,'debug');directory.mkdir(exist_ok=True)
        self.path=safe_path(directory,'debug.jsonl')
        for suffix in ('','.1','.2','.3'):safe_path(directory,'debug.jsonl'+suffix)
        self.handler=RotatingFileHandler(self.path,maxBytes=4*1024*1024,backupCount=3,encoding='utf-8',delay=True)
        self.record('debug.started')

    def record(self,event,room='',job='',**fields):
        if not self.enabled or self.closed:return
        try:
            with self.lock:
                if self.closed:return
                self.sequence+=1
                row=dict(time=datetime.now(timezone.utc).isoformat(),session=self.session,sequence=self.sequence,
                         pid=os.getpid(),thread=threading.current_thread().name,event=event,room=room,job=job,**fields)
                line=json.dumps(clean(row),ensure_ascii=False,allow_nan=False,separators=(',',':'))
                record=logging.LogRecord('akari.debug',logging.DEBUG,'',0,line,(),None)
                self.handler.emit(record)
        except Exception:
            # Tracing must not interrupt generation, writes, receipts, or shutdown.
            pass

    def close(self):
        with self.lock:
            if self.closed:return
            self.record('debug.closed');self.closed=True
            if self.handler:self.handler.close()
