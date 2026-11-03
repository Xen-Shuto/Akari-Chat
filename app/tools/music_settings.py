"""Common link-provider preferences. YouTube is always included."""
import json,uuid
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict

SERVICES={'youtube':'YouTube','spotify':'Spotify（要登録）','bandcamp':'Bandcamp','soundcloud':'SoundCloud'}
DEFAULTS={key:True for key in SERVICES}

class MusicSettings:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.executescript("""CREATE TABLE IF NOT EXISTS music_settings(id INTEGER PRIMARY KEY,revision TEXT,values_json TEXT);
   CREATE TABLE IF NOT EXISTS music_setting_versions(id INTEGER PRIMARY KEY,revision TEXT,values_json TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
   CREATE TABLE IF NOT EXISTS music_setting_receipts(request_id TEXT PRIMARY KEY,signature TEXT,result TEXT);""")
 def _read(self,db):
  row=db.execute('SELECT revision,values_json FROM music_settings WHERE id=1').fetchone()
  return {'revision':row['revision'],'values':json.loads(row['values_json'])} if row else {'revision':'unconfigured','values':dict(DEFAULTS)}
 def view(self):
  with self.store.connect() as db:return self._read(db)
 def save(self,values,revision,request_id):
  if not isinstance(values,dict) or set(values)!=set(SERVICES) or any(type(v) is not bool for v in values.values()):raise ValueError('音楽検索先をチェックボックスで指定してください。')
  if values['youtube'] is not True:raise ValueError('YouTubeは常に有効です。')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  signature=fingerprint([values,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing:raise ValueError('終了処理中です。')
   db.execute('BEGIN IMMEDIATE');receipt=db.execute('SELECT signature,result FROM music_setting_receipts WHERE request_id=?',(request_id,)).fetchone()
   if receipt:
    if receipt['signature']!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
    return json.loads(receipt['result'])
   old=self._read(db)
   if old['revision']!=revision:raise EditConflict('音楽検索の設定が変わりました。再読込してください。')
   result=old
   if old['values']!=values:
    db.execute('INSERT INTO music_setting_versions(revision,values_json) VALUES(?,?)',(old['revision'],json.dumps(old['values'])))
    result={'revision':uuid.uuid4().hex,'values':dict(values)}
    db.execute('INSERT OR REPLACE INTO music_settings VALUES(1,?,?)',(result['revision'],json.dumps(values)))
   db.execute('INSERT INTO music_setting_receipts VALUES(?,?,?)',(request_id,signature,json.dumps(result)))
   return result
