"""Validated inventory writes through the UI's atomic Lifestyle manager."""
import copy
import json
from litechat.file_editor import EditConflict
from .lifestyle_tools import lifestyle_snapshot
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema

ACTIONS=['create','create-gift','edit','duplicate','remove','consume','transfer','place','pickup','appearance','closet-create','closet-edit','wear','take-off','closet-remove','outfit']
VALUE_PROPERTIES={
    'name':{'type':'string','maxLength':200},
    'category':{'type':'string','maxLength':100},
    'description':{'type':'string','maxLength':4000},
    'taste':{'type':'string','maxLength':4000},
    'amount':{'type':'integer','minimum':1,'maximum':1000000},
    'note':{'type':'string','maxLength':4000},
    'enabled':{'type':'boolean'},
    'worn_ids':{'type':'array','maxItems':1000,'items':{'type':'string','maxLength':2000}},
    'owner':{'type':'string','maxLength':7,'enum':['user','persona']}}


def change_lifestyle(app,context,args):
    manager=app.store.lifestyle
    with app.store.lock:
        try:
            state,location,revision,deps=lifestyle_snapshot(app,context.room,args['scope'],args['location'])
            if revision!=args['revision']:raise EditConflict('生活データまたは現在地が変わりました。')
            if state.get('shared_user') and args['scope']=='user' and args['action'] in ('appearance','closet-create','closet-edit','wear','take-off','closet-remove','outfit'):
                raise ToolFailure('permission_denied','ユーザー共通の外見・衣装は他ルームにも反映されるため、画面からのみ編集できます。ルーム専用への切替はユーザーが行います。')
            values=dict(args['values'],scope=args['scope'],location=location)
            clothing=args['action'] in ('wear','take-off','closet-remove','closet-edit')
            if args['scope']=='placed' and args['action'] in ('appearance','closet-create','outfit','wear','take-off','closet-remove','closet-edit'):raise ValueError('衣装・外見はuserかpersonaを指定してください。')
            items=state['data'][args['scope']+'_closet']['items'] if clothing else state['data'][args['scope']]['items'] if args['scope']!='placed' else state['data']['placed']['locations'].get(location,[])
            if args['index']>=0:
                if args['index']>=len(items):raise ValueError('アイテムがありません。')
                if args['id'] and args['id']!=items[args['index']]['id']:raise ValueError('indexとidが一致しません。')
                values['id']=items[args['index']]['id']
            elif args['id']:values['id']=args['id']
            chosen=next((i for i in items if i['id']==values.get('id')),None)
            if args['action']=='closet-create' and (args['id'] or args['index']>=0) and not chosen:raise ValueError('登録元の所持品がありません。')
            if args['action']=='outfit' and 'worn_ids' not in values:raise ValueError('装いの一括変更はworn_idsを明示してください。')
            if clothing:values['closet_id']=values.get('id','')
            if args['action']=='closet-create' and chosen:
                for key in ('name','description'):values.setdefault(key,chosen.get(key,''))
                values.setdefault('category',chosen.get('category','その他'))
            if args['action']=='appearance':
                profile=state['data'][args['scope']+'_profile']
                values.setdefault('description',profile.get('base',{}).get('description',''))
                values.setdefault('note',profile.get('current',{}).get('note',''))
                values.setdefault('enabled',profile.get('enabled') is True)
            if args['action']=='place' and not args['location']:raise ValueError('配置先のlocationを明示してください。')
            candidate=copy.deepcopy(state['data']);manager.apply(candidate,args['action'],values);manager.validate(candidate)
            if len(json.dumps(candidate,ensure_ascii=False).encode())>1048576:raise ValueError('生活データは1MiBまでです。')
        except EditConflict:raise
        except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
        def guard():
            context.checkpoint()
            if any(app.resources.read(context.room,k)['revision']!=v for k,v in deps.items()):
                raise EditConflict('保存直前に現在地が変更されました。')
        guard()
        saved=manager.change(context.room,state['revision'],args['action'],values,guard)
        changed=saved['revision']!=state['revision']
        receipt={'kind':'lifestyle','action':args['action'],'scope':args['scope'],'location':location,
            'revision':saved['revision'],'history_id':saved['revisions'][0]['id'] if changed else None}
        if args['action'] in ('create','create-gift','duplicate'):
            destination='user' if args['action']=='create-gift' else args['scope']
            old_items=state['data']['placed']['locations'].get(location,[]) if destination=='placed' else state['data'][destination]['items']
            new_items=saved['data']['placed']['locations'].get(location,[]) if destination=='placed' else saved['data'][destination]['items']
            before={i['id'] for i in old_items}
            receipt['created_ids']=[i['id'] for i in new_items if i['id'] not in before]
        if args['action']=='closet-create':
            old={i['id'] for i in state['data'][args['scope']+'_closet']['items']}
            receipt['created_ids']=[i['id'] for i in saved['data'][args['scope']+'_closet']['items'] if i['id'] not in old]
        if chosen and args['action']=='consume':
            receipt['consumed']={'name':chosen.get('name','')[:200],'amount':values.get('amount',1),
                'taste':str(chosen.get('taste',''))[:1600],'description':str(chosen.get('description',''))[:1600]}
    return ToolOutput(status='ok' if changed else 'no_change',data=receipt,
        changes=[receipt] if changed else [],message='生活データへ反映しました。原本JSONは保持し、変更前の状態を履歴に保存しています。')


ENTRIES=[ToolSpec(schema('change_lifestyle',
    '所持品・衣装・外見を即時更新。所持品=create/create-gift/edit/duplicate/remove/consume/transfer/place/pickup。衣装=closet-create（inventoryのindex/idで既存品から登録も可）/closet-edit/wear/take-off/closet-remove（衣装一覧のindex/id）。outfitはvalues.worn_idsとnoteを一括変更。appearanceはdescription/note/enabledの指定項目のみ変更。read_lifestyleで取得した同じscope/locationのrevision必須。配置品のlocation省略は現在地（参照時も省略する）。placeは配置先を明示。indexは一覧の番号、idは任意。valuesは変更項目のみ。transferはuserとpersona間、create-giftはpersonaが作成してuserに渡す。数量移動と履歴保存は一トランザクション。',
    {'action':{'type':'string','maxLength':13,'enum':ACTIONS},
     'scope':{'type':'string','maxLength':7,'enum':['user','persona','placed'],'default':'persona'},
     'location':{'type':'string','maxLength':100,'default':''},
     'revision':{'type':'string','minLength':64,'maxLength':64},
     'index':{'type':'integer','minimum':-1,'maximum':999,'default':-1},
     'id':{'type':'string','maxLength':2000,'default':''},
     'values':{'type':'object','properties':VALUE_PROPERTIES,'required':[],'additionalProperties':False,'default':{}}},['action','revision']),
    change_lifestyle,category='items',operation='write',effects=('lifestyle',))]
