"""Atomic Akari inventory/appearance overlay; Nexus original files remain intact."""
import copy
import json
import uuid
from .episodes import fingerprint
from .file_editor import safe_path, FileEditor, decode, EditConflict
from .resources import strict_json

FILES={'user':'items/user_inventory.json','persona':'items/persona_inventory.json','placed':'items/placed_items.json',
       'persona_profile':'closet/persona_profile.json','persona_closet':'closet/persona_closet.json',
       'user_profile':'closet/user_profile.json','user_closet':'closet/user_closet.json'}

def quantity(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=1000000:raise ValueError('数量は1～1000000の整数です。')
    return value

def text(value,limit=4000):
    if not isinstance(value,str) or len(value)>limit:raise ValueError('本文または名前が長すぎます。')
    return value

class Lifestyle:
    def __init__(self,store):
        self.store=store
        from tools.shared_closet import SharedCloset
        self.shared=SharedCloset(store)
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lifestyle(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS lifestyle_revisions(id INTEGER PRIMARY KEY,room TEXT,payload TEXT,action TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP)')
    def originals(self,room):
        root=self.store.room_path(room);data={};exists=False
        for key,relative in FILES.items():
            target=safe_path(root,relative)
            default={'locations':{}} if key=='placed' else {'enabled':False,'base':{'description':''},'current':{'note':'','worn':[]}} if key.endswith('_profile') else {'items':[]}
            if target.is_file():data[key]=strict_json(decode(FileEditor.read_bytes(target))['text']);exists=True
            else:data[key]=default
        self.validate(data)
        return data,exists
    def validate(self,data):
        for key in ('user','persona','user_closet','persona_closet'):
            obj=data[key]
            if not isinstance(obj,dict) or not isinstance(obj.get('items'),list) or len(obj['items'])>1000:raise ValueError('アイテム一覧の形式または上限が不正です。')
            ids=set()
            for item in obj['items']:
                if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in ids:raise ValueError('アイテムIDが不正または重複しています。')
                ids.add(item['id']);text(item.get('name',''),200)
                if key in ('user','persona'):quantity(item.get('amount'))
        placed=data['placed']
        if not isinstance(placed,dict) or not isinstance(placed.get('locations'),dict):raise ValueError('配置アイテムの形式が不正です。')
        for name,items in placed['locations'].items():
            text(name,100)
            if not isinstance(items,list) or len(items)>1000:raise ValueError('配置一覧が不正です。')
            for item in items:
                if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise ValueError('配置アイテムIDが不正です。')
                quantity(item.get('amount'))
        for key in ('user_profile','persona_profile'):
            obj=data[key]
            if not isinstance(obj,dict) or not isinstance(obj.get('base',{}),dict) or not isinstance(obj.get('current',{}),dict):raise ValueError('外見設定が不正です。')
            if not isinstance(obj.get('current',{}).get('worn',[]),list):raise ValueError('着用一覧が不正です。')
    def private(self,room,db):
        row=db.execute('SELECT payload FROM lifestyle WHERE room=?',(room,)).fetchone()
        if row:return json.loads(row['payload']),True,True
        data,exists=self.originals(room)
        return data,exists,False
    def state(self,room,db=None):
        self.store.room_path(room)
        if db is None:
            with self.store.lock,self.store.connect() as conn:
                conn.execute('BEGIN')
                return self.state(room,conn)
        data,exists,overlay=self.private(room,db)
        shared=self.shared.snapshot(room,db)
        if shared['enabled']:
            for key in ('user_profile','user_closet'):data[key]=copy.deepcopy(shared['data'][key])
        history=[dict(r) for r in db.execute('SELECT id,action,created FROM lifestyle_revisions WHERE room=? ORDER BY id DESC LIMIT 20',(room,))]
        revision=fingerprint([data,shared['mode_revision'],shared['revision'] if shared['enabled'] else None])
        return {'room':room,'data':data,'revision':revision,'overlay':overlay,'exists':exists or shared['enabled'],'revisions':history,'shared_user':shared['enabled']}
    def change(self,room,revision,action,values,checkpoint=lambda: None):
        with self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.change_in_transaction(db,room,revision,action,values,checkpoint)
    def change_in_transaction(self,db,room,revision,action,values,checkpoint=lambda:None):
        if not db.in_transaction:raise ValueError('生活データの保存にはトランザクションが必要です。')
        if not isinstance(values,dict):raise ValueError("操作引数はオブジェクトが必要です。")
        state=self.state(room,db)
        if state['revision']!=revision:raise EditConflict('所持品または外見が変わりました。再読込してください。')
        data=copy.deepcopy(state['data'])
        if state['shared_user'] and action in ('restore','import-originals'):
            raise ValueError('共通衣装の使用をOFFにしてからルーム全体の復元・再取込を行ってください。共通衣装は専用の履歴で復元できます。')
        if action=='restore':
            row=db.execute('SELECT payload FROM lifestyle_revisions WHERE room=? AND id=?',(room,values.get('revision_id'))).fetchone()
            if row is None:raise ValueError('復元対象がありません。')
            data=json.loads(row['payload'])
        elif action=='import-originals':data,_=self.originals(room)
        else:self.apply(data,action,values)
        self.validate(data)
        payload=json.dumps(data,ensure_ascii=False)
        if len(payload.encode())>1048576:raise ValueError('生活データは合計1MiBまでです。')
        checkpoint()
        if data==state['data']:return state
        db.execute('INSERT INTO lifestyle_revisions(room,payload,action) VALUES(?,?,?)',(room,json.dumps(state['data'],ensure_ascii=False),action))
        if state['shared_user']:
            old=self.shared.snapshot(room,db)['data']
            shared=self.shared.portable(data)
            self.shared.write(db,old,shared,action+':'+room)
            private=self.private(room,db)[0]
            for key in ('user_profile','user_closet'):data[key]=private[key]
            payload=json.dumps(data,ensure_ascii=False)
        db.execute('INSERT OR REPLACE INTO lifestyle VALUES(?,?)',(room,payload))
        return self.state(room,db)
    def apply(self,data,action,v):
        scope=v.get('scope','user')
        if scope not in ('user','persona','placed'):raise ValueError('所持先が不正です。')
        location=text(v.get('location',''),100)
        items=data[scope]['items'] if scope!='placed' else data['placed']['locations'].get(location,[])
        selected=[i for i in items if i['id']==v.get('id')]
        if len(selected)>1:raise ValueError('同じIDのアイテムが複数あり選択できません。')
        item=selected[0] if selected else None
        if action in ('appearance','closet-create','closet-edit','wear','take-off','closet-remove','outfit'):
            if scope=='placed':raise ValueError('ユーザーかキャラを選んでください。')
            profile=data[scope+'_profile'];catalog=data[scope+'_closet']['items']
            profile.setdefault('current',{});profile['current'].setdefault('worn',[])
            if action=='appearance':
                profile.setdefault('base',{})['description']=text(v.get('description',''))
                profile['current']['note']=text(v.get('note',''));profile['enabled']=v.get('enabled') is True
            elif action=='outfit':
                worn=v.get('worn_ids',[])
                if not isinstance(worn,list) or len(worn)>1000 or any(not isinstance(k,str) for k in worn) or len(set(worn))!=len(worn):
                    raise ValueError('着用一覧は重複のない衣装IDの配列が必要です。')
                if any(not any(i['id']==key for i in catalog) for key in worn):raise ValueError('登録されていない衣装があります。')
                profile['current']['worn']=list(worn)
                if 'note' in v:profile['current']['note']=text(v['note'])
                profile['enabled']=True
            elif action=='closet-create':
                name=text(v.get('name',''),200)
                if not name.strip():raise ValueError('衣装名が必要です。')
                catalog.append({'id':uuid.uuid4().hex,'name':name,'part':text(v.get('category','その他'),100),'description':text(v.get('description','')),'linked_item_id':item['id'] if item else ''})
            else:
                key=v.get('closet_id')
                if not any(i['id']==key for i in catalog):raise ValueError('衣装が見つかりません。')
                worn=profile['current']['worn']
                if action=='closet-edit':
                    clothing=next(i for i in catalog if i['id']==key)
                    for field,source,limit in [('name','name',200),('part','category',100),('description','description',4000)]:
                        if source in v:clothing[field]=text(v[source],limit)
                    if not clothing.get('name','').strip():raise ValueError('衣装名が必要です。')
                    return
                if action=='wear':
                    if key not in worn:worn.append(key)
                    profile['enabled']=True
                else:profile['current']['worn']=[k for k in worn if k!=key]
                if action=='closet-remove':data[scope+'_closet']['items']=[i for i in catalog if i['id']!=key]
            return
        if action in ('create','create-gift'):
            if scope=='placed':raise ValueError('所持先を選んで作成してください。')
            name=text(v.get('name',''),200)
            if not name.strip():raise ValueError('名前が必要です。')
            if action=='create-gift':
                if scope!='persona':raise ValueError('作成して贈る操作はキャラからユーザーへ行います。')
                items=data['user']['items']
            items.append({'id':uuid.uuid4().hex,'name':name,'category':text(v.get('category','その他'),100),'description':text(v.get('description','')),'taste':text(v.get('taste','')),'amount':quantity(v.get('amount',1)),'creator':'User' if scope=='user' else 'Persona'})
            return
        if item is None:raise ValueError('アイテムを選択してください。')
        if action=='edit':
            for key in ('name','category','description','taste'):
                if key in v:item[key]=text(v[key],200 if key=='name' else 4000)
            item['amount']=quantity(v.get('amount',item['amount']))
        elif action=='duplicate':
            duplicate=copy.deepcopy(item);duplicate['id']=uuid.uuid4().hex;items.append(duplicate)
        elif action=='remove':items.remove(item)
        elif action in ('consume','transfer','place','pickup'):
            count=quantity(v.get('amount',1))
            if item['amount']<count:raise ValueError('所持数量が足りません。')
            target=None
            if action=='transfer':
                if scope=='placed':raise ValueError('配置品は回収してください。')
                target=data['persona' if scope=='user' else 'user']['items']
            elif action=='place':
                if scope=='placed' or not location:raise ValueError('所持品と配置先を選んでください。')
                target=data['placed']['locations'].setdefault(location,[])
            elif action=='pickup':
                owner=v.get('owner','user')
                if scope!='placed' or owner not in ('user','persona'):raise ValueError('回収先が不正です。')
                target=data[owner]['items']
            if target is not None:
                existing=next((i for i in target if i['id']==item['id']),None)
                if existing:existing['amount']+=count
                else:
                    moved=copy.deepcopy(item);moved['amount']=count;target.append(moved)
            item['amount']-=count
            if not item['amount']:items.remove(item)
        else:raise ValueError('操作が不正です。')
    def context(self,room):
        try:state=self.state(room)
        except (ValueError,OSError) as error:return {'label':'所持品・外見','path':'生活データ','exists':False,'text':'','encoding':None,'selection_reason':str(error)}
        data=state['data'];root=self.store.room_path(room);loc=safe_path(root,'current_location.txt')
        location=decode(FileEditor.read_bytes(loc))['text'].strip() if loc.is_file() else ''
        temporary=safe_path(root,'cache/temporary_location.json')
        if temporary.is_file():
            data_temp=strict_json(decode(FileEditor.read_bytes(temporary))['text'])
            if isinstance(data_temp,dict) and data_temp.get('active') is True:location='一時的現在地'
        parts=[]
        for scope,label in (('user','ユーザー所持品'),('persona','キャラ所持品')):
            items=data[scope]['items'];parts.append(label+': '+', '.join(str(i.get('name',''))+'×'+str(i['amount']) for i in items[:30]))
            profile=data[scope+'_profile'];catalog=data[scope+'_closet']['items']
            if profile.get('enabled') is True:
                current=profile.get('current',{});worn=current.get('worn',[])
                parts.append(label+'の外見: '+str(profile.get('base',{}).get('description',''))[:1500]+' '+str(current.get('note',''))[:1000]+' 着用: '+', '.join(str(i.get('name',''))+': '+str(i.get('description',''))[:200] for i in catalog if i['id'] in worn)[:2000])
        items=data['placed']['locations'].get(location,[])
        parts.append('現在地の配置品: '+', '.join(str(i.get('name',''))+'×'+str(i['amount']) for i in items[:30]))
        return {'label':'所持品・外見','path':'app/data/chat.sqlite3:lifestyle' if state['overlay'] or state['shared_user'] else 'items/ + closet/ (読取のみ)','exists':state['exists'],'text':'\n'.join(parts) if state['exists'] else '','encoding':None,'selection_reason':'所持品は各30件、外見は有効なプロフィールのみ。ユーザー外見・衣装は'+('全ルーム共通。' if state['shared_user'] else 'ルーム専用。')}
