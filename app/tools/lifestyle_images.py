"""Room-local image links; character originals and image bytes are never changed."""
import json
import re
import uuid
from urllib.parse import urlencode
from litechat.autonomy import now
from litechat.episodes import fingerprint
from litechat.file_editor import EditConflict


class LifestyleImages:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lifestyle_image_links(room TEXT,target_key TEXT,payload TEXT,PRIMARY KEY(room,target_key))')
            db.execute('CREATE TABLE IF NOT EXISTS lifestyle_image_versions(id INTEGER PRIMARY KEY,room TEXT,target_key TEXT,before_payload TEXT,after_payload TEXT,created TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS lifestyle_image_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')

    def targets(self,room):
        state=self.store.lifestyle.state(room);data=state['data'];result={}
        def add(kind,scope,key,location,label,source):
            identity=dict(kind=kind,scope=scope,id=key,location=location)
            token=fingerprint(identity)
            if token in result:raise ValueError('画像の関連先を一意に選べません。IDの重複を確認してください。')
            result[token]=dict(key=token,label=label,**identity,source_revision=fingerprint([identity,source]))
            if len(result)>5000:raise ValueError('関連先は1ルーム5000件以内にしてください。')
        for scope,label in [('user','ユーザー'),('persona','キャラ')]:
            profile=data[scope+'_profile'];closet=data[scope+'_closet']['items'];worn=profile.get('current',{}).get('worn',[])
            shared=scope=='user' and state['shared_user']
            source=[profile.get('base',{}),profile.get('current',{}),[i for i in closet if i['id'] in worn],shared]
            add('profile',scope,'','',label+'の外見・現在の装い',source)
            for item in closet:add('closet',scope,item['id'],'',label+'の衣装 / '+item.get('name',''),[item,shared])
            for item in data[scope]['items']:add('item',scope,item['id'],'',label+'の所持品 / '+item.get('name',''),{k:v for k,v in item.items() if k!='amount'})
        for location,items in data['placed']['locations'].items():
            for item in items:add('item','placed',item['id'],location,'配置品 / '+location+' / '+item.get('name',''),{k:v for k,v in item.items() if k!='amount'})
        return result

    def options(self,room):
        room=self.store.room_path(room).name;targets=self.targets(room)
        with self.store.connect() as db:
            for row in db.execute('SELECT target_key,payload FROM lifestyle_image_links WHERE room=?',(room,)):
                if row[0] not in targets:
                    old=json.loads(row[1]);targets[row[0]]=dict(old['target'],missing=True,label=old['target']['label']+'（対象が未配置）')
        images=self.app.images.listing(room)['images']
        return dict(targets=[{k:v for k,v in t.items() if k!='source_revision'} for t in targets.values()],images=[dict(id=i['id'],label=(i.get('prompt') or i['id'])[:80]) for i in images])

    def _snapshot(self,room,key,image_id='',db=None):
        room=self.store.room_path(room).name
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key) or not isinstance(image_id,str) or len(image_id)>256:raise ValueError('画像または関連先の指定が不正です。')
        if db is None:
            with self.store.lock,self.store.connect() as connection:return self._snapshot(room,key,image_id,connection)
        row=db.execute('SELECT payload FROM lifestyle_image_links WHERE room=? AND target_key=?',(room,key)).fetchone();binding=json.loads(row[0]) if row else None
        target=self.targets(room).get(key)
        if target is None and binding is None:raise ValueError('関連先が見つかりません。対象一覧を読み直してください。')
        selected=target or dict(binding['target'],missing=True,source_revision='missing')
        cache={}
        def artifact(identifier):
            if identifier not in cache:cache[identifier]=self.app.images.artifact(room,identifier)
            return cache[identifier]
        bound=None;raw=None;available=False;image_changed=False
        if binding:
            try:
                bound,raw=artifact(binding['image_id']);image_changed=bound['sha256']!=binding['image_sha256'];available=not image_changed
            except (ValueError,OSError):pass
        candidate=None
        if image_id:
            doc,_=artifact(image_id)
            candidate=dict(id=image_id,sha256=doc['sha256'],mime=doc['mime'],width=doc['width'],height=doc['height'],label=(doc.get('name') or doc.get('prompt') or image_id)[:80])
        revision=fingerprint([room,key,selected['source_revision'],binding,bound['sha256'] if bound else 'unavailable',image_id,candidate['sha256'] if candidate else ''])
        clear_revision=fingerprint([room,key,selected['source_revision'],binding,bound['sha256'] if bound else 'unavailable','',''])
        state=dict(room=room,target={k:v for k,v in selected.items() if k!='source_revision'},target_revision=selected['source_revision'],revision=revision,clear_revision=clear_revision,binding=binding,candidate=candidate,
            target_changed=bool(binding and (not target or binding['target_revision']!=target['source_revision'])),image_changed=image_changed,available=available)
        if available:state['url']='/api/lifestyle-linked-image?'+urlencode(dict(room=room,target_key=key,revision=revision,image_id=image_id))
        return state,raw if available else None

    def preview(self,room,key,image_id=''):
        return self._snapshot(room,key,image_id)[0]

    def image(self,room,key,revision,image_id=''):
        doc,raw=self._snapshot(room,key,image_id)
        if doc['revision']!=revision:raise EditConflict('画像の関連付けまたは対象が変わりました。読み直してください。')
        if not doc['available']:raise ValueError('関連画像が未配置、変更済み、または未登録です。')
        return raw,doc['binding']['mime']

    def reference(self,room,key,revision,image_id=''):
        doc,_=self._snapshot(room,key,image_id)
        if doc['revision']!=revision or not doc['available'] or doc['target_changed'] or doc['image_changed']:raise EditConflict('現在の対象に一致する関連画像を確認できません。更新してください。')
        reference=self.app.images.reference(room,doc['binding']['image_id'])
        if reference['sha256']!=doc['binding']['image_sha256']:raise EditConflict('参照画像が変更されました。')
        return dict(id=doc['binding']['image_id'],label=doc['binding']['image_label'],sha256=reference['sha256'])

    def change(self,room,key,image_id,revision,action,request_id,checkpoint=lambda:None):
        room=self.store.room_path(room).name
        if action not in ('link','clear') or not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('関連付けの操作または操作IDが不正です。')
        if action=='clear' and image_id:raise ValueError('解除時は候補画像を空にしてください。')
        signature=fingerprint([key,image_id,revision,action])
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成が終わってから画像を関連付けてください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM lifestyle_image_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに別の関連付けが指定されています。')
                return dict(json.loads(receipt[1]),replayed=True)
            current,_=self._snapshot(room,key,image_id,db)
            if current['revision']!=revision:raise EditConflict('対象・画像・関連付けが変わりました。確認し直してください。')
            before=current['binding'];after=None
            if action=='link':
                if current['target'].get('missing') or not current['candidate']:raise ValueError('現在存在する対象と画像を選択してください。')
                c=current['candidate']
                if not before and db.execute('SELECT count(*) FROM lifestyle_image_links WHERE room=?',(room,)).fetchone()[0]>=1000:raise ValueError('関連付けは1ルーム1000件までです。')
                after=dict(target=current['target'],target_revision=current['target_revision'],image_id=c['id'],image_sha256=c['sha256'],mime=c['mime'],width=c['width'],height=c['height'],image_label=c['label'],revision=uuid.uuid4().hex,created=now())
            same=bool(before and after and all(before[k]==after[k] for k in ('target_revision','image_id','image_sha256')))
            if same:after=before
            checkpoint();changed=not same and (before is not None or after is not None)
            if changed:
                db.execute('INSERT INTO lifestyle_image_versions(room,target_key,before_payload,after_payload,created) VALUES(?,?,?,?,?)',(room,key,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),now()))
                if after:db.execute('INSERT OR REPLACE INTO lifestyle_image_links VALUES(?,?,?)',(room,key,json.dumps(after,ensure_ascii=False)))
                else:db.execute('DELETE FROM lifestyle_image_links WHERE room=? AND target_key=?',(room,key))
            result=dict(target_key=key,changed=changed,binding=after,replayed=False)
            db.execute('INSERT INTO lifestyle_image_receipts VALUES(?,?,?,?)',(room,request_id,signature,json.dumps(result,ensure_ascii=False)))
            return result

    def history(self,room,key):
        self.store.room_path(room)
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('関連先の指定が不正です。')
        with self.store.connect() as db:
            return [dict(id=r[0],before=json.loads(r[1]),after=json.loads(r[2]),created=r[3]) for r in db.execute('SELECT id,before_payload,after_payload,created FROM lifestyle_image_versions WHERE room=? AND target_key=? ORDER BY id DESC LIMIT 20',(room,key))]


    def validate_plan(self,room,plan,db):
        if not isinstance(plan,dict) or set(plan)!={'target_key','revision'} or any(not isinstance(plan[k],str) or not re.fullmatch('[0-9a-f]{64}',plan[k]) for k in plan):raise ValueError('生成後の関連付け対象を確認してください。')
        current,_=self._snapshot(room,plan['target_key'],db=db)
        if current['target'].get('missing') or current['revision']!=plan['revision']:raise EditConflict('関連先が変わりました。生成予約前に選び直してください。')
        return current

    def link_generated(self,db,item,image):
        plan=item['link_plan'];request_id='image-job-'+item['id'];signature=fingerprint([plan,image['id']])
        receipt=db.execute('SELECT signature,result FROM lifestyle_image_receipts WHERE room=? AND request_id=?',(item['room'],request_id)).fetchone()
        if receipt:
            if receipt[0]!=signature:raise EditConflict('生成画像の関連付け受領記録が一致しません。')
            return json.loads(receipt[1])
        if item.get('cancel_requested'):return dict(state='cancelled',message='取消要求があるため関連付けませんでした。画像は保持しています。')
        try:current=self.validate_plan(item['room'],plan,db)
        except (ValueError,OSError):return dict(state='review',message='対象・画像・関連が変わったため自動関連付けを見送りました。画像を確認して手動で関連付けてください。')
        before=current['binding']
        if not before and db.execute('SELECT count(*) FROM lifestyle_image_links WHERE room=?',(item['room'],)).fetchone()[0]>=1000:return dict(state='review',message='関連付けの件数上限です。生成画像は保持しています。')
        after=dict(target=current['target'],target_revision=current['target_revision'],image_id=image['id'],image_sha256=image['sha256'],mime=image['mime'],width=image['width'],height=image['height'],image_label=image['prompt'][:80],revision=uuid.uuid4().hex,created=now())
        db.execute('INSERT INTO lifestyle_image_versions(room,target_key,before_payload,after_payload,created) VALUES(?,?,?,?,?)',(item['room'],plan['target_key'],json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),now()))
        db.execute('INSERT OR REPLACE INTO lifestyle_image_links VALUES(?,?,?)',(item['room'],plan['target_key'],json.dumps(after,ensure_ascii=False)))
        result=dict(state='linked',message='予約時に選択した対象へ画像を関連付けました。',target_key=plan['target_key'],image_id=image['id'])
        db.execute('INSERT INTO lifestyle_image_receipts VALUES(?,?,?,?)',(item['room'],request_id,signature,json.dumps(result,ensure_ascii=False)))
        return result
