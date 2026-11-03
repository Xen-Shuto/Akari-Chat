"""Room-scoped source/quote matching; never a factual truth assessment."""
import hashlib
import json
from litechat.autonomy import encoded
from litechat.file_editor import EditConflict


REFERENCE = {'type':'object','properties':{
    'kind':{'type':'string','maxLength':16,'enum':['conversation','autonomy_run']},
    'id':{'type':'string','minLength':1,'maxLength':32},
    'revision':{'type':'string','minLength':64,'maxLength':64},
    'quote':{'type':'string','minLength':1,'maxLength':1000}},
    'required':['kind','id','revision','quote'],'additionalProperties':False}
REFERENCES = {'type':'array','items':REFERENCE,'maxItems':3}


def normalize(references):
    if not isinstance(references,list) or len(references)>3:raise ValueError('根拠参照は3件までです。')
    result=[]
    for ref in references:
        if not isinstance(ref,dict) or set(ref)!={'kind','id','revision','quote'}:raise ValueError('根拠参照の項目が不正です。')
        if ref['kind'] not in ('conversation','autonomy_run'):raise ValueError('根拠参照の種類が不正です。')
        if not all(isinstance(ref[k],str) and 1<=len(ref[k])<=size for k,size in [('id',32),('revision',64),('quote',1000)]) or len(ref['revision'])!=64 or not ref['quote'].strip():raise ValueError('参照ID・revision・引用が必要です。')
        if ref in result:raise ValueError('同じ根拠参照が重複しています。')
        result.append(dict(ref))
    return result


def source(db,room,kind,key,before=None):
    if kind=='conversation':
        if not isinstance(key,str) or not key.isascii() or not key.isdecimal() or len(key)>18:raise ValueError('会話の発言IDが不正です。')
        if before is not None and int(key)>=before:raise ValueError('この生成の履歴参照境界より後の発言は参照できません。')
        row=db.execute("SELECT id,role,content,status,created FROM messages WHERE room=? AND id=? AND deleted=0 AND status='complete' AND role IN ('user','assistant')",(room,int(key))).fetchone()
        if row is None:raise ValueError('参照できる完了発言がありません。削除・未完了・他ルームは対象外です。')
        snapshot=dict(id=str(row[0]),role=row[1],text=row[2],status=row[3],created_at=row[4])
    elif kind=='autonomy_run':
        row=db.execute("SELECT payload FROM autonomy_runs WHERE room=? AND id=? AND state='succeeded'",(room,key)).fetchone()
        if row is None:raise ValueError('参照できる正常完了の自律実行がありません。')
        item=json.loads(row[0])
        if item.get('state')!='succeeded' or not item.get('text'):raise ValueError('参照できる正常完了の本文がありません。')
        snapshot=dict(id=key,text=item['text'],revision=item['revision'],state=item['state'],created_at=item.get('created_at'),completed_at=item.get('completed_at'))
    else:raise ValueError('根拠参照の種類が不正です。')
    if not snapshot['text']:raise ValueError('参照元の本文が空です。')
    revision=hashlib.sha256(encoded([room,kind,snapshot]).encode()).hexdigest()
    return dict(snapshot,kind=kind,revision=revision)


def check(db,room,references,strict=False,before=None):
    checks=[]
    for ref in references:
        try:
            current=source(db,room,ref['kind'],ref['id'],before)
            if current['revision']!=ref['revision']:raise EditConflict('根拠の参照元が変更されました。読み直して再提案してください。')
            position=current['text'].find(ref['quote'])
            if position<0:raise EditConflict('根拠の引用が参照元の本文と一致しません。')
            checks.append(dict(kind=ref['kind'],id=ref['id'],matched=True,role=current.get('role'),created_at=current.get('created_at'),
                               quote=ref['quote'],context=current['text'][max(0,position-120):position+len(ref['quote'])+120]))
        except (ValueError,EditConflict) as error:
            if strict:raise EditConflict(str(error)) from error
            checks.append(dict(kind=ref['kind'],id=ref['id'],matched=False,quote=ref['quote'],reason=str(error)))
    return checks
