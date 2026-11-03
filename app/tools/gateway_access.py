"""Per-room Gateway grants, separate from shared network credentials."""
import json,uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict

DEFAULTS={'enabled':False,'allow_chat':False,'allow_events':False}

class GatewayAccess:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.lock,self.store.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS gateway_room_access(room TEXT PRIMARY KEY,revision TEXT,value TEXT);
   CREATE TABLE IF NOT EXISTS gateway_room_access_versions(id INTEGER PRIMARY KEY,room TEXT,revision TEXT,value TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
   CREATE TABLE IF NOT EXISTS gateway_room_access_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id));
   CREATE TABLE IF NOT EXISTS gateway_config_legacy(id INTEGER PRIMARY KEY,revision TEXT,value TEXT);""")
   db.execute('BEGIN IMMEDIATE')
   old=db.execute('SELECT * FROM gateway_config WHERE id=1').fetchone()
   if old:
    values=json.loads(old['value'])
    if 'rooms' in values or 'scopes' in values:
     db.execute('INSERT OR IGNORE INTO gateway_config_legacy VALUES(1,?,?)',(old['revision'],old['value']))
     scopes=values.get('scopes',['read'])
     for room in values.get('rooms',[]):
      if not isinstance(room,str):continue
      grant={'enabled':True,'allow_chat':'chat' in scopes,'allow_events':'events' in scopes}
      db.execute('INSERT OR IGNORE INTO gateway_room_access VALUES(?,?,?)',(room,uuid.uuid4().hex,json.dumps(grant)))
     values.pop('rooms',None);values.pop('scopes',None)
     db.execute('UPDATE gateway_config SET revision=?,value=? WHERE id=1',(uuid.uuid4().hex,json.dumps(values)))
 def _read(self,db,room):
  row=db.execute('SELECT * FROM gateway_room_access WHERE room=?',(room,)).fetchone()
  return {'room':room,'revision':row['revision'],'values':json.loads(row['value'])} if row else {'room':room,'revision':'0','values':dict(DEFAULTS)}
 def view(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:return self._read(db,room)
 def scopes(self,room):
  values=self.view(room)['values']
  return ['read']+(['chat'] if values['allow_chat'] else [])+(['events'] if values['allow_events'] else []) if values['enabled'] else []
 def rooms(self):
  with self.store.connect() as db:rooms=[r[0] for r in db.execute("SELECT room FROM gateway_room_access WHERE json_extract(value,'$.enabled')=1 ORDER BY room")]
  visible=[]
  for room in rooms:
   try:self.store.room_path(room);visible.append(room)
   except ValueError:pass
  return visible
 def save(self,room,values,revision,request_id):
  self.store.room_path(room)
  if not isinstance(values,dict) or set(values)!=set(DEFAULTS) or any(type(v) is not bool for v in values.values()):raise ValueError('ルーム別Gateway許可が不正です。')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  signature=fingerprint([values,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing:raise ValueError('終了処理中です。')
   self.store.room_path(room);db.execute('BEGIN IMMEDIATE')
   receipt=db.execute('SELECT * FROM gateway_room_access_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if receipt:
    if receipt['signature']!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
    return json.loads(receipt['result'])
   old=self._read(db,room)
   if old['revision']!=revision:raise EditConflict('このルームのGateway許可が変わりました。再読込してください。')
   result=old
   if old['values']!=values:
    db.execute('INSERT INTO gateway_room_access_versions(room,revision,value) VALUES(?,?,?)',(room,old['revision'],json.dumps(old['values'])))
    result={'room':room,'revision':uuid.uuid4().hex,'values':dict(values)}
    db.execute('INSERT OR REPLACE INTO gateway_room_access VALUES(?,?,?)',(room,result['revision'],json.dumps(values)))
   db.execute('INSERT INTO gateway_room_access_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
   return result
