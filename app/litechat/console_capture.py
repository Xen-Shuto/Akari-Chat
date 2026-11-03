"""Bounded tee for this Python process only; original streams stay open."""
import sys
import threading
from collections import OrderedDict


class Tee:
    def __init__(self,owner,stream,channel):self.owner,self.stream,self.channel=owner,stream,channel
    def write(self,text):
        result=self.stream.write(text)  # Preserve terminal behavior and write errors.
        try:self.owner.feed(self.channel,text[:result] if type(result) is int and 0<=result<len(text) else text)
        except Exception:pass  # Diagnostics must not fail the caller.
        return result
    def writelines(self,lines):
        for line in lines:self.write(line)
    def flush(self):
        result=self.stream.flush()
        try:self.owner.flush(self.channel)
        except Exception:pass
        return result
    def __getattr__(self,name):return getattr(self.stream,name)


class ConsoleCapture:
    def __init__(self,diagnostics):
        self.diagnostics=diagnostics;self.lock=threading.RLock();self.local=threading.local();self.pending=OrderedDict();self.closed=False;self.streams={}
    def install(self):
        with self.lock:
            if self.closed:raise ValueError('終了済みのコンソール記録です。')
            if self.streams:return self
            if any(isinstance(getattr(sys,name),Tee) for name in ('stdout','stderr')):raise ValueError('別のコンソール記録が使用中です。')
            for channel in ('stdout','stderr'):
                original=getattr(sys,channel)
                if original is not None:
                    wrapper=Tee(self,original,channel);self.streams[channel]=(original,wrapper);setattr(sys,channel,wrapper)
        return self
    def emit(self,channel,text):
        if text:
            try:self.diagnostics.record('ERROR' if channel=='stderr' else 'INFO',channel,text,source='console')
            except Exception:pass
    def feed(self,channel,text):
        if not text or getattr(self.local,'active',False):return
        self.local.active=True
        try:
            with self.lock:
                if self.closed:return
                key=(threading.current_thread(),channel);prior,dropping=self.pending.pop(key,('',False))
                if len(text)>16384 or text.count('\n')>64:
                    self.emit(channel,'（大量のコンソール出力を省略しました）');prior='';dropping=not text.endswith(('\n','\r'))
                else:
                    for part in text.replace('\r\n','\n').replace('\r','\n').splitlines(keepends=True):
                        newline=part.endswith('\n');piece=part[:-1] if newline else part
                        if not dropping:
                            if len(prior)+len(piece)>4096:
                                self.emit(channel,'（長いコンソール行を省略しました）');prior='';dropping=True
                            else:prior+=piece
                        if newline:
                            if not dropping:self.emit(channel,prior)
                            prior='';dropping=False
                if prior or dropping:self.pending[key]=(prior,dropping)
                while len(self.pending)>32:
                    old,(value,skip)=self.pending.popitem(last=False)
                    self.emit(old[1],'（未完了のコンソール行を保持上限により省略しました）')
        finally:self.local.active=False
    def flush(self,channel):
        if getattr(self.local,'active',False):return
        self.local.active=True
        try:
            with self.lock:
                key=(threading.current_thread(),channel);value,skip=self.pending.pop(key,('',False))
                if not self.closed and value and not skip:self.emit(channel,value)
                if skip:self.pending[key]=('',True)
        finally:self.local.active=False
    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            for (thread,channel),(text,skip) in self.pending.items():
                if text and not skip:self.emit(channel,text)
            self.pending.clear()
            for channel,(original,wrapper) in self.streams.items():
                if getattr(sys,channel) is wrapper:setattr(sys,channel,original)
