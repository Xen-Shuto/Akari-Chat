"""Seed activity purposes only for the initial bundled-character publication."""
import hashlib
import json
import re
import uuid
from .activity_details import normalize

PATH = 'data/ACTIVITY_PURPOSE.md'
FIELDS = {'価値観':'core_values', '長期的な関心':'stable_interests',
          '望む行動':'preferred_behaviors', '避けたい行動':'avoid_behaviors',
          '現在の関心':'active_interests', '探求中の問い':'open_questions'}
STATES = {'active':'active', '進行中':'active', '進行中（active）':'active',
          'paused':'paused', '一時停止':'paused', '一時停止（paused）':'paused',
          'completed':'completed', '完了':'completed', '完了（completed）':'completed',
          'archived':'archived', '保管':'archived', '保管（archived）':'archived'}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def parse(raw):
    if len(raw)>65536:raise ValueError('ACTIVITY_PURPOSE.mdは64KB以内で用意してください。')
    try:body=raw.decode('utf-8-sig')
    except UnicodeDecodeError:body=raw.decode('cp932')
    sections={};current=None
    known={'タイトル','本文','状態','次の一手'}|set(FIELDS)
    for line in body.splitlines():
        heading=re.fullmatch(r'##[ \t]+(.+?)[ \t]*',line)
        if heading:
            name=heading[1].strip();current=name if name in known else None
            if current:
                if current in sections:raise ValueError('ACTIVITY_PURPOSE.mdの見出しが重複しています: '+current)
                sections[current]=[]
        elif current:sections[current].append(line)
    if not {'タイトル','本文'}<=set(sections):raise ValueError('ACTIVITY_PURPOSE.mdに「## タイトル」と「## 本文」が必要です。')
    def text(name):return '\n'.join(sections.get(name,[])).strip()
    title=text('タイトル');body=text('本文');action=text('次の一手')
    if not title or len(title)>120 or len(body)>4000 or len(action)>600:
        raise ValueError('ACTIVITY_PURPOSE.mdのタイトル/本文/次の一手の長さを確認してください（120/4000/600文字）。')
    state=STATES.get(text('状態') or 'active')
    if state is None:raise ValueError('ACTIVITY_PURPOSE.mdの状態が不正です。')
    details={key:[re.sub(r'^[-*+][ \t]+','',line.strip()) for line in sections.get(label,[]) if line.strip()] for label,key in FIELDS.items()}
    return dict(title=title,body=body,state=state,next_action=action,details=normalize('purpose',details))


def schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS initial_activity_purposes(
        room TEXT PRIMARY KEY,request_id TEXT NOT NULL,source_hash TEXT NOT NULL,
        config_hash TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
        document_id TEXT,error TEXT NOT NULL DEFAULT '')""")


def prepare(store, stage, names):
    """Validate copied snapshots and durably bind pending seeds before publication."""
    seeds=[]
    for room in names:
        path=stage/room/PATH
        if path.is_file():
            with path.open('rb') as source:raw=source.read(65537)
            document=parse(raw)
            seeds.append((room,'initial-purpose:'+uuid.uuid4().hex,digest(raw),
                          digest((stage/room/'room_config.json').read_bytes()),json.dumps(document,ensure_ascii=False)))
    with store.connect() as db:
        schema(db)
        for seed in seeds:
            db.execute("INSERT OR REPLACE INTO initial_activity_purposes(room,request_id,source_hash,config_hash,payload,state) VALUES(?,?,?,?,?,'pending')",seed)


def apply(autonomy):
    """Recover only queued first-setup seeds; use the ordinary writer and receipt."""
    store=autonomy.store
    with store.lock,store.connect() as db:
        db.execute('BEGIN IMMEDIATE');schema(db)
        rows=db.execute("SELECT * FROM initial_activity_purposes WHERE state='pending' ORDER BY room").fetchall()
        for seed in rows:
            room=seed['room']
            try:root=store.room_path(room)
            except ValueError:continue
            file=root/PATH;config=root/'room_config.json'
            # Never apply a draft that the user replaced/edited during setup.
            valid=(file.is_file() and file.stat().st_size<=65536 and file.resolve().is_relative_to(root) and
                   config.is_file() and config.resolve().is_relative_to(root) and
                   digest(file.read_bytes())==seed['source_hash'] and digest(config.read_bytes())==seed['config_hash'])
            exists=db.execute("SELECT 1 FROM autonomy_documents WHERE room=? AND kind='purpose'",(room,)).fetchone()
            if not valid or exists:
                db.execute("UPDATE initial_activity_purposes SET state='skipped',error=? WHERE room=?",('初回資料が変更されたか、既存の活動目的があります。既存内容を保持しました。',room))
                continue
            document=json.loads(seed['payload'])
            result,_=autonomy.save(room,'purpose',request_id=seed['request_id'],allow_purpose=True,_db=db,**document)
            db.execute("UPDATE initial_activity_purposes SET state='complete',document_id=?,error='' WHERE room=?",(result['id'],room))
