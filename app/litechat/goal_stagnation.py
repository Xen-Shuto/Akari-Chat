"""Age of recorded progress, not a judgement about real-world goal activity."""
from datetime import datetime

WINDOW=7*86400


def progress_age(goal,clock):
    value=goal.get('progress_updated_at')
    result=dict(progress_updated_at=None,progress_age_seconds=None,progress_record_state='unknown')
    if not isinstance(value,str) or not value or len(value)>40:return result
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:return result
        age=max(0,clock-parsed.timestamp())
    except (ValueError,OverflowError,OSError):return result
    return dict(progress_updated_at=parsed.isoformat(),progress_age_seconds=round(age),progress_record_state='stale' if age>=WINDOW else 'recent')


def summarize(goals,clock,truncated=False):
    result=dict(total=0,stale=0,unknown=0,ratio=0.,penalty=0.,window_days=7,truncated=truncated)
    if truncated:return result
    goals=[goal for goal in goals if goal['kind']=='goal' and goal['state']=='active' and goal.get('details',{}).get('progress',0)<100]
    states=[progress_age(goal,clock)['progress_record_state'] for goal in goals]
    result.update(total=len(states),stale=states.count('stale'),unknown=states.count('unknown'))
    if states:
        ratio=result['stale']/len(states);result.update(ratio=ratio,penalty=min(.15,ratio*.15) if ratio>=.5 else 0.)
    return result
