"""Aggregate complete same-period episode files without changing source bytes."""
import json,threading
from contextlib import contextmanager
from datetime import date
from .episode_compression import EpisodeCompression,period_bounds
from litechat.episodes import fingerprint
from litechat.resources import strict_json
from litechat.retrieval import record_arousal
from litechat.file_editor import normalize,safe_path
from litechat.working_slots import slot_name

class CrossfileCompression(EpisodeCompression):
 def __init__(self,app):
  self.app,self.store=app,app.store;self.local=threading.local();self.cache={};self.warnings={}
  with self.store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS episode_crossfile_archives(room TEXT,draft_id INTEGER PRIMARY KEY,sources TEXT)')
 @contextmanager
 def context(self,names):
  if not isinstance(names,list) or not 2<=len(names)<=10 or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names):raise ValueError('異なるエピソードファイルを2～10件選んでください。')
  prior=getattr(self.local,'names',None);self.local.names=names
  try:yield
  finally:self.local.names=prior
 def inspect(self,room,names,target,week,mode='weekly'):
  with self.context(names):
   result=self.preview(room,target,week,mode)[0]
   return {k:v for k,v in result.items() if k not in ('crossfile_sources','dependencies')}
 def generate_bundle(self,room,names,target,week,revision,request_id,mode='weekly',*,background=False,before_start=None):
  with self.context(names):return super().generate(room,target,week,revision,request_id,mode,background=background,before_start=before_start)
 def preview(self,room,name,week,mode='weekly'):
  room=self.store.room_path(room).name;slot_name(name);names=self.local.names
  if name.startswith('@') or name in names:raise ValueError('入力元と異なる新しい保存名を指定してください。')
  target=self.app.resources.read(room,'episode',name)
  if target['exists']:raise ValueError('集約先は新しいファイル名を指定してください。')
  start,end=period_bounds(week,mode);sources=[];records=[];effective_all=[];total=0;excluded=self.excluded(room);affect=self.app.memory_affect.settings(room)
  for source in names:
   doc=self.app.resources.read(room,'episode',source)
   if not doc['exists'] or doc['path'] in excluded:raise ValueError('未集約の既存ファイルを選択してください。')
   total+=len(doc['text'])
   if total>200000:raise ValueError('入力の合計は20万文字までです。')
   entries=strict_json(doc['text'])
   if not isinstance(entries,list) or not entries:raise ValueError('空でないJSON配列のエピソードが必要です。')
   effective_values=self.app.memory_resonance.record_values(room,doc['path'],entries);effective_all.append(effective_values)
   for entry_index,entry in enumerate(entries):
    if isinstance(entry,dict) and affect['arousal_protection'] and (effective_values[entry_index] or 0)>=.7:raise ValueError('高Arousalの記録を含むため横断集約を見送りました。保護設定を確認してください。')
    if not isinstance(entry,dict) or not isinstance(entry.get('summary'),str) or not entry['summary'].strip():raise ValueError('エピソード本文が不正です。')
    try:day=date.fromisoformat(entry.get('date',''));last=date.fromisoformat(entry['period_end'] if mode=='monthly' else entry.get('period_end',entry['date']))
    except (ValueError,TypeError,KeyError):raise ValueError('記録の期間が不正です。') from None
    compressed=entry.get('akari_compression')
    allowed=not compressed and not entry.get('compressed') if mode=='weekly' else isinstance(compressed,dict) and compressed.get('version')==1 and compressed.get('mode','weekly')=='weekly' and entry.get('type')=='weekly_summary'
    if not allowed or not start<=day<=last<=end:raise ValueError('各ファイルの全記録が対象期間・圧縮段階に収まるものを選択してください。')
   sources.append(dict(name=source,path=doc['path'],revision=doc['revision'],records=entries));records.extend(entries)
  if not 2<=len(records)<=40:raise ValueError('記録の合計は2～40件です。')
  material=json.dumps([dict(date=r['date'],summary=r['summary'],**({'period_end':r['period_end']} if mode=='monthly' else {})) for r in records],ensure_ascii=False)
  revision=fingerprint([room,name,week,mode,sources,self.model(room)])
  if affect['arousal_protection']:revision=fingerprint([revision,affect['revision'],effective_all])
  if self.app.memory_resonance.settings(room)['memory']:revision=fingerprint([revision,effective_all])
  return dict(resonance_values=[v for source in effective_all for v in source],room=room,kind='crossfile',names=names,name=name,week=week,compression_mode=mode,revision=revision,source_hash=revision,source=material,count=len(records),crossfile_sources=sources,dependencies=[dict(kind='episode',name=s['name'],revision=s['revision']) for s in sources],warning='元ファイルを変更せず新しい要約案を作ります。承認後、元と要約が一致する間だけ検索では要約へまとめます。'),target,records,list(enumerate(records))
 def operation_signature(self,name,week,revision,mode):return fingerprint([name,week,revision,mode,self.local.names])
 def metadata(self,preview):
  sources=preview['crossfile_sources']
  return dict(files=[{k:v for k,v in s.items() if k!='records'}|{'count':len(s['records'])} for s in sources],originals_hash=fingerprint([r for s in sources for r in s['records']]))
 def decorate(self,aggregate,preview):
  aggregate['akari_crossfile']={'version':1,**self.metadata(preview)};return aggregate
 def record_draft(self,db,room,key,preview,text):
  if len(text.encode())>1048576:raise ValueError('元の全項目を保持した集約結果が1MiBを超えます。')
  if db.execute('SELECT COUNT(*) FROM episode_crossfile_archives WHERE room=?',(room,)).fetchone()[0]>=500:raise ValueError('横断集約の履歴は1ルーム500件までです。')
  db.execute('INSERT INTO episode_crossfile_archives VALUES(?,?,?)',(room,key,json.dumps(self.metadata(preview),ensure_ascii=False)))
 def context_selection(self,room):
  with self.store.lock:
   excluded=self.excluded(room);warning=self.warnings.get(room)
   return excluded,([warning] if warning else [])
 def excluded(self,room):
  with self.store.lock:return self._excluded(room)
 def _excluded(self,room):
  with self.store.connect() as db:rows=db.execute("SELECT d.name,d.id,a.sources FROM episode_crossfile_archives a JOIN resource_drafts d ON d.id=a.draft_id WHERE a.room=? AND d.room=a.room AND d.status='approved' ORDER BY d.id LIMIT 501",(room,)).fetchall()
  if not rows:return set()
  self.warnings.pop(room,None)
  if len(rows)>500:self.warnings[room]='横断集約の確認上限です。元の記録を検索に残します。';return set()
  root=self.store.room_path(room);stamps=[]
  try:
   for row in rows:
    plan=strict_json(row[2]);paths=[self.app.resources.target('episode',row[0],room)[1]]+[s['path'] for s in plan['files']]
    for relative in paths:
     path=safe_path(root,relative)
     try:info=path.stat();stamp=(info.st_size,info.st_mtime_ns,info.st_ctime_ns,getattr(info,'st_ino',0))
     except FileNotFoundError:stamp=None
     stamps.append((relative,stamp))
  except (ValueError,OSError,TypeError,KeyError):self.warnings[room]='横断集約の資料を確認できません。元の記録を検索に残します。';return set()
  key=fingerprint([[tuple(r) for r in rows],stamps])
  if room in self.cache and self.cache[room][0]==key:
   if self.cache[room][2]:self.warnings[room]=self.cache[room][2]
   return set(self.cache[room][1])
  excluded=set();invalid=set();read_docs={};budget=0
  def read(name):
   nonlocal budget
   if name not in read_docs:
    read_docs[name]=self.app.resources.read(room,'episode',name);budget+=len(read_docs[name]['text'].encode())
    if budget>16*1048576:raise OverflowError('crossfile budget')
   return read_docs[name]
  for row in rows:
   try:
    target=read(row[0]);plan=strict_json(row[2])
    with self.store.connect() as db:entry=db.execute('SELECT text FROM resource_drafts WHERE room=? AND id=?',(room,row[1])).fetchone()
    records=strict_json(target['text']) if target['exists'] else []
    if not records or not entry or normalize(target['text'])!=normalize(entry[0]) or records[0].get('akari_crossfile')!={'version':1,**plan} or fingerprint(records[0].get('akari_compression_sources'))!=plan['originals_hash']:continue
    if any(s['path'] in invalid or read(s['name'])['revision']!=s['revision'] for s in plan['files']):excluded.add(target['path']);invalid.add(target['path'])
    else:excluded.update(s['path'] for s in plan['files'])
   except OverflowError:
    self.warnings[room]='横断集約の確認が16MiBを超えました。元の記録を検索に残します。'
    if len(self.cache)>=16:self.cache.pop(next(iter(self.cache)))
    self.cache[room]=(key,set(),self.warnings[room]);return set()
   except (ValueError,OSError,KeyError,TypeError):continue
  if len(self.cache)>=16:self.cache.pop(next(iter(self.cache)))
  self.cache[room]=(key,set(excluded),None);return excluded
