"""Local persona-to-user messages; human read state and permissions stay UI-only."""
import hashlib
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema
from litechat.file_editor import EditConflict


def create(app,context,args,kind):
    request=hashlib.sha256((context.job_id+'\0'+context.call_id).encode()).hexdigest()
    try:item,changed=app.inbox.create(context.room,kind,args.get('title','お知らせ'),args['message'],request,context.job_id,args.get('allow_similar',False),context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data={k:item[k] for k in ('id','kind','state','revision')},status='ok' if changed else 'no_change',changes=[{'kind':'local_inbox','id':item['id']}] if changed else [],
                      message=('手紙箱へ保存しました。ユーザー既読や外部配信を確認したものではありません。' if kind=='letter' else 'アプリ内お知らせの表示待ちとして保存しました。会話終了後、静かな時間帯を避けて通知一覧へ保存します。OS/外部サービスには送信しません。') if changed else '同じ要求、または似た内容が保存済みなので新規保存していません。状態はread_lettersで確認してください。')


def leave_letter(app,context,args):return create(app,context,args,'letter')
def notify(app,context,args):return create(app,context,args,'notice')


def read_letters(app,context,args):
    context.checkpoint()
    try:
        if args['view']=='document':
            if args['offset'] and not args['revision']:raise ValueError('本文の続きにはrevisionが必要です。')
            data=app.inbox.document(context.room,args['key'],args['offset'],4000,args['revision'])
            continuation=dict(args,offset=args['offset']+len(data['body']),revision=data['revision']) if data['more'] else None
        else:
            if args['offset'] and not args['revision']:raise ValueError('一覧の続きにはrevisionが必要です。')
            data=app.inbox.listing(context.room,args['kind'],args['filter'],args['offset'],5,args['revision'])
            continuation=dict(args,offset=args['offset']+5,revision=data['revision']) if data['more'] else None
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data=data,truncated=bool(continuation),continuation=continuation,message='同じルームの手紙/お知らせです。参照だけでは既読になりません。queuedは表示待ち、deliveredはアプリ内通知への保存で、人が読んだ証拠ではありません。')


ENTRIES=[ToolSpec(schema('leave_letter_for_user','ユーザーへ後で読んでほしい手紙を、このルームの手紙箱へ残す。外部へ送信しない。既読や削除/承認は人が操作する。似た手紙は直近7日/最大50通を確認し、意図的な再送だけallow_similar=true。',
    {'title':{'type':'string','minLength':1,'maxLength':120},'message':{'type':'string','minLength':1,'maxLength':8000},'allow_similar':{'type':'boolean','default':False}},['title','message']),leave_letter,category='time',operation='write',effects=('local_inbox',)),
    ToolSpec(schema('send_user_notification','短いお知らせを、このアプリの通知一覧への表示待ちとして保存する。会話終了後、静かな時間帯以外に表示。OS・Discord・Pushoverなどへは送信しない。予約受付は表示完了や既読ではない。同文は直近1日/最大50件を確認して重複を抑制。',
    {'title':{'type':'string','minLength':1,'maxLength':120,'default':'お知らせ'},'message':{'type':'string','minLength':1,'maxLength':2000}},['message']),notify,category='time',operation='write',effects=('local_inbox',)),
    ToolSpec(schema('read_letters','同ルームの手紙/お知らせの一覧・本文を参照。view=list/document。本文はkeyとrevisionを指定して分割。読むだけでは人の既読を変更しない。',
    {'view':{'type':'string','maxLength':8,'enum':['list','document'],'default':'list'},'kind':{'type':'string','maxLength':6,'enum':['letter','notice'],'default':'letter'},'filter':{'type':'string','maxLength':8,'enum':['all','unread','archived'],'default':'all'},'key':{'type':'string','maxLength':32,'default':''},'offset':{'type':'integer','minimum':0,'maximum':8000,'default':0},'revision':{'type':'string','maxLength':64,'default':''}},[]),read_letters,category='time',operation='read',max_result_chars=14000)]
