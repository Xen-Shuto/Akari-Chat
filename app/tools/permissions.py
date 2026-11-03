"""Room policy narrows existing tool scopes; only the owner UI can edit it."""
import hashlib
import json
import threading
import uuid
import time
from .capability_tools import CATEGORIES
from .contracts import ToolFailure,ToolContext,ToolOutput,validate
from litechat.resources import strict_json
from litechat.autonomy import encoded,now
from litechat.file_editor import EditConflict

MODES={'off':frozenset(),'ask':frozenset(),'read':frozenset({'read'}),'propose':frozenset({'read','propose'}),'write':frozenset({'read','propose','write'})}


class ToolPermissions:
    def __init__(self,app,registry):
        self.app,self.store,self.registry=app,app.store,registry
        self.lock=threading.RLock();self.active=0
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tool_policies(room TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS tool_policy_versions(id INTEGER PRIMARY KEY,room TEXT,payload TEXT,created TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS tool_policy_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS tool_approvals(id TEXT PRIMARY KEY,room TEXT,payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS tool_approvals_room ON tool_approvals(room)')
            db.execute('CREATE TABLE IF NOT EXISTS tool_approval_events(id INTEGER PRIMARY KEY,room TEXT,approval_id TEXT,payload TEXT,created TEXT)')
            for row in db.execute("SELECT payload FROM tool_approvals WHERE json_extract(payload,'$.state')='running'").fetchall():
                item=json.loads(row[0])
                if item['state']=='running':
                    item.update(state='unknown',revision=uuid.uuid4().hex)
                    self._approval_write(db,item,'restart_unknown')

    def _settings(self,db,room):
        row=db.execute('SELECT payload FROM tool_policies WHERE room=?',(room,)).fetchone()
        if row is None:return dict(levels={key:'write' for key in CATEGORIES},revision='unconfigured',configured=False)
        data=json.loads(row[0])
        if not isinstance(data,dict) or not isinstance(data.get('levels'),dict) or not isinstance(data.get('revision'),str):raise ValueError('ツール権限の保存データが不正です。')
        levels={key:data['levels'].get(key,'off') for key in CATEGORIES}
        if any(not isinstance(value,str) or value not in MODES for value in levels.values()):raise ValueError('ツール権限の保存データが不正です。')
        return dict(levels=levels,revision=data['revision'],configured=True)

    def settings(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:return self._settings(db,room)

    @staticmethod
    def permits(spec,settings):
        if spec.category=='capabilities' and spec.name in ('request_capability','request_tool_permission'):return True
        return spec.operation in MODES[settings['levels'].get(spec.category,'off')]

    def acquire(self,room,spec,context=None,args=None):
        with self.lock:
            settings=self.settings(room)
            approval_used=False
            if not self.permits(spec,settings):
                if context is None or not self.consume(spec,context,args,settings):raise ToolFailure('permission_denied','このルームのツール権限で許可されていません。毎回確認の場合は個別申請が必要です。')
                approval_used=True
            self.active+=1
            return approval_used

    def release(self):
        with self.lock:self.active-=1

    def catalog(self,room):
        settings=self.settings(room);context=ToolContext(room,'permission-preview',interactive=True)
        return dict(room=room,settings=settings,categories=CATEGORIES,tools=[dict(item,permitted=self.permits(self.registry.get(item['name']),settings),available=self.registry.usable(self.registry.get(item['name']),self.app,context)) for item in self.registry.catalog()])

    def save(self,room,levels,revision,request_id,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(levels,dict) or set(levels)!=set(CATEGORIES) or any(not isinstance(value,str) or value not in MODES for value in levels.values()):raise ValueError('全カテゴリの権限を正しい範囲で指定してください。')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256 or not isinstance(revision,str):raise ValueError('操作IDとrevisionが必要です。')
        signature=hashlib.sha256(encoded([levels,revision]).encode()).hexdigest()
        with self.app.lock,self.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters or self.active:raise ValueError('会話・生成・ツール処理が終わってから権限を変更してください。')
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT signature,result FROM tool_policy_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if old:
                if old[0]!=signature:raise EditConflict('同じ操作IDに別の設定が指定されています。')
                return json.loads(old[1]),False
            current=self._settings(db,room)
            if current['revision']!=revision:raise EditConflict('ツール権限が変更されています。再読込してください。')
            changed=not current['configured'] or levels!=current['levels']
            checkpoint()
            result=current
            if changed:
                result=dict(levels=levels,revision=uuid.uuid4().hex,configured=True)
                db.execute('INSERT INTO tool_policy_versions(room,payload,created) VALUES(?,?,?)',(room,encoded(current),now()))
                db.execute('INSERT OR REPLACE INTO tool_policies VALUES(?,?)',(room,encoded(result)))
            db.execute('INSERT INTO tool_policy_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(result)))
        return result,changed

    def _approval_write(self,db,item,event):
        db.execute('INSERT INTO tool_approvals VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(item['id'],item['room'],encoded(item)))
        db.execute('INSERT INTO tool_approval_events(room,approval_id,payload,created) VALUES(?,?,?,?)',
                   (item['room'],item['id'],encoded(dict(event=event,state=item['state'],revision=item['revision'])),now()))

    def _spec_hash(self,spec):
        return hashlib.sha256(encoded([spec.definition,spec.category,spec.operation,list(spec.effects)]).encode()).hexdigest()

    def _current(self,item,settings):
        spec=self.registry.get(item['tool'])
        return spec is not None and item['policy_revision']==settings['revision'] and settings['levels'].get(spec.category)=='ask' and item['expires']>time.time() and item['spec_hash']==self._spec_hash(spec)

    def approvals(self,room):
        settings=self.settings(room)
        with self.store.connect() as db:items=[json.loads(row[0]) for row in db.execute("SELECT payload FROM tool_approvals WHERE room=? ORDER BY (json_extract(payload,'$.state') IN ('pending','approved') AND json_extract(payload,'$.expires')>?) DESC,rowid DESC LIMIT 50",(room,time.time()))]
        return [dict(item,current=self._current(item,settings)) for item in items]

    def approved(self,context,settings):
        if not context.interactive:return []
        with self.store.connect() as db:items=[json.loads(row[0]) for row in db.execute("SELECT payload FROM tool_approvals WHERE room=? AND json_extract(payload,'$.state')='approved' AND json_extract(payload,'$.expires')>? ORDER BY rowid DESC LIMIT 20",(context.room,time.time()))]
        return [item for item in items if self._current(item,settings)]

    def request(self,context,args):
        context.checkpoint()
        if not context.interactive:raise ToolFailure('unavailable','個別申請は通常会話のみです。')
        if args['action']=='status':
            matches=[item for item in self.approvals(context.room) if not args['tool_name'] or item['tool']==args['tool_name']]
            items=[{key:item[key] for key in ('id','tool','arguments','intent','state','expires','current')} for item in matches[:2]]
            return ToolOutput(data={'requests':items},truncated=len(matches)>2,message='最新2件まで。tool_nameで絞り込めます。承認は実行済みではありません。approvedかつcurrentの内容のみ一度呼び出せます。')
        spec=self.registry.get(args['tool_name']);settings=self.settings(context.room)
        if spec is None or settings['levels'].get(spec.category)!='ask' or not self.registry.usable(spec,self.app,context):raise ToolFailure('unavailable','毎回確認に設定され、現在利用可能なツールだけ申請できます。')
        if args['action']=='inspect':return ToolOutput(data={'definition':spec.definition,'operation':spec.operation,'effects':list(spec.effects)})
        if not args['intent'].strip():raise ToolFailure('invalid_arguments','申請理由が必要です。')
        try:arguments=validate(strict_json(args['arguments_json']),spec.definition['function']['parameters'])
        except (ValueError,TypeError,RecursionError):raise ToolFailure('invalid_arguments','対象引数のJSONが不正です。') from None
        key=uuid.uuid5(uuid.NAMESPACE_URL,encoded([context.room,context.job_id,context.call_id])).hex
        signature=hashlib.sha256(encoded([spec.name,arguments,args['intent']]).encode()).hexdigest()
        with self.lock,self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');context.checkpoint()
            if self._settings(db,context.room)['revision']!=settings['revision']:raise EditConflict('ツール権限が変更されています。')
            old=db.execute('SELECT payload FROM tool_approvals WHERE id=?',(key,)).fetchone()
            if old:
                item=json.loads(old[0])
                if item['signature']!=signature:raise EditConflict('同じ申請IDに異なる内容が指定されています。')
            else:
                count=db.execute("SELECT COUNT(*) FROM tool_approvals WHERE room=? AND json_extract(payload,'$.state') IN ('pending','approved') AND json_extract(payload,'$.expires')>? AND json_extract(payload,'$.policy_revision')=?",(context.room,time.time(),settings['revision'])).fetchone()[0]
                if count>=20:raise ToolFailure('limit_exceeded','未完了の申請は20件までです。')
                item=dict(id=key,room=context.room,tool=spec.name,arguments=arguments,intent=args['intent'],state='pending',revision=uuid.uuid4().hex,
                          signature=signature,policy_revision=settings['revision'],spec_hash=self._spec_hash(spec),created=now(),expires=time.time()+3600)
                self._approval_write(db,item,'request')
        return ToolOutput(status='pending',data={'request_id':key,'state':item['state']},message='設定のツール権限へ個別申請を保存しました。処理は未実行です。人の承認後、次の通常会話で同じ引数を一度呼べます。')

    def decide(self,room,key,revision,action,request_id):
        self.store.room_path(room)
        if action not in ('approve','reject') or not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('承認操作が不正です。')
        signature=hashlib.sha256(encoded([revision,action]).encode()).hexdigest()
        with self.app.lock,self.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters or self.active:raise ValueError('会話・ツール処理の終了後に操作してください。')
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM tool_approvals WHERE id=? AND room=?',(key,room)).fetchone()
            if row is None:raise ValueError('申請が見つかりません。')
            item=json.loads(row[0])
            if item.get('decision_id')==request_id:
                if item.get('decision_signature')!=signature:raise EditConflict('同じ操作IDに異なる判断が指定されています。')
                return item
            if item['revision']!=revision:raise EditConflict('申請が変更されています。再読込してください。')
            if item['state'] not in ('pending','approved') or action=='approve' and item['state']!='pending':raise EditConflict('この申請は判断済みです。')
            if action=='approve' and not self._current(item,self._settings(db,room)):raise EditConflict('期限・権限設定・ツール定義が変わっています。再申請してください。')
            item.update(state='approved' if action=='approve' else 'rejected',revision=uuid.uuid4().hex,decision_id=request_id,decision_signature=signature,decided=now())
            self._approval_write(db,item,action)
        return item

    def consume(self,spec,context,args,settings):
        if not context.interactive or settings['levels'].get(spec.category)!='ask':return False
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT payload FROM tool_approvals WHERE room=? AND json_extract(payload,'$.tool')=? AND (json_extract(payload,'$.state')='approved' OR (json_extract(payload,'$.use_job')=? AND json_extract(payload,'$.use_call')=?)) ORDER BY (json_extract(payload,'$.use_job')=? AND json_extract(payload,'$.use_call')=?) DESC,rowid",(context.room,spec.name,context.job_id,context.call_id,context.job_id,context.call_id)).fetchall():
                item=json.loads(row[0])
                if item['tool']!=spec.name or item['arguments']!=args or not self._current(item,settings):continue
                if item['state'] in ('running','used','unknown') and item.get('use_job')==context.job_id and item.get('use_call')==context.call_id:
                    # A crash between consuming the grant and claiming the tool
                    # must never turn a retry into a new execution.
                    return db.execute('SELECT 1 FROM tool_invocations WHERE room=? AND job=? AND call_id=?',(context.room,context.job_id,context.call_id)).fetchone() is not None
                if item['state']!='approved':continue
                context.checkpoint()
                item.update(state='running',revision=uuid.uuid4().hex,use_job=context.job_id,use_call=context.call_id)
                self._approval_write(db,item,'consume');return True
        return False

    def finish(self,context,result):
        with self.lock,self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT payload FROM tool_approvals WHERE room=? AND json_extract(payload,'$.state')='running' AND json_extract(payload,'$.use_job')=? AND json_extract(payload,'$.use_call')=?",(context.room,context.job_id,context.call_id)).fetchall():
                item=json.loads(row[0])
                if item['state']=='running' and item.get('use_job')==context.job_id and item.get('use_call')==context.call_id:
                    item.update(state='unknown' if result['status']=='unknown' else 'used',revision=uuid.uuid4().hex,result_status=result['status'],finished=now())
                    self._approval_write(db,item,'finish')
