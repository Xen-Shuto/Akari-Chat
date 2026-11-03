"""Explicit timeline reports, separate from runtime-verified tool receipts."""
import hashlib
import json
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema


def read_timeline(app,context,args):
    context.checkpoint()
    data=app.timelines.document(context.room,args['id'],args['offset'],args['limit'],args['revision']) if args['view']=='document' else app.timelines.listing(context.room,args['offset'],args['limit'],args['revision'])
    return ToolOutput(data=data,truncated=data['more'],continuation=dict(args,offset=args['offset']+args['limit'],revision=data['revision']) if data['more'] else None,
                      message='actor=model/userは自己申告・注記、systemはアプリが確認した記録です。計画や自己申告を実行の証拠と扱わないでください。')


def change_timeline(app,context,args):
    request=hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest()
    try:item,changed=app.timelines.change(context.room,request_id=request,checkpoint=context.checkpoint,**args)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data=item,status='ok' if changed else 'no_change',changes=[dict(kind='activity_timeline',id=item['id'],revision=item['revision'])] if changed else [],message='活動過程の自己申告を記録しました。行動やツールそのものを実行した証拠ではありません。')


ENTRIES=[ToolSpec(schema('read_timeline','このルームの型付き活動タイムラインを一覧/分割参照する。実行IDが分かればidへ指定。',
    {'view':{'type':'string','maxLength':16,'enum':['list','document'],'default':'list'},'id':{'type':'string','maxLength':32,'default':''},
     'offset':{'type':'integer','minimum':0,'maximum':5000,'default':0},'limit':{'type':'integer','minimum':1,'maximum':10,'default':5},'revision':{'type':'string','maxLength':64,'default':''}},[]),read_timeline,category='autonomy',operation='read',max_result_chars=12000),
    ToolSpec(schema('change_timeline','活動過程の自己申告。startで新規、appendでobserve/orient/decide/act/reflect追記、editで自己申告の訂正、finishで終了。更新は読取済みkey/revision必須。アプリの確認記録は編集不可。自律実行の終了はアプリが記録する。',
    {'action':{'type':'string','maxLength':16,'enum':['start','append','edit','finish']},'key':{'type':'string','maxLength':32,'default':''},'revision':{'type':'string','maxLength':32,'default':''},
     'title':{'type':'string','maxLength':120,'default':''},'phase':{'type':'string','maxLength':16,'enum':['observe','orient','decide','act','reflect'],'default':'observe'},
     'summary':{'type':'string','maxLength':600,'default':''},'step_id':{'type':'string','maxLength':32,'default':''},'state':{'type':'string','maxLength':16,'enum':['completed','failed','cancelled'],'default':'completed'},'related_id':{'type':'string','maxLength':32,'default':''}},['action']),change_timeline,category='autonomy',operation='write',effects=('activity_timeline',))]
