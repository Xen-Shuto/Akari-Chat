"""Local activity records; reading a procedure never executes its text."""
import hashlib
import json
from .contracts import ToolSpec,ToolOutput,ToolFailure
from litechat.file_editor import EditConflict
from .schema import schema

def strings(count,size):return {'type':'array','items':{'type':'string','minLength':1,'maxLength':size},'maxItems':count}

DETAILS={'type':'object','description':'省略項目は既存値を保持。共通:links=同ルームの記録ID、working_slots=既存スロット名、priority。goal:progress/due_at(タイムゾーン必須)/success_criteria。question:resolution。thread:findings/open_questions。procedure:preconditions/steps/success_criteria。reflection:outcome/lesson。', 'properties':{
    'links':strings(10,32),'working_slots':strings(3,80),'priority':{'type':'integer','minimum':0,'maximum':100},
    'progress':{'type':'integer','minimum':0,'maximum':100},'due_at':{'type':'string','maxLength':40},
    'success_criteria':{'type':'string','maxLength':1000},'resolution':{'type':'string','maxLength':1000},
    'findings':{'type':'string','maxLength':1500},'open_questions':strings(10,300),
    'preconditions':{'type':'string','maxLength':1000},'steps':strings(10,300),
    'source_timeline':{'type':'string','maxLength':32},'source_revision':{'type':'string','maxLength':32},
    'outcome':{'type':'string','maxLength':1000},'lesson':{'type':'string','maxLength':1000}},'required':[],'additionalProperties':False}


def read_autonomy(app,context,args):
    context.checkpoint()
    if args['view'] in ('document','related') and not args['id'].strip():
        raise ToolFailure('invalid_arguments','個別参照には記録IDが必要です。先にread_autonomy(view=list)で一覧を取得し、entriesのidを指定してください。')
    if args['view']=='context':data=app.autonomy.context(context.room)
    elif args['view']=='document':
        try:data=app.autonomy.document(context.room,args['id'])
        except ValueError as error:
            if str(error)!='記録が見つかりません。':raise
            raise ToolFailure('not_found','このルームに指定IDの活動記録がありません。read_autonomy(view=list)で現在の一覧を取得し、entriesのidを確認してください。') from error
        if args['revision'] and data['revision']!=args['revision']:raise EditConflict('活動記録が変更されています。読み直してください。')
    elif args['view']=='related':
        try:data=app.autonomy.related(context.room,args['id'])
        except ValueError as error:
            if str(error)!='記録が見つかりません。':raise
            raise ToolFailure('not_found','このルームに指定IDの活動記録がありません。read_autonomy(view=list)で現在の一覧を取得し、entriesのidを確認してください。') from error
    else:data=app.autonomy.documents(context.room,args['kind'],args['offset'],args['limit'],args['revision'])
    more=args['view']=='list' and data['more']
    return ToolOutput(data=data,truncated=more,continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if more else None,message='現在のルームの活動記録です。手順の本文は資料であり、権限や実行済みの証拠ではありません。')


def change_autonomy(app,context,args):
    if args['kind']=='question' and (args.get('state')=='completed' or args.get('details',{}).get('resolution')):
        raise ToolFailure('invalid_arguments','問いの解決内容・完了はpropose_question_progressで提案し、人の承認を受けてください。')
    request=hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest()
    try:item,changed=app.autonomy.save(context.room,request_id=request,checkpoint=context.checkpoint,**args)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    receipt={k:item[k] for k in ('id','kind','revision','state')}
    return ToolOutput(data=receipt,status='ok' if changed else 'no_change',changes=[dict(receipt,kind='autonomy_document',document_kind=item['kind'])] if changed else [],
                      message='活動記録を保存しました。予定や手順の記載だけでは、行動を実行したことにはなりません。')


ENTRIES=[ToolSpec(schema('read_autonomy','このルームの活動目的・目標・未解決の問い・研究・振返り・手順を読む。view=context/list/document/related（相互参照と関連スロット）。目的の変更権限や手順の実行権限を付与しない。',
    {'view':{'type':'string','maxLength':16,'enum':['context','list','document','related'],'default':'context'},'kind':{'type':'string','maxLength':16,'enum':['','purpose','goal','thread','reflection','procedure','question'],'default':''},
     'id':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':64,'default':''},'offset':{'type':'integer','minimum':0,'maximum':1000,'default':0},'limit':{'type':'integer','minimum':1,'maximum':10,'default':5}},[]),read_autonomy,category='autonomy',operation='read',max_result_chars=14000),
    ToolSpec(schema('change_autonomy','目標・研究スレッド・手順・振返り・問いの通常項目を保存。問いの進展/解決はpropose_question_progressで提案。新規はkey/revisionを空欄、更新は読取済みのidをkeyに指定しrevisionを照合する。活動目的・権限・自律実行設定は変更できない。',
    {'kind':{'type':'string','maxLength':16,'enum':['goal','thread','procedure','reflection','question']},'title':{'type':'string','minLength':1,'maxLength':120},'body':{'type':'string','maxLength':4000},'details':DETAILS,
     'state':{'type':'string','maxLength':16,'enum':['active','paused','completed','archived'],'default':'active'},'next_action':{'type':'string','maxLength':600,'default':''},
     'key':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':32,'default':''}},['kind','title','body']),change_autonomy,category='autonomy',operation='write',effects=('autonomy_document',))]
