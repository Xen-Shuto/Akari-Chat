"""Opt-in JST research schedules sharing the bounded autonomy queue.

Reports are review material, never silently appended to a research document.
"""
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from .autonomy import encoded, now, text
from .file_editor import EditConflict

JST=timezone(timedelta(hours=9))
FIELDS={'thread_id','focus','source','frequency','at','weekday','enabled','max_per_day'}


def next_due(config,clock):
    if not config['enabled'] or config['frequency']=='manual':return None
    current=datetime.fromtimestamp(clock,JST)
    hour,minute=map(int,config['at'].split(':'))
    due=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if config['frequency']=='weekly':due+=timedelta(days=(config['weekday']-due.weekday())%7)
    if due<=current:due+=timedelta(days=7 if config['frequency']=='weekly' else 1)
    return due.timestamp()


class ResearchSubscriptions:
    def __init__(self,store,autonomy):
        self.store,self.autonomy=store,autonomy
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS research_subscriptions(id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS research_subscriptions_room ON research_subscriptions(room)')
            db.execute("CREATE INDEX IF NOT EXISTS research_subscriptions_due ON research_subscriptions(json_extract(payload,'$.next_due')) WHERE json_extract(payload,'$.enabled')=1")
            db.execute('CREATE TABLE IF NOT EXISTS research_subscription_versions(id INTEGER PRIMARY KEY,room TEXT,subscription_id TEXT,payload TEXT,changed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS research_subscription_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS research_reports(id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS research_reports_room ON research_reports(room)')

    def listing(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:
            db.execute('BEGIN')
            entries=[json.loads(r[0]) for r in db.execute('SELECT payload FROM research_subscriptions WHERE room=? ORDER BY rowid DESC LIMIT 100',(room,))]
            reports=[json.loads(r[0]) for r in db.execute('SELECT payload FROM research_reports WHERE room=? ORDER BY rowid DESC LIMIT 20',(room,))]
        return dict(subscriptions=entries,reports=reports)

    def read(self,room,view='list',key='',offset=0,limit=5,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=100 or type(limit) is not int or not 1<=limit<=5:raise ValueError('一覧の範囲が不正です。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            if view=='subscription':
                item=self._get(db,room,key)
                if revision and item['revision']!=revision:raise EditConflict('研究購読が変わりました。')
                return item
            if view=='report':
                row=db.execute('SELECT payload FROM research_reports WHERE room=? AND id=?',(room,key)).fetchone()
                if row is None:raise ValueError('研究レポートが見つかりません。')
                item=json.loads(row[0])
                if revision and item['revision']!=revision:raise EditConflict('研究レポートが変わりました。')
                return item
            if view not in ('list','reports'):raise ValueError('参照の種類が不正です。')
            table='research_reports' if view=='reports' else 'research_subscriptions'
            versions=db.execute('SELECT id,json_extract(payload,"$.revision") FROM '+table+' WHERE room=? ORDER BY rowid DESC',(room,)).fetchall()
            current=hashlib.sha256(encoded([tuple(row) for row in versions]).encode()).hexdigest()
            if revision and revision!=current:raise EditConflict('研究購読の一覧が変わりました。先頭から読み直してください。')
            rows=db.execute('SELECT payload FROM '+table+' WHERE room=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(room,limit+1,offset)).fetchall()
            entries=[json.loads(r[0]) for r in rows[:limit]]
            if view=='reports':entries=[{k:v for k,v in item.items() if k not in ('text','sources')} for item in entries]
        return dict(entries=entries,more=len(rows)>limit,offset=offset,revision=current)

    def _get(self,db,room,key):
        row=db.execute('SELECT payload FROM research_subscriptions WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('研究購読が見つかりません。')
        return json.loads(row[0])

    def _thread(self,db,room,key):
        row=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND id=? AND kind="thread"',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームの研究スレッドを選んでください。')
        item=json.loads(row[0])
        if item['state']!='active':raise ValueError('進行中の研究スレッドを選んでください。')
        return item

    def _receipt(self,db,room,request,signature):
        text(request,128,True)
        row=db.execute('SELECT signature,result FROM research_subscription_receipts WHERE room=? AND request_id=?',(room,request)).fetchone()
        if row:
            if row[0]!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
            return json.loads(row[1])

    def save(self,room,config,key='',revision='',request_id='',archive=False,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(config,dict) or set(config) not in (FIELDS,FIELDS|{'auto_apply'}):raise ValueError('研究購読の項目が不正です。')
        config=dict(config,thread_id=text(config['thread_id'],64,True),focus=text(config['focus'],600))
        if config['source'] not in ('local','web') or config['frequency'] not in ('manual','daily','weekly'):raise ValueError('情報源または頻度が不正です。')
        if not isinstance(config['at'],str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',config['at']):raise ValueError('時刻はHH:MM形式です。')
        if type(config['weekday']) is not int or not 0<=config['weekday']<=6 or type(config['max_per_day']) is not int or not 1<=config['max_per_day']<=6 or type(config['enabled']) is not bool or type(archive) is not bool:raise ValueError('曜日・回数・有効設定が不正です。')
        if type(config.get('auto_apply',False)) is not bool or config.get('auto_apply',False) and config['source']!='local':raise ValueError('自動追記はローカル購読だけでtrue/falseを指定してください。')
        signed=dict(config)
        if not signed.get('auto_apply'):signed.pop('auto_apply',None)
        signature=encoded(['save',signed,key,revision,archive])
        config['auto_apply']=config.get('auto_apply',False)
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            old=self._get(db,room,key) if key else None
            if (old['revision'] if old else '')!=revision:raise EditConflict('購読設定が変わりました。再読込してください。')
            if not archive:self._thread(db,room,config['thread_id'])
            if not old and db.execute('SELECT COUNT(*) FROM research_subscriptions WHERE room=?',(room,)).fetchone()[0]>=100:raise ValueError('購読設定は1ルーム100件までです。')
            if archive:config['enabled']=False
            item=dict(config,id=key or uuid.uuid4().hex,room=room,revision=uuid.uuid4().hex,archived=archive,updated_at=now(),last_error='')
            item['next_due']=next_due(item,time.time())
            checkpoint()
            if old:db.execute('INSERT INTO research_subscription_versions(room,subscription_id,payload,changed_at) VALUES(?,?,?,?)',(room,key,encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO research_subscriptions VALUES(?,?,?)',(item['id'],room,encoded(item)))
            db.execute('INSERT INTO research_subscription_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(item)))
        return item,True

    def queue(self,runner,room,key,revision,request_id,scheduled=False,clock=None):
        clock=time.time() if clock is None else clock
        self.store.room_path(room)
        config=runner.settings(room)
        signature=encoded(['queue',key,revision,scheduled])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            sub=self._get(db,room,key)
            if sub['revision']!=revision:raise EditConflict('購読設定が変わりました。')
            if sub['archived'] or scheduled and not sub['enabled']:raise ValueError('購読が停止されています。')
            thread=self._thread(db,room,sub['thread_id'])
            if sub['source']=='web' and not config['allow_web']:raise ValueError('このルームの自律設定でWeb利用を許可してください。')
            pending=db.execute("SELECT 1 FROM autonomy_runs WHERE room=? AND state IN ('queued','running','unknown') AND json_extract(payload,'$.research.id')=? AND COALESCE(json_extract(payload,'$.reviewed'),0)=0",(room,key)).fetchone()
            if pending:raise ValueError('この購読は待機・実行中、または結果未確認です。')
            day=datetime.fromtimestamp(clock,JST).date().isoformat()
            used=db.execute("SELECT COUNT(*) FROM autonomy_runs WHERE room=? AND json_extract(payload,'$.research.id')=? AND json_extract(payload,'$.day_jst')=?",(room,key,day)).fetchone()[0]
            if used>=sub['max_per_day']:raise ValueError('この研究購読は1日の実行上限に達しました。')
            snapshot=dict(id=key,revision=revision,thread_id=thread['id'],thread_revision=thread['revision'],title=thread['title'],focus=sub['focus'],source=sub['source'],auto_apply=sub.get('auto_apply',False),scheduled=scheduled)
            item,_=runner.enqueue(room,'research:'+hashlib.sha256(request_id.encode()).hexdigest(),trigger='research',research=snapshot,_db=db)
            sub.update(last_run_id=item['id'],last_error='',next_due=next_due(sub,clock))
            db.execute('UPDATE research_subscriptions SET payload=? WHERE id=?',(encoded(sub),key))
            db.execute('INSERT INTO research_subscription_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(item)))
        return item,True

    def poll(self,runner,clock=None):
        clock=time.time() if clock is None else clock
        with self.store.connect() as db:
            due=[json.loads(r[0]) for r in db.execute("SELECT payload FROM research_subscriptions WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) AND json_extract(payload,'$.enabled')=1 AND json_extract(payload,'$.next_due')<=? LIMIT 100",(clock,))]
        for sub in due:
            request='due:'+hashlib.sha256(encoded([sub['id'],sub['revision'],sub['next_due']]).encode()).hexdigest()
            try:self.queue(runner,sub['room'],sub['id'],sub['revision'],request,True,clock)
            except (ValueError,EditConflict) as error:
                with self.store.lock,self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE');current=self._get(db,sub['room'],sub['id'])
                    if current['revision']==sub['revision']:
                        current.update(next_due=next_due(current,clock),last_error=str(error));db.execute('UPDATE research_subscriptions SET payload=? WHERE id=?',(encoded(current),sub['id']))

    def validate_run(self,db,item,config,day):
        sub=self._get(db,item['room'],item['research']['id']);snapshot=item['research']
        if sub['revision']!=snapshot['revision'] or sub['archived'] or snapshot['scheduled'] and not sub['enabled']:raise ValueError('研究購読の設定変更により実行しません。')
        thread=self._thread(db,item['room'],snapshot['thread_id'])
        if thread['revision']!=snapshot['thread_revision']:raise ValueError('研究本文が変わったため、再度実行してください。')
        if snapshot['source']=='web' and not config['allow_web']:raise ValueError('Web利用が許可されていません。')
        used=db.execute("SELECT COUNT(*) FROM autonomy_runs WHERE room=? AND json_extract(payload,'$.research.id')=? AND json_extract(payload,'$.day_jst')=?",(item['room'],sub['id'],day)).fetchone()[0]
        if used>=sub['max_per_day']:raise ValueError('研究購読の1日上限に達しました。')

    def finish(self,db,item,sources):
        if 'research' not in item:return
        # A generated narrative alone is not evidence of a retrieval operation.
        report=dict(id=item['id'],room=item['room'],subscription_id=item['research']['id'],thread_id=item['research']['thread_id'],thread_revision=item['research']['thread_revision'],
                    title=item['research']['title'],state='ready' if item['state']=='succeeded' and sources and item['text'].strip() else 'unverified',run_state=item['state'],
                    text=item['text'],sources=sources,created_at=now(),revision=uuid.uuid4().hex)
        db.execute('INSERT INTO research_reports VALUES(?,?,?)',(report['id'],item['room'],encoded(report)))
        if item['research'].get('auto_apply'):
            db.execute('SAVEPOINT research_auto_apply')
            try:
                sub=self._get(db,item['room'],item['research']['id'])
                if sub['revision']!=item['research']['revision'] or not sub.get('auto_apply') or sub['archived'] or sub['source']!='local':raise EditConflict('購読設定が変更されています。')
                if report['state']!='ready':raise ValueError('参照結果のある正常終了レポートではありません。')
                thread=self._thread(db,item['room'],report['thread_id'])
                if thread['revision']!=report['thread_revision']:raise EditConflict('研究本文が変更されています。')
                body=thread['body']+'\n\n【研究レポート '+report['created_at']+'】\n'+report['text']
                self.autonomy.save(item['room'],'thread',thread['title'],body,thread['state'],thread['next_action'],thread['id'],thread['revision'],'auto-research-report:'+report['id'],_db=db)
                db.execute('RELEASE research_auto_apply')
                report.update(state='applied',auto_apply_state='applied',revision=uuid.uuid4().hex)
            except Exception:
                db.execute('ROLLBACK TO research_auto_apply');db.execute('RELEASE research_auto_apply')
                report.update(auto_apply_state='review',auto_apply_error='自動追記を見送りました。購読設定・研究本文・参照・保存結果を確認してください。')
            db.execute('UPDATE research_reports SET payload=? WHERE room=? AND id=?',(encoded(report),item['room'],report['id']))

    def apply(self,room,key,revision,request_id):
        self.store.room_path(room)
        signature=encoded(['apply',key,revision])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            row=db.execute('SELECT payload FROM research_reports WHERE room=? AND id=?',(room,key)).fetchone()
            if row is None:raise ValueError('研究レポートが見つかりません。')
            report=json.loads(row[0])
            if report['revision']!=revision:raise EditConflict('レポートの状態が変わりました。')
            if report['state']!='ready':raise ValueError('参照結果のある完了レポートだけを追記できます。')
            thread=self._thread(db,room,report['thread_id'])
            if thread['revision']!=report['thread_revision']:raise EditConflict('研究本文が変わりました。レポートを確認して手動で編集してください。')
            body=thread['body']+'\n\n【研究レポート '+report['created_at']+'】\n'+report['text']
            # Do not truncate original research or the generated report to fit.
            self.autonomy.save(room,'thread',thread['title'],body,thread['state'],thread['next_action'],thread['id'],thread['revision'],'research-report:'+key,_db=db)
            report.update(state='applied',revision=uuid.uuid4().hex)
            db.execute('UPDATE research_reports SET payload=? WHERE room=? AND id=?',(encoded(report),room,key))
            db.execute('INSERT INTO research_subscription_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(report)))
        return report,True
