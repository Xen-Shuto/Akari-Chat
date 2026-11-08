"""Room-scoped, opt-in URL monitoring without LLM calls or extra worker threads."""
import difflib
import hashlib
import json
import re
import time
import threading
import uuid
from datetime import datetime
from urllib.parse import urlunsplit
from .autonomy import encoded,now,text
from .research_subscriptions import JST
from .file_editor import EditConflict
from .public_http import parsed_url,RemoteError
from .engine import Generation
from tools.background_guard import BackgroundQueueFull, queue_full

INTERVALS={'manual':0,'hourly_1':3600,'hourly_3':10800,'hourly_6':21600,'hourly_12':43200,'daily':86400}
FIELDS={'title','url','group_id','interval','at','enabled','max_per_day'}


def canonical(url):
    p=parsed_url(url)
    if (p.port or (443 if p.scheme=='https' else 80)) not in (80,443):raise ValueError('標準のHTTP/HTTPSポートを指定してください。')
    host=p.hostname.encode('idna').decode().lower()
    if ':' in host:host='['+host+']'
    if p.port is not None and p.port!=(443 if p.scheme=='https' else 80):host+=':'+str(p.port)
    # Registration never performs DNS or HTTP. Execution revalidates every hop.
    return urlunsplit((p.scheme.lower(),host,p.path or '/',p.query,''))


def due_time(config,clock):
    if not config['enabled'] or config['interval']=='manual':return None
    if config['interval']!='daily':return clock+INTERVALS[config['interval']]
    current=datetime.fromtimestamp(clock,JST);hour,minute=map(int,config['at'].split(':'))
    due=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
    from datetime import timedelta
    return (due if due>current else due+timedelta(days=1)).timestamp()


class URLWatches:
    def __init__(self,store,web,clock=time.time):
        self.store,self.web,self.clock=store,web,clock
        self.warning=''
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS url_watches(id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS url_watches_room ON url_watches(room)')
            db.execute("CREATE INDEX IF NOT EXISTS url_watches_due ON url_watches(json_extract(payload,'$.next_due')) WHERE json_extract(payload,'$.enabled')=1")
            db.execute('CREATE TABLE IF NOT EXISTS url_watch_groups(id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS url_watch_checks(id TEXT PRIMARY KEY,room TEXT NOT NULL,watch_id TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS url_watch_checks_state ON url_watch_checks(state,room)')
            db.execute('CREATE TABLE IF NOT EXISTS url_watch_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS url_watch_versions(id INTEGER PRIMARY KEY,room TEXT,document_id TEXT,payload TEXT,changed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS url_watch_cache(watch_id TEXT PRIMARY KEY,room TEXT NOT NULL,payload TEXT NOT NULL)')
            for row in db.execute("SELECT payload FROM url_watch_checks WHERE state='running'").fetchall():
                item=json.loads(row[0]);item.update(state='unknown',error='前回の取得結果は未確認です。自動再実行しません。',revision=uuid.uuid4().hex)
                self._write_check(db,item)

    def _get(self,db,room,key,group=False):
        table='url_watch_groups' if group else 'url_watches'
        row=db.execute('SELECT payload FROM '+table+' WHERE room=? AND id=?',(room,key)).fetchone()
        if row is None:raise ValueError('同じルームの監視対象/グループを選んでください。')
        return json.loads(row[0])

    def _receipt(self,db,room,request,signature):
        text(request,128,True)
        row=db.execute('SELECT signature,result FROM url_watch_receipts WHERE room=? AND request_id=?',(room,request)).fetchone()
        if row:
            if row[0]!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
            return json.loads(row[1])

    def _remember(self,db,room,request,signature,item):
        db.execute('INSERT INTO url_watch_receipts VALUES(?,?,?,?)',(room,request,signature,encoded(item)))

    def listing(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:
            db.execute('BEGIN')
            entries=[json.loads(r[0]) for r in db.execute('SELECT payload FROM url_watches WHERE room=? ORDER BY rowid DESC LIMIT 50',(room,))]
            groups=[json.loads(r[0]) for r in db.execute('SELECT payload FROM url_watch_groups WHERE room=? ORDER BY rowid DESC LIMIT 20',(room,))]
            checks=[json.loads(r[0]) for r in db.execute("SELECT json_remove(payload,'$.diff') FROM url_watch_checks WHERE room=? ORDER BY (state IN ('queued','running')) DESC,rowid DESC LIMIT 50",(room,))]
        return dict(entries=entries,groups=groups,checks=checks,warning=self.warning)

    def read(self,room,view='watches',key='',offset=0,limit=5,revision=''):
        self.store.room_path(room)
        if type(offset) is not int or not 0<=offset<=1000 or type(limit) is not int or not 1<=limit<=5:raise ValueError('一覧の範囲が不正です。')
        with self.store.connect() as db:
            db.execute('BEGIN')
            if view in ('watch','group'):item=self._get(db,room,key,view=='group')
            elif view=='check':
                row=db.execute('SELECT payload FROM url_watch_checks WHERE room=? AND id=?',(room,key)).fetchone()
                if row is None:raise ValueError('取得記録が見つかりません。')
                item=json.loads(row[0])
            else:
                if view not in ('watches','groups','checks'):raise ValueError('参照の種類が不正です。')
                table={'watches':'url_watches','groups':'url_watch_groups','checks':'url_watch_checks'}[view]
                rows=db.execute('SELECT id,json_extract(payload,"$.revision") FROM '+table+' WHERE room=? ORDER BY rowid DESC',(room,)).fetchall()
                current=hashlib.sha256(encoded([tuple(r) for r in rows]).encode()).hexdigest()
                if revision and revision!=current:raise EditConflict('一覧が変わりました。先頭から読み直してください。')
                rows=db.execute("SELECT json_remove(payload,'$.diff') FROM "+table+' WHERE room=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(room,limit+1,offset)).fetchall()
                return dict(entries=[json.loads(r[0]) for r in rows[:limit]],more=len(rows)>limit,revision=current)
            if revision and item['revision']!=revision:raise EditConflict('取得後に内容が変わりました。')
            return item

    def save(self,room,config,key='',revision='',request_id='',archive=False,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(config,dict) or set(config)!=FIELDS:raise ValueError('監視設定の項目が不正です。')
        config=dict(config,title=text(config['title'],120,True),url=canonical(config['url']),group_id=text(config['group_id'],32))
        if config['interval'] not in INTERVALS or not isinstance(config['at'],str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',config['at']):raise ValueError('頻度または日本時間の時刻が不正です。')
        if type(config['enabled']) is not bool or type(archive) is not bool or type(config['max_per_day']) is not int or not 1<=config['max_per_day']<=24:raise ValueError('有効設定または1日の上限が不正です。')
        signature=encoded(['save',config,key,revision,archive])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');old_receipt=self._receipt(db,room,request_id,signature)
            if old_receipt:return old_receipt,False
            old=self._get(db,room,key) if key else None
            if (old['revision'] if old else '')!=revision:raise EditConflict('監視設定が変わりました。再読込してください。')
            if config['group_id'] and not archive:
                if self._get(db,room,config['group_id'],True)['archived']:raise ValueError('保管済みのグループは指定できません。')
            duplicate=db.execute("SELECT id FROM url_watches WHERE room=? AND id!=? AND json_extract(payload,'$.url')=? AND json_extract(payload,'$.archived')=0",(room,key,config['url'])).fetchone()
            if duplicate and not archive:raise ValueError('同じURLは登録済みです。既存の監視を編集してください。')
            if not old and (db.execute('SELECT COUNT(*) FROM url_watches WHERE room=?',(room,)).fetchone()[0]>=50 or db.execute('SELECT COUNT(*) FROM url_watches').fetchone()[0]>=500):raise ValueError('監視設定の上限は1ルーム50件、全体500件です。')
            if archive:config['enabled']=False
            item=dict(config,id=key or uuid.uuid4().hex,room=room,revision=uuid.uuid4().hex,archived=archive,updated_at=now(),last_error='',next_due=due_time(config,self.clock()))
            if old:
                for field in ('last_check_id','last_checked_at','last_result'):item[field]=old.get(field)
            checkpoint()
            if old:db.execute('INSERT INTO url_watch_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,key,encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO url_watches VALUES(?,?,?)',(item['id'],room,encoded(item)))
            if old and old['url']!=item['url']:db.execute('DELETE FROM url_watch_cache WHERE room=? AND watch_id=?',(room,key))
            self._remember(db,room,request_id,signature,item)
        return item,True

    def group(self,room,title,key='',revision='',request_id='',archive=False,interval='',checkpoint=lambda:None):
        self.store.room_path(room);title=text(title,80,True)
        if type(archive) is not bool or interval and interval not in INTERVALS:raise ValueError('グループの設定が不正です。')
        signature=encoded(['group',title,key,revision,archive,interval])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            old=self._get(db,room,key,True) if key else None
            if (old['revision'] if old else '')!=revision:raise EditConflict('グループが変わりました。')
            if not old and db.execute('SELECT COUNT(*) FROM url_watch_groups WHERE room=?',(room,)).fetchone()[0]>=20:raise ValueError('グループは1ルーム20件までです。')
            duplicate=db.execute("SELECT 1 FROM url_watch_groups WHERE room=? AND id!=? AND json_extract(payload,'$.title')=? AND json_extract(payload,'$.archived')=0",(room,key,title)).fetchone()
            if duplicate and not archive:raise ValueError('同じ名前のグループが存在します。')
            item=dict(id=key or uuid.uuid4().hex,room=room,title=title,archived=archive,revision=uuid.uuid4().hex,updated_at=now())
            members=[json.loads(r[0]) for r in db.execute("SELECT payload FROM url_watches WHERE room=? AND json_extract(payload,'$.group_id')=?",(room,item['id']))]
            checkpoint()
            for member in members:
                if not archive and not interval:continue
                db.execute('INSERT INTO url_watch_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,member['id'],encoded(member),now()))
                if archive:member['group_id']=''
                if interval:member['interval']=interval;member['next_due']=due_time(member,self.clock())
                member.update(revision=uuid.uuid4().hex,updated_at=now())
                db.execute('UPDATE url_watches SET payload=? WHERE id=?',(encoded(member),member['id']))
            if old:db.execute('INSERT INTO url_watch_versions(room,document_id,payload,changed_at) VALUES(?,?,?,?)',(room,item['id'],encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO url_watch_groups VALUES(?,?,?)',(item['id'],room,encoded(item)))
            self._remember(db,room,request_id,signature,item)
        return item,True

    def _write_check(self,db,item):
        db.execute('UPDATE url_watch_checks SET state=?,payload=? WHERE room=? AND id=?',(item['state'],encoded(item),item['room'],item['id']))

    def queue(self,app,room,key,revision,request_id,origin='user',checkpoint=lambda:None):
        self.store.room_path(room)
        if origin not in ('user','model','schedule'):raise ValueError('起動元が不正です。')
        signature=encoded(['queue',key,revision,origin])
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._receipt(db,room,request_id,signature)
            if previous:return previous,False
            watch=self._get(db,room,key)
            if watch['revision']!=revision:raise EditConflict('監視設定が変わりました。')
            if watch['archived'] or origin!='user' and not watch['enabled']:raise ValueError('監視が停止されています。')
            if not app.autonomous.settings(room)['allow_web']:raise ValueError('自律実行設定でWeb利用を許可してください。')
            if db.execute("SELECT 1 FROM url_watch_checks WHERE room=? AND watch_id=? AND state IN ('queued','running','unknown') AND COALESCE(json_extract(payload,'$.reviewed'),0)=0",(room,key)).fetchone():raise ValueError('同じURLは待機/実行中、または結果未確認です。')
            if queue_full(db):
                raise BackgroundQueueFull()
            item=dict(id=uuid.uuid4().hex,room=room,watch_id=key,watch_revision=revision,url=watch['url'],title=watch['title'],origin=origin,state='queued',revision=uuid.uuid4().hex,created_at=now(),error='',diff='')
            checkpoint()
            db.execute('INSERT INTO url_watch_checks VALUES(?,?,?,?,?)',(item['id'],room,key,'queued',encoded(item)))
            watch.update(next_due=due_time(watch,self.clock()),last_error='')
            db.execute('UPDATE url_watches SET payload=? WHERE id=?',(encoded(watch),key))
            self._remember(db,room,request_id,signature,item)
        return item,True

    def cancel(self,app,room,key,revision,acknowledge=False):
        self.store.room_path(room)
        with app.lock,self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM url_watch_checks WHERE room=? AND id=?',(room,key)).fetchone()
            if row is None:raise ValueError('取得記録が見つかりません。')
            item=json.loads(row[0])
            if item['revision']!=revision:raise EditConflict('取得状態が変わりました。')
            if acknowledge:
                if item['state']!='unknown':raise ValueError('結果未確認の取得だけを確認済みにできます。')
                item.update(reviewed=True)
            elif item['state']=='queued':item.update(state='cancelled',error='開始前に取り消しました。')
            elif item['state']=='running':
                if app.job and app.job['id']==key:app.job['generation'].cancel();item['cancel_requested']=True
            item['revision']=uuid.uuid4().hex;self._write_check(db,item)
        if acknowledge:self.warning=''
        return item

    def poll(self,app):
        clock=self.clock()
        with self.store.connect() as db:
            watches=[json.loads(r[0]) for r in db.execute("SELECT payload FROM url_watches WHERE room NOT IN (SELECT room FROM room_states WHERE archived=1) AND json_extract(payload,'$.enabled')=1 AND json_extract(payload,'$.next_due')<=? ORDER BY json_extract(payload,'$.next_due') LIMIT 20",(clock,))]
        for watch in watches:
            request='due:'+hashlib.sha256(encoded([watch['id'],watch['revision'],watch['next_due']]).encode()).hexdigest()
            try:self.queue(app,watch['room'],watch['id'],watch['revision'],request,'schedule')
            except ValueError as error:
                with self.store.lock,self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE');current=self._get(db,watch['room'],watch['id'])
                    if current['revision']==watch['revision']:
                        current.update(next_due=due_time(current,clock),last_error=str(error));db.execute('UPDATE url_watches SET payload=? WHERE id=?',(encoded(current),current['id']))

    def tick(self,app,*,owner=None,room=None,key=None):
        """Claim one HTTP read under the same conversation gate as autonomy."""
        with app.lock:
            if self.warning or app.closing or (app.job is not owner if owner is not None else app.job or app.conversation_waiters):return False
            if owner is None:self.poll(app)
            with self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute("SELECT payload FROM url_watch_checks c WHERE c.room NOT IN (SELECT room FROM room_states WHERE archived=1) AND state='queued' AND NOT EXISTS (SELECT 1 FROM url_watch_checks u WHERE u.room=c.room AND u.state='unknown' AND COALESCE(json_extract(u.payload,'$.reviewed'),0)=0) AND NOT EXISTS (SELECT 1 FROM autonomy_runs a WHERE a.room=c.room AND a.state='unknown' AND COALESCE(json_extract(a.payload,'$.reviewed'),0)=0) AND NOT EXISTS(SELECT 1 FROM sleep_night_jobs sleep_unknown WHERE sleep_unknown.room=c.room AND sleep_unknown.state='unknown' AND sleep_unknown.reviewed=0) AND (? IS NULL OR c.room=?) AND (? IS NULL OR c.id=?) ORDER BY rowid LIMIT 1",(room,room,key,key)).fetchone()
                if row is None:return False
                item=json.loads(row[0]);watch=self._get(db,item['room'],item['watch_id'])
                day=datetime.fromtimestamp(self.clock(),JST).date().isoformat()
                used=db.execute("SELECT COUNT(*) FROM url_watch_checks WHERE room=? AND json_extract(payload,'$.day_jst')=?",(item['room'],day)).fetchone()[0]
                own=db.execute("SELECT COUNT(*) FROM url_watch_checks WHERE room=? AND watch_id=? AND json_extract(payload,'$.day_jst')=?",(item['room'],watch['id'],day)).fetchone()[0]
                if watch['revision']!=item['watch_revision'] or watch['archived'] or item['origin']!='user' and not watch['enabled'] or not app.autonomous.settings(item['room'])['allow_web'] or used>=48 or own>=watch['max_per_day']:
                    item.update(state='cancelled',error='設定変更・許可不足・1日の上限により取得しませんでした。',revision=uuid.uuid4().hex);self._write_check(db,item);return True
                item.update(state='running',day_jst=day,started_at=now(),revision=uuid.uuid4().hex);self._write_check(db,item)
            job=dict(id=item['id'],kind='autonomy',url_watch=True,room=item['room'],generation=Generation(thinking_mode=self.store.settings().get('thinking_mode','auto')),finished=threading.Event(),executing=True)
            if owner is not None:
                job['generation'].cancelled=owner['generation'].cancelled
                owner.setdefault('nested_generations',[]).append(job['generation'])
            else:app.job=job
        def checkpoint():
            if app.closing or job['generation'].cancelled.is_set():raise InterruptedError('取得を中止しました。')
        try:
            try:
                checkpoint();doc,_=self.web.read(item['room'],url=item['url'],limit=50000,checkpoint=checkpoint)
                if doc['source_may_be_truncated'] or doc['total_chars']>50000:raise RemoteError('監視本文の上限は50000文字です。基準は変更していません。')
                app.watch_extensions.summarize(item,doc,job,checkpoint)
                checkpoint();self.finish(item,doc,checkpoint=checkpoint)
                try:app.watch_extensions.notify(item)
                except Exception:pass
            except (ValueError,OSError) as error:
                self.finish(item,None,'cancelled' if isinstance(error,InterruptedError) else 'failed',str(error))
        except Exception:
            self.warning='URL取得の完了保存を確認できません。取得履歴を確認してください。'
            with self.store.lock,self.store.connect() as db:
                row=db.execute('SELECT payload FROM url_watch_checks WHERE id=?',(item['id'],)).fetchone()
                uncertain=json.loads(row[0]);uncertain.update(state='unknown',error=self.warning,revision=uuid.uuid4().hex);self._write_check(db,uncertain)
            raise
        finally:
            with app.lock:
                if app.job is job:app.job=None
                job['finished'].set()
        return True

    def finish(self,item,doc,state='succeeded',error='',checkpoint=lambda:None):
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=json.loads(db.execute('SELECT payload FROM url_watch_checks WHERE room=? AND id=?',(item['room'],item['id'])).fetchone()[0])
            if current['state']!='running':raise EditConflict('取得処理は実行中ではありません。')
            watch=self._get(db,item['room'],item['watch_id'])
            current.update(state=state,error=error,completed_at=now(),revision=uuid.uuid4().hex)
            if doc is not None:
                if watch['revision']!=item['watch_revision']:current.update(state='conflict',error='取得中に監視設定が変わりました。基準は変更しません。')
                else:
                    current.update({k:doc[k] for k in ('summary','summary_error','summary_source') if k in doc})
                    before=db.execute('SELECT payload FROM url_watch_cache WHERE room=? AND watch_id=?',(item['room'],watch['id'])).fetchone()
                    before=json.loads(before[0]) if before else None
                    digest=hashlib.sha256(re.sub(r'\s+',' ',doc['text']).strip().encode()).hexdigest()
                    result='baseline' if before is None else 'unchanged' if before['digest']==digest else 'changed'
                    current.update(result=result,final_url=doc['url'],before_digest=before['digest'] if before else '',digest=digest)
                    if result=='changed':
                        lines=difflib.unified_diff(before['text'].splitlines(),doc['text'].splitlines(),fromfile='previous',tofile='current',n=2)
                        # Stream a bounded preview; comparison uses the entire accepted text.
                        preview=''
                        for line in lines:
                            preview+=line[:4001]+'\n'
                            if len(preview)>4000:break
                        current.update(diff=preview[:4000],diff_truncated=len(preview)>4000)
                        note=dict(id=uuid.uuid4().hex,schedule_id='url-watch:'+item['id'],room=item['room'],title='URL更新: '+watch['title'],message=watch['url']+'\n取得した本文に変更があります。URL監視の取得履歴で差分を確認してください。',created_at=now(),phase='',cycle=0,step=0,late_seconds=0,watch_check_id=item['id'])
                        db.execute('INSERT INTO local_notifications VALUES(?,?,?,?,?,0)',(note['id'],item['room'],note['schedule_id'],0,encoded(note)))
                    cache=dict(text=doc['text'],digest=digest,final_url=doc['url'],fetched_at=doc['fetched_at'])
                    db.execute('INSERT OR REPLACE INTO url_watch_cache VALUES(?,?,?)',(watch['id'],item['room'],encoded(cache)))
            checkpoint();self._write_check(db,current)
            if watch['revision']==item['watch_revision']:
                watch.update(last_check_id=item['id'],last_checked_at=now(),last_result=current.get('result',current['state']),last_error=current['error'])
                db.execute('UPDATE url_watches SET payload=? WHERE id=?',(encoded(watch),watch['id']))
