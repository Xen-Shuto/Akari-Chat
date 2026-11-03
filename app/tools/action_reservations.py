"""Persisted intentions and weekday alarms dispatched into the existing autonomy queue."""
import hashlib,json,uuid,time
from datetime import datetime,timedelta,timezone
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput
from .schema import schema
from .background_guard import uncertain
JST=timezone(timedelta(hours=9))

class ActionReservations:
 def __init__(self,app):
  self.app,self.store=app,app.store
  with self.store.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS action_reservations(id TEXT PRIMARY KEY,room TEXT,request_id TEXT,signature TEXT,state TEXT,payload TEXT,UNIQUE(room,request_id))')
 def prepare(self,kind,intent,details,at,weekdays=None,emotion='',context_type='NEW',clock=None):
  now=time.time() if clock is None else clock
  if kind not in ('action','alarm') or context_type not in ('NEW','CONTINUE','DEEPEN'):raise ValueError('予約の種類/文脈が不正です。')
  for text,limit in [(intent,2000),(details,4000),(emotion,200)]:
   if not isinstance(text,str) or len(text)>limit:raise ValueError('予約の目的・詳細・感情が長すぎます。')
  if not intent.strip() or not details.strip():raise ValueError('目的と詳細を入力してください。')
  weekdays=[] if weekdays is None else weekdays
  if not isinstance(weekdays,list) or len(weekdays)>7 or any(type(d) is not int or not 0<=d<=6 for d in weekdays) or len(set(weekdays))!=len(weekdays):raise ValueError('曜日は月曜0～日曜6の重複しない配列です。')
  if weekdays and kind!='alarm':raise ValueError('曜日繰り返しはアラームだけです。')
  try:
   when=datetime.fromisoformat(at.replace('Z','+00:00'))
   if when.utcoffset() is None:raise ValueError()
  except (ValueError,TypeError,AttributeError):raise ValueError('UTCオフセット付きのISO日時を指定してください。') from None
  due=when.timestamp()
  if not now<due<=now+31622400:raise ValueError('予約は現在より後、366日以内です。')
  if weekdays and when.astimezone(JST).weekday() not in weekdays:raise ValueError('初回日時の曜日を繰り返し曜日に含めてください。')
  return dict(kind=kind,intent=intent.strip(),details=details.strip(),emotion=emotion,context_type=context_type,at=when.isoformat(),due=due,weekdays=sorted(weekdays))
 def create(self,room,prepared,request_id,checkpoint=lambda:None):
  self.store.room_path(room)
  if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDが必要です。')
  signature=hashlib.sha256(json.dumps(prepared,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  with self.app.lock,self.store.lock,self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT signature,payload FROM action_reservations WHERE room=? AND request_id=?',(room,request_id)).fetchone()
   if old:
    if old[0]!=signature:raise EditConflict('同じ操作IDの予約内容が異なります。')
    return json.loads(old[1])
   if db.execute("SELECT COUNT(*) FROM action_reservations WHERE state='queued' AND room=?",(room,)).fetchone()[0]>=20 or db.execute("SELECT COUNT(*) FROM action_reservations WHERE state='queued'").fetchone()[0]>=200:raise ValueError('予約上限（ルーム20件/全体200件）です。')
   if db.execute("SELECT 1 FROM action_reservations WHERE room=? AND state='queued' AND signature=?",(room,signature)).fetchone():raise EditConflict('同じ内容の予約が既にあります。')
   item=dict(prepared,id=uuid.uuid4().hex,room=room,state='queued',revision=uuid.uuid4().hex,run_id=None)
   checkpoint();db.execute('INSERT INTO action_reservations VALUES(?,?,?,?,?,?)',(item['id'],room,request_id,signature,'queued',json.dumps(item,ensure_ascii=False)))
   return item
 def listing(self,room):
  self.store.room_path(room)
  with self.store.connect() as db:
   rows=db.execute("SELECT payload FROM action_reservations WHERE room=? ORDER BY (state='queued') DESC,rowid DESC LIMIT 200",(room,)).fetchall();items=[]
   for row in rows:
    item=json.loads(row[0]);run=db.execute('SELECT state FROM autonomy_runs WHERE id=? AND room=?',(item['run_id'],room)).fetchone() if item.get('run_id') else None
    items.append(dict(item,run_state=run[0] if run else None))
  return dict(entries=items)
 def cancel(self,room,key,revision,checkpoint=lambda:None):
  self.store.room_path(room)
  with self.app.lock,self.store.lock,self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM action_reservations WHERE room=? AND id=?',(room,key)).fetchone()
   if not row:raise ValueError('予約が見つかりません。')
   item=json.loads(row[0])
   if item['revision']!=revision:raise EditConflict('予約の版が変わりました。再読込してください。')
   if item.get('run_id'):
    run=db.execute('SELECT payload FROM autonomy_runs WHERE room=? AND id=?',(room,item['run_id'])).fetchone()
    if run:
     run=json.loads(run[0])
     if run['state']=='running' and not item['weekdays']:raise ValueError('実行中です。自律実行履歴から停止してください。')
     if run['state']=='queued':run['state']='cancelled';self.app.autonomous._write(db,run)
   item.update(state='cancelled',revision=uuid.uuid4().hex);checkpoint();db.execute('UPDATE action_reservations SET state=?,payload=? WHERE id=?',(item['state'],json.dumps(item,ensure_ascii=False),key));return item
 def dispatch(self,clock=None):
  now=time.time() if clock is None else clock
  with self.app.lock,self.store.lock,self.store.connect() as db:
   if self.app.closing or self.app.job or self.app.conversation_waiters:return False
   db.execute('BEGIN IMMEDIATE');rows=db.execute("SELECT payload FROM action_reservations WHERE state='queued' AND room NOT IN(SELECT room FROM room_states WHERE archived=1) AND json_extract(payload,'$.due')<=? ORDER BY json_extract(payload,'$.due') LIMIT 20",(now,)).fetchall()
   for row in rows:
    item=json.loads(row[0]);room=item['room']
    if uncertain(db,room):continue
    if item.get('run_id') and db.execute("SELECT 1 FROM autonomy_runs WHERE id=? AND state IN ('queued','running','unknown')",(item['run_id'],)).fetchone():continue
    plan={k:item[k] for k in ('id','kind','intent','details','emotion','context_type','due')}
    run,_=self.app.autonomous.enqueue(room,'action:'+item['id']+':'+str(item['due']),trigger='action',action_plan=plan,_db=db)
    item['run_id']=run['id']
    if item['weekdays']:
     previous=datetime.fromisoformat(item['at']).astimezone(JST);local=datetime.fromtimestamp(now,JST)
     for offset in range(8):
      candidate=(local+timedelta(days=offset)).replace(hour=previous.hour,minute=previous.minute,second=previous.second,microsecond=0)
      if candidate.timestamp()>now and candidate.weekday() in item['weekdays']:item['due']=candidate.timestamp();break
    else:item['state']='dispatched'
    item['revision']=uuid.uuid4().hex;db.execute('UPDATE action_reservations SET state=?,payload=? WHERE id=?',(item['state'],json.dumps(item,ensure_ascii=False),item['id']))
    return True
  return False

def read(app,context,args):
 context.checkpoint();entries=app.action_reservations.listing(context.room)['entries'];revision=hashlib.sha256(json.dumps(entries,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 if args['offset'] and not args['revision']:raise ValueError('続きには一覧のrevisionが必要です。')
 if args['revision'] and args['revision']!=revision:raise EditConflict('予約一覧が変わりました。再確認してください。')
 end=args['offset']+args['limit'];continuation=dict(args,offset=end,revision=revision) if end<len(entries) else None
 return ToolOutput(data=dict(entries=entries[args['offset']:end],revision=revision,total_visible=len(entries),retained_limit=200),truncated=bool(continuation),continuation=continuation,message='予約と自律実行の状態です。登録は実行完了ではありません。')
def change(app,context,args):
 if args['operation']=='cancel':item=app.action_reservations.cancel(context.room,args['id'],args['revision'],context.checkpoint)
 else:
  prepared=app.action_reservations.prepare(args['kind'],args['intent'],args['details'],args['at'],args['weekdays'],args['emotion'],args['context_type'])
  request=hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest();item=app.action_reservations.create(context.room,prepared,request,context.checkpoint)
 return ToolOutput(data=item,changes=[dict(kind='action_reservation',id=item['id'],state=item['state'])],message='行動予約を保存しました。期限後に会話を優先して自律実行へ渡し、結果は実行履歴に残します。OS/外部通知はしません。')
PROPS={'operation':{'type':'string','enum':['create','cancel']},'kind':{'type':'string','enum':['action','alarm'],'default':'action'},'intent':{'type':'string','maxLength':2000,'default':''},'details':{'type':'string','maxLength':4000,'default':''},'at':{'type':'string','maxLength':64,'default':''},'weekdays':{'type':'array','items':{'type':'integer','minimum':0,'maximum':6},'maxItems':7,'default':[]},'emotion':{'type':'string','maxLength':200,'default':''},'context_type':{'type':'string','enum':['NEW','CONTINUE','DEEPEN'],'default':'NEW'},'id':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':32,'default':''}}
for _spec in PROPS.values():
 if _spec['type']=='string' and 'maxLength' not in _spec:_spec['maxLength']=max(map(len,_spec['enum']))
ENTRIES=[ToolSpec(schema('read_action_reservations','行動予約・AIアラームと直近の実行状態を参照。続きはoffsetとrevisionを指定。',{'offset':{'type':'integer','minimum':0,'maximum':200,'default':0},'limit':{'type':'integer','minimum':1,'maximum':2,'default':1},'revision':{'type':'string','maxLength':64,'default':''}},[]),read,category='time',operation='read'),ToolSpec(schema('manage_action_reservation','目的と詳細を引き継ぐ行動予約/AIアラームを登録または取消。atはオフセット付きISO日時。曜日はJSTの月0～日6。既存の権限・日次上限で自律実行し、外部/OS通知はしない。',PROPS,['operation']),change,category='time',operation='write',effects=('schedule',))]
