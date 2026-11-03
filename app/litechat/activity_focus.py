"""Bounded, explicit/lexical purpose alignment; no semantic inference or writes."""
import hashlib
import json
import unicodedata
import time
from datetime import datetime
from .autonomy import encoded
from .file_editor import EditConflict
from .goal_stagnation import progress_age,summarize


def folded(text):return unicodedata.normalize('NFKC',text).casefold().strip()


def source_rows(db,room):
    return db.execute("SELECT id,payload FROM autonomy_documents WHERE room=? AND kind IN ('purpose','goal','question') AND json_extract(payload,'$.state')='active' ORDER BY id LIMIT 1001",(room,)).fetchall()


def fingerprint(rows):return hashlib.sha256(encoded([tuple(row) for row in rows]).encode()).hexdigest()


def deadline(details,clock):
    """A capped heuristic, not permission to run or proof of task completion."""
    due=details.get('due_at','')
    result=dict(due_at=due,deadline_state='none',deadline_bonus=0.,deadline_seconds=None)
    if not due:return result
    if not isinstance(due,str) or len(due)>40:
        result.update(due_at='',deadline_state='invalid');return result
    try:
        date=datetime.fromisoformat(due.replace('Z','+00:00'))
        if date.tzinfo is None:raise ValueError('timezone required')
        seconds=date.timestamp()-clock
    except (AttributeError,TypeError,ValueError,OverflowError,OSError):
        result.update(deadline_state='invalid');return result
    window=7*86400
    urgency=max(0.,min(1.,1-seconds/window))
    remaining=1-details.get('progress',0)/100
    result.update(deadline_state='overdue' if seconds<0 else 'due' if seconds==0 else 'upcoming',deadline_seconds=round(seconds),deadline_bonus=.15*urgency*remaining)
    return result


def plan(db,room,clock=None):
    clock=time.time() if clock is None else clock
    rows=source_rows(db,room);snapshot=fingerprint(rows)
    result=dict(snapshot=snapshot,evaluated_at=clock,scanned=min(len(rows),1000),truncated=len(rows)>1000,goal=dict(score=0.,target=None,ties=0),question=dict(score=0.,target=None,ties=0))
    result['goal_stagnation']=summarize([],clock,truncated=result['truncated'])
    if result['truncated']:return result
    docs=[json.loads(row[1]) for row in rows];purposes=[doc for doc in docs if doc['kind']=='purpose']
    result['goal_stagnation']=summarize(docs,clock)
    purpose=purposes[0] if len(purposes)==1 else None
    for kind in ('goal','question'):
        candidates=[]
        for doc in docs:
            if doc['kind']!=kind:continue
            details=doc.get('details',{});progress=details.get('progress',0) if kind=='goal' else 0
            if progress>=100:continue
            score=details.get('priority',50)/100*(1-progress/100);matched=[]
            if purpose:
                profile=purpose.get('details',{})
                if purpose['id'] in details.get('links',[]) or doc['id'] in profile.get('links',[]):matched.append('目的との明示リンク')
                terms=profile.get('stable_interests',[])+profile.get('active_interests',[])+(profile.get('open_questions',[]) if kind=='question' else [])
                title=folded(doc['title'])
                for term in terms:
                    normalized=folded(term)
                    if len(title)>=2 and len(normalized)>=2 and (normalized in title or title in normalized):matched.append('目的の関心事項との文字一致');break
            timing=deadline(details,clock) if kind=='goal' else {}
            bonus=.1 if matched else 0.;score=min(1.,score+bonus+timing.get('deadline_bonus',0.))
            target={key:doc[key] for key in ('id','revision','kind','title')}
            target.update(body=doc['body'][:1200],body_truncated=len(doc['body'])>1200,next_action=doc.get('next_action',''),priority=details.get('priority',50),progress=progress,
                          alignment_bonus=bonus,alignment_reasons=matched,purpose_id=purpose['id'] if purpose else None,purpose_revision=purpose['revision'] if purpose else None)
            target.update(timing)
            if kind=='goal':target.update(progress_age(doc,clock))
            candidates.append((score,target))
        if candidates:
            best=max(score for score,_ in candidates);top=[target for score,target in candidates if abs(score-best)<1e-9]
            result[kind]=dict(score=best,target=top[0] if len(top)==1 else None,ties=len(top))
    return result


def choose(data,dominant):
    result={key:data[key] for key in ('snapshot','scanned','truncated','evaluated_at')};result['target']=None
    kind={'curiosity':'question','goal_achievement':'goal'}.get(dominant)
    if data['truncated']:result['reason']='活動候補が1000件を超えるため対象を自動選択しません。'
    elif kind is None:result['reason']='今回の動機は目標・未解決の問いではありません。'
    elif data[kind]['target']:
        result['target']=data[kind]['target'];result['reason']='優先度・進捗と目的への明示リンク/文字一致'+('、期限の近さ' if data[kind]['target'].get('deadline_bonus',0)>0 else '')+'から、この実行の対象を選びました。'
    elif data[kind]['ties']>1:result['reason']='同じ点数の候補が複数あるため、一つに決めません。'
    else:result['reason']='対応する未完了の活動候補がありません。'
    return result


def validate(store,room,focus):
    with store.connect() as db:current=fingerprint(source_rows(db,room))
    if current!=focus['snapshot']:raise EditConflict('目的・活動候補が開始判定後に変更されました。再実行してください。')
