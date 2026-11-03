"""Opt-in exact-title local consolidation with existing reversible merges."""
import json,time,uuid,unicodedata
from datetime import datetime
from litechat.episodes import JST,fingerprint
from litechat.file_editor import EditConflict
from .sleep_schedule import night_key
from .background_guard import uncertain

def title_key(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
DEFAULTS=dict(enabled=False,transfer_local=False)
class ResearchMaintenance:
 def __init__(self,app):
  self.app,self.store=app,app.store;self.next_scan=0;self.warning=''
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS research_maintenance_settings(room TEXT PRIMARY KEY,payload TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS research_maintenance_runs(id TEXT PRIMARY KEY,room TEXT,night TEXT,state TEXT,revision TEXT,payload TEXT,UNIQUE(room,night))')
   db.execute('CREATE TABLE IF NOT EXISTS research_maintenance_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
   db.execute("UPDATE research_maintenance_runs SET state='unknown',revision=? WHERE state='applying'",(uuid.uuid4().hex,))
 def settings(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:row=db.execute('SELECT payload FROM research_maintenance_settings WHERE room=?',(room,)).fetchone()
  return dict(DEFAULTS,revision='unconfigured') if not row else json.loads(row[0])
 def view(self,room):
  config=self.settings(room)
  with self.store.connect() as db:rows=[dict(r) for r in db.execute('SELECT * FROM research_maintenance_runs WHERE room=? ORDER BY rowid DESC LIMIT 30',(room,))]
  for row in rows:row['result']=json.loads(row.pop('payload'))
  return dict(settings=config,runs=rows,warning=self.warning)
 def save(self,room,data,revision,request_id):
  self.store.room_path(room)
  if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or any(type(v) is not bool for v in data.values()) or not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('整理設定または操作IDが不正です。')
  signature=fingerprint([data,revision])
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成後に設定してください。')
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,result FROM research_maintenance_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDに別の設定があります。')
    return json.loads(old[1])
   if self.settings(room)['revision']!=revision:raise EditConflict('整理設定が変わっています。再読込してください。')
   result=dict(data,revision=uuid.uuid4().hex);db.execute('INSERT OR REPLACE INTO research_maintenance_settings VALUES(?,?)',(room,json.dumps(result)))
   db.execute('INSERT INTO research_maintenance_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result)))
  self.next_scan=0;return result
 def acknowledge(self,room,key,revision):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('処理終了後に確認してください。')
   if not db.execute("UPDATE research_maintenance_runs SET state='reviewed',revision=? WHERE room=? AND id=? AND revision=? AND state='unknown'",(uuid.uuid4().hex,room,key,revision)).rowcount:raise EditConflict('確認対象が変わっています。')
  return self.view(room)
 def tick(self,moment=None):
  if self.warning:return False
  explicit=moment is not None
  if not explicit and time.monotonic()<self.next_scan:return False
  self.next_scan=time.monotonic()+60;moment=moment or datetime.now(JST)
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job or self.app.conversation_waiters:return False
   with self.store.connect() as db:configs=db.execute('SELECT room,payload FROM research_maintenance_settings WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) ORDER BY room').fetchall()
   for row in configs:
    room,raw=row;config=json.loads(raw)
    if not config['enabled']:continue
    try:schedule=self.app.sleep_schedule.settings(room);night=night_key(moment,schedule['start'],schedule['end'])
    except ValueError:continue
    if night is None:continue
    with self.store.connect() as db:
     if uncertain(db,room):continue
     if db.execute("SELECT 1 FROM research_maintenance_runs WHERE room=? AND (night=? OR state='unknown')",(room,night)).fetchone():continue
     if db.execute("SELECT 1 FROM autonomy_runs WHERE room=? AND state IN ('queued','running','unknown') AND COALESCE(json_extract(payload,'$.reviewed'),0)=0",(room,)).fetchone():continue
     rows=db.execute("SELECT payload FROM autonomy_documents WHERE room=? AND kind='thread' AND json_extract(payload,'$.state')='active' ORDER BY id LIMIT 101",(room,)).fetchall()
     history=db.execute('SELECT payload FROM research_merges WHERE room=? LIMIT 501',(room,)).fetchall()
    reason='一致する候補はありません。';pair=None
    if len(rows)>100 or len(history)>500:reason='確認上限を超えるため見送ります。'
    else:
     documents=[json.loads(r[0]) for r in rows];groups={}
     for doc in documents:
      key=title_key(doc['title'])
      if key:groups.setdefault(key,[]).append(doc)
     used={(json.loads(h[0])['source']['id'],json.loads(h[0])['source']['revision']) for h in history}
     for group in groups.values():
      if len(group)!=2:continue
      left,right=group
      if (right['id'],right['revision']) in used or (left['id'],left['revision']) in used:continue
      pair=(left,right);break
    key=uuid.uuid4().hex;payload=dict(config_revision=config['revision'],reason=reason)
    if pair:
     left,right=pair;preview=self.app.research_merges.preview(room,left['id'],right['id']);payload.update(target=left['id'],source=right['id'],source_revision=right['revision'],comparison=preview['revision'])
     ids=[r['id'] for r in preview['references']['source']['subscriptions'] if r['source']=='local' and not r['archived']] if config['transfer_local'] else []
     if any(len(preview['proposal'][field])>4000 for field in ('body',)):
      pair=None;payload['reason']='統合後の本文が上限を超えるため見送ります。'
    with self.store.connect() as db:db.execute('INSERT INTO research_maintenance_runs VALUES(?,?,?,?,?,?)',(key,room,night,'applying' if pair else 'skipped',uuid.uuid4().hex,json.dumps(payload)))
    if not pair:return True
    try:
     def guard():
      current=self.settings(room);instant=moment if explicit else datetime.now(JST);window=self.app.sleep_schedule.settings(room)
      if current['revision']!=config['revision'] or not current['enabled'] or night_key(instant,window['start'],window['end'])!=night or self.app.conversation_waiters or self.app.closing:raise EditConflict('設定・時間帯・会話待ちが変わりました。')
     guard();result,_=self.app.research_merges.apply(room,left['id'],right['id'],preview['revision'],preview['proposal'],'night-research-'+key,checkpoint=guard,transfer_ids=ids)
     payload.update(merge_id=result['id'],reason='元の研究を保持して統合しました。');state='complete'
    except (ValueError,EditConflict) as error:payload['reason']=str(error);state='skipped'
    except Exception as error:payload['reason']='結果を確認できません。研究の統合履歴を確認してください。 '+str(error)[:300];state='unknown'
    with self.store.connect() as db:db.execute('UPDATE research_maintenance_runs SET state=?,revision=?,payload=? WHERE id=?',(state,uuid.uuid4().hex,json.dumps(payload),key))
    return True
  return False
