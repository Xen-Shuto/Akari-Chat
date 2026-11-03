"""Derive a paused local procedure candidate, without executing it or another LLM."""
import hashlib
import json
from litechat.file_editor import EditConflict
from .contracts import ToolSpec,ToolOutput,ToolFailure
from .schema import schema


def derive_procedure(app,context,args):
    request=hashlib.sha256(json.dumps([context.job_id,context.call_id]).encode()).hexdigest()
    try:item,changed,truncated=app.timelines.derive_procedure(app.autonomy,context.room,args['id'],args['revision'],args['title'],request,context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    receipt={key:item[key] for key in ('id','revision','state','kind')}
    return ToolOutput(data=receipt,status='ok' if changed else 'no_change',truncated=truncated,changes=[dict(receipt,kind='autonomy_document',document_kind=item['kind'])] if changed else [],
                      message='正常終了した実行のアプリ確認済み操作から、保留状態の手順候補を保存しました。適用条件・成功条件を確認してください。手順は実行していません。')


ENTRIES=[ToolSpec(schema('derive_procedure','正常終了した自律実行のタイムラインから保留状態の手順候補を作成。read_timelineで得たid/revisionが必須。アプリ確認済み操作のみ抽出し、推論やツールの再実行はしない。',
    {'id':{'type':'string','minLength':32,'maxLength':32},'revision':{'type':'string','minLength':32,'maxLength':32},'title':{'type':'string','minLength':1,'maxLength':120}},['id','revision','title']),derive_procedure,category='autonomy',operation='write',effects=('autonomy_document',))]
