"""Bounded structured activity data; links never select or rewrite a memory slot."""
from datetime import datetime, timezone

COMMON={'links','working_slots','priority'}
PURPOSE_FIELDS={'core_values','stable_interests','preferred_behaviors','avoid_behaviors','active_interests','open_questions'}
FIELDS={
    'purpose':PURPOSE_FIELDS,
    'goal':{'progress','due_at','success_criteria'},
    'question':{'resolution','resolution_state','progress_summary','progress_proposal'},
    'thread':{'findings','open_questions'},
    'procedure':{'preconditions','steps','success_criteria','source_timeline','source_revision'},
    'reflection':{'outcome','lesson'},
}


def normalize(kind, value):
    if not isinstance(value,dict) or set(value)-(COMMON|FIELDS[kind]):
        raise ValueError('記録の種類に対応しない詳細項目があります。')
    result={}
    for key,item in value.items():
        if key in ('priority','progress'):
            if type(item) is not int or not 0<=item<=100:raise ValueError('進捗・優先度は0～100の整数です。')
        elif key in ('links','working_slots','steps','open_questions') or key in PURPOSE_FIELDS:
            profile=kind=='purpose' and key in PURPOSE_FIELDS
            limit=5 if profile else 3 if key=='working_slots' else 10
            chars=200 if profile else {'links':32,'working_slots':80,'steps':300,'open_questions':300}[key]
            if not isinstance(item,list) or len(item)>limit or any(not isinstance(s,str) or not s.strip() or len(s)>chars for s in item):
                raise ValueError('関連先・項目の件数または長さが範囲外です。')
            item=[s.strip() for s in item] if key=='steps' else list(dict.fromkeys(s.strip() for s in item))
        else:
            if key=='resolution_state' and item not in ('open','partial','contradicted','resolved'):raise ValueError('問いの進展状態が不正です。')
            limit=32 if key in ('source_timeline','source_revision') else 40 if key=='due_at' else 1500 if key=='findings' else 1000
            if key=='progress_proposal':limit=32
            if not isinstance(item,str) or len(item)>limit:raise ValueError('詳細本文が長すぎます。')
            item=item.strip()
            if key=='due_at' and item:
                try:date=datetime.fromisoformat(item.replace('Z','+00:00'))
                except ValueError:raise ValueError('期限はタイムゾーン付き日時で指定してください。') from None
                if date.tzinfo is None:raise ValueError('期限のタイムゾーンが必要です。')
                item=date.astimezone(timezone.utc).isoformat()
        result[key]=item
    return result
