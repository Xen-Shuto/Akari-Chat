"""Read-only appearance/outfit prompt drafts; never submits an image job."""
from litechat.file_editor import EditConflict


def checked(value, limit):
    if not isinstance(value,str) or len(value)>limit:
        raise ValueError('外見・衣装の文章または追加指定が長すぎるか、形式が不正です。')
    return value.strip()


class AppearanceImages:
    def __init__(self,app):self.app,self.store=app,app.store

    def preview(self,room,scope,extra='',revision='',template_revision=''):
        room=self.store.room_path(room).resolve().name
        if scope not in ('user','persona'):raise ValueError('ユーザーかキャラを選択してください。')
        extra=checked(extra,1000)
        if not isinstance(revision,str):raise ValueError('生活データのrevisionが不正です。')
        if not isinstance(template_revision,str):raise ValueError('書式のrevisionが不正です。')
        with self.app.lock,self.store.lock:
            template=self.app.appearance_templates.settings(room,scope)
            if template_revision and template_revision!=template['revision']:
                raise EditConflict('下書きの書式が変わりました。書式を読み直して下書きを作り直してください。')
            state=self.store.lifestyle.state(room)
            if revision and revision!=state['revision']:
                raise EditConflict('外見・衣装または生活データが変わりました。下書きを作り直してください。')
            profile=state['data'][scope+'_profile']
            if profile.get('enabled') is not True:
                raise ValueError('衣装・外見で対象の「外見を会話に反映」をオンにして保存してください。')
            base=checked(profile.get('base',{}).get('description',''),4000)
            current=profile.get('current',{});note=checked(current.get('note',''),4000)
            catalog={item['id']:item for item in state['data'][scope+'_closet']['items']}
            worn=current.get('worn',[])
            if len(worn)>1000 or any(not isinstance(key,str) for key in worn):raise ValueError('着用情報の形式が不正です。')
            lines=[];seen=set();ids=[]
            for key in worn:
                if key in seen:continue
                seen.add(key)
                if key not in catalog:raise ValueError('着用中の衣装情報が見つかりません。衣装・外見で確認してください。')
                item=catalog[key];name=checked(item.get('name',''),200)
                if not name:raise ValueError('着用中の衣装名が空です。衣装・外見で確認してください。')
                part=checked(item.get('part',''),100);description=checked(item.get('description',''),4000)
                lines.append('- '+name+('（'+part+'）' if part else '')+(': '+description if description else ''));ids.append(key)
            if not (base or note or lines):raise ValueError('基本の外見・現在の装い・着用衣装のいずれかを保存してください。')
            parts=['Generate a full-body appearance image based on the current appearance profile and outfit.']
            if base:parts.append('Base appearance: '+base)
            if note:parts.append('Current outfit note: '+note)
            if lines:parts.extend(['Worn items:',*lines])
            if extra:parts.append('Additional request: '+extra)
            prompt='\n'.join(parts)
            if template['enabled']:
                prompt=self.app.appearance_templates.render(template,dict(appearance=base,note=note,outfit='\n'.join(lines),extra=extra))
            if len(prompt)>6000:raise ValueError('画像生成の内容が6000文字を超えます。外見・衣装の説明を整理してください。')
            if self.store.lifestyle.state(room)['revision']!=state['revision']:
                raise EditConflict('読取中に外見・衣装が変わりました。下書きを作り直してください。')
            if self.app.appearance_templates.settings(room,scope)['revision']!=template['revision']:
                raise EditConflict('読取中に下書きの書式が変わりました。再確認してください。')
            return dict(room=room,scope=scope,revision=state['revision'],template_revision=template['revision'],extra=extra,prompt=prompt,worn_ids=ids,
                        reference_ids=[],warning='文章の下書きです。画像は未生成です。参照画像は自動追加しません。生成欄で対応する画像を選択してください。')
