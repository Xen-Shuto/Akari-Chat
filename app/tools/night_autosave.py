"""Opt-in nightly approval through the existing revision/backup protected writer."""
import uuid
import time
from litechat.file_editor import EditConflict
from .background_guard import ready_sql

class NightAutosave:
 def __init__(self,scheduler):
  self.scheduler=scheduler;self.app=scheduler.app;self.store=self.app.store;self.pending=True;self.next_scan=0
  with self.store.connect() as db:
   db.execute("CREATE TABLE IF NOT EXISTS night_autosaves(job_id TEXT PRIMARY KEY,room TEXT,draft_id INTEGER,config_revision TEXT,state TEXT,error TEXT,revision TEXT)")
   db.execute('CREATE INDEX IF NOT EXISTS night_autosave_state ON night_autosaves(state,room)')
   db.execute("UPDATE night_autosaves SET state='unknown',error='前回の保存結果を確認してください。自動再試行しません。',revision=? WHERE state='applying'",(uuid.uuid4().hex,))
 def enqueue(self,item,draft_id):
  with self.store.lock,self.store.connect() as db:
   old=db.execute('SELECT room,draft_id,config_revision FROM night_autosaves WHERE job_id=?',(item['id'],)).fetchone()
   if old:
    if tuple(old)!=(item['room'],draft_id,item['config_revision']):raise EditConflict('同じ夜間実行の保存対象が変わりました。')
    return
   db.execute("INSERT INTO night_autosaves VALUES(?,?,?,?,?,?,?)",(item['id'],item['room'],draft_id,item['config_revision'],'queued','',uuid.uuid4().hex))
  self.pending=True
 def finish(self,key,state,error=''):
  with self.store.lock,self.store.connect() as db:
   db.execute('UPDATE night_autosaves SET state=?,error=?,revision=? WHERE job_id=?',(state,error[:500],uuid.uuid4().hex,key))
   row=db.execute('SELECT room FROM night_autosaves WHERE job_id=?',(key,)).fetchone()
   if row and state!='applying':self.app.timelines.record_night_save(db,row[0],key,state)
 def acknowledge(self,room,key,revision):
  room=self.store.room_path(room).name
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成が終わってから確認してください。')
   if not db.execute("UPDATE night_autosaves SET state='review',revision=? WHERE room=? AND job_id=? AND revision=? AND state='unknown'",(uuid.uuid4().hex,room,key,revision)).rowcount:raise EditConflict('自動保存の記録が変わりました。')
  self.pending=True
  return self.scheduler.listing(room)
 def process_one(self,moment):
  from .sleep_schedule import night_key
  app=self.app
  if not self.pending and time.monotonic()<self.next_scan:return False
  with app.lock:
   if app.closing or app.job or app.conversation_waiters:return False
   with self.store.connect() as db:
    row=db.execute("SELECT a.*,n.night,n.state AS night_state FROM night_autosaves a JOIN sleep_night_jobs n ON n.id=a.job_id WHERE a.state='queued' AND "+ready_sql("a.room")+" ORDER BY a.rowid LIMIT 1").fetchone()
   if row is None:self.pending=False;self.next_scan=time.monotonic()+30;return False
   item=dict(row);key=item['job_id']
   if item['night_state']!='complete':self.finish(key,'review','夜間実行の完了を確認できないため自動保存しません。');return True
   try:config=self.scheduler.settings(item['room'])
   except ValueError:self.finish(key,'review','ルームを確認できません。');return True
   if not config['enabled'] or not config.get('sleep_enabled',True) or not config.get('auto_save') or config['revision']!=item['config_revision'] or night_key(moment,config['start'],config['end'])!=item['night']:
    self.finish(key,'review','設定・時間帯が変わったため、確認待ち案を維持しました。');return True
   try:
    review=app.resources.review(item['room'],item['draft_id'])
    if review['conflict']:raise EditConflict('資料が変わったため自動保存を見送りました。')
   except (ValueError,OSError):
    self.finish(key,'review','草案・資料・現在本文を再確認してください。自動保存は行いません。');return True
   self.finish(key,'applying')  # Durable intent before the existing file/backup writer.
   try:
    result=app.resources.approve(item['room'],item['draft_id'],review['text'])
   except EditConflict:
    self.finish(key,'review','保存直前に資料が変わりました。確認待ち案を保持しました。');return True
   except Exception:
    self.finish(key,'unknown','保存の結果を確認できません。原本・バックアップ・草案状態を確認してください。');return True
   try:indexed=app.index_refresh.saved(item['room'],result)
   except Exception:indexed={'index_warning':'本文は保存しましたが索引更新の予約を確認できません。'}
   self.finish(key,'saved',indexed.get('index_warning',''))
   return True
