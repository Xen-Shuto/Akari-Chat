"""Only same-room managed images can leave the app; freeze their content at proposal time."""
import base64,hashlib,json,uuid
from litechat.file_editor import EditConflict,safe_path
from litechat import image_backends
from datetime import datetime,timezone

class SocialMedia:
 def __init__(self,app):
  self.app=app
  with app.store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS social_image_imports(room TEXT,request_id TEXT,signature TEXT,artifact TEXT,PRIMARY KEY(room,request_id))')
 def import_image(self,room,name,data,request_id):
  if not isinstance(name,str) or len(name)>200 or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('画像名/操作IDが不正です。')
  if not isinstance(data,str) or not data.startswith('data:image/') or ',' not in data:raise ValueError('画像データが不正です。')
  try:raw=base64.b64decode(data.split(',',1)[1],validate=True)
  except Exception:raise ValueError('画像データが不正です。')
  if len(raw)>5*1048576:raise ValueError('画像は5MiB以内です。')
  raw,mime=image_backends.image_bytes(raw);width,height=image_backends.dimensions(raw,mime)
  sha=hashlib.sha256(raw).hexdigest();signature=hashlib.sha256((name+sha).encode()).hexdigest()
  with self.app.lock,self.app.store.lock:
   if self.app.closing:raise ValueError('終了処理中です。')
   root=self.app.store.room_path(room)
   with self.app.store.connect() as db:previous=db.execute('SELECT * FROM social_image_imports WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if previous:
    if previous['signature']!=signature:raise EditConflict('同じ操作IDの画像が異なります。')
    return self.app.images.artifact(room,previous['artifact'])[0]
   key=hashlib.sha256((room+':'+request_id).encode()).hexdigest()[:32];ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[mime]
   rel='Akari_Data/social_images/'+key+'.'+ext;path=safe_path(root,rel);path.parent.mkdir(parents=True,exist_ok=True)
   if path.exists():
    if path.read_bytes()!=raw:raise EditConflict('保存先に別の画像があります。')
   else:
    with path.open('xb') as stream:stream.write(raw)
   doc={'id':key,'room':room,'path':rel,'name':name,'mime':mime,'sha256':sha,'width':width,'height':height,'bytes':len(raw),'created_at':datetime.now(timezone.utc).isoformat(),'prompt':'外部送信用に取り込んだ画像','model':'user-upload','reference_used':False}
   with self.app.store.connect() as db:
    db.execute('INSERT INTO image_artifacts VALUES(?,?,?,?)',(key,room,rel,json.dumps(doc)))
    db.execute('INSERT INTO social_image_imports VALUES(?,?,?,?)',(room,request_id,signature,key))
   return doc
 def snapshot(self,room,keys,provider):
  if not isinstance(keys,list) or len(keys)>4 or any(not isinstance(k,str) or len(k)>128 for k in keys) or len(set(keys))!=len(keys):raise ValueError('画像IDは重複なしで4件までです。')
  items=[];size=0
  for key in keys:
   doc,raw=self.app.images.artifact(room,key);size+=len(raw)
   if size>8*1048576 or len(raw)>5*1048576:raise ValueError('送信画像は1枚5MiB、合計8MiB以内です。')
   if doc['mime'] not in (('image/png','image/jpeg') if provider=='x' else ('image/png','image/jpeg','image/webp')):raise ValueError('この接続先で扱えない画像形式です。XはPNG/JPEG、DiscordはPNG/JPEG/WebPです。')
   items.append({'id':key,'sha256':hashlib.sha256(raw).hexdigest(),'mime':doc['mime'],'bytes':len(raw)})
  return items
 def load(self,room,items,provider):
  now=self.snapshot(room,[i['id'] for i in items],provider)
  if now!=items:raise EditConflict('確認待ち作成後に画像が変わりました。送信せず選び直してください。')
  result=[]
  for item in items:
   raw=self.app.images.artifact(room,item['id'])[1]
   if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise EditConflict('画像が変更されています。')
   result.append((item,raw))
  return result
 def x_upload(self,external,token,images,record):
  ids=[]
  for item,raw in images:
   result=external.fetch('x','POST','/2/media/upload',token,{'media':base64.b64encode(raw).decode(),'media_category':'tweet_image'})
   data=result.get('data',{})
   if not isinstance(data.get('id'),str) or not data['id'] or data.get('processing_info',{}).get('state','succeeded')!='succeeded':raise ValueError('画像アップロードの完了を確認できません。投稿を中止しました。')
   ids.append(data['id']);record({'uploaded_media_ids':list(ids)})
  return ids
 def discord_body(self,payload,images):
  boundary='akari'+uuid.uuid4().hex;data=bytearray()
  ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}
  payload=dict(payload,attachments=[{'id':index,'filename':('post.txt' if item['mime']=='text/plain' else 'image'+str(index)+'.'+ext[item['mime']])} for index,(item,raw) in enumerate(images)])
  def add(headers,raw):
   data.extend(('--'+boundary+'\r\n'+headers+'\r\n\r\n').encode());data.extend(raw);data.extend(b'\r\n')
  add('Content-Disposition: form-data; name="payload_json"'+chr(13)+chr(10)+'Content-Type: application/json',json.dumps(payload,ensure_ascii=False).encode())
  for index,(item,raw) in enumerate(images):
   name=payload['attachments'][index]['filename']
   add('Content-Disposition: form-data; name="files['+str(index)+']"; filename="'+name+'"'+chr(13)+chr(10)+'Content-Type: '+item['mime'],raw)
  data.extend(('--'+boundary+'--\r\n').encode())
  return bytes(data),{'Content-Type':'multipart/form-data; boundary='+boundary}
