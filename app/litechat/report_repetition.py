"""Bounded textual report repetition; never proof of duplicate real-world work."""
import json
import hashlib
import math
import unicodedata
from datetime import datetime

DRIVES=('boredom','curiosity','goal_achievement','relatedness')


def normalized(text):
    return ''.join(unicodedata.normalize('NFKC',text[:8000]).casefold().split())


def similarity(first,second):
    first,second=normalized(first),normalized(second)
    if min(len(first),len(second))<80 or min(len(first),len(second))/max(len(first),len(second))<.9:return None
    a={first[i:i+3] for i in range(len(first)-2)}
    b={second[i:i+3] for i in range(len(second)-2)}
    return len(a&b)/len(a|b)


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError,TypeError,OverflowError):return None


def evaluate(db,room,clock,config,activity_plan,last_interaction,*,comparator=None,threshold=.9,require_tools=False):
    result=dict(penalties={key:0. for key in DRIVES},matches=[],scanned=0,method='normalized_character_trigram',threshold=.9,window_seconds=43200,conversation_boundary=None,evaluated_at=clock)
    if not config['enabled'] or not activity_plan or activity_plan['truncated']:return result
    user=db.execute("SELECT id,substr(content,1,100000),length(content) FROM messages WHERE room=? AND role='user' ORDER BY id DESC LIMIT 1",(room,)).fetchone()
    if user and user[2]>100000:return result
    boundary=hashlib.sha256(json.dumps(list(user) if user else [],ensure_ascii=False).encode()).hexdigest()
    result['conversation_boundary']=boundary
    rows=db.execute("SELECT payload FROM autonomy_runs WHERE room=? AND state='succeeded' AND json_extract(payload,'$.trigger')='scheduled' ORDER BY rowid DESC LIMIT 6",(room,)).fetchall()
    groups={key:[] for key in DRIVES}
    for row in rows:
        item=json.loads(row[0]);result['scanned']+=1
        decision=item.get('motivation') or {};settings=decision.get('settings') or {}
        focus=decision.get('activity_focus') or {};drive=decision.get('dominant')
        completed=timestamp(item.get('completed_at'));metrics=item.get('metrics') or {}
        if (drive not in groups or settings.get('revision')!=config['revision'] or not settings.get('enabled')
                or focus.get('snapshot')!=activity_plan['snapshot']
                or (decision.get('report_repetition') or {}).get('conversation_boundary')!=boundary):continue
        if (completed is None or completed>clock or clock-completed>=43200
                or last_interaction is not None and completed<=last_interaction
                or type(metrics.get('tool_calls')) is not int or (metrics['tool_calls']<1 if require_tools else metrics['tool_calls']!=0)):
            groups[drive].append(None)
            continue
        groups[drive].append((item,completed))
    for drive,items in groups.items():
        # Only consecutive matching reports in this comparable group contribute.
        # A different or short report breaks the sequence instead of cherry-picking.
        for index in range(min(2,len(items)-1)):
            if items[index] is None or items[index+1] is None:break
            newer,completed=items[index];older,_=items[index+1]
            score=comparator(newer,older) if comparator else similarity(newer.get('text',''),older.get('text',''))
            if score is None or score<threshold:break
            amount=.15*math.exp(-max(0,clock-completed)/21600)
            result['penalties'][drive]+=amount
            result['matches'].append(dict(drive=drive,newer_id=newer['id'],older_id=older['id'],score=score,penalty=amount))
        result['penalties'][drive]=min(.3,result['penalties'][drive])
    return result
