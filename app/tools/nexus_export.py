"""Reviewed, non-overwriting NexusArk character-folder snapshots. No Nexus runtime imports."""
import hashlib,json,re,threading,uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from litechat.file_editor import safe_path,EditConflict,decode
from litechat.storage import reject_links
from litechat.initial_log import RELATIVE as INITIAL_MESSAGE
from litechat.lifestyle import FILES

JST=timezone(timedelta(hours=9))
SKIP={'akari_data','backups','__pycache__','.git','faiss_index','faiss_index_static','faiss_index_dynamic'}
SECRET={'api_key','api_keys','token','access_token','refresh_token','client_secret','password','authorization','browser_state'}

def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')
def scrub(obj):
 if isinstance(obj,dict):return {k:scrub(v) for k,v in obj.items() if k.lower() not in SECRET and not k.lower().endswith(('_api_key','_api_keys','_access_token','_refresh_token'))}
 if isinstance(obj,list):return [scrub(v) for v in obj]
 return obj

def file_hash(path,checkpoint=lambda:None):
 h=hashlib.sha256()
 with path.open('rb') as source:
  while True:
   checkpoint();chunk=source.read(1048576)
   if not chunk:break
   h.update(chunk)
 return h.hexdigest()

class NexusExport:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.events={};self.lock=threading.RLock()
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS nexus_exports(request_id TEXT PRIMARY KEY,room TEXT,signature TEXT,state TEXT,result TEXT)')
   db.execute("UPDATE nexus_exports SET state='unknown' WHERE state='running'")
 def checkpoint(self,event):
  if self.app.closing or event.is_set():raise ValueError('エクスポートを中止しました。未完成フォルダは成功扱いにしません。')
 def cancel(self,request_id):
  with self.lock:
   event=self.events.get(request_id)
   if event:event.set()
  return {'cancel_requested':bool(event)}
 def snapshot(self,room,checkpoint=lambda:None):
  root=self.store.room_path(room);reject_links(root)
  if room not in {p.name for p in self.store.characters.iterdir()}:raise ValueError('一覧からルームを指定してください。')
  self.store.index_room(room);copies={};excluded=[]
  for path in sorted(root.rglob('*')):
   checkpoint();relative=path.relative_to(root).as_posix();parts=relative.split('/')
   if any(p.casefold() in SKIP for p in parts) or parts[0]=='logs' or relative=='log.txt' or relative.casefold()==INITIAL_MESSAGE.casefold() or path.suffix.lower() in ('.sqlite3','.db','.faiss','.pkl','.pyc','.pem') or path.name.casefold().startswith('.env') or path.name.casefold() in ('cookies.json','credentials.json','token.json','tokens.json','storage_state.json') or (parts[0]=='cache' and relative!='cache/temporary_location.json'):
    if path.is_file():excluded.append(relative)
    continue
   if path.is_file():copies[relative]={'path':path,'size':path.stat().st_size,'sha256':file_hash(path,checkpoint)}
  if 'room_config.json' not in copies:raise ValueError('room_config.jsonが必要です。')
  config=json.loads(decode((root/'room_config.json').read_bytes())['text']);generated={'room_config.json':encoded(scrub(config))}
  warnings=['会話ログはDBの現行履歴から再構築します。削除済み発言と旧ログの別コピーは含めません。','休眠/統合除外など、あかりのDBだけにある記憶状態は変換しません。','接続認証・検索索引・あかりのDB内実行履歴/確認待ち案は書き出しません。','秘密日記などキャラフォルダ内の私的な文章も含みます。','NexusArk側では記憶検索索引を再構築してください。','DB内の独自自律状態/Persona Contract/タイマー等はNexusArk形式に変換しません。']
  with self.store.connect() as db:
   db.execute('BEGIN')
   messages=[dict(r) for r in db.execute('SELECT id,role,content,status,created FROM messages WHERE room=? AND deleted=0 ORDER BY id',(room,))]
   life=self.store.lifestyle.state(room,db)
   if life['exists']:
    for key,relative in FILES.items():generated[relative]=encoded(life['data'][key])
    config=scrub(config);config.setdefault('override_settings',{})['user_closet_use_common']=False;generated['room_config.json']=encoded(config)
    if life['shared_user']:
     warnings.append('使用中の共通衣装は、このキャラ専用のコピーとして書き出します。')
   avatars=[dict(r) for r in db.execute('SELECT name,mime,raw,hidden FROM avatar_assets WHERE room=?',(room,))] if self.table(db,'avatar_assets') else []
   attachments=[dict(r) for r in db.execute('SELECT i.*,i.message_id AS target FROM images i JOIN messages m ON m.id=i.message_id WHERE i.room=? AND m.deleted=0 AND NOT EXISTS(SELECT 1 FROM attachment_states s WHERE s.image_id=i.id AND s.hidden=1)',(room,))]
   attachments += [dict(r) for r in db.execute('SELECT i.*,l.message_id AS target FROM images i JOIN message_attachment_links l ON l.image_id=i.id JOIN messages m ON m.id=l.message_id WHERE i.room=? AND m.deleted=0 AND NOT EXISTS(SELECT 1 FROM attachment_states s WHERE s.image_id=i.id AND s.hidden=1)',(room,))]
  names={};extensions={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif','video/mp4':'.mp4','video/webm':'.webm'}
  for asset in avatars:
   checkpoint()
   if not re.fullmatch(r'[\w-]{1,40}',asset['name']):raise ValueError('表情名の形式が不正です。')
   if asset['hidden'] or asset['raw'] is not None:
    for suffix in extensions.values():copies.pop('avatar/'+asset['name']+suffix,None)
   if not asset['hidden'] and asset['raw'] is not None:generated['avatar/'+asset['name']+extensions[asset['mime']]]=bytes(asset['raw'])
  if avatars:
   expression=json.loads(decode((root/'expressions.json').read_bytes())['text']) if (root/'expressions.json').is_file() else {}
   hidden={a['name'] for a in avatars if a['hidden']};entries=list(dict.fromkeys(expression.get('expressions',[])+[a['name'] for a in avatars if not a['hidden']]))
   expression['expressions']=[n for n in entries if n not in hidden]
   if expression.get('default_expression') not in expression['expressions']:expression['default_expression']=next(iter(expression['expressions']),'idle')
   generated['expressions.json']=encoded(expression)
  for image in attachments:
   checkpoint();path=self.store.image_path(room,image['id'],image['mime']);relative='attachments/'+image['id']+extensions[image['mime']]
   if not path.is_file():raise ValueError('添付画像の実体が欠けています。')
   copies[relative]={'path':path,'size':path.stat().st_size,'sha256':file_hash(path,checkpoint)};names.setdefault(image['target'],[]).append(relative)
  if attachments:warnings.append('添付画像はattachmentsへ保持します。ログには相対パスを記載しますが、NexusArkの画像再入力としての再現は未対応です。')
  logs={};escapes=0
  for message in messages:
   checkpoint();body=message['content'];match=re.match(r'^([0-9]{4}-[0-9]{2}-[0-9]{2}) \(.*?\) ([0-9]{2}:[0-9]{2}:[0-9]{2})(?: \|.*)?$',body,re.M)
   if match:stamp=datetime.fromisoformat(match[1]+'T'+match[2]);line=match[0];body=body[:match.start()]+body[match.end():];body=body.strip('\r\n')
   else:
    stamp=datetime.fromisoformat(message['created'].replace('Z','+00:00'))
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
    stamp=stamp.astimezone(JST);line=stamp.strftime('%Y-%m-%d (%a) %H:%M:%S')
   role={'user':'USER','assistant':'AGENT','system':'SYSTEM'}.get(message['role'])
   if not role:raise ValueError('未対応の発言ロールです。')
   escapes+=len(re.findall(r'^## (?:USER|AGENT|SYSTEM):',body,re.M));body=re.sub(r'^(## (?:USER|AGENT|SYSTEM):)',r' \1',body,flags=re.M)
   header=config.get('user_display_name') or 'ユーザー' if role=='USER' else config.get('agent_display_name') or room if role=='AGENT' else 'system'
   header=str(header).replace('\n',' ').replace('\r',' ')
   if names.get(message['id']):body+='\n\n[添付画像: '+', '.join(names[message['id']])+']'
   if message['status']!='complete':body+='\n\n[あかりでの生成状態: '+message['status']+']'
   logs.setdefault(stamp.strftime('%Y-%m'),[]).append('## '+role+':'+header+'\n'+line+'\n'+body+'\n')
  for month,entries in logs.items():generated['logs/'+month+'.txt']='\n'.join(entries).encode('utf8')
  if escapes:warnings.append('ログ内の発言見出しと同じ行は、誤分割防止のため先頭に空白を追加します。')
  for relative,item in list(copies.items()):
   native=relative.split('/')[0] in ('memory','notes','knowledge','spaces') or relative in ('SystemPrompt.txt','core_memory.txt','current_location.txt')
   if native and Path(relative).suffix.lower() in ('.txt','.md','.json') and relative not in generated:
    if item['size']>32*1048576:raise ValueError('互換変換するテキストは1ファイル32MiBまでです。')
    raw=item['path'].read_bytes();decoded=decode(raw)
    if decoded['encoding']!='utf-8' or decoded['bom']:generated[relative]=decoded['text'].encode('utf8')
  for key in generated:copies.pop(key,None)
  size=sum(x['size'] for x in copies.values())+sum(map(len,generated.values()))
  if size>2*1024**3:raise ValueError('1回の書出し上限2GiBを超えています。')
  source={k:{f:v for f,v in item.items() if f!='path'} for k,item in copies.items()}
  signature=digest(encoded([source,{k:digest(v) for k,v in generated.items()},messages,excluded]))
  return dict(room=room,revision=signature,copies=copies,generated=generated,excluded=excluded,warnings=warnings,files=len(copies)+len(generated),bytes=size,messages=len(messages))
 @staticmethod
 def table(db,name):return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
 def preview(self,room):
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job:raise ValueError('生成処理の完了後に確認してください。')
   snap=self.snapshot(room)
   return {k:snap[k] for k in ('room','revision','files','bytes','messages','excluded','warnings')}
 def write(self,room,expected,request_id):
  if not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
  signature=digest(encoded([room,expected]));event=threading.Event()
  with self.app.lock,self.store.lock:
   with self.store.connect() as db:old=db.execute('SELECT * FROM nexus_exports WHERE request_id=?',(request_id,)).fetchone()
   if old:
    if old['signature']!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
    if old['state']!='complete':raise EditConflict('前回の書出しは未完了または結果不明です。出力フォルダを確認してください。自動再実行しません。')
    result=json.loads(old['result']);report_path=Path(result['manifest']);base=safe_path(self.store.root.parent.parent,'Exports/NexusArk');relative=report_path.relative_to(base)
    safe_path(base,relative);report=json.loads(report_path.read_text('utf8'));target=Path(result['path']);reject_links(target)
    for name,sha in report['files'].items():
     if file_hash(safe_path(target,name))!=sha:raise EditConflict('前回の出力が変更されています。上書きや自動再作成はしません。')
    return result
   if self.app.closing or self.app.job:raise ValueError('生成処理の完了後に書き出してください。')
   with self.lock:self.events[request_id]=event
   checkpoint=lambda:self.checkpoint(event)
   stage=None
   try:
    snap=self.snapshot(room,checkpoint)
    if snap['revision']!=expected:raise EditConflict('確認後にデータが変わりました。内容を再確認してください。')
    base=safe_path(self.store.root.parent.parent,'Exports/NexusArk');base.mkdir(parents=True,exist_ok=True)
    key=datetime.now(JST).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:12];stage=safe_path(base,'.pending-'+key);final=safe_path(base,key)
    with self.store.connect() as db:db.execute("INSERT INTO nexus_exports VALUES(?,?,?,'running','{}')",(request_id,room,signature))
    target=stage/'characters'/room;target.mkdir(parents=True);manifest={}
    for directory in ('memory/episodic','memory/dreaming','notes/working_memories','knowledge','spaces','items','closet','avatar','logs'):(target/directory).mkdir(parents=True,exist_ok=True)
    for relative,item in snap['copies'].items():
     checkpoint();out=safe_path(target,relative);out.parent.mkdir(parents=True,exist_ok=True)
     with item['path'].open('rb') as source,out.open('xb') as dest:
      while True:
       checkpoint();chunk=source.read(1048576)
       if not chunk:break
       dest.write(chunk)
     if file_hash(out,checkpoint)!=item['sha256']:raise EditConflict('コピー中に原本が変化しました。')
     manifest[relative]=item['sha256']
    for relative,raw in snap['generated'].items():
     checkpoint();out=safe_path(target,relative);out.parent.mkdir(parents=True,exist_ok=True)
     with out.open('xb') as dest:dest.write(raw)
     manifest[relative]=digest(raw)
    if self.snapshot(room,checkpoint)['revision']!=expected:raise EditConflict('書出し中に原本が変わりました。未完成フォルダを確認してください。')
    for relative,sha in manifest.items():
     if file_hash(safe_path(target,relative),checkpoint)!=sha:raise ValueError('出力ファイルの照合に失敗しました。')
    checkpoint();report=dict(format='akari-nexus-character-folder-v1',room=room,verified=True,source_revision=expected,files=manifest,warnings=snap['warnings'],excluded=snap['excluded'])
    (stage/'manifest.json').write_bytes(encoded(report));stage.rename(final)
    result=dict(verified=True,path=str(final/'characters'/room),manifest=str(final/'manifest.json'),files=len(manifest),warnings=snap['warnings'])
    with self.store.connect() as db:db.execute("UPDATE nexus_exports SET state='complete',result=? WHERE request_id=?",(json.dumps(result,ensure_ascii=False),request_id))
    return result
   except Exception:
    if stage and stage.exists():
     (stage/'INCOMPLETE.txt').write_text('未完了の書出しです。NexusArkへ取り込まないでください。','utf8')
     (stage/'manifest.json').write_bytes(encoded(dict(verified=False,room=room,message='未完成の出力です。取り込まないでください。')))
    with self.store.connect() as db:db.execute("UPDATE nexus_exports SET state='unknown' WHERE request_id=? AND state='running'",(request_id,))
    raise
   finally:
    with self.lock:self.events.pop(request_id,None)
