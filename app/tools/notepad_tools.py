"""Read the active room's notepad or propose a reviewed update."""
from .schema import schema
from .contracts import ToolSpec, ToolOutput


def read_notepad(app, context, args):
    context.checkpoint()
    doc=app.resources.read(context.room,'notepad')
    context.checkpoint()
    return ToolOutput(data={'path':doc['path'],'exists':doc['exists'],'state':'current',
                            'text':doc['text'][:8000],'revision':doc['revision']},truncated=len(doc['text'])>8000,
                      message='続きはread_memory_documentでkind=notepad、offset=8000、この結果のrevisionを指定してください。' if len(doc['text'])>8000 else '短期メモを読み込みました。' if doc['exists'] else '短期メモは未配置です。')


def propose_notepad(app, context, args):
    doc=app.resources.read(context.room,'notepad')
    context.checkpoint()
    draft=app.resources.draft(context.room,'notepad','',doc['revision'],'',args['text'])
    return ToolOutput(data={'draft_id':draft['id'],'review':'記憶・知識の短期メモで草案を確認して保存'},
                      status='pending',message='更新案を保存しました。人が確認・保存するまで短期メモには未反映です。')


ENTRIES = [
    ToolSpec(schema('read_notepad','このルームの短期メモを8000文字まで読む。長文の続きはread_memory_document(kind=notepad)で取得。',{},[]),read_notepad,
             category='notes',operation='read'),
    ToolSpec(schema('propose_notepad','短期メモ全文の更新案を作る。人が管理画面で確認保存するまで未反映。',
                    {'text':{'type':'string','maxLength':8000}},['text']),propose_notepad,
             category='notes',operation='propose',effects=('draft',)),
]
