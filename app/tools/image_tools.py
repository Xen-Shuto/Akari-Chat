"""Image registration, cancellation and artifact references; no model-provided paths."""
import hashlib
import json
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema
from .completion_wait import wait_for_result


def generate_image(app,context,args):
    try:prepared=app.images.prepare(context.room,args['prompt'],args['aspect'],args['reference_ids'])
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    prepared.update(source_job_id=context.job_id,source_call_id=context.call_id,execution='inline')
    item,changed=app.images.enqueue(context.room,prepared,hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest(),context.checkpoint)
    return wait_for_result(app,context,'image',item['id'],changed)


def cancel_image_job(app,context,args):
    try:item,changed=app.images.cancel(context.room,args['id'],args['revision'],context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    receipt={'kind':'image_job','id':item['id'],'revision':item['revision'],'state':item['state']}
    return ToolOutput(data=receipt,status='ok' if changed else 'no_change',changes=[receipt] if changed else [],
        message='取消を記録しました。cancellingは実行中の取消要求であり生成先の停止確認ではありません。返ってきた画像は保全します。')


def read_images(app,context,args):
    context.checkpoint()
    try:
        if args['view']=='image':
            doc,_=app.images.artifact(context.room,args['id'])
            return ToolOutput(data={'artifact':doc,'visual_content':'not_supplied'},message='画像の出典情報です。この結果のテキストだけでは画像自体を見たことにはなりません。')
        if args['view']=='references':rows=app.images.references(context.room)
        elif args['view']=='legacy':rows=app.images.legacy_images(context.room)
        else:
            state=app.images.listing(context.room);rows=state['jobs'] if args['view']=='jobs' else state['images']
            if args['view']=='jobs':rows=[{k:i.get(k) for k in ('id','revision','state','provider','model','created_at','artifact_id','cancel_requested','error','error_code','completed','timeout_seconds','duration_ms')} for i in rows]
            else:rows=[{k:i.get(k) for k in ('id','path','provider','model','created_at','width','height')} for i in rows]
    except EditConflict:raise
    except (ValueError,OSError) as error:raise ToolFailure('image_unavailable','画像または一覧を確認できません。') from error
    revision=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if args['offset'] and not args['revision']:raise ToolFailure('invalid_arguments','続きにはrevisionが必要です。')
    if args['revision'] and args['revision']!=revision:raise EditConflict('画像/ジョブ一覧が変わりました。')
    end=min(len(rows),args['offset']+args['limit']);more=end<len(rows)
    return ToolOutput(data={'view':args['view'],'entries':rows[args['offset']:end],'total_visible':len(rows),'revision':revision},truncated=more,
        continuation=dict(args,offset=end,revision=revision) if more else None,message='このルームの上限付き一覧です。legacyは既存generated_imagesの最大200件、referencesはPNG/JPEG参照候補です。')

ENTRIES=[ToolSpec(schema('generate_image','設定した生成先で画像を作り、応答と保存が終わるまで待って結果を返す。prompt、縦横比、必要ならこのルームのreference_idsを渡す。返された状態と保存先を踏まえて応答する。生成結果をユーザーに確認させる前に、まずツール結果を受け取る。参照画像は生成先へ送信される。',
    {'prompt':{'type':'string','minLength':1,'maxLength':6000},'aspect':{'type':'string','maxLength':9,'enum':['square','portrait','landscape'],'default':'square'},
     'reference_ids':{'type':'array','maxItems':4,'items':{'type':'string','maxLength':40},'default':[]}},['prompt']),generate_image,
     category='images',operation='write',effects=('image_job',),output='job',timeout_s=1900,available=lambda app,context:app.images.configured()),
    ToolSpec(schema('cancel_image_job','このルームの画像生成を取消。read_imagesのジョブid/revision必須。queuedなら取消、runningなら取消要求。生成先の実行/課金停止を保証しない。',
    {'id':{'type':'string','minLength':32,'maxLength':32},'revision':{'type':'string','minLength':32,'maxLength':32}},['id','revision']),cancel_image_job,category='images',operation='write',effects=('image_job',)),
    ToolSpec(schema('read_images','画像生成ジョブ/生成画像/既存画像/参照画像を調べる。view=jobs/images/legacy/referencesは一覧、imageは一覧のid。画像自体の視覚入力は対応会話モデルと送信枠が必要。取得結果のvisual_contentを確認し、テキスト情報だけで見たふりをしない。',
    {'view':{'type':'string','maxLength':10,'enum':['jobs','images','legacy','references','image'],'default':'jobs'},'id':{'type':'string','maxLength':40,'default':''},
     'offset':{'type':'integer','minimum':0,'maximum':200,'default':0},'limit':{'type':'integer','minimum':1,'maximum':5,'default':5},'revision':{'type':'string','maxLength':64,'default':''}},[]),read_images,category='images',operation='read')]
