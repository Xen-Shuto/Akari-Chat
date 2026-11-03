"""One opening assistant message per room; this is not an AI-callable tool."""
import threading
import os
import uuid
from datetime import datetime,timezone
from litechat.engine import Generation
from litechat.memory_context import build_context
from litechat.file_editor import EditConflict
from litechat.diagnostics import redact

OPENING_REQUEST='（アプリからの開始案内）あなたから会話を始めてください。'


def settings_enabled_from_environment(environment=None):
    environment=os.environ if environment is None else environment
    return environment.get('INIT_SETTING','').casefold()=='y'


def checked_text(value):
    if not isinstance(value,str) or len(value)>4000:raise ValueError('最初のメッセージは4000文字までで指定してください。')
    return value.strip()


class FirstMessage:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS first_messages(room TEXT PRIMARY KEY,state TEXT,mode TEXT,message_id INTEGER,error TEXT)')
            db.execute("UPDATE first_messages SET state='unknown',error='再起動前の生成結果を確認できません。自動再送しません。' WHERE state='running'")

    def queue_created(self,room):
        # Called only after the character wizard creates a room. Importing a
        # character_creation.json file is deliberately not an enqueue operation.
        with self.store.lock,self.store.connect() as db:
            self.store.room_path(room)
            db.execute("INSERT OR IGNORE INTO first_messages VALUES(?,'pending','ai',NULL,'')",(room,))

    def status(self,room):
        if not (self.store.room_path(room)/'room_config.json').is_file():return dict(state='unconfigured',mode='fixed')
        with self.store.connect() as db:
            initial=db.execute('SELECT * FROM initial_log_imports WHERE room=?',(room,)).fetchone()
            row=db.execute('SELECT * FROM first_messages WHERE room=?',(room,)).fetchone()
        if initial:return dict(state=initial['state'],mode='fixed',message_id=initial['first_id'])
        if row and row['state'] in ('complete','skipped'):return dict(row)
        doc=self.app.room_management.snapshot(room)
        if doc['first_message']:return dict(room=room,state='pending',mode='fixed')
        if row:
            result=dict(row);job=self.app.job
            if row['state']=='running' and job and job.get('kind')=='first_message' and job['room']==room:result['job_id']=job['id']
            return result
        return dict(room=room,state='pending' if doc['first_message'] else 'unconfigured',mode='fixed',message_id=None,error='')

    def ensure(self,room,retry=False,request_id=None):
        app=self.app
        if type(retry) is not bool:raise ValueError('再試行の指定が不正です。')
        if request_id is not None:
            try:request_id=str(uuid.UUID(request_id))
            except (ValueError,TypeError,AttributeError):raise ValueError('操作IDが不正です。') from None
        with app.lock,self.store.lock:
            self.store.room_path(room);self.store.index_room(room)
            if app.closing or app.job or app.conversation_waiters:return dict(state='waiting')
            with self.store.connect() as db:initial=db.execute('SELECT * FROM initial_log_imports WHERE room=?',(room,)).fetchone()
            if initial:return dict(state=initial['state'],mode='fixed',message_id=initial['first_id'])
            doc=app.room_management.snapshot(room);text=checked_text(doc['first_message'])
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM first_messages WHERE room=?',(room,)).fetchone()
                if row and row['state'] in ('complete','skipped'):return dict(row)
                if db.execute('SELECT 1 FROM messages WHERE room=? LIMIT 1',(room,)).fetchone():
                    db.execute("INSERT OR REPLACE INTO first_messages VALUES(?,'skipped',?,NULL,'')",(room,row['mode'] if row else 'fixed'))
                    return dict(state='skipped')
                if row and row['state'] in ('error','cancelled','unknown') and not retry:return dict(row)
                if row and row['state']=='running':return dict(state='waiting')
                if not text and (not row or row['mode']!='ai'):return dict(state='unconfigured')
                mode='fixed' if text else 'ai'
                if mode=='fixed':
                    key=self._append(db,room,text,'')
                    db.execute("INSERT OR REPLACE INTO first_messages VALUES(?,'complete','fixed',?,'')",(room,key))
                    return dict(state='complete',message_id=key,mode='fixed')
                target=app.conversation_api.target(room)
                if target is None and app.engine.status()['state']!='ready':return dict(state='waiting')
                character=self.store.room(room);instructions,_=build_context(character)
                settings=self.store.settings();condition={k:settings[k] for k in ('model','context','max_tokens','temperature','top_p','thinking_mode')}
                api_revision=app.conversation_api.revision(room)
                model_pid=app.engine.status().get('pid') if target is None else None
                prompt=instructions+'\n\nこれは新しい会話の最初の発言です。キャラクターとして、相手に自然に短く話しかけてください。まだ交わしていない会話や実行していない操作を過去の出来事として扱わず、ツールは使わず発言本文だけを返してください。'
                if len(prompt)>max(2000,settings['context']*2):raise ValueError('初回発言用の設定が長すぎます。コンテキスト設定を確認してください。')
                generation=Generation(settings.get('thinking_mode','auto'))
                job=dict(id=request_id or str(uuid.uuid4()),kind='first_message',room=room,generation=generation,executing=True,finished=threading.Event())
                db.execute("INSERT OR REPLACE INTO first_messages VALUES(?,'running','ai',NULL,'')",(room,))
            app.job=job
        timer=threading.Timer(120,generation.cancel);timer.daemon=True;timer.start()
        api_recorded=False
        app.diagnostics.trace('first_message.started',room,job['id'],remote=target is not None)
        try:
            payload=dict(model=target.model if target else 'Local_LLM',stream=True,temperature=settings['temperature'],top_p=settings['top_p'],
                max_tokens=min(settings['max_tokens'] or 512,512),messages=[dict(role='system',content=prompt),dict(role='user',content=OPENING_REQUEST)])
            if target is None:
                from litechat import autonomy_context
                measured=autonomy_context.measure_prompt(app.engine.port,payload,generation,settings['context'])
                app.diagnostics.trace('first_message.context',room,job['id'],**measured)
                if measured['input_tokens']+payload['max_tokens']+128>measured['context_limit']:
                    raise ValueError('最初のメッセージの入力と生成枠がコンテキスト上限を超えます。生成せず停止しました。')
            if target is not None:app.conversation_api.record(room,job['id'],'first-message','attempted')
            output=''
            for chunk in generation.stream(target or app.engine.port,payload):
                output+=chunk
                if len(output)>4000:raise ValueError('初回発言が長すぎるため保存しませんでした。')
            if generation.cancelled.is_set():raise ValueError('最初のメッセージの生成を中止しました。')
            if not output.strip() or getattr(generation,'tool_calls',[]) or getattr(generation,'finish_reason',None) not in (None,'stop'):
                raise ValueError('最初のメッセージが空、または未完了です。自動再送しません。')
            if target is not None:
                app.conversation_api.record(room,job['id'],'first-message-result',getattr(generation,'api_outcome','unknown'));api_recorded=True
            with app.lock,self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');self.store.room_path(room)
                if app.closing or generation.cancelled.is_set():raise ValueError('最初のメッセージの生成を中止しました。')
                if app.room_management.snapshot(room)['revision']!=doc['revision'] or character!=self.store.room(room):raise EditConflict('生成中にキャラクター設定が変更されました。保存せず停止しました。')
                if condition!={k:self.store.settings()[k] for k in condition} or app.conversation_api.revision(room)!=api_revision or target is None and app.engine.status().get('pid')!=model_pid:
                    raise EditConflict('生成中に会話モデルの設定が変更されました。保存せず停止しました。')
                if db.execute('SELECT 1 FROM messages WHERE room=? LIMIT 1',(room,)).fetchone():
                    db.execute("UPDATE first_messages SET state='skipped' WHERE room=?",(room,));return dict(state='skipped')
                key=self._append(db,room,output.strip(),target.model if target else settings.get('model','').replace('\\','/').rsplit('/',1)[-1] or 'Local_LLM')
                db.execute("UPDATE first_messages SET state='complete',message_id=?,error='' WHERE room=?",(key,room))
            app.diagnostics.trace('first_message.finished',room,job['id'],status='complete',message_id=key)
            return dict(state='complete',message_id=key,mode='ai')
        except Exception as error:
            state='cancelled' if generation.cancelled.is_set() else 'unknown' if target is not None and getattr(generation,'api_outcome','not_sent')=='unknown' else 'error'
            if target is not None and not api_recorded:
                try:app.conversation_api.record(room,job['id'],'first-message-result',getattr(generation,'api_outcome','not_sent'))
                except Exception:state='unknown'
            with self.store.connect() as db:db.execute("UPDATE first_messages SET state=?,error=? WHERE room=? AND state='running'",(state,redact(str(error)),room))
            app.diagnostics.trace('first_message.finished',room,job['id'],status=state,error_type=type(error).__name__)
            return dict(state=state,mode='ai',error=redact(str(error)))
        finally:
            timer.cancel()
            with app.lock:
                if app.job is job:app.job=None
                job['finished'].set()

    @staticmethod
    def _append(db,room,text,model):
        return db.execute("INSERT INTO messages(room,role,content,status,created,model,origin) VALUES(?,'assistant',?,'complete',?,?,'opening')",
            (room,text,datetime.now(timezone.utc).isoformat(),model)).lastrowid
