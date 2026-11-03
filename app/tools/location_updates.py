"""Single-file location writes with world/location/temporary dependencies."""
from litechat.file_editor import EditConflict
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema


def change_location(app, context, args):
    with app.store.lock:
        try:
            prepared=app.world.prepare_location(context.room,args['action'],args['id'],args['text'],
                {k:args[k+'_revision'] for k in ('world','location','temporary')})
        except EditConflict:raise
        except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
        context.checkpoint()
        saved=app.world.change_location(context.room,prepared,context.checkpoint)
        kind=prepared[1]
        receipt={'action':args['action'],'kind':kind,'revision':saved['document']['revision'],'backup':saved['backup']}
    return ToolOutput(status='ok' if saved['changed'] else 'no_change',data=receipt,
        changes=[receipt] if saved['changed'] else [],message='現在地を反映しました。世界設定本文は変更していません。')


ENTRIES=[ToolSpec(schema('change_location',
    '現在地を即時変更。moveはread_world(places)のidへ移動。temporaryは情景textを設定、clearは一時的現在地を解除。read_world(current)のworld_revision/location_revision/temporary_revisionが全て必須。一時的現在地が有効なら先にclearして読み直してからmove。一操作一ファイル、バックアップあり。世界設定編集はpropose_document。',
    {'action':{'type':'string','maxLength':9,'enum':['move','temporary','clear']},
     'id':{'type':'integer','minimum':0,'maximum':5000,'default':0},
     'text':{'type':'string','maxLength':6000,'default':''},
     'world_revision':{'type':'string','minLength':64,'maxLength':64},
     'location_revision':{'type':'string','minLength':64,'maxLength':64},
     'temporary_revision':{'type':'string','minLength':64,'maxLength':64}},
     ['action','world_revision','location_revision','temporary_revision']),
    change_location,category='world',operation='write',effects=('location','temporary'))]
