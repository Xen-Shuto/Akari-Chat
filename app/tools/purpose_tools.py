"""Read a purpose profile or propose changes. Human approval is never a tool."""
import hashlib
import json
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema


PROFILE={'type':'object','properties':{key:{'type':'array','maxItems':5,'items':{'type':'string','minLength':1,'maxLength':200}} for key in (
    'core_values','stable_interests','preferred_behaviors','avoid_behaviors','active_interests','open_questions')},'required':[],'additionalProperties':False}


def read_purpose(app,context,args):
    context.checkpoint()
    if args['view']=='profile':data=app.purposes.profile(context.room)
    elif args['view']=='proposals':data=app.purposes.listing(context.room,args['offset'],args['limit'],args['revision'])
    else:
        item=app.purposes.document(context.room,args['id'])
        data={key:value for key,value in item.items() if key not in ('before','after')}
        data.update(part=args['part'],document=item[args['part']])
    more=args['view']=='proposals' and data['more']
    return ToolOutput(data=data,truncated=more,continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if more else None,
                      message='現在の目的または変更提案です。提案は人が承認するまで活動目的に反映されません。')


def propose_purpose_change(app,context,args):
    token=hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest()
    try:item,_=app.purposes.propose(context.room,request_id=token,checkpoint=context.checkpoint,**args)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    pending=item['state']=='pending'
    return ToolOutput(status='pending' if pending else 'no_change',data={'proposal_id':item['id'],'revision':item['revision'],'proposal_state':item['state'],'review':'自律行動 → 目的の提案'},
                      message='目的の変更案を保存しました。あなたの承認前で、活動目的には未反映です。' if pending else '既存提案の受付記録です。目的を再更新していません。現在の状態を参照してください。')


ENTRIES=[ToolSpec(schema('read_purpose','活動目的のプロフィール、変更提案一覧、選択した提案の変更前/変更後を読む。未設定のprofileはrevision=absent。',
    {'view':{'type':'string','maxLength':16,'enum':['profile','proposals','proposal'],'default':'profile'},'id':{'type':'string','maxLength':32,'default':''},
     'part':{'type':'string','maxLength':8,'enum':['before','after'],'default':'after'},'offset':{'type':'integer','minimum':0,'maximum':1000,'default':0},
     'limit':{'type':'integer','minimum':1,'maximum':5,'default':5},'revision':{'type':'string','maxLength':64,'default':''}},[]),read_purpose,category='autonomy',operation='read',max_result_chars=14000),
    ToolSpec(schema('propose_purpose_change','活動目的の変更を提案。人の承認まで未反映。read_purpose(profile)のrevisionが必須、未設定時はabsent。title/bodyは全文、detailsは変更するプロフィール項目のみ。AIの承認・権限変更は不可。',
    {'title':{'type':'string','minLength':1,'maxLength':120},'body':{'type':'string','maxLength':4000},'reason':{'type':'string','minLength':1,'maxLength':600},
     'revision':{'type':'string','minLength':1,'maxLength':32},'details':PROFILE},['title','body','reason','revision']),propose_purpose_change,category='autonomy',operation='propose',effects=('purpose_proposal',))]
