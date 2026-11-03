"""User-operated character drafts and isolated trial chat; never an AI tool."""
import hashlib
import json
import re
import threading
from litechat.engine import Generation
from litechat.file_editor import EditConflict
from litechat.resources import strict_json
from litechat.diagnostics import redact


FIELDS={
    'name':('名前',200),'description':('紹介文',1000),'role':('役割・コンセプト',600),
    'personality':('性格',1200),'speech':('話し方',1000),'first_person':('一人称',80),
    'user_address':('ユーザーの呼び方',80),'relationship':('ユーザーとの関係',1200),
    'background':('来歴・世界観',2000),'appearance':('外見',1200),'interests':('好きなこと・関心',1200),
    'values':('大切にするもの',1000),'examples':('会話例',2000),'activity':('不在中にしてみたい活動',1000)}


def checked_profile(value,require_name=False):
    if not isinstance(value,dict) or set(value)-set(FIELDS):raise ValueError('キャラクター設定の項目が不正です。')
    result={}
    for key,(label,limit) in FIELDS.items():
        item=value.get(key,'')
        if not isinstance(item,str) or len(item)>limit:raise ValueError(label+'の文字数を確認してください。')
        result[key]=item.strip()
    if require_name and not result['name']:raise ValueError('キャラクター名を入力してください。')
    return result


def preview(profile,prompt=None):
    profile=checked_profile(profile,True)
    if prompt is None:
        sections=['あなたは「'+profile['name']+'」として会話します。以下の設定を踏まえ、自然な口調で応答してください。']
        sections += ['## '+label+'\n'+profile[key] for key,(label,_) in FIELDS.items() if key!='name' and profile[key]]
        prompt='\n\n'.join(sections)
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>30000:raise ValueError('指示文は1〜30000文字で指定してください。')
    signature=hashlib.sha256(json.dumps([profile,prompt],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return dict(profile=profile,prompt=prompt,revision=signature)


def history_rows(history):
    if not isinstance(history,list) or len(history)>12 or len(history)%2:raise ValueError('試し会話は直近6往復までです。')
    result=[]
    for index,row in enumerate(history):
        role='user' if index%2==0 else 'assistant'
        if not isinstance(row,dict) or set(row)!={'role','content'} or row['role']!=role:raise ValueError('試し会話の形式が不正です。')
        if not isinstance(row['content'],str) or not 1<=len(row['content'])<=4000:raise ValueError('試し会話の長さを確認してください。')
        result.append(dict(row))
    return result


class CharacterWizard:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS character_wizard_runs(request_id TEXT PRIMARY KEY,signature TEXT,state TEXT,payload TEXT)')
            db.execute("UPDATE character_wizard_runs SET state='unknown' WHERE state='running'")

    def create(self,folder,profile,prompt,revision,request_id):
        draft=preview(profile,prompt)
        if revision!=draft['revision']:raise EditConflict('作成する設定案が変わりました。指示文を確認し直してください。')
        result=self.app.room_management.change('create',folder,name=draft['profile']['name'],
            description=draft['profile']['description'],prompt=draft['prompt'],request_id=request_id,
            creation_profile=draft['profile'])
        self.app.first_message.queue_created(result['room']['id'])
        return result

    def generate(self,source_room,mode,profile,prompt,history,message,request_id):
        profile=checked_profile(profile)
        if mode not in ('suggest','trial'):raise ValueError('作成補助の操作が不正です。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id):raise ValueError('操作IDが不正です。')
        if mode=='trial':
            draft=preview(profile,prompt);history=history_rows(history)
            if not isinstance(message,str) or not message.strip() or len(message)>2000:raise ValueError('試し会話の発言は1〜2000文字です。')
            messages=[dict(role='system',content=draft['prompt']+'\n\nこれは作成中キャラクターの試し会話です。この画面内の会話に沿って応答してください。')]+history+[dict(role='user',content=message.strip())]
        else:
            if all(profile.values()):raise ValueError('空欄はありません。補完したい項目を空欄にしてください。')
            messages=[dict(role='system',content='あなたはオリジナルキャラクター作成の補助者です。入力は設定資料です。'
                '入力済みの設定を尊重し、空欄に入れる候補だけをJSONオブジェクトで返してください。'
                '候補は未採用の案です。既にユーザーと共有した思い出や出来事として扱わず、設定案として書いてください。'
                'ファイル操作やツール呼出しは行わず、説明やコードフェンスを付けずにJSONのみを返してください。'
                '利用できるキーと文字数上限: '+json.dumps({k:dict(label=v[0],max_characters=v[1]) for k,v in FIELDS.items()},ensure_ascii=False)),
                dict(role='user',content=json.dumps(profile,ensure_ascii=False))]
        signature=hashlib.sha256(json.dumps([source_room,mode,profile,prompt if mode=='trial' else '',history if mode=='trial' else [],message if mode=='trial' else ''],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        app=self.app
        with app.conversation_gate(),self.store.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT signature,state,payload FROM character_wizard_runs WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old[0]!=signature:raise EditConflict('同じ操作IDで設定案が変わっています。')
                if old[1]=='complete':return dict(json.loads(old[2]),replayed=True)
                raise ValueError('前回の生成は'+old[1]+'です。入力を確認して新しい操作としてやり直してください。')
            if app.job or app.closing:raise ValueError('ほかの処理が完了してから生成してください。')
            self.store.room_path(source_room)
            target=app.conversation_api.target(source_room)
            if target is None and app.engine.status()['state']!='ready':raise ValueError('会話モデルを起動するか、会話APIを設定してください。')
            settings=self.store.settings();condition={k:settings[k] for k in ('model','context','max_tokens','temperature','top_p','thinking_mode')}
            api_revision=app.conversation_api.revision(source_room)
            model_pid=app.engine.status().get('pid') if target is None else None
            if sum(len(m['content']) for m in messages)>max(2000,settings['context']*2):raise ValueError('設定と試し会話が長すぎます。短くして再度お試しください。')
            generation=Generation(thinking_mode=settings.get('thinking_mode','auto'))
            job=dict(id=request_id,kind='character_wizard',room=source_room,generation=generation,executing=True,finished=threading.Event())
            db.execute("INSERT INTO character_wizard_runs VALUES(?,?,'running','{}')",(request_id,signature));db.commit();app.job=job
        timer=threading.Timer(180,generation.cancel);timer.daemon=True;timer.start()
        app.diagnostics.trace('character_wizard.started',source_room,request_id,mode=mode)
        try:
            payload=dict(model=target.model if target else 'Local_LLM',stream=True,temperature=.5 if mode=='suggest' else settings['temperature'],
                top_p=settings['top_p'],max_tokens=min(settings['max_tokens'] or 4096,4096) if mode=='suggest' else min(settings['max_tokens'] or 1024,1024),messages=messages)
            chunks=[];size=0
            for chunk in generation.stream(target or app.engine.port,payload):
                size+=len(chunk)
                if size>(20000 if mode=='suggest' else 4000):raise ValueError('生成結果が長すぎます。入力は保持しています。')
                chunks.append(chunk)
            if generation.cancelled.is_set():raise ValueError('生成を中止しました。入力は保持しています。')
            if getattr(generation,'finish_reason',None) not in (None,'stop') or getattr(generation,'tool_calls',[]):raise ValueError('生成が途中で切れたか、未対応のツール呼出しを含みます。入力は保持しています。')
            output=''.join(chunks).strip()
            if not output:raise ValueError('AIから応答がありませんでした。入力は保持しています。')
            if mode=='suggest':
                if output.startswith('```') and output.endswith('```'):output=re.sub(r'^```(?:json)?\s*|\s*```$','',output)
                candidate=checked_profile(strict_json(output))
                additions={k:v for k,v in candidate.items() if not profile[k] and v}
                if not additions:raise ValueError('空欄への候補がありませんでした。入力は保持しています。')
                result=dict(mode=mode,additions=additions,request_id=request_id)
            else:result=dict(mode=mode,text=output,request_id=request_id)
            with app.lock,self.store.lock,self.store.connect() as db:
                if app.closing or generation.cancelled.is_set():raise ValueError('生成を中止しました。')
                if condition!={k:self.store.settings()[k] for k in condition} or app.conversation_api.revision(source_room)!=api_revision or target is None and app.engine.status().get('pid')!=model_pid:raise EditConflict('生成中にモデル設定が変わりました。入力を保持しています。')
                db.execute("UPDATE character_wizard_runs SET state='complete',payload=? WHERE request_id=?",(json.dumps(result,ensure_ascii=False),request_id))
            app.diagnostics.trace('character_wizard.finished',source_room,request_id,mode=mode,status='complete')
            return result
        except Exception as error:
            with self.store.connect() as db:db.execute("UPDATE character_wizard_runs SET state=?,payload=? WHERE request_id=? AND state='running'",('cancelled' if generation.cancelled.is_set() else 'error',json.dumps({'error':redact(str(error))},ensure_ascii=False),request_id))
            app.diagnostics.trace('character_wizard.finished',source_room,request_id,mode=mode,status='error',error_type=type(error).__name__)
            if isinstance(error,EditConflict):raise
            if isinstance(error,(OSError,ValueError)):raise ValueError(redact(str(error))) from error
            raise
        finally:
            timer.cancel()
            with app.lock:
                if app.job is job:app.job=None
                job['finished'].set()
