"""User-only room creation, metadata editing and reversible archival."""
import hashlib,json,os,shutil,uuid
from pathlib import PureWindowsPath
from litechat.file_editor import EditConflict,safe_path
from litechat.resources import strict_json


def folder_name(value):
    if (not isinstance(value,str) or not 1<=len(value)<=80 or value!=value.strip()
        or value.startswith('.') or '..' in value or value.endswith('.')
        or any(ord(c)<32 or c in '/\\:<>"|?*' for c in value) or PureWindowsPath(value).is_reserved()):
        raise ValueError('フォルダ名は1～80文字で指定してください。パス・予約名・末尾の空白やドットは使えません。')
    return value


def fields(name,description):
    if not isinstance(name,str) or not name.strip() or len(name)>200:
        raise ValueError('表示名は1～200文字で指定してください。')
    if not isinstance(description,str) or len(description)>4000:
        raise ValueError('説明は4000文字までで指定してください。')
    return name.strip(),description


class RoomManagement:
    def __init__(self,app):
        self.app,self.store=app,app.store
        from .first_message import settings_enabled_from_environment
        self.initial_settings_enabled=settings_enabled_from_environment()
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS room_management_receipts(request_id TEXT PRIMARY KEY,signature TEXT,state TEXT,result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS room_management_history(id INTEGER PRIMARY KEY,room TEXT,action TEXT,request_id TEXT UNIQUE,result TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')

    def snapshot(self,room):
        with self.store.lock:
            self.store.room_path(room,include_archived=True)
            if room not in {p.name for p in self.store.characters.iterdir()}:
                raise ValueError('ルーム一覧に表示された正確なフォルダ名を指定してください。')
            path=safe_path(self.store.characters,room+'/room_config.json')
            if not path.is_file():raise ValueError('room_config.jsonが見つかりません。')
            raw=self.app.editor.read_bytes(path)
            from litechat.file_editor import decode
            config=strict_json(decode(raw)['text'])
            if not isinstance(config,dict):raise ValueError('room_config.jsonはオブジェクト形式が必要です。')
            from litechat.initial_log import snapshot as initial_snapshot
            initial=initial_snapshot(self.store.room_path(room,include_archived=True),config.get('first_message',''))
            with self.store.connect() as db:state=db.execute('SELECT archived,revision FROM room_states WHERE room=?',(room,)).fetchone()
            archived,version=(bool(state[0]),state[1]) if state else (False,'active')
            revision=hashlib.sha256(raw+json.dumps([archived,version,initial['revision']]).encode()).hexdigest()
            return dict(id=room,name=config.get('agent_display_name') or config.get('room_name') or room,
                        description=config.get('description',''),first_message=initial['value'],initial_log_revision=initial['revision'],archived=archived,revision=revision,
                        config_revision=hashlib.sha256(b'room_config.json'+bytes([True])+raw).hexdigest())

    def listing(self):
        result=[]
        for item in self.store.rooms(include_archived=True):
            try:result.append(self.snapshot(item['id']))
            except (ValueError,OSError):continue
        return dict(rooms=result,initial_settings_enabled=self.initial_settings_enabled)

    def _known_room(self,db,room):
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            table='"'+row[0].replace('"','""')+'"'
            if 'room' in {r[1] for r in db.execute('PRAGMA table_info('+table+')')} and db.execute('SELECT 1 FROM '+table+' WHERE room=? COLLATE NOCASE LIMIT 1',(room,)).fetchone():return True
        return False

    def _running(self,db,room):
        # Reject archival while independent image/index jobs are running as well.
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            table='"'+row[0].replace('"','""')+'"'
            columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
            if 'room' not in columns:continue
            if 'state' in columns and db.execute('SELECT 1 FROM '+table+" WHERE room=? AND state IN ('running','cancelling') LIMIT 1",(room,)).fetchone():return True
            if 'state' not in columns and 'payload' in columns and db.execute('SELECT 1 FROM '+table+" WHERE room=? AND json_valid(payload) AND json_extract(payload,'$.state') IN ('running','cancelling') LIMIT 1",(room,)).fetchone():return True
        return False

    def change(self,action,room,name='',description='',prompt='',revision='',request_id='',creation_profile=None,first_message=None):
        if action not in ('create','update','archive','restore'):raise ValueError('ルーム操作が不正です。')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('操作IDを指定してください。')
        signature_data=[action,room,name,description,prompt,revision]
        if first_message is not None:
            if not self.initial_settings_enabled:raise ValueError('最初のメッセージの設定はINIT_SETTING=Yで起動した場合のみ変更できます。')
            from .first_message import checked_text
            first_message=checked_text(first_message);signature_data.append(first_message)
            from litechat.initial_log import render
            render(first_message,room)
        if creation_profile is not None:
            from .character_wizard import checked_profile
            if action!='create':raise ValueError('作成用設定は新規ルームにだけ保存できます。')
            creation_profile=checked_profile(creation_profile,True);signature_data.append(creation_profile)
        signature=hashlib.sha256(json.dumps(signature_data,ensure_ascii=False).encode()).hexdigest()
        with self.app.lock,self.store.lock:
            if self.app.closing or self.app.job:raise ValueError('処理中です。完了してから操作してください。')
            with self.store.connect() as db:
                old=db.execute('SELECT * FROM room_management_receipts WHERE request_id=?',(request_id,)).fetchone()
                if old:
                    if old['signature']!=signature:raise EditConflict('同じ操作IDの内容が異なります。')
                    if old['state']!='complete':raise EditConflict('前回の操作結果を確定できません。再読込して実際の状態を確認してください。自動再実行しません。')
                    return json.loads(old['result'])
            if action=='create':
                folder_name(room);name,description=fields(name,description)
                if not isinstance(prompt,str) or len(prompt)>100000:raise ValueError('キャラクター指示は10万文字までです。')
                with self.store.connect() as db:
                    if self._known_room(db,room):raise EditConflict('過去のデータに使われているフォルダ名です。別の名前を指定してください。')
                target=safe_path(self.store.characters,room)
                if target.exists() or any(p.name.casefold()==room.casefold() for p in self.store.characters.iterdir()):raise EditConflict('同じフォルダ名のルームが存在します。')
            else:
                current=self.snapshot(room)
                if current['revision']!=revision:raise EditConflict('ルーム設定が変更されました。再読込してください。')
                if action=='update':
                    if current['archived']:raise ValueError('保管中のルームは復元してから編集してください。')
                    name,description=fields(name,description)
                else:
                    if current['archived']!=(action=='restore'):raise EditConflict('ルームの保管状態が変更されています。')
                    with self.store.connect() as db:
                        if self._running(db,room):raise ValueError('このルームの処理が実行中です。完了してから操作してください。')
                    if action=='archive' and len(self.store.rooms())<=1:raise ValueError('最後のルームは保管できません。先に別のルームを作成してください。')
            with self.store.connect() as db:db.execute("INSERT INTO room_management_receipts VALUES(?,?,'started','{}')",(request_id,signature))
            backup=None
            if action=='create':
                temporary=safe_path(self.store.characters,'.room-create-'+uuid.uuid4().hex)
                try:
                    temporary.mkdir()
                    for directory in ('data','memory/episodic','memory/dreaming','memory/entities','notes/working_memories','knowledge','spaces','items','closet'):
                        (temporary/directory).mkdir(parents=True,exist_ok=True)
                    config=dict(room_name=room,agent_display_name=name,description=description,override_settings={})
                    if first_message is not None:
                        from litechat.initial_log import RELATIVE,render
                        (temporary/RELATIVE).write_text(render(first_message,room),encoding='utf-8')
                    (temporary/'room_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
                    (temporary/'SystemPrompt.txt').write_text(prompt,encoding='utf-8')
                    if creation_profile is not None:
                        (temporary/'Akari_Data').mkdir()
                        (temporary/'Akari_Data/character_creation.json').write_text(json.dumps(dict(version=1,profile=creation_profile),ensure_ascii=False,indent=2),encoding='utf-8')
                    os.rename(temporary,target)
                finally:
                    if temporary.exists() and temporary.resolve().parent==self.store.characters.resolve():shutil.rmtree(temporary)
            elif action=='update':
                read=lambda:self.app.editor.read_snapshot(room,'room_config','ルーム設定','room_config.json')
                document=read()
                if document['revision']!=current['config_revision']:raise EditConflict('設定が変更されました。再読込してください。')
                config=strict_json(document['text']);config.update(agent_display_name=name,description=description)
                if first_message is not None:config.pop('first_message',None)
                result=self.app.editor.save_snapshot(room,'room_config',json.dumps(config,ensure_ascii=False,indent=2),document['revision'],read)
                backup=result['backup']
                if first_message is not None:
                    from litechat.initial_log import RELATIVE,render
                    read_initial=lambda:self.app.editor.read_snapshot(room,'initial_log','初期会話ログ',RELATIVE)
                    self.app.editor.save_snapshot(room,'initial_log',render(first_message,room),current['initial_log_revision'],read_initial)
            else:
                if self.snapshot(room)['revision']!=revision:raise EditConflict('設定が変更されました。再読込してください。')
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO room_states VALUES(?,?,?)',(room,int(action=='archive'),uuid.uuid4().hex))
            result=dict(room=self.snapshot(room),action=action,backup=backup)
            with self.store.connect() as db:
                db.execute("UPDATE room_management_receipts SET state='complete',result=? WHERE request_id=?",(json.dumps(result,ensure_ascii=False),request_id))
                db.execute('INSERT INTO room_management_history(room,action,request_id,result) VALUES(?,?,?,?)',(room,action,request_id,json.dumps(result,ensure_ascii=False)))
            return result
