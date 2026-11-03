"""Finite app-native appearance presets, common defaults and sparse room overrides."""
import json,uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict
OPTIONS=dict(theme=('akari','neutral','light'),accent=('theme','green','blue','purple','orange'),ui_font=('standard','readable','serif'),chat_font=('ui','standard','readable','serif'),ui_size=('small','normal','large'),chat_size=('12','14','16','18','20'),chat_spacing=('compact','normal','wide'))
DEFAULTS=dict(theme='akari',accent='theme',ui_font='standard',chat_font='ui',ui_size='normal',chat_size='14',chat_spacing='normal')
class Appearance:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS appearance_settings(scope TEXT,room TEXT,payload TEXT,revision TEXT,PRIMARY KEY(scope,room))')
   db.execute('CREATE TABLE IF NOT EXISTS appearance_versions(id INTEGER PRIMARY KEY,scope TEXT,room TEXT,payload TEXT,revision TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
   db.execute('CREATE TABLE IF NOT EXISTS appearance_receipts(scope TEXT,room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(scope,room,request_id))')
 def _read(self,db,scope,room):
  row=db.execute('SELECT payload,revision FROM appearance_settings WHERE scope=? AND room=?',(scope,room)).fetchone()
  return dict(values=json.loads(row[0]),revision=row[1]) if row else dict(values=dict(DEFAULTS) if scope=='common' else {},revision='unconfigured')
 def _view(self,db,room):
  common=self._read(db,'common','');override=self._read(db,'room',room) if room else dict(values={},revision='unconfigured')
  return dict(room=room,common=common,override=override,effective=dict(common['values'],**override['values']),origins={key:'room' if key in override['values'] else 'common' for key in DEFAULTS},revision=fingerprint([common,override]))
 def view(self,room=''):
  if room:self.store.room_path(room)
  with self.store.connect() as db:
   db.execute('BEGIN');return self._view(db,room)
 def save(self,scope,room,values,revision,request_id,common_revision=None,checkpoint=lambda:None):
  if not isinstance(room,str):raise ValueError('ルームが不正です。')
  if scope not in ('common','room') or scope=='room' and not room:raise ValueError('共通/ルームの保存先が不正です。')
  if room:self.store.room_path(room)
  if not isinstance(values,dict) or set(values)-set(OPTIONS) or scope=='common' and set(values)!=set(OPTIONS):raise ValueError('外観の設定項目が不正です。')
  if any(type(value) is not str or value not in OPTIONS[key] for key,value in values.items()):raise ValueError('外観は用意された候補から選んでください。')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  target=room if scope=='room' else '';signature=fingerprint([room,values,revision,common_revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing:raise ValueError('終了中です。')
   db.execute('BEGIN IMMEDIATE');receipt=db.execute('SELECT signature,result FROM appearance_receipts WHERE scope=? AND room=? AND request_id=?',(scope,target,request_id)).fetchone()
   if receipt:
    if receipt[0]!=signature:raise EditConflict('同じ操作IDに異なる外観設定があります。')
    return json.loads(receipt[1])
   before=self._read(db,scope,target)
   if before['revision']!=revision:raise EditConflict('外観設定が変わりました。再読込してください。')
   if scope=='room' and self._read(db,'common','')['revision']!=common_revision:raise EditConflict('共通の外観が変わりました。再読込して見え方を確認してください。')
   checkpoint();new_revision=uuid.uuid4().hex
   db.execute('INSERT INTO appearance_versions(scope,room,payload,revision) VALUES(?,?,?,?)',(scope,target,json.dumps(before['values']),before['revision']))
   db.execute('INSERT OR REPLACE INTO appearance_settings VALUES(?,?,?,?)',(scope,target,json.dumps(values),new_revision));result=self._view(db,room)
   db.execute('INSERT INTO appearance_receipts VALUES(?,?,?,?,?)',(scope,target,request_id,signature,json.dumps(result)));checkpoint();return result
