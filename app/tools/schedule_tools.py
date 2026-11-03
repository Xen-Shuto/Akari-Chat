"""Schedule registration is distinct from actual in-app notification delivery."""
import hashlib
import json
from datetime import datetime,timezone
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema


def read_schedules(app,context,args):
    context.checkpoint()
    state=app.schedules.listing(context.room)
    revision=hashlib.sha256(json.dumps(state,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if args['offset'] and not args['revision']:raise ToolFailure('invalid_arguments','続きにはrevisionが必要です。')
    if args['revision'] and args['revision']!=revision:raise EditConflict('予約/通知の一覧が変わりました。取得し直してください。')
    rows=state['schedules'] if args['view']=='schedules' else state['notifications']
    page=rows[args['offset']:args['offset']+args['limit']]
    return ToolOutput(data={'now_utc':datetime.fromtimestamp(app.schedules.wall(),timezone.utc).isoformat(),'local_now':datetime.fromtimestamp(app.schedules.wall()).astimezone().isoformat(),'view':args['view'],'revision':revision,'entries':page,'unread':state['unread'],'total_visible':len(rows),
        'retained_view_limit':200 if args['view']=='schedules' else 100},
        truncated=args['offset']+args['limit']<len(rows),
        continuation=dict(args,offset=args['offset']+args['limit'],revision=revision) if args['offset']+args['limit']<len(rows) else None,
        message='現在の予約/通知一覧です。通知のreadは人が既読にした状態で、予約登録や通知保存だけでは既読ではありません。')


def change_schedule(app,context,args):
    if args['action']=='cancel':
        try:
            state=app.schedules.listing(context.room)
            if not any(i['id']==args['id'] for i in state['schedules']):raise ValueError('予約が一覧にありません。')
        except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
        item,changed=app.schedules.cancel(context.room,args['id'],args['revision'],context.checkpoint)
    else:
        try:prepared=app.schedules.prepare(args['action'],args['title'],args['message'],args['seconds'],args['at'],args['work_seconds'],args['break_seconds'],args['cycles'])
        except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
        item,changed=app.schedules.create(context.room,prepared,hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest(),context.checkpoint)
    receipt={'kind':'schedule','id':item['id'],'revision':item['revision'],'state':item['state'],'due_at':item['due_at'],
        'type':item['kind'],'phase':item['phase'],'cycle':item['cycle']}
    return ToolOutput(status='ok' if changed else 'no_change',data=receipt,changes=[receipt] if changed else [],
        message='予約の登録/取消を保存しました。期限到達後、会話処理が終わってからアプリ内通知へ保存します。追加のAI返答やOS/外部通知は行いません。')

ENTRIES=[ToolSpec(schema('read_schedules','このルームのタイマー/アラーム/ポモドーロ予約とアプリ内通知を参照。新しい順の上限付き一覧。取消はid/revisionをchange_scheduleへ。',
    {'view':{'type':'string','maxLength':13,'enum':['schedules','notifications'],'default':'schedules'},
     'offset':{'type':'integer','minimum':0,'maximum':200,'default':0},
     'limit':{'type':'integer','minimum':1,'maximum':5,'default':5},
     'revision':{'type':'string','maxLength':64,'default':''}},[]),read_schedules,category='time',operation='read'),
    ToolSpec(schema('change_schedule','タイマー(timer)/日時アラーム(alarm)/ポモドーロ(pomodoro)を登録、またはcancel。alarmのatはUTCオフセット必須ISO日時（366日以内）。timerはseconds、pomodoroはwork_seconds/break_seconds/cycles。登録は配信完了ではない。会話優先で通知を遅延し、停止中の期限は起動後に通知。cancelは一覧のid/revision必須。',
    {'action':{'type':'string','maxLength':8,'enum':['timer','alarm','pomodoro','cancel']},
     'title':{'type':'string','maxLength':120,'default':'お知らせ'},
     'message':{'type':'string','maxLength':2000,'default':''},
     'seconds':{'type':'integer','minimum':1,'maximum':31622400,'default':300},
     'at':{'type':'string','maxLength':64,'default':''},
     'work_seconds':{'type':'integer','minimum':1,'maximum':86400,'default':1500},
     'break_seconds':{'type':'integer','minimum':1,'maximum':86400,'default':300},
     'cycles':{'type':'integer','minimum':1,'maximum':20,'default':4},
     'id':{'type':'string','maxLength':32,'default':''},
     'revision':{'type':'string','maxLength':32,'default':''}},['action']),change_schedule,category='time',operation='write',effects=('schedule',))]
