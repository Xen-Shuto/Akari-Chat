"""Explicit entity consolidation proposals; never delete or retire source files."""
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from litechat.file_editor import EditConflict, normalize
from .contracts import ToolSpec, ToolOutput, ToolFailure
from .schema import schema


def normalized_name(name):
    value=unicodedata.normalize('NFKC',name).casefold()
    value=re.sub(r'(さん|ちゃん|くん|様|さま)$','',value)
    return ''.join(c for c in value if c.isalnum())


class EntityReviews:
    def __init__(self, resources):
        self.resources,self.store=resources,resources.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entity_review_requests(room TEXT, request_id TEXT, fingerprint TEXT, draft_id INTEGER, PRIMARY KEY(room,request_id))')

    def document(self, room, name):
        doc=self.resources.read(room,'entity',name)
        if not doc['exists'] or not doc['editable']:raise ValueError('既存の編集可能なエンティティを選択してください。')
        if len(doc['text'])>32000:raise ValueError('整理対象は各32000文字までです。先に分割してください。')
        return doc

    def candidates(self, room, name, checkpoint=lambda:None):
        checkpoint()
        self.document(room,name)
        base=normalized_name(name)
        if not base:raise ValueError('名前を比較できません。別の対象を選択してください。')
        rows=[]
        entries=self.resources.inventory(room,'entity')
        dormant=sum(entry.get('state')=='dormant' for entry in entries)
        blocked=sum(entry.get('state')!='dormant' and (entry.get('state')=='merged' or bool(entry.get('merge_pending'))) for entry in entries)
        for entry in entries:
            checkpoint()
            if entry['name'].casefold()==name.casefold() or entry.get('state')!='active' or entry.get('merge_pending'):continue
            other=normalized_name(entry['name'])
            score=SequenceMatcher(None,base,other,autojunk=False).ratio()
            if score>=.6:
                rows.append({'name':entry['name'],'score':round(score,3),'reason':'表記の正規化後に一致' if base==other else '名前の文字列が近い'})
        rows.sort(key=lambda row:(-row['score'],row['name']))
        return {'name':name,'basis':'name_only','scanned':len(entries)-dormant-blocked,'skipped_dormant':dormant,'skipped_merging':blocked,'total':len(rows),'rows':rows[:20],
                'overflow':max(0,len(rows)-20),'warning':'名前だけの候補です。同一人物・同一概念という判定ではありません。本文と関係を確認してください。'}

    def preview(self, room, target, source):
        if isinstance(target,str) and isinstance(source,str) and target.casefold()==source.casefold():raise ValueError('異なる2件を選択してください。')
        return {'target':self.document(room,target),'source':self.document(room,source)}

    def propose(self, room, target, source, revision, source_revision, text, request_id, checkpoint=lambda:None, *, _duplicate=False):
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',request_id):raise ValueError('要求IDが不正です。')
        if not isinstance(text,str) or not text.strip() or len(text)>64000:raise ValueError('整理案の本文は1～64000文字にしてください。')
        fingerprint=hashlib.sha256(json.dumps([target,source,revision,source_revision,text]+(['exact_duplicate'] if _duplicate else []),ensure_ascii=False).encode()).hexdigest()
        with self.store.lock,self.store.connect() as db:
            checkpoint()
            self.store.room_path(room)
            previous=db.execute('SELECT * FROM entity_review_requests WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise EditConflict('同じ要求IDに異なる整理案が指定されました。')
                state=db.execute('SELECT status FROM resource_drafts WHERE room=? AND id=?',(room,previous['draft_id'])).fetchone()
                return {'draft_id':previous['draft_id'],'replayed':True,'draft_status':state['status'] if state else 'unavailable'}
            docs=self.preview(room,target,source)
            if docs['target']['revision']!=revision or docs['source']['revision']!=source_revision:
                raise EditConflict('対象または参照元が変わりました。両方を読み直してください。')
            if _duplicate:
                if not normalized_name(target) or normalized_name(target)!=normalized_name(source) or normalize(text)!=normalize(docs['target']['text']) or normalize(text)!=normalize(docs['source']['text']):raise ValueError('名前と本文が一致する重複だけを指定してください。')
            elif normalize(text)==normalize(docs['target']['text']):raise ValueError('整理案が現在の本文と同じです。')
            checkpoint()
            dependencies=json.dumps([{'kind':'entity','name':source,'revision':source_revision}],ensure_ascii=False)
            key=db.execute("INSERT INTO resource_drafts(room,kind,name,revision,source_hash,text,dependencies) VALUES(?,'entity',?,?,'',?,?)",(room,target,revision,text,dependencies)).lastrowid
            db.execute('INSERT INTO entity_review_requests VALUES(?,?,?,?)',(room,request_id,fingerprint,key))
            return {'draft_id':key,'replayed':False,'draft_status':'pending'}


def candidates(app, context, args):
    try:data=(app.entity_semantic if args['method']=='semantic' else app.entity_reviews).candidates(context.room,args['name'],context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    return ToolOutput(data=data,message=data['warning'],truncated=bool(data['overflow']))


def propose(app, context, args):
    request=hashlib.sha256((context.job_id+'\0'+context.call_id).encode()).hexdigest()
    try:data=app.entity_reviews.propose(context.room,args['target'],args['source'],args['revision'],args['source_revision'],args['text'],request,context.checkpoint)
    except EditConflict:raise
    except ValueError as error:raise ToolFailure('invalid_arguments',str(error)) from error
    pending=data['draft_status']=='pending'
    return ToolOutput(status='pending' if pending else 'no_change',data={**data,'review':'記憶・知識 → AI更新案','source_retained':True,'index_update_required':True},
                      message='確認待ちの本文整理案です。通常の本文保存では参照元は残ります。人が「統合して元側を退避」を選んだ場合だけ退避します。AIは実行できません。' if pending else 'この要求はすでに確認済み、または参照できません。draft_statusを確認してください。新規の草案は作成していません。')


ENTRIES=[ToolSpec(schema('find_entity_candidates','最大20件の整理候補。method=nameは名前比較、semanticは現在の索引で本文の類似度を比較（索引が必要）。同一性判定ではない。両方をread_memory_documentで読み、人の確認用にpropose_entity_consolidationを使う。',
    {'name':{'type':'string','minLength':1,'maxLength':80},'method':{'type':'string','maxLength':8,'enum':['name','semantic'],'default':'name'}},['name']),candidates,category='memory',operation='read'),
    ToolSpec(schema('propose_entity_consolidation','既存2件を参照した本文整理案を保存。通常の本文保存ではtargetだけを更新しsourceを残す。人が専用の統合操作を選んだ時だけ原本を保全して元側を退避する。AIは統合/取消を実行できない。両方を読みrevision/source_revisionを指定し、別人や本体と側面を同一視せず矛盾や出典を残す。',
    {'target':{'type':'string','minLength':1,'maxLength':80},'source':{'type':'string','minLength':1,'maxLength':80},
     'revision':{'type':'string','minLength':64,'maxLength':64},'source_revision':{'type':'string','minLength':64,'maxLength':64},
     'text':{'type':'string','minLength':1,'maxLength':12000}},['target','source','revision','source_revision','text']),propose,category='memory',operation='propose',effects=('draft',))]
