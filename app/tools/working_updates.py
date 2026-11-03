"""Revision-guarded immediate working-memory operations."""
from litechat.file_editor import EditConflict
from litechat.working_slots import slot_name
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema


def change_working_memory(app, context, args):
    with app.store.lock:
        try:
            slot_name(args['name'])
            action=args['action']
            state=app.slots.readonly(context.room,args['name'],args['config_revision'])
            doc=state['document']
            if action!='create' and doc['revision']!=args['revision']:
                raise EditConflict('本文が変わりました。読み直してください。')
            if action=='create' and doc['exists']:raise ValueError('既存スロットは上書きできません。')
            if action!='create' and not doc['exists']:raise ValueError('先にスロットを作成してください。')
            if action=='select' and doc.get('state')=='dormant':raise ValueError('休眠中の話題です。人が復帰してから切り替えてください。')
            if action=='patch' and (not args['old_text'] or doc['text'].replace('\r\n','\n').replace('\r','\n').count(args['old_text'].replace('\r\n','\n').replace('\r','\n'))!=1):
                raise EditConflict('置換元は一度だけ現れる本文を指定してください。')
        except EditConflict:raise
        except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
        context.checkpoint()
        if action=='create':saved=app.slots.create(context.room,args['name'],args['text'],args['config_revision'],context.checkpoint)
        elif action=='select':saved=app.slots.select(context.room,args['name'],args['config_revision'],args['revision'],context.checkpoint)
        else:saved=app.slots.replace(context.room,args['name'],args['text'],args['config_revision'],args['revision'],
            args['old_text'] if action=='patch' else None,context.checkpoint)
        final=saved.get('document',doc)
        selection=saved.get('state',{'config_revision':state['config_revision'],'active':state['active']})
        receipt={'action':action,'slot':args['name'],'revision':final['revision'],
            'config_revision':selection['config_revision'],'active_slot':selection['active'],'backup':saved['backup']}
        changed=saved.get('changed',True)
    return ToolOutput(status='ok' if changed else 'no_change',data=receipt,
        changes=[{'kind':'working',**receipt}] if changed else [],message='反映しました。' if changed else '変更はありません。')


ENTRIES=[ToolSpec(schema('change_working_memory',
    'ワーキングメモリの即時作成(create)・選択(select)・全文更新(replace)・一箇所置換(patch)。一覧/本文で得たconfig_revision必須、create以外は本文revisionも必須。nameは拡張子なし。選択は別操作で、作成では切り替わらない。休眠中は人の復帰後に選択する。原本をバックアップする。',
    {'action':{'type':'string','maxLength':7,'enum':['create','select','replace','patch']},
     'name':{'type':'string','minLength':1,'maxLength':80},
     'config_revision':{'type':'string','minLength':64,'maxLength':64},
     'revision':{'type':'string','maxLength':64,'default':''},
     'text':{'type':'string','maxLength':12000,'default':''},
     'old_text':{'type':'string','maxLength':6000,'default':''}},['action','name','config_revision']),
    change_working_memory,category='memory',operation='write',effects=('working',))]
