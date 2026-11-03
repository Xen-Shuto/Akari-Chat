"""One-way, transactionally deduplicated Discord -> local chat projection."""
from datetime import datetime,timezone
from .discord_labels import cached,heading,initialize as initialize_names

def initialize(db):
 initialize_names(db)
 db.execute('CREATE TABLE IF NOT EXISTS discord_chat_links(room TEXT,connection TEXT,channel TEXT,remote_id TEXT,message_id INTEGER,PRIMARY KEY(room,connection,channel,remote_id))')
 db.execute("CREATE INDEX IF NOT EXISTS discord_chat_room ON messages(room,id) WHERE origin='discord' AND deleted=0")

def record(db,room,connection,message,role='user',name='',model=''):
 initialize(db)
 remote=message.get('id');channel=message.get('channel') or message.get('channel_id');content=message.get('content','')
 if not isinstance(remote,str) or not remote.isascii() or not remote.isdigit() or not isinstance(channel,str) or not channel.isascii() or not channel.isdigit():return None
 if not isinstance(content,str):return None
 attachments=message.get('attachments') or []
 files=[str(a.get('filename','添付ファイル'))[:200] for a in attachments[:10] if isinstance(a,dict)] if isinstance(attachments,list) else []
 if files:content=content+('\n' if content else '')+'添付ファイル: '+', '.join(files)
 if not content.strip():return None
 old=db.execute('SELECT message_id FROM discord_chat_links WHERE room=? AND connection=? AND channel=? AND remote_id=?',(room,connection,channel,remote)).fetchone()
 if old:
  if role=='assistant':db.execute("UPDATE messages SET role='assistant',model=CASE WHEN model='' THEN ? ELSE model END WHERE id=? AND origin='discord'",(model,old[0]))
  if role=='assistant' and model and model!='Discord':db.execute("UPDATE messages SET model=? WHERE id=? AND origin='discord' AND model='Discord'",(model,old[0]))
  return old[0]
 try:created=datetime.fromisoformat(message.get('timestamp','').replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
 except (ValueError,TypeError,AttributeError):created=datetime.now(timezone.utc).isoformat()
 author=message.get('author') or {}
 if not isinstance(author,dict):author={}
 name=name or author.get('global_name') or author.get('username') or author.get('id') or ('Bot' if role=='assistant' else 'Discordユーザー')
 # Keep origin/speaker readable in logs and exports without treating model text as metadata.
 body=heading(role,cached(db,room,connection,channel))+'\n\n'+content[:4000]
 key=db.execute("INSERT INTO messages(room,role,content,status,created,model,origin) VALUES(?,?,?,'complete',?,?,'discord')",(room,role,body,created,model if role=='assistant' else '')).lastrowid
 db.execute('INSERT INTO discord_chat_links VALUES(?,?,?,?,?)',(room,connection,channel,remote,key))
 return key

def received(db,room,connection,message,bot_id):
 author=message.get('author') or {}
 own=bool(bot_id) and str(author.get('id'))==str(bot_id)
 return record(db,room,connection,message,'assistant' if own else 'user',model='Discord' if own else '')

def sent(db,row,result,model='Discord'):
 import json
 payload=json.loads(row['payload']);response=result.get('response',{})
 if not isinstance(response,dict) or not response.get('id'):return None
 content=response.get('content') or (('<@'+payload['recipient']+'> ') if payload.get('recipient') else '')+payload.get('text','')
 message={**response,'channel':payload.get('channel'),'content':content}
 key=record(db,row['room'],payload.get('connection_id','default'),message,'assistant',name='Bot',model=model)
 if key:
  for image in payload.get('images',[]):
   artifact=image.get('id')
   if db.execute('SELECT 1 FROM image_artifacts WHERE room=? AND id=?',(row['room'],artifact)).fetchone():
    db.execute('INSERT OR IGNORE INTO message_generated_images VALUES(?,?,?)',(key,artifact,row['room']))
 return key

def latest(store,room):
 if not room:return 0
 store.room_path(room)
 with store.connect() as db:return db.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE room=? AND origin='discord' AND deleted=0",(room,)).fetchone()[0]
