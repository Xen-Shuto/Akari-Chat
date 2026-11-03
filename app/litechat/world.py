"""Nexus world text and current place, read without initialization."""
import json
from pathlib import Path
from urllib.parse import urlencode

def world_relative(root):
    """Prefer NexusArk's layout; retain existing early Akari documents in place."""
    from .file_editor import safe_path
    canonical='spaces/world_settings.txt'
    if safe_path(root,canonical).exists():return canonical
    legacy='world_settings.txt'
    return legacy if safe_path(root,legacy).exists() else canonical


def sections(text):
    lines=text.splitlines(keepends=True);areas=[];places=[];area=None
    for i,line in enumerate(lines):
        if line.strip().startswith('## '):
            area=line.strip()[3:].strip();areas.append({'name':area,'start':i})
        elif line.strip().startswith('### ') and area:
            places.append({'area':area,'name':line.strip()[4:].strip(),'start':i})
    boundaries=sorted([r['start'] for r in areas+places]+[len(lines)])
    for place in places:
        place['end']=next(x for x in boundaries if x>place['start'])
        place['description']=''.join(lines[place['start']+1:place['end']]).strip()
    for a in areas:a['end']=next((x['start'] for x in areas if x['start']>a['start']),len(lines))
    return lines,areas,places

def source(root):
    from .file_editor import safe_path, decode, FileEditor
    def read(path):
        target=safe_path(root,path)
        return decode(FileEditor.read_bytes(target))['text'].strip() if target.is_file() else ''
    relative=world_relative(root)
    world=read(relative);location=read('current_location.txt')
    temporary=read('cache/temporary_location.json')
    current=location;description='';path=relative+' + current_location.txt'
    warnings=[]
    if temporary:
        try:
            data=json.loads(temporary)
            if data.get('active') is True:
                description=data.get('current',{}).get('scenery_text','')
                if not isinstance(description,str):raise ValueError('情景が文字列ではありません')
                current='一時的現在地';path='cache/temporary_location.json'
        except (ValueError,AttributeError):warnings.append('一時的現在地の形式を読めません')
    if not description and current != "一時的現在地":
        _,_,places=sections(world)
        matches=[p for p in places if p['name']==location]
        if len(matches)==1:description=matches[0]['description']
        elif len(matches)>1:warnings.append('同名の場所が複数あり説明を選択できません')
    text=('現在地: '+current+('\n'+description[:6000] if description else '')) if current else ''
    return {'label':'現在地・世界設定','path':path,'exists':bool(world or location or temporary),'text':text,'encoding':None,'selection_reason':'現在地の説明のみを使用。世界設定全体は送信しません。'+'／'.join(warnings)}

class World:
    def __init__(self,store,resources):self.store,self.resources=store,resources
    def state(self,room):
        doc=self.resources.read(room,'world');loc=self.resources.read(room,'location')
        _,areas,places=sections(doc['text'])
        scene=self.resources.read(room,'scene')
        prefs=json.loads(scene['text']) if scene['exists'] else {}
        images=self.images(room)
        from .scenery_images import time_context,find_image
        from .storage import read_json
        root=self.store.room_path(room);config=read_json(root/'room_config.json',{})
        season,period=time_context(config if isinstance(config,dict) else {})
        current=loc['text'].strip()
        automatic=find_image(images,current,season,period)
        manual=prefs.get('background','')
        if prefs.get('background_place')==current:background=manual if manual in images else ''
        else:background=automatic or (manual if 'background_place' not in prefs and manual in images else '')
        return {'images':images,'place_backgrounds':[find_image(images,p['name'],season,period) for p in places],'scene':scene,'background':background,'automatic_background':automatic,'current_location':current,
                'world':doc,'location':loc,'areas':[a['name'] for a in areas],'places':places,'source':source(root)}
    def images(self,room):
        from .file_editor import safe_path
        root=self.store.room_path(room);images=[]
        for folder in ('spaces/images','backgrounds','images'):
            directory=safe_path(root,folder)
            for path in sorted(directory.glob('*')):
                if len(images)>=500:break
                if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):continue
                try:
                    if safe_path(root,path.relative_to(root)).is_file():images.append(path.relative_to(root).as_posix())
                except ValueError:continue
        return images
    def image(self,room,relative):
        from .file_editor import safe_path
        if relative not in self.images(room):raise ValueError('背景画像を一覧から選んでください。')
        path=safe_path(self.store.room_path(room),relative)
        with path.open('rb') as stream:raw=stream.read(8*1048576+1)
        if len(raw)>8*1048576:raise ValueError('背景画像は8MiBまでです。')
        mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else 'image/webp' if raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' else None
        if not mime:raise ValueError('対応する画像形式ではありません。')
        return raw,mime
    def background(self,room,relative,revision):
        if relative:self.image(room,relative)
        doc=self.resources.read(room,'scene');prefs=json.loads(doc['text']) if doc['exists'] else {}
        prefs['background']=relative
        prefs['background_place']=self.resources.read(room,'location')['text'].strip()
        self.resources.save(room,'scene','',json.dumps(prefs,ensure_ascii=False,indent=2),revision)
        return self.state(room)

    def update(self,room,revision,area,place,description,action):
        if action not in ('save','delete-place','delete-area'):raise ValueError('操作が不正です。')
        for name in (area,place):
            if not isinstance(name,str) or len(name)>100 or '\n' in name or '\r' in name or name.startswith('#'):raise ValueError('エリア名・場所名が不正です。')
        if not area.strip() or (action!='delete-area' and not place.strip()):raise ValueError('名前を入力してください。')
        if not isinstance(description,str) or len(description)>6000 or any(l.startswith(('## ','### ')) for l in description.splitlines()):raise ValueError('説明は6000文字までで、場所見出しは含めないでください。')
        doc=self.resources.read(room,'world');lines,areas,places=sections(doc['text'])
        found=[p for p in places if p['area']==area and p['name']==place]
        matches=[a for a in areas if a['name']==area]
        if len(found)>1 or len(matches)>1:raise ValueError('名前が重複しています。RAW編集で確認してください。')
        if action=='delete-area':
            if not matches:raise ValueError('エリアがありません。')
            a=matches[0];lines[a['start']:a['end']]=[]
        elif found:
            p=found[0];lines[p['start']:p['end']]=[] if action=='delete-place' else ['### '+place+'\n',description+'\n\n']
        elif action=='delete-place':raise ValueError('場所がありません。')
        elif matches:
            lines[matches[0]['end']:matches[0]['end']]=['\n### '+place+'\n'+description+'\n']
        else:lines.append('\n## '+area+'\n### '+place+'\n'+description+'\n')
        self.resources.save(room,'world','',''.join(lines),revision)
        return self.state(room)
    def move(self,room,place,world_revision,location_revision):
        from .file_editor import EditConflict
        state=self.state(room)
        if state['world']['revision']!=world_revision:raise EditConflict('世界設定が変わりました。再読込してください。')
        if sum(p['name']==place for p in state['places'])!=1:raise ValueError('一意に決まる場所を選んでください。')
        self.resources.save(room,'location','',place,location_revision)
        return self.state(room)

    def prepare_location(self, room, action, place_id, text, revisions):
        from .file_editor import EditConflict
        from .resources import strict_json
        docs={kind:self.resources.read(room,kind) for kind in ('world','location','temporary')}
        if any(docs[k]['revision']!=revisions[k] for k in docs):
            raise EditConflict('世界設定または現在地が変わりました。読み直してください。')
        temporary=strict_json(docs['temporary']['text']) if docs['temporary']['exists'] and docs['temporary']['text'].strip() else {'active':False}
        if not isinstance(temporary,dict) or type(temporary.get('active')) is not bool:
            raise ValueError('一時的現在地の形式を確認してください。')
        if action=='move':
            if temporary['active']:raise ValueError('一時的現在地を先に解除してください。')
            places=sections(docs['world']['text'])[2]
            if place_id>=len(places):raise ValueError('場所がありません。一覧を取得し直してください。')
            place=places[place_id]['name']
            if sum(p['name']==place for p in places)!=1:raise ValueError('同名の場所が複数あり現在地を特定できません。')
            return docs,'location',place
        if action=='temporary':
            if not text.strip():raise ValueError('一時的現在地の情景が必要です。')
            current=temporary.get('current',{})
            if not isinstance(current,dict):raise ValueError('currentはオブジェクトが必要です。')
            temporary['current']=dict(current,scenery_text=text)
            temporary['active']=True
        else:temporary['active']=False
        return docs,'temporary',json.dumps(temporary,ensure_ascii=False,indent=2)

    def change_location(self, room, prepared, checkpoint=lambda: None):
        from .file_editor import EditConflict
        docs,kind,text=prepared
        def read():
            checkpoint()
            for key,doc in docs.items():
                if self.resources.read(room,key)['revision']!=doc['revision']:
                    raise EditConflict('保存直前に世界設定または現在地が変わりました。')
            return self.resources.read(room,kind)
        return self.resources.editor.save_snapshot(room,'resource',text,docs[kind]['revision'],read,
            after_read=lambda:self.resources.editor.read_snapshot(room,'resource','現在地',docs[kind]['path']))
