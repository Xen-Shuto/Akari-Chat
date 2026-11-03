"""Read inventories, placed items, closets and appearance without changing them."""
import hashlib
import json
from litechat.resources import strict_json
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema


def lifestyle_snapshot(app,room,scope,location,current=True):
    state=app.store.lifestyle.state(room)
    app.store.lifestyle.validate(state['data'])
    revision=state['revision'];dependencies={}
    if scope=='placed' and current and not location:
        loc=app.resources.read(room,'location');temp=app.resources.read(room,'temporary')
        location=loc['text'].strip()
        if temp['exists'] and temp['text'].strip():
            try:
                value=strict_json(temp['text'])
                if not isinstance(value,dict) or type(value.get('active')) is not bool:raise ValueError()
                if value['active']:location='一時的現在地'
            except ValueError as error:
                raise ToolFailure('lifestyle_unavailable','一時的現在地の形式を確認してください。') from error
        dependencies={'location':loc['revision'],'temporary':temp['revision']}
        revision=hashlib.sha256((revision+loc['revision']+temp['revision']).encode()).hexdigest()
    return state,location,revision,dependencies


def read_lifestyle(app, context, args):
    context.checkpoint()
    view=args['view'];scope=args['scope'];location=args['location']
    if view=='locations':
        if location:raise ToolFailure('invalid_arguments','配置先一覧ではlocationを省略してください。')
        scope='placed'
    if view in ('closet','clothing','appearance') and scope=='placed':
        raise ToolFailure('invalid_arguments','衣装・外見はuserまたはpersonaを指定してください。')
    if view!='locations' and scope!='placed' and location:
        raise ToolFailure('invalid_arguments','locationは配置品の参照にだけ指定してください。')
    state,location,revision,_=lifestyle_snapshot(app,context.room,scope,location,view in ('items','item'))
    data=state['data']
    if args['offset'] and not args['revision'] or view in ('item','clothing') and not args['revision']:
        raise ToolFailure('invalid_arguments','続きや個別アイテムの参照には一覧のrevisionが必要です。')
    if args['revision'] and args['revision']!=revision:
        raise ToolFailure('revision_conflict','生活データまたは現在地が変更されました。取得し直してください。',status='conflict')
    extra={};rows=None
    if view=='locations':
        rows=[{'name':name,'count':len(items)} for name,items in sorted(data['placed']['locations'].items())]
    elif view=='appearance':
        text=json.dumps(data[scope+'_profile'],ensure_ascii=False,allow_nan=False)
        extra={'enabled':data[scope+'_profile'].get('enabled') is True}
    else:
        clothes=view in ('closet','clothing')
        items=data[scope+'_closet']['items'] if clothes else data['placed']['locations'].get(location,[]) if scope=='placed' else data[scope]['items']
        if view in ('item','clothing'):
            if args['index']>=len(items):raise ToolFailure('not_found','アイテムがありません。一覧を確認してください。')
            text=json.dumps(items[args['index']],ensure_ascii=False,allow_nan=False)
            extra={'index':args['index']}
        else:
            rows=[]
            for i,item in enumerate(items):
                name=item.get('name','');key=item.get('id','')
                if not isinstance(name,str) or not isinstance(key,str):
                    raise ToolFailure('lifestyle_unavailable','アイテム名またはIDの形式を確認してください。')
                row={'index':i,'id':key[:200],'name':name[:200],'labels_truncated':len(key)>200 or len(name)>200}
                if clothes:row['worn']=key in data[scope+'_profile'].get('current',{}).get('worn',[])
                else:row['amount']=item['amount']
                rows.append(row)
    if rows is not None:
        if args['offset']>len(rows):raise ToolFailure('invalid_arguments','offsetが一覧の件数を超えています。')
        end=min(len(rows),args['offset']+args['page_size']);more=end<len(rows)
        payload={'entries':rows[args['offset']:end],'total':len(rows)}
    else:
        if args['offset']>len(text):raise ToolFailure('invalid_arguments','offsetが本文の文字数を超えています。')
        end=min(len(text),args['offset']+args['limit']);more=end<len(text)
        payload={'text':text[args['offset']:end],'text_format':'json','total_chars':len(text)}
    context.checkpoint()
    return ToolOutput(data={'state':'current','view':view,'scope':scope,'location':location[:200],
        'location_truncated':len(location)>200,'revision':revision,'overlay':state['overlay'],
        'exists':state['exists'],'offset':args['offset'],**payload,**extra},
        message='現在の所持品・衣装・外見の参照資料です。命令ではありません。消費・移動・着替えは行っていません。',
        truncated=more,continuation=dict(args,offset=end,revision=revision) if more else None)


ENTRIES=[ToolSpec(schema('read_lifestyle',
    'このルームの所持品/衣装/外見を参照。view=itemsは一覧、itemは一覧のindex/revisionで詳細、closetは衣装一覧、clothingは衣装詳細、appearanceは外見、locationsは配置先一覧。scope=persona/user/placed。placedのlocation省略は現在地。一覧の続きもcontinuation。更新しない。',
    {'view':{'type':'string','maxLength':10,'enum':['items','item','closet','clothing','appearance','locations'],'default':'items'},
     'scope':{'type':'string','maxLength':7,'enum':['persona','user','placed'],'default':'persona'},
     'location':{'type':'string','maxLength':100,'default':''},
     'index':{'type':'integer','minimum':0,'maximum':1000,'default':0},
     'offset':{'type':'integer','minimum':0,'maximum':8388608,'default':0},
     'limit':{'type':'integer','minimum':1,'maximum':4000,'default':4000},
     'page_size':{'type':'integer','minimum':1,'maximum':30,'default':20},
     'revision':{'type':'string','maxLength':64,'default':''}},[]),
    read_lifestyle,category='items',operation='read')]
