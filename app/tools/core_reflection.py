"""Lossless identity-to-core proposals; diary summaries are left unchanged."""
import hashlib,json,re
from litechat.file_editor import EditConflict,normalize
from .contracts import ToolSpec,ToolOutput
from .schema import schema
HEADER='--- [永続記憶 (Permanent) - 要約せずそのまま記載] ---'
BEGIN='<!-- AKARI_IDENTITY_BEGIN -->'
END='<!-- AKARI_IDENTITY_END -->'

def reflect(core,identity):
 core,identity=normalize(core),normalize(identity)
 if not identity.strip():raise ValueError('永続記憶が空です。')
 if BEGIN in identity or END in identity:raise ValueError('永続記憶に管理マーカーがあるため反映を見送りました。')
 if core.count(BEGIN) or core.count(END):
  if core.count(BEGIN)!=1 or core.count(END)!=1 or core.index(BEGIN)>=core.index(END):raise ValueError('コアの管理範囲が曖昧です。本文を確認してください。')
  return core[:core.index(BEGIN)]+BEGIN+'\n'+identity+'\n'+END+core[core.index(END)+len(END):]
 block=HEADER+'\n'+BEGIN+'\n'+identity+'\n'+END+'\n\n'
 if core.count(HEADER)>1:raise ValueError('永続記憶の節が複数あります。本文を確認してください。')
 if HEADER not in core:return block+core
 start=core.index(HEADER);rest=core[start+len(HEADER):];following=re.search(r'(?m)^--- \[.+\] ---[ \t]*$',rest)
 end=start+len(HEADER)+following.start() if following else len(core)
 return core[:start]+block+core[end:]

class CoreReflection:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS core_reflections(room TEXT,request_id TEXT,signature TEXT,draft_id INTEGER,PRIMARY KEY(room,request_id))')
 def preview(self,room):
  with self.store.lock:
   core=self.app.resources.read(room,'core');identity=self.app.resources.read(room,'identity')
   text=reflect(core['text'],identity['text']);revision=hashlib.sha256((core['revision']+identity['revision']).encode()).hexdigest()
   return dict(room=room,revision=revision,text=text,core_revision=core['revision'],identity_revision=identity['revision'])
 def propose(self,room,revision,request_id,checkpoint=lambda:None):
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  with self.store.lock,self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,draft_id FROM core_reflections WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=revision:raise EditConflict('同じ操作IDの資料が異なります。')
    return dict(id=old[1],state='pending_or_reviewed')
   preview=self.preview(room)
   if preview['revision']!=revision:raise EditConflict('永続記憶かコアが変更されました。再確認してください。')
   if len(preview['text'].encode())>1048576:raise ValueError('反映後のコアが1MiBを超えます。')
   dependencies=[dict(kind='identity',name='',revision=preview['identity_revision'])]
   checkpoint();key=db.execute("INSERT INTO resource_drafts(room,kind,name,revision,source_hash,text,dependencies) VALUES(?,'core','',?,'',?,?)",(room,preview['core_revision'],preview['text'],json.dumps(dependencies))).lastrowid
   db.execute('INSERT INTO core_reflections VALUES(?,?,?,?)',(room,request_id,revision,key));return dict(id=key,state='pending')

def propose(app,context,args):
 preview=app.core_reflection.preview(context.room)
 if args['revision'] and args['revision']!=preview['revision']:raise EditConflict('資料の版が変わりました。')
 result=app.core_reflection.propose(context.room,preview['revision'],hashlib.sha256((context.job_id+':'+context.call_id).encode()).hexdigest(),context.checkpoint)
 return ToolOutput(status='pending',data=result,message='永続記憶の全文をコアへ反映する確認案です。日記要約は変更せず、確認保存前は未反映です。')
ENTRIES=[ToolSpec(schema('propose_identity_to_core','永続記憶を要約せず全文でコアの該当節へ反映する確認案を作る。日記の要約節は保持。',{'revision':{'type':'string','maxLength':64,'default':''}},[]),propose,category='memory',operation='propose',effects=('draft',))]
