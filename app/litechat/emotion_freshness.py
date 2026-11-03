"""Decay a model self-report's influence without rewriting its historical value."""
import math

HALF_LIFE=6*3600
MAX_AGE=72*3600


def effect(state,clock):
    timestamp=state.get('emotion_at')
    result=dict(intensity=0.,factor=0.,age_seconds=None,reported_at=None,status='unknown',half_life_seconds=HALF_LIFE)
    if type(timestamp) not in (int,float) or not math.isfinite(timestamp):return result
    raw=state.get('intensity',0.)
    if type(raw) not in (int,float) or not math.isfinite(raw) or not 0<=raw<=1:return result
    age=max(0.,clock-timestamp);factor=2**(-age/HALF_LIFE) if age<MAX_AGE else 0.
    return dict(result,intensity=raw*factor,factor=factor,age_seconds=round(age),reported_at=timestamp,status='expired' if age>=MAX_AGE else 'current')
