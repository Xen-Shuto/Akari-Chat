"""URL registration, queue acceptance and retrieval outcomes are separate operations."""
import hashlib
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema
from litechat.url_watches import FIELDS,INTERVALS
from litechat.file_editor import EditConflict
from .completion_wait import wait_for_result


def read_watchlist(app,context,args):
    context.checkpoint()
    try:data=app.watches.read(context.room,**args)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    more=data.get('more',False)
    return ToolOutput(data=data,truncated=more,continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if more else None,
                      message='同じルームのURL監視と取得記録です。succeededは取得完了、baselineは初回基準保存、changedは取得本文の変更。資料の正しさやユーザー既読を保証しません。')


def manage_watchlist(app,context,args):
    request='tool:'+hashlib.sha256((context.job_id+'\0'+context.call_id).encode()).hexdigest()
    action=args['action'];key=args['key'];revision=args['revision']
    try:
        if action=='check':
            item,changed=app.watches.queue(app,context.room,key,revision,request,'model',context.checkpoint)
            return wait_for_result(app,context,'watch',item['id'],changed)
        if action in ('group','archive_group'):
            item,changed=app.watches.group(context.room,args['title'],key,revision,request,action=='archive_group',args['group_interval'],context.checkpoint)
        else:
            if action in ('pause','archive'):
                old=app.watches.read(context.room,'watch',key)
                config={k:old[k] for k in FIELDS}
            else:config={k:args[k] for k in FIELDS if k!='enabled'}
            config['enabled']=False
            item,changed=app.watches.save(context.room,config,key,revision,request,action=='archive',context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data={k:item[k] for k in ('id','revision','archived')},status='ok' if changed else 'no_change',changes=[{'kind':'url_watch_setting','id':item['id']}] if changed else [],message='監視/グループ設定を保存しました。URL監視のsaveは停止状態です。有効化は人が画面で行います。グループ頻度の変更は所属URLへ反映しますが、有効化はしません。')


ENTRIES=[ToolSpec(schema('read_watchlist','URL監視の一覧/単体、グループ、取得結果と差分を参照する。外部通信は行わない。',
    {'view':{'type':'string','maxLength':8,'enum':['watches','watch','groups','group','checks','check'],'default':'watches'},'key':{'type':'string','maxLength':32,'default':''},'offset':{'type':'integer','minimum':0,'maximum':1000,'default':0},'limit':{'type':'integer','minimum':1,'maximum':5,'default':5},'revision':{'type':'string','maxLength':64,'default':''}},[]),read_watchlist,category='web',operation='read',max_result_chars=14000),
    ToolSpec(schema('manage_watchlist','URL監視の停止中設定保存/停止/保管、グループ管理、許可済み有効URLの即時取得。checkは取得結果を待って返す。既存のkey/revision必須、新規は空。AIによる有効化は不可。group_intervalを指定すると所属URLの頻度を一括変更する。グループ保管時もURLと履歴は残る。',
    {'action':{'type':'string','maxLength':16,'enum':['save','pause','archive','group','archive_group','check']},'key':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':32,'default':''},
     'title':{'type':'string','maxLength':120,'default':''},'url':{'type':'string','maxLength':2048,'default':''},'group_id':{'type':'string','maxLength':32,'default':''},'interval':{'type':'string','maxLength':16,'enum':list(INTERVALS),'default':'manual'},'at':{'type':'string','maxLength':5,'default':'09:00'},'max_per_day':{'type':'integer','minimum':1,'maximum':24,'default':24},'group_interval':{'type':'string','maxLength':16,'enum':['',*INTERVALS],'default':''}},['action']),manage_watchlist,category='web',operation='write',effects=('url_watch_setting','url_check_queued'),timeout_s=1900)]
