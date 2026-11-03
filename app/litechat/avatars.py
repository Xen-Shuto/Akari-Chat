"""Static expression library; inherited character assets remain read-only."""
import hashlib
import base64
import binascii
import json
import re
import uuid
import time
from datetime import datetime,timezone
from urllib.parse import urlencode
from .autonomy import encoded,now
from .file_editor import safe_path,decode,EditConflict
from .multimodal import decode_images

EXTENSIONS={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}
ANIMATED={'.mp4':'video/mp4','.webm':'video/webm','.gif':'image/gif'}
EXTENSIONS.update(ANIMATED)
STANDARD_EXPRESSIONS=("anger","anxious","idle","joy","neutral","sadness","thinking")
LIMIT=20*1024*1024

def media_header(raw,mime):
    if mime=='image/gif':return len(raw)>=13 and raw[:6] in (b'GIF87a',b'GIF89a') and 0<int.from_bytes(raw[6:8],'little')<=8192 and 0<int.from_bytes(raw[8:10],'little')<=8192
    if mime=='video/mp4':return len(raw)>=16 and raw[4:8]==b'ftyp' and 16<=int.from_bytes(raw[:4],'big')<=LIMIT
    if mime=='video/webm':return raw[:4]==b'\x1a\x45\xdf\xa3' and b'webm' in raw[:64]
    return False

def uploaded_image(image):
    if not isinstance(image,dict):raise ValueError('表情のデータが不正です。')
    data=image.get('data','')
    if isinstance(data,str) and data.startswith(('data:image/gif;','data:video/')):
        if len(data)>7*1048576:raise ValueError('登録は1ファイル5MiBまでです。')
        match=re.fullmatch(r'data:(image/gif|video/mp4|video/webm);base64,([A-Za-z0-9+/=]+)',data)
        if not match:raise ValueError('GIF/MP4/WebMのデータを選んでください。')
        try:raw=base64.b64decode(match[2],validate=True)
        except (ValueError,binascii.Error):raise ValueError('表情データのbase64が不正です。') from None
        if len(raw)>5*1048576 or not media_header(raw,match[1]):raise ValueError('表情メディアの形式またはサイズが不正です。')
        return dict(raw=raw,mime=match[1])
    return decode_images([image])[0]


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[\w-]{1,40}',value) or value.startswith('_'):raise ValueError('表情名は40文字以内の文字・数字・ハイフン・アンダースコアで指定してください。')
    return value


class Avatars:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS avatar_settings(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS avatar_assets(room TEXT,name TEXT,mime TEXT,raw BLOB,hidden INTEGER,revision TEXT,PRIMARY KEY(room,name))')
            db.execute('CREATE TABLE IF NOT EXISTS avatar_asset_versions(id INTEGER PRIMARY KEY,room TEXT,name TEXT,mime TEXT,raw BLOB,hidden INTEGER,revision TEXT,created TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS avatar_events(id INTEGER PRIMARY KEY,room TEXT,action TEXT,name TEXT,revision TEXT,created TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS avatar_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def source(self,root,relative):
        path=safe_path(root,relative)
        if not path.is_file():return None
        stat=path.stat()
        if stat.st_size==0:raise ValueError('既存の表情画像が空です。')
        if stat.st_size>LIMIT:raise ValueError('既存の表情画像は20MiB以下にしてください。')
        mime=EXTENSIONS[path.suffix.lower()]
        if path.suffix.lower() in ANIMATED:
            with path.open('rb') as stream:header=stream.read(64)
            if not media_header(header,mime):raise ValueError('表情メディアの形式を確認できません。')
        return dict(relative=relative,mime=mime,revision=hashlib.sha256(encoded([relative,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]).encode()).hexdigest())

    def catalog(self,room,db=None):
        root=self.store.room_path(room);warning=[];config={}
        try:
            path=safe_path(root,'expressions.json')
            if path.is_file():
                with path.open('rb') as stream:raw=stream.read(1024*1024+1)
                if len(raw)>1024*1024:raise ValueError('expressions.jsonが大きすぎます。')
                config=json.loads(decode(raw)['text'])
                if not isinstance(config,dict) or not isinstance(config.get('expressions',[]),list) or len(config.get('expressions',[]))>60:raise ValueError('expressions.jsonの表情一覧が不正です。')
                for item in config.get('expressions',[]):name(item)
        except (ValueError,OSError) as error:warning.append(str(error));config={}
        inherited=list(dict.fromkeys(['profile']+list(STANDARD_EXPRESSIONS)+config.get('expressions',[])))
        default=config.get('default_expression','idle')
        if default not in inherited:default='idle'
        if db is None:
            with self.store.connect() as connection:return self._catalog(connection,room,root,inherited,default,warning)
        return self._catalog(db,room,root,inherited,default,warning)

    def _catalog(self,db,room,root,inherited,default,warning):
        managed={row['name']:dict(row) for row in db.execute('SELECT name,mime,hidden,revision,raw IS NOT NULL AS has_image FROM avatar_assets WHERE room=?',(room,))}
        settings_row=db.execute('SELECT payload FROM avatar_settings WHERE room=?',(room,)).fetchone()
        animated=bool(settings_row and json.loads(settings_row[0]).get('animated'))
        # Discover actual files too: expressions.json is optional.
        discovered={};fallback_sources=[]
        extensions=list(ANIMATED)+[e for e in EXTENSIONS if e not in ANIMATED] if animated else [e for e in EXTENSIONS if e not in ANIMATED]
        for folder in ('avatar',''):
            try:
                directory=safe_path(root,folder) if folder else root
                files=sorted(directory.iterdir(),key=lambda p:(p.stem.casefold(),extensions.index(p.suffix.lower()) if p.suffix.lower() in extensions else len(extensions),p.name)) if directory.is_dir() else []
                for path in files:
                    if path.suffix.lower() not in extensions or not path.is_file():continue
                    try:
                        source=self.source(root,(folder+'/' if folder else '')+path.name)
                    except (ValueError,OSError) as error:warning.append(str(error));continue
                    if source:
                        fallback_sources.append(source)
                        try:item=name(path.stem)
                        except ValueError:continue
                        discovered.setdefault(item,source)
            except (ValueError,OSError) as error:warning.append(str(error))
            if fallback_sources:break
        entries=[]
        for item in list(dict.fromkeys(inherited+list(managed)+list(discovered))):
            asset=managed.get(item);source=None
            managed_available=bool(asset and asset['has_image'] and (animated or asset['mime'] not in ANIMATED.values()))
            if not managed_available:
                source=discovered.get(item)
                extensions=list(ANIMATED)+[e for e in EXTENSIONS if e not in ANIMATED] if animated else [e for e in EXTENSIONS if e not in ANIMATED]
                for extension in ([] if source else extensions):
                    try:source=self.source(root,'avatar/'+item+extension)
                    except (ValueError,OSError) as error:warning.append(item+': '+str(error));continue
                    if source:break
            entry=dict(name=item,hidden=bool(asset and asset['hidden']),available=bool(managed_available or source),source='あかり' if managed_available else 'キャラフォルダ',revision=asset['revision'] if managed_available else source['revision'] if source else '',relative=source['relative'] if source else '',mime=asset['mime'] if managed_available else source['mime'] if source else '',state_revision=asset['revision'] if asset else '')
            entries.append(entry)
        try:profile=next((v for v in fallback_sources if v['relative'].rsplit('/',1)[-1].rsplit('.',1)[0].casefold()=='profile'),None) or next((v for v in fallback_sources if v['mime'] not in ANIMATED.values()),next(iter(discovered.values()),None))
        except (ValueError,OSError) as error:warning.append(str(error));profile=None
        fallback_hidden={item for item,asset in managed.items() if asset['hidden']}
        if profile and profile['relative'].rsplit('/',1)[-1].rsplit('.',1)[0] in fallback_hidden:
            profile=next((v for v in fallback_sources if v['relative'].rsplit('/',1)[-1].rsplit('.',1)[0] not in fallback_hidden and v['mime'] not in ANIMATED.values()),None)
        profile_asset=managed.get('profile')
        if profile_asset and profile_asset['has_image'] and not profile_asset['hidden'] and (animated or profile_asset['mime'] not in ANIMATED.values()):
            profile=dict(source='あかり',mime=profile_asset['mime'],revision=profile_asset['revision'],relative='')
        row=db.execute('SELECT payload FROM avatar_settings WHERE room=?',(room,)).fetchone()
        settings={'animated':False,'idle_seconds':0,'internal_emotion':False,**(json.loads(row[0]) if row else dict(mode='auto',expression=default,revision='unconfigured'))}
        revision=hashlib.sha256(encoded([settings,entries,profile,default,warning]).encode()).hexdigest()
        for entry in entries:
            if entry['available']:entry['url']='/api/avatar-asset?'+urlencode(dict(room=room,name=entry['name'],revision=revision))
        return dict(room=room,settings=settings,entries=entries,profile=profile,default=default,revision=revision,warning=' / '.join(warning[:5]))

    def prompt(self,room):
        doc=self.catalog(room)
        if doc['settings']['mode']=='manual':return ''
        available={entry['name'] for entry in doc['entries'] if entry['available'] and not entry['hidden']}
        choices=[item for item in STANDARD_EXPRESSIONS if item not in ('idle','thinking')]
        if not available.intersection(choices):return ''
        return ('\n\n【アバターの表情】最終回答の末尾に、あなた自身の回答時の感情に合う表情を1つ、'
                '【表情】…表情名… の形式で付けてください。本文や人格・口調は変更しません。'
                'ユーザーの感情ではなく、あなたの表情です。表情の候補: '+', '.join(choices)+
                '。anger=怒り、anxious=不安・心配、joy=喜び、neutral=平静、sadness=悲しみ。'
                'idle（通常待機）とthinking（生成中）はアプリが制御するので回答には使いません。'
                'ツール呼び出しの引数や思考内には入れず、相手に返す最終回答の末尾に出力してください。')

    def state(self,room):
        doc=self.catalog(room);settings=doc['settings'];expression=settings['expression'];reason='通常の表情'
        if settings['mode'] in ('auto','profile'):
            expression='idle';reason='通常待機'
            job=self.app.job
            if job and job.get('room')==room and (job.get('kind') in (None,'group') or job.get('kind')=='external_reply' and not job.get('limit_notice')):
                expression='thinking';reason='Discord返信の生成中' if job.get('kind')=='external_reply' else '会話の生成中'
            else:
                with self.store.connect() as db:row=db.execute("SELECT content,status,created FROM messages WHERE room=? AND deleted=0 AND role='assistant' ORDER BY id DESC LIMIT 1",(room,)).fetchone()
                idle=False
                if row and settings['idle_seconds']:
                    try:
                        stamp=datetime.fromisoformat(row['created'])
                        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
                        idle=max(0,time.time()-stamp.timestamp())>=settings['idle_seconds']
                    except (ValueError,TypeError,OverflowError):pass
                if idle:expression='idle';reason='設定した待機時間を経過'
                elif row and row['status']=='complete':
                    allowed={entry['name'] for entry in doc['entries'] if not entry['hidden']}
                    match=re.search(r'【表情】…([\w-]+)…',row['content'])
                    emotion=re.search(r'<persona_emotion\s+category=["\']([\w-]+)["\']\s+intensity=["\'][0-9.]+["\']\s*/>',row['content'],re.I)
                    for candidate in (match,emotion):
                        if candidate and candidate[1] in allowed:expression=candidate[1];reason='回答の表情タグ';break
                    else:
                        if settings['internal_emotion']:
                            from .emotion_freshness import effect
                            with self.store.connect() as db:
                                config=self.app.motivation._settings(db,room);state=self.app.motivation._state(db,room)
                            fresh=effect(state,time.time());candidate=state.get('emotion')
                            if config['enabled'] and fresh['intensity']>=.2 and candidate in allowed:
                                expression=candidate;reason='内部状態のモデル自己申告（時間減衰後）'
        candidates=[expression]
        if not doc['profile']:candidates+=['idle']
        for target in candidates:
            entry=next((item for item in doc['entries'] if item['name']==target and not item['hidden'] and item['available']),None)
            if entry:return dict(room=room,url=entry['url'],expression=target,mime=entry['mime'],fallback_url='/api/avatar-asset?'+urlencode(dict(room=room,name='_profile',revision=doc['revision'])) if doc['profile'] else '',reason=reason if target==expression else 'idleへ代替',revision=doc['revision'])
        return dict(room=room,url='/api/avatar-asset?'+urlencode(dict(room=room,name='_profile',revision=doc['revision'])) if doc['profile'] else '',expression='profile',mime=doc['profile']['mime'] if doc['profile'] else 'image/png',fallback_url='',reason='プロフィール画像',revision=doc['revision'])

    def image(self,room,expression,revision):
        with self.store.lock:return self._image(room,expression,revision)

    def _image(self,room,expression,revision):
        doc=self.catalog(room)
        if revision!=doc['revision']:raise EditConflict('表情画像や設定が変更されています。再読込してください。')
        if expression=='_profile':entry=doc['profile']
        else:
            name(expression);entry=next((item for item in doc['entries'] if item['name']==expression),None)
        if not entry:raise ValueError('表情画像が見つかりません。')
        if entry.get('source')=='あかり':
            with self.store.connect() as db:row=db.execute('SELECT raw,mime FROM avatar_assets WHERE room=? AND name=?',(room,'profile' if expression=='_profile' else expression)).fetchone()
            raw,mime=row['raw'],row['mime']
        else:
            path=safe_path(self.store.room_path(room),entry['relative'])
            with path.open('rb') as stream:raw=stream.read(LIMIT+1)
            mime=entry['mime']
        if len(raw)>LIMIT:raise ValueError('表情画像が大きすぎます。')
        return raw,mime

    def change(self,room,revision,action,request_id,expression='',mode='profile',image=None,checkpoint=lambda:None,idle_seconds=0,internal_emotion=False,animated=False):
        self.store.room_path(room)
        if action not in ('settings','upload','hide','restore','delete') or not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('表情の操作または操作IDが不正です。')
        name(expression)
        if type(idle_seconds) is not int or not (idle_seconds==0 or 30<=idle_seconds<=86400) or type(internal_emotion) is not bool or type(animated) is not bool:raise ValueError('待機時間は0（OFF）または30～86400秒、内部状態連動はtrue/falseを指定してください。')
        uploaded=uploaded_image(image) if action=='upload' else None
        signature=hashlib.sha256(encoded([revision,action,expression,mode,idle_seconds,internal_emotion,hashlib.sha256(uploaded['raw']).hexdigest() if uploaded else None]).encode()).hexdigest()
        if animated:signature=hashlib.sha256((signature+':animated').encode()).hexdigest()
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理が終わってから表情を変更してください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM avatar_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if signature!=receipt[0]:raise EditConflict('同じ操作IDに別の内容が指定されています。')
                return json.loads(receipt[1]),False
            doc=self.catalog(room,db)
            if revision!=doc['revision']:raise EditConflict('表情画像や設定が変更されています。再読込してください。')
            entry=next((item for item in doc['entries'] if item['name']==expression),None)
            if action=='settings':
                if mode not in ('profile','manual','auto') or not entry or mode=='manual' and entry['hidden']:raise ValueError('表示方式と利用中の表情を選んでください。')
                checkpoint();settings=dict(mode=mode,expression=expression,animated=animated,idle_seconds=idle_seconds,internal_emotion=internal_emotion,revision=uuid.uuid4().hex)
                db.execute('INSERT OR REPLACE INTO avatar_settings VALUES(?,?)',(room,encoded(settings)))
            else:
                if action=='hide' and (expression in ('idle','thinking') or doc['settings']['mode']!='profile' and expression==doc['settings']['expression']):raise ValueError('idle/thinkingや通常表示に選択中の表情は保管できません。')
                if action!='upload' and not entry:raise ValueError('表情が見つかりません。')
                if not entry and len(doc['entries'])>=64:raise ValueError('表情は1ルーム64件までです。')
                old=db.execute('SELECT * FROM avatar_assets WHERE room=? AND name=?',(room,expression)).fetchone()
                checkpoint()
                if old:db.execute('INSERT INTO avatar_asset_versions(room,name,mime,raw,hidden,revision,created) VALUES(?,?,?,?,?,?,?)',(room,expression,old['mime'],old['raw'],old['hidden'],old['revision'],now()))
                db.execute('INSERT OR REPLACE INTO avatar_assets VALUES(?,?,?,?,?,?)',(room,expression,uploaded['mime'] if uploaded else old['mime'] if old else '',uploaded['raw'] if uploaded else old['raw'] if old else None,int(action in ('hide','delete')),uuid.uuid4().hex))
            after=self.catalog(room,db)
            db.execute('INSERT INTO avatar_events(room,action,name,revision,created) VALUES(?,?,?,?,?)',(room,action,expression,after['revision'],now()))
            db.execute('INSERT INTO avatar_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(after)))
        return after,True
