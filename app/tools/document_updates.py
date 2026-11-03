"""Reviewed narrative edits; AI never approves its own proposals."""
import json
from litechat.file_editor import EditConflict, normalize
from litechat.resources import strict_json
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema

KINDS=['core','identity','diary','notepad','secret','creative','analysis','entity','knowledge','episode','dream','archive','world']


def _prepare(app, context, args):
    context.checkpoint()
    kind,name,action=args['kind'],args['name'],args['action']
    if kind not in ('entity','knowledge','episode','dream','archive') and name:
        raise ToolFailure('invalid_arguments','固定の本文ではnameを省略してください。')
    try:app.resources.target(kind,name,context.room)
    except ValueError as error:raise ToolFailure('invalid_arguments','対象名が不正です。') from error
    with app.store.lock:
        doc=app.resources.read(context.room,kind,name)
        if kind=='entity' and (doc.get('merge_pending') or doc.get('state')=='merged'):
            raise ToolFailure('conflict','未完了の統合または統合済みの退避元です。統合履歴を確認してください。','conflict')
        if doc['revision']!=args['revision']:raise EditConflict('本文が変わりました。読み直してください。')
        dependencies=[]
        if action=='entry':
            if kind not in ('diary','creative','analysis','episode','dream'):
                raise ToolFailure('invalid_arguments','個別エントリの種類ではありません。')
            selected=app.entries.read(context.room,kind,name,args['entry_id'],expected=args['revision'])
            if args['field']!='text' and kind!='dream':raise ToolFailure('invalid_arguments','追加項目は夢日記だけです。')
            fields=None;text=args['text']
            if args['field']!='text':
                fields={**selected['fields'],args['field']:text};text=selected['text']
            candidate=app.entries.prepare(context.room,kind,name,args['entry_id'],text,args['revision'],fields)['text']
        elif action in ('patch','archive'):
            source,needle=normalize(doc['text']),normalize(args['old_text'])
            if not needle or source.count(needle)!=1:
                raise ToolFailure('conflict','置換元は空でなく、本文に一度だけ現れる必要があります。','conflict')
            if action=='archive':
                if kind!='diary' or args['text']:
                    raise ToolFailure('invalid_arguments','archiveは日記の指定箇所を除く操作です。textは省略してください。')
                archive=app.resources.read(context.room,'archive',args['archive_name'])
                if not archive['exists'] or archive['revision']!=args['archive_revision'] or needle not in normalize(archive['text']):
                    raise ToolFailure('conflict','同じ本文を含むアーカイブを先に確認保存してから指定してください。','conflict')
                dependencies=[{'kind':'archive','name':args['archive_name'],'revision':archive['revision']}]
            candidate=source.replace(needle,normalize(args['text']),1)
        elif action=='append' and kind in ('episode','dream'):
            previous=strict_json(doc['text']) if doc['text'].strip() else []
            addition=strict_json(args['text'])
            if not isinstance(previous,list) or not isinstance(addition,list):
                raise ToolFailure('invalid_arguments','JSONへの追記は配列を指定してください。')
            candidate=json.dumps(previous+addition,ensure_ascii=False,indent=2)
        else:candidate=doc['text']+args['text'] if action=='append' else args['text']
        if len(candidate.encode('utf8'))>1048576:raise ToolFailure('limit_exceeded','草案はUTF-8で1MiBまでです。')
        app.resources.validate(kind,candidate)
        if doc['exists'] and normalize(candidate)==normalize(doc['text']):
            return ToolOutput(status='no_change',message='元の本文と同じなので草案は作成していません。')
        return doc,candidate,dependencies


def propose_document(app, context, args):
    with app.store.lock:
        try:prepared=_prepare(app,context,args)
        except EditConflict:raise
        except ValueError as error:
            raise ToolFailure('invalid_arguments','引数または元ファイルの形式を確認してください。') from error
        if isinstance(prepared,ToolOutput):return prepared
        doc,candidate,dependencies=prepared
        context.checkpoint()
        kind,name=args['kind'],args['name']
        draft=app.resources.draft(context.room,kind,name,doc['revision'],'',candidate,dependencies)
    return ToolOutput(status='pending',data={'draft_id':draft['id'],'kind':kind,'name':name,
        'review':'記憶・知識 → AI更新案','index_update_required':kind in ('identity','diary','creative','analysis','entity','knowledge','episode','dream','archive')},
        message='確認待ちの更新案を保存しました。原本には未反映です。人がAI更新案で確認保存してください。')


ENTRIES=[ToolSpec(schema('propose_document',
    '記憶・ノート・世界設定の確認待ち更新案。kindと読取結果のrevision必須。action=replace/append/patch、entryは個別エントリ、archiveは保存済みアーカイブを確認して日記のold_textを除く。AIは承認できず、原本は人の保存まで変わらない。',
    {'kind':{'type':'string','maxLength':9,'enum':KINDS},
     'name':{'type':'string','maxLength':84,'default':''},
     'revision':{'type':'string','minLength':64,'maxLength':64},
     'action':{'type':'string','maxLength':7,'enum':['replace','append','patch','entry','archive'],'default':'replace'},
     'text':{'type':'string','maxLength':12000,'default':''},
     'old_text':{'type':'string','maxLength':6000,'default':''},
     'entry_id':{'type':'string','maxLength':6,'default':''},
     'field':{'type':'string','maxLength':13,'enum':['text','trigger_topic','insight','log_entry'],'default':'text'},
     'archive_name':{'type':'string','maxLength':80,'default':''},
     'archive_revision':{'type':'string','maxLength':64,'default':''}},['kind','revision']),
    propose_document,category='memory',operation='propose',effects=('draft',))]
