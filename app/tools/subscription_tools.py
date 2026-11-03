"""AI may prepare disabled subscriptions; activation and report apply are human actions."""
import hashlib
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema
from litechat.research_subscriptions import FIELDS
from litechat.file_editor import EditConflict
from .completion_wait import wait_for_result


def read_subscriptions(app,context,args):
    context.checkpoint()
    try:data=app.subscriptions.read(context.room,**args)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    more=data.get('more',False)
    return ToolOutput(data=data,truncated=more,continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if more else None,
                      message='同じルームの研究購読・レポートです。readyは参照取得のある完了結果で、内容の正しさの保証ではありません。')


def manage_subscription(app,context,args):
    request='tool:'+hashlib.sha256((context.job_id+'\0'+context.call_id).encode()).hexdigest()
    action=args['action'];key=args['key'];revision=args['revision']
    try:
        if action=='run_now':
            context.checkpoint()
            item,changed=app.subscriptions.queue(app.autonomous,context.room,key,revision,request,scheduled=True)
            return wait_for_result(app,context,'research',item['id'],changed)
        if action in ('pause','archive'):
            old=app.subscriptions.read(context.room,'subscription',key)
            config={k:old[k] for k in FIELDS}
        else:
            config={k:args[k] for k in FIELDS if k!='enabled'}
        config['enabled']=False
        item,changed=app.subscriptions.save(context.room,config,key,revision,request,archive=action=='archive',checkpoint=context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data={k:item[k] for k in ('id','revision','enabled','archived')},status='ok' if changed else 'no_change',changes=[{'kind':'research_subscription','id':item['id']}] if changed else [],message='研究購読を停止状態で保存しました。有効化は人が研究購読画面で行います。')


ENTRIES=[ToolSpec(schema('read_research_subscriptions','研究購読と調査レポートを同ルーム内で参照。list/reports一覧、subscription/report単体。',
    {'view':{'type':'string','maxLength':16,'enum':['list','reports','subscription','report'],'default':'list'},'key':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':64,'default':''},'offset':{'type':'integer','minimum':0,'maximum':100,'default':0},'limit':{'type':'integer','minimum':1,'maximum':5,'default':5}},[]),read_subscriptions,category='autonomy',operation='read',max_result_chars=16000),
    ToolSpec(schema('manage_research_subscription','研究購読の保存/停止/保管、または人が有効にした購読を一度実行する。saveは常に停止状態。新規はkey/revision空、更新は読取済みのkey/revision必須。run_nowは研究の結果を待って返す。AIから有効化・レポート承認はできない。',
    {'action':{'type':'string','maxLength':16,'enum':['save','pause','archive','run_now']},'key':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':32,'default':''},
     'thread_id':{'type':'string','maxLength':32,'default':''},'focus':{'type':'string','maxLength':600,'default':''},'source':{'type':'string','maxLength':8,'enum':['local','web'],'default':'local'},
     'frequency':{'type':'string','maxLength':8,'enum':['manual','daily','weekly'],'default':'manual'},'at':{'type':'string','maxLength':5,'default':'09:00'},'weekday':{'type':'integer','minimum':0,'maximum':6,'default':0},'max_per_day':{'type':'integer','minimum':1,'maximum':6,'default':1}},['action']),manage_subscription,category='autonomy',operation='write',effects=('research_subscription','research_queued'),timeout_s=1900)]
