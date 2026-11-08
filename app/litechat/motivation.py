"""Deterministic motivation gate; never guesses the user's feelings."""
import hashlib
import json
import math
import re
import time
import uuid
from .autonomy import encoded,now
from .file_editor import EditConflict
from .activity_focus import plan,choose
from .emotion_freshness import effect as emotion_effect
from .report_repetition import evaluate as report_repetition

DEFAULTS=dict(enabled=True,threshold=0.6,cooldown_seconds=1800,boredom_seconds=7200)
RELATED=dict(protective=.9,anxious=.8,sadness=.5,anger=.4,joy=.2,contentment=.1,neutral=.3)
AROUSAL=dict(joy=.8,anger=1.,sadness=.6,protective=.5,anxious=.5,contentment=.2,neutral=0.)
TAG=re.compile(r'<persona_emotion\s+category=["\']([a-z]+)["\']\s+intensity=["\'](0(?:\.\d+)?|1(?:\.0+)?)["\']\s*/>',re.I)
DRIVES=('boredom','curiosity','goal_achievement','relatedness')


class Motivation:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS motivation_settings(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS motivation_states(room TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS motivation_events(room TEXT,event_id TEXT,payload TEXT,PRIMARY KEY(room,event_id))')
            db.execute('CREATE TABLE IF NOT EXISTS motivation_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,PRIMARY KEY(room,request_id))')
            db.execute('CREATE TABLE IF NOT EXISTS motivation_versions(id INTEGER PRIMARY KEY,room TEXT,payload TEXT,created TEXT)')

    def _settings(self,db,room):
        row=db.execute('SELECT payload FROM motivation_settings WHERE room=?',(room,)).fetchone()
        return json.loads(row[0]) if row else dict(DEFAULTS,revision='unconfigured')

    def activate(self,db,room):
        """Start an unconfigured intrinsic clock only when activity is enabled."""
        if not self._settings(db,room)['enabled']:return
        state=self._state(db,room)
        if any(state.get(key) is not None for key in ('anchor','last_interaction','last_action')):return
        state.update(anchor=time.time(),revision=uuid.uuid4().hex)
        db.execute('INSERT OR REPLACE INTO motivation_states VALUES(?,?)',(room,encoded(state)))

    def _state(self,db,room):
        row=db.execute('SELECT payload FROM motivation_states WHERE room=?',(room,)).fetchone()
        state=json.loads(row[0]) if row else dict(last_interaction=None,last_action=None,anchor=None,emotion='neutral',intensity=0.,arousal=0.,revision='unconfigured')
        return {'satisfaction':{},'recent_drives':[],'recent_at':None,'emotion_at':None,**state}

    def evaluate(self,room,db=None,clock=None):
        if db is None:
            with self.store.connect() as connection:
                connection.execute('BEGIN')
                return self.evaluate(room,connection,clock)
        self.store.room_path(room,db=db)
        config=self._settings(db,room);state=self._state(db,room);clock=time.time() if clock is None else clock
        emotion=emotion_effect(state,clock)
        anchor=max(value for value in (state['last_interaction'],state['last_action'],state['anchor'],0) if value is not None)
        elapsed=max(0,clock-anchor) if anchor else 0
        rows=db.execute("SELECT kind,MAX(COALESCE(json_extract(payload,'$.details.priority'),50)/100.0*(CASE WHEN kind='goal' THEN 1-COALESCE(json_extract(payload,'$.details.progress'),0)/100.0 ELSE 1 END)) FROM autonomy_documents WHERE room=? AND kind IN ('question','goal') AND json_extract(payload,'$.state')='active' GROUP BY kind",(room,)).fetchall() if config['enabled'] else []
        levels=dict(boredom=min(1.,elapsed/config['boredom_seconds']),curiosity=0.,goal_achievement=0.,relatedness=RELATED.get(state['emotion'],0)*emotion['intensity'])
        for kind,value in rows:levels['curiosity' if kind=='question' else 'goal_achievement']=max(0.,min(1.,float(value or 0)))
        activity_plan=plan(db,room,clock) if config['enabled'] else None
        if activity_plan and not activity_plan['truncated']:
            levels['curiosity']=max(levels['curiosity'],activity_plan['question']['score'])
            levels['goal_achievement']=max(levels['goal_achievement'],activity_plan['goal']['score'])
        raw_levels=dict(levels);satisfaction={key:0. for key in DRIVES};penalties={key:0. for key in DRIVES}
        stagnation=activity_plan['goal_stagnation'] if activity_plan else None
        if stagnation:levels['goal_achievement']=max(0.,levels['goal_achievement']-stagnation['penalty'])
        if config['enabled']:
            for key,item in state['satisfaction'].items():
                if key in levels:
                    age=max(0,clock-item['at'])
                    satisfaction[key]=item['level']*math.exp(-age/43200) if age<259200 else 0.
                    levels[key]=max(0.,levels[key]-satisfaction[key])
            recent=state['recent_drives']
            if recent and state['recent_at'] is not None and max(0,clock-state['recent_at'])<43200:
                consecutive=0
                for key in recent:
                    if key!=recent[0]:break
                    consecutive+=1
                if consecutive>=2 and recent[0] in levels:
                    penalties[recent[0]]=min(.15,consecutive*.05);levels[recent[0]]=max(0.,levels[recent[0]]-penalties[recent[0]])
        comparison_after=max((v for v in (state['last_interaction'],state.get('reset_at')) if v is not None),default=None)
        reports=report_repetition(db,room,clock,config,activity_plan,comparison_after)
        additional=self.app.action_repetition.evaluate(db,room,clock,config,activity_plan,comparison_after)
        for key,penalty in reports['penalties'].items():levels[key]=max(0.,levels[key]-max(penalty,additional['penalties'][key]))
        dominant=max(levels,key=levels.get);cooldown=max(0,config['cooldown_seconds']-elapsed)
        ready=cooldown<=0 and levels[dominant]>=config['threshold']
        reason='従来の間隔による判定' if not config['enabled'] else 'クールダウン中' if cooldown>0 else '動機が閾値未満' if not ready else '動機が閾値に到達'
        focus=choose(activity_plan,dominant) if activity_plan else None
        return dict(settings=config,state=state,emotion_effect=emotion,goal_stagnation=stagnation,raw_levels=raw_levels,satisfaction=satisfaction,repetition_penalties=penalties,report_repetition=reports,additional_repetition=additional,levels=levels,dominant=dominant,activity_focus=focus,cooldown_remaining=round(cooldown),eligible=not config['enabled'] or ready,reason=reason)

    def view(self,room):
        with self.store.connect() as db:
            db.execute('BEGIN');result=self.evaluate(room,db)
            result['events']=[json.loads(row[0]) for row in db.execute('SELECT payload FROM motivation_events WHERE room=? ORDER BY rowid DESC LIMIT 30',(room,))]
        return result

    def save(self,room,data,revision,request_id,checkpoint=lambda:None):
        self.store.room_path(room)
        if not isinstance(data,dict) or set(data)!=set(DEFAULTS) or type(data['enabled']) is not bool:raise ValueError('内部状態の設定項目が不正です。')
        value=data['threshold']
        if type(value) not in (int,float) or not math.isfinite(value) or not .1<=value<=1:raise ValueError('閾値は0.1〜1です。')
        for key,low,high in [('cooldown_seconds',300,86400),('boredom_seconds',300,604800)]:
            if type(data[key]) is not int or not low<=data[key]<=high:raise ValueError('時間設定が範囲外です。')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=256:raise ValueError('操作IDが必要です。')
        signature=hashlib.sha256(encoded([data,revision]).encode()).hexdigest()
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job or self.app.conversation_waiters:raise ValueError('会話・生成処理の終了後に設定してください。')
            db.execute('BEGIN IMMEDIATE');receipt=db.execute('SELECT signature,result FROM motivation_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=signature:raise EditConflict('同じ操作IDに異なる設定があります。')
                return json.loads(receipt[1])
            old=self._settings(db,room)
            if old['revision']!=revision:raise EditConflict('内部状態の設定が変更されました。再読込してください。')
            checkpoint();result=dict(data,revision=uuid.uuid4().hex)
            db.execute('INSERT INTO motivation_versions(room,payload,created) VALUES(?,?,?)',(room,encoded(old),now()))
            db.execute('INSERT OR REPLACE INTO motivation_settings VALUES(?,?)',(room,encoded(result)))
            state=self._state(db,room)
            if data['enabled'] and (not old['enabled'] or state['anchor'] is None):
                state.update(anchor=time.time(),revision=uuid.uuid4().hex)
                db.execute('INSERT OR REPLACE INTO motivation_states VALUES(?,?)',(room,encoded(state)))
            db.execute('INSERT INTO motivation_receipts VALUES(?,?,?,?)',(room,request_id,signature,encoded(result)))
        return result

    def record(self,db,room,event_id,kind,body='',outcome=None,decision=None):
        config=self._settings(db,room)
        if not config['enabled']:return
        if db.execute('SELECT 1 FROM motivation_events WHERE room=? AND event_id=?',(room,event_id)).fetchone():return
        clock=time.time();before=self.evaluate(room,db,clock);state=before['state'];old_emotion=state['emotion'];old_intensity=before['emotion_effect']['intensity']
        emotion_source='タグなし・前回保持'
        if kind=='conversation':
            state['last_interaction']=clock
            # Only a final, explicit model self-report; not evidence of user emotion.
            matches=list(TAG.finditer(body[:100000]))
            if matches and matches[-1][1].lower() in RELATED:
                state.update(emotion=matches[-1][1].lower(),intensity=float(matches[-1][2]),emotion_at=clock);emotion_source='回答の明示タグ（自己申告）'
        else:state['last_action']=clock
        satisfied=None
        # A finite run completing is not proof that a goal was achieved. This
        # only moderates the drive that actually caused this scheduled run.
        if kind=='autonomy_finish' and outcome=='succeeded' and decision and decision.get('settings',{}).get('enabled'):
            drive=decision.get('dominant')
            if drive in DRIVES and decision['settings']['revision']==config['revision']:
                effective=before['satisfaction'][drive]
                state['satisfaction'][drive]=dict(level=min(1.,max(effective,.25)),at=time.time())
                recent=state['recent_drives'] if state['recent_at'] is not None and max(0,time.time()-state['recent_at'])<43200 else []
                state['recent_drives']=([drive]+recent)[:5];state['recent_at']=time.time();satisfied=drive
        effective=emotion_effect(state,clock)
        previous=AROUSAL.get(old_emotion,0)*old_intensity;current=AROUSAL.get(state['emotion'],0)*effective['intensity']
        delta=abs(current-previous);peak=max(current,previous)
        if peak>=.5:delta=max(delta,.3)
        related_delta=abs(RELATED.get(old_emotion,0)*old_intensity-RELATED.get(state['emotion'],0)*effective['intensity'])
        if kind=='conversation':state['arousal']=round(min(1.,2*(delta*.4+peak*.15+related_delta*.2)),3)
        state['revision']=uuid.uuid4().hex
        db.execute('INSERT OR REPLACE INTO motivation_states VALUES(?,?)',(room,encoded(state)))
        event=dict(id=event_id,kind=kind,created=now(),emotion=state['emotion'],intensity=state['intensity'],emotion_source=emotion_source,emotion_effect=effective,arousal=state['arousal'],prior_levels=before['levels'],outcome=outcome,satisfied_drive=satisfied)
        db.execute('INSERT INTO motivation_events VALUES(?,?,?)',(room,event_id,encoded(event)))
