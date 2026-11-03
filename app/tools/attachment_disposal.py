"""Explicit reversible quarantine, then separately confirmed permanent disposal."""
import hashlib,json,os,re,stat,uuid
from litechat.episodes import fingerprint
from litechat.autonomy import now
from litechat.file_editor import safe_path,EditConflict
from litechat.multimodal import MAX_IMAGE_BYTES

class AttachmentDisposal:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS attachment_disposals(id TEXT PRIMARY KEY,room TEXT,image_id TEXT,payload TEXT)')
   db.execute('CREATE INDEX IF NOT EXISTS disposal_room ON attachment_disposals(room,image_id)')
   db.execute('CREATE TABLE IF NOT EXISTS attachment_disposal_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
   db.execute("CREATE INDEX IF NOT EXISTS disposal_phase ON attachment_disposals(json_extract(payload,'$.state'))")
   for row in db.execute("SELECT id,payload FROM attachment_disposals WHERE json_extract(payload,'$.state') IN ('archiving','restoring','purging')").fetchall():
    job=json.loads(row[1])
    if job['state'] in ('archiving','restoring','purging'):
     job.update(state='unknown',error='前回のファイル操作を確認してください。自動再実行しません。',revision=uuid.uuid4().hex);db.execute('UPDATE attachment_disposals SET payload=? WHERE id=?',(json.dumps(job),row[0]))
 def path(self,key):
  if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('退避IDが不正です。')
  return safe_path(self.store.root,'attachment_disposals/'+key+'.bin')
 def file(self,path):
  try:
   info=path.lstat()
   if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('通常の単独ファイルだけを操作できます。')
   with path.open('rb') as stream:raw=stream.read(MAX_IMAGE_BYTES+1)
   if len(raw)>MAX_IMAGE_BYTES:raise ValueError('退避画像の上限を超えています。')
   return raw
  except FileNotFoundError:return None
 def get(self,room,key):
  self.store.room_path(room);self.path(key)
  with self.store.connect() as db:row=db.execute('SELECT payload FROM attachment_disposals WHERE room=? AND id=?',(room,key)).fetchone()
  if not row:raise ValueError('このルームの退避記録がありません。')
  return json.loads(row[0])
 def listing(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:return [json.loads(r[0]) for r in db.execute("SELECT payload FROM attachment_disposals WHERE room=? ORDER BY (json_extract(payload,'$.state') NOT IN ('restored','purged')) DESC,rowid DESC LIMIT 50",(room,))]
 def document(self,room,key):
  with self.store.lock:
   job=self.get(room,key);image=self.app.attachments.document(room,job['image_id']);source=self.file(self.store.image_path(room,job['image_id'],job['mime']));staged=self.file(self.path(key))
   hashes=[hashlib.sha256(raw).hexdigest() if raw is not None else None for raw in (source,staged)]
   return dict(job,image_revision=image['revision'],hidden=bool(image['hidden']),source_exists=source is not None,staged_exists=staged is not None,source_matches=hashes[0]==job['sha256'],staged_matches=hashes[1]==job['sha256'],revision=fingerprint([job,image['revision'],hashes]))
 def write(self,db,job):
  job['revision']=uuid.uuid4().hex;job['updated_at']=now();db.execute('INSERT OR REPLACE INTO attachment_disposals VALUES(?,?,?,?)',(job['id'],job['room'],job['image_id'],json.dumps(job,ensure_ascii=False)))
 def copy(self,path,raw):
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
 def change(self,room,key,revision,action,request_id,confirm_permanent=False,checkpoint=lambda:None):
  room=self.store.room_path(room).name
  if action not in ('archive','restore','purge','recover') or not isinstance(request_id,str) or not re.fullmatch('[\w-]{1,128}',request_id):raise ValueError('退避の操作または操作IDが不正です。')
  if action=='purge' and confirm_permanent is not True:raise ValueError('完全削除と復元不可を確認してください。')
  signature=fingerprint([key,revision,action,confirm_permanent])
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成が終わってから整理してください。')
   with self.store.connect() as db:
    receipt=db.execute('SELECT signature,result FROM attachment_disposal_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
    if receipt:
     if receipt[0]!=signature:raise EditConflict('同じ操作IDの内容が変わっています。')
     result=json.loads(receipt[1]);return dict(result,replayed=True)
   if action=='archive':
    image=self.app.attachments.document(room,key)
    if image['revision']!=revision:raise EditConflict('元の添付が変わりました。')
    if not image['hidden'] or not image['available']:raise ValueError('先に会話から外した、確認できる画像を選んでください。')
    source=self.store.image_path(room,key,image['mime']);raw=self.file(source)
    with self.store.connect() as db:
     if db.execute("SELECT COUNT(*) FROM attachment_disposals WHERE room=? AND json_extract(payload,'$.state') NOT IN ('restored','purged')",(room,)).fetchone()[0]>=20:raise ValueError('未完了の退避は1ルーム20件までです。先に既存分を確認してください。')
     for row in db.execute('SELECT payload FROM attachment_disposals WHERE room=? AND image_id=?',(room,key)):
      if json.loads(row[0])['state'] not in ('restored','purged'):raise ValueError('既存の退避記録を先に確認してください。')
    job=dict(id=uuid.uuid4().hex,room=room,image_id=key,mime=image['mime'],name=image['name'],size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),state='archiving',phase='archive',error='',revision='',created_at=now())
   else:
    before=self.document(room,key)
    if before['revision']!=revision:raise EditConflict('退避記録や画像実体が変わりました。')
    job=self.get(room,key);source=self.store.image_path(room,job['image_id'],job['mime'])
    if action=='restore' and (job['state']!='quarantined' or before['source_exists'] or not before['staged_matches']):raise ValueError('復元先が空で、退避画像が一致する場合だけ戻せます。')
    if action=='purge' and (job['state'] not in ('quarantined','redundant') or not before['staged_matches'] or not before['hidden']):raise ValueError('退避画像と除外状態を確認してください。')
    if action=='recover' and job['state'] not in ('unknown','archiving','restoring','purging'):raise ValueError('結果未確認の操作を選んでください。')
    job.update(state={'restore':'restoring','purge':'purging','recover':'unknown'}[action],phase=job['phase'] if action=='recover' else action,error='')
   stage=self.path(job['id'])
   with self.store.connect() as db:
    self.write(db,job);db.execute('INSERT INTO attachment_disposal_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(dict(id=job['id'],state='unknown',message='操作結果を確認してください。'))))
   try:
    checkpoint()
    if action=='archive':
     self.copy(stage,raw);checkpoint()
     if self.app.attachments.document(room,key)['revision']!=revision or self.file(source)!=raw:raise EditConflict('退避中に元の画像が変わりました。')
     source.unlink();state='quarantined'
    elif action=='restore':
     raw=self.file(stage)
     if hashlib.sha256(raw).hexdigest()!=job['sha256']:raise EditConflict('退避画像が変わりました。')
     self.copy(source,raw);checkpoint();stage.unlink();state='restored'
    elif action=='purge':
     if hashlib.sha256(self.file(stage)).hexdigest()!=job['sha256']:raise EditConflict('退避画像が変わりました。')
     stage.unlink();state='purged'
    else:
     if before['source_matches'] and before['staged_matches']:state='redundant'
     elif not before['source_exists'] and before['staged_matches']:state='quarantined'
     elif before['source_matches'] and not before['staged_exists']:state='restored'
     elif not before['source_exists'] and not before['staged_exists'] and job['phase']=='purge':state='purged'
     else:raise ValueError('実体が一致しません。原本・退避先を手動で確認してください。')
    job.update(state=state,error='')
   except Exception as error:job.update(state='unknown',error='ファイル操作の結果を確認してください。 '+type(error).__name__)
   result=dict(id=job['id'],state=job['state'],error=job['error'],replayed=False)
   with self.store.connect() as db:
    self.write(db,job);db.execute('UPDATE attachment_disposal_receipts SET result=? WHERE room=? AND request_id=?',(json.dumps(result),room,request_id))
   return result
