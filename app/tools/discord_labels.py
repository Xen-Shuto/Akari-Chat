"""Cached room/connection scoped Discord names; no network during rendering."""
import json,time

def initialize(db):
 db.execute('CREATE TABLE IF NOT EXISTS discord_channel_names(room TEXT,connection TEXT,channel TEXT,name TEXT,checked REAL,PRIMARY KEY(room,connection,channel))')

def remember(db,room,connection,channel,name):
 if not isinstance(channel,str) or not channel.isascii() or not channel.isdigit():return
 if not isinstance(name,str) or not name.strip():return
 name=' '.join(name.split())[:100]
 db.execute('INSERT OR REPLACE INTO discord_channel_names VALUES(?,?,?,?,?)',(room,connection,channel,name,time.time()))

def cached(db,room,connection,channel):
 row=db.execute('SELECT name FROM discord_channel_names WHERE room=? AND connection=? AND channel=?',(room,connection,channel)).fetchone()
 return row[0] if row and row[0] else channel

def resolve(external,room,connection,channel):
 with external.store.connect() as db:
  row=db.execute('SELECT name,checked FROM discord_channel_names WHERE room=? AND connection=? AND channel=?',(room,connection,channel)).fetchone()
  if row and time.time()-row[1]<(3600 if row[0] else 60):return row[0] or channel
 try:
  result=external.read(room,'discord','channel',{'channel':channel})['data']
  if isinstance(result,dict) and isinstance(result.get('name'),str) and result['name'].strip():
   with external.store.connect() as db:remember(db,room,connection,channel,result['name'])
   return result['name']
 except Exception:pass # Advisory only; it must not suppress a received message or resend a mutation.
 with external.store.connect() as db:
  db.execute('INSERT OR IGNORE INTO discord_channel_names VALUES(?,?,?,?,?)',(room,connection,channel,'',time.time()))
  db.execute('UPDATE discord_channel_names SET checked=? WHERE room=? AND connection=? AND channel=?',(time.time(),room,connection,channel))
  return cached(db,room,connection,channel)

def heading(role,name):return '【'+('To' if role=='assistant' else 'From')+': Discord / '+name+' 】'

def present(db,room,rows):
 """Add trusted sender labels and display old headers without rewriting stored logs."""
 result=[dict(row) for row in rows]
 ids=[r['id'] for r in result if r.get('origin')=='discord']
 if not ids:return result
 tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name IN ('discord_chat_links','discord_channel_names','discord_received_v2','external_configs')")}
 if 'discord_chat_links' not in tables:return result
 owner=db.execute("SELECT json_extract(value,'$.user_id') FROM external_configs WHERE scope=? AND provider='discord'",(room,)).fetchone() if 'external_configs' in tables else None
 owner=owner[0] if owner else ''
 marks=','.join('?' for _ in ids)
 has_received='discord_received_v2' in tables
 has_names='discord_channel_names' in tables
 names_join='LEFT JOIN discord_channel_names n ON n.room=l.room AND n.connection=l.connection AND n.channel=l.channel' if has_names else ''
 received_join='LEFT JOIN discord_received_v2 r ON r.room=l.room AND r.connection=l.connection AND r.channel=l.channel AND r.id=l.remote_id' if has_received else ''
 links={r['message_id']:r for r in db.execute(f"""SELECT l.message_id,l.channel,{'n.name' if has_names else 'NULL'} AS name,{'r.content' if has_received else 'NULL'} AS received FROM discord_chat_links l
 {names_join}
 {received_join}
 WHERE l.room=? AND l.message_id IN ({marks})""",[room]+ids)}
 for item in result:
  link=links.get(item['id'])
  if not link:continue
  item['discord_label']=heading(item['role'],link['name'] or link['channel'])
  old,sep,body=item['content'].partition('\n\n')
  if sep and (old.startswith('【Discord / ') or old.startswith('【From: Discord / ') or old.startswith('【To: Discord / ')):
   item['content']=item['discord_label']+'\n\n'+body
  if item['role']=='user':
   try:author=json.loads(link['received'] or '{}').get('author') or {}
   except (ValueError,TypeError):author={}
   if not isinstance(author,dict):author={}
   identity=str(author.get('id',''))
   fallback=old.rsplit(' / ',1)[-1].rstrip('】') if old.startswith('【Discord / ') else 'Discordユーザー'
   item['speaker']='YOU' if owner and identity==owner else str(author.get('global_name') or author.get('username') or identity or fallback)[:100]
 return result
