"""Plaintext opening conversation template, independent of the live SQLite log."""
import hashlib
import re
from .file_editor import MAX_BYTES,FileEditor,decode,safe_path

RELATIVE='data/Init_Msg.txt'
HEADER=re.compile(r'^## (USER|AGENT|SYSTEM):(.+)$')


def rows(text):
    if not text.strip():return []
    result=[];role=None;lines=[];has_header=False
    for line in text.splitlines(keepends=True):
        match=HEADER.match(line.rstrip('\r\n'))
        if match:
            has_header=True
            if role is None and ''.join(lines).strip():raise ValueError('初期ログの最初の発言ヘッダーより前に本文があります。')
            if role and ''.join(lines).strip():result.append((role,''.join(lines).strip()))
            role={'USER':'user','AGENT':'assistant','SYSTEM':'system'}[match[1]];lines=[]
        else:lines.append(line)
    if not has_header:return [('assistant',text.strip())]
    if role and ''.join(lines).strip():result.append((role,''.join(lines).strip()))
    if not result:raise ValueError('初期ログに発言本文がありません。')
    if len(result)>200:raise ValueError('初期ログの発言は200件までです。')
    return result


def render(text,room):
    parsed=rows(text)
    if not parsed:return ''
    return text if any(HEADER.match(line) for line in text.splitlines()) else '## AGENT:'+room+'\n'+text.strip()+'\n'


def preview(text):
    if not isinstance(text,str) or len(text.encode('utf8'))>MAX_BYTES:raise ValueError('初期ログのプレビューは1MiBまでの文字列で指定してください。')
    return dict(messages=[dict(role=role,content=content) for role,content in rows(text)])


def snapshot(root,legacy=''):
    path=safe_path(root,RELATIVE)
    exists=path.exists()
    if exists and not path.is_file():raise ValueError('初期ログがファイルではありません。')
    if exists and path.stat().st_size>MAX_BYTES:raise ValueError('初期ログが1MiBを超えています。')
    raw=FileEditor.read_bytes(path) if exists else b''
    if len(raw)>MAX_BYTES:raise ValueError('初期ログが1MiBを超えています。')
    text=decode(raw)['text'] if exists else legacy
    if not isinstance(text,str):raise ValueError('初期ログの本文が文字列ではありません。')
    parsed=rows(text)
    # Keep the simple single-message form convenient; multi-message templates
    # retain their headers so the UI cannot flatten their conversation roles.
    editable=parsed[0][1] if len(parsed)==1 and parsed[0][0]=='assistant' else text
    revision=hashlib.sha256(RELATIVE.encode()+bytes([exists])+raw).hexdigest()
    return dict(exists=exists,raw=raw,text=text,value=editable,rows=parsed,revision=revision,path=RELATIVE)


def import_once(db,room,root):
    if db.execute('SELECT 1 FROM initial_log_imports WHERE room=?',(room,)).fetchone():return
    # An unused or malformed template must not block an established conversation.
    if safe_path(root,RELATIVE).exists() and db.execute('SELECT 1 FROM messages WHERE room=? LIMIT 1',(room,)).fetchone():
        db.execute("INSERT INTO initial_log_imports(room,state,source_hash) VALUES(?,'skipped','')",(room,));return
    doc=snapshot(root)
    if not doc['exists'] or not doc['rows']:return
    state='skipped';first=None;last=None
    if not db.execute('SELECT 1 FROM messages WHERE room=? LIMIT 1',(room,)).fetchone():
        for role,content in doc['rows']:
            key=db.execute("INSERT INTO messages(room,role,content,origin) VALUES(?,?,?,'opening')",(room,role,content)).lastrowid
            if first is None:first=key
            last=key
        state='complete'
    db.execute('INSERT INTO initial_log_imports(room,state,first_id,last_id,source_hash) VALUES(?,?,?,?,?)',
               (room,state,first,last,hashlib.sha256(doc['raw']).hexdigest()))
