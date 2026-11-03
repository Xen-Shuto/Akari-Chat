"""User-only state reset with a revisioned, atomic original snapshot."""
import hashlib,json,time,uuid
from litechat.autonomy import encoded,now
from litechat.file_editor import EditConflict

class InternalStateReset:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:
   db.execute('CREATE TABLE IF NOT EXISTS internal_state_resets(room TEXT,request_id TEXT,signature TEXT,snapshot TEXT,result TEXT,created TEXT,PRIMARY KEY(room,request_id))')
 def snapshot(self,db,room):
  questions=[json.loads(row[0]) for row in db.execute("SELECT payload FROM autonomy_documents WHERE room=? AND kind='question' AND json_extract(payload,'$.state') IN ('active','paused') ORDER BY id",(room,))]
  return dict(state=self.app.motivation._state(db,room),settings=self.app.motivation._settings(db,room),questions=questions)
 def view(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:
   db.execute('BEGIN');snapshot=self.snapshot(db,room)
  return dict(room=room,revision=hashlib.sha256(encoded(snapshot).encode()).hexdigest(),question_count=len(snapshot['questions']))
 def reset(self,room,revision,include_questions,confirmed,request_id,checkpoint=lambda:None):
  self.store.room_path(room)
  if type(include_questions) is not bool or confirmed is not True:raise ValueError('対象と確認チェックが必要です。')
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  signature=hashlib.sha256(encoded([revision,include_questions]).encode()).hexdigest()
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・自律処理の終了後にリセットしてください。')
   db.execute('BEGIN IMMEDIATE')
   receipt=db.execute('SELECT signature,result FROM internal_state_resets WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if receipt:
    if receipt[0]!=signature:raise EditConflict('同じ操作IDに異なるリセットがあります。')
    return json.loads(receipt[1])
   snapshot=self.snapshot(db,room)
   if hashlib.sha256(encoded(snapshot).encode()).hexdigest()!=revision:raise EditConflict('内部状態・設定・問いが変わりました。対象を再読込してください。')
   checkpoint();clock=time.time();stamp=now();state=dict(snapshot['state'])
   state.update(last_interaction=None,last_action=None,anchor=clock,emotion='neutral',intensity=0.,arousal=0.,satisfaction={},recent_drives=[],recent_at=None,emotion_at=None,reset_at=clock,revision=uuid.uuid4().hex)
   db.execute('INSERT OR REPLACE INTO motivation_states VALUES(?,?)',(room,encoded(state)))
   count=0
   if include_questions:
    for original in snapshot['questions']:
     checkpoint();item=dict(original,state='archived',revision=uuid.uuid4().hex,updated_at=stamp)
     db.execute('INSERT INTO autonomy_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,item['id'],encoded(original),stamp))
     db.execute('UPDATE autonomy_documents SET payload=? WHERE room=? AND id=?',(encoded(item),room,item['id']));count+=1
   checkpoint();event_id='reset:'+uuid.uuid4().hex
   event=dict(id=event_id,kind='reset',created=stamp,emotion='neutral',intensity=0.,arousal=0.,emotion_source='画面から内部状態をリセット',archived_questions=count)
   db.execute('INSERT INTO motivation_events VALUES(?,?,?)',(room,event_id,encoded(event)))
   result=dict(reset=True,archived_questions=count,state_revision=state['revision'],created=stamp)
   db.execute('INSERT INTO internal_state_resets VALUES(?,?,?,?,?,?)',(room,request_id,signature,encoded(snapshot),encoded(result),stamp))
   checkpoint()
  return result
