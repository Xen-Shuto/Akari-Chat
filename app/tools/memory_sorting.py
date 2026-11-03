"""Deterministic legacy-heading split into additive, individually reviewed drafts."""
import hashlib
import json
import re

from litechat.file_editor import EditConflict, normalize


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True)


def sections(text):
    """Keep exact decoded blocks; only unambiguous second-level headings classify."""
    protected=set();fence=None;offset=0
    for line in text.splitlines(keepends=True):
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})(.*)',line)
        if fence:
            protected.add(offset)
            if marker and marker[1][0]==fence[0] and len(marker[1])>=fence[1] and not marker[2].strip():fence=None
        elif marker:
            protected.add(offset);fence=(marker[1][0],len(marker[1]))
        offset+=len(line)
    matches=[m for m in re.finditer(r'^##[ \t]+(.+?)[ \t]*\r?$',text,re.MULTILINE) if m.start() not in protected]
    result=[]
    if not matches:
        return [dict(kind='unclassified',title='見出しなし',text=text)] if text.strip() else []
    if text[:matches[0].start()].strip():
        result.append(dict(kind='unclassified',title='見出しより前の本文',text=text[:matches[0].start()]))
    for i,match in enumerate(matches):
        title=match[1].strip();header=title.casefold()
        identity=any(v in header for v in ('永続記憶','permanent','自己同一性','self identity'))
        diary=any(v in header for v in ('日記','diary','アーカイブ','archive'))
        kind='identity' if identity and not diary else 'diary' if diary and not identity else 'unclassified'
        result.append(dict(kind=kind,title=title,text=text[match.start():matches[i+1].start() if i+1<len(matches) else len(text)]))
    return result


class MemorySorting:
    def __init__(self,app):
        self.app,self.store=app,app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS memory_sort_receipts(room TEXT,request_id TEXT,signature TEXT,result TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(room,request_id))')

    def preview(self,room):
        room=self.store.room_path(room).resolve().name
        with self.store.lock:
            source=self.app.resources.read(room,'legacy_memory')
            if not source['exists'] or not source['text'].strip():
                raise ValueError('旧主記憶 memory/memory_main.txt がありません。または空です。')
            if len(source['text'])>200000:
                raise ValueError('旧主記憶が20万文字を超えています。分割してから確認してください。')
            blocks=sections(source['text'])
            if len(blocks)>200:
                raise ValueError('旧主記憶の見出しが200件を超えています。分割してから確認してください。')
            targets=[]
            for kind in ('identity','diary'):
                current=self.app.resources.read(room,kind)
                if not current['editable']:raise ValueError('追記先を編集できません。原本は変更していません。')
                text=current['text'];added=[];duplicates=0
                for block in blocks:
                    if block['kind']!=kind:continue
                    body=block['text'].strip()
                    if normalize(body) in normalize(text):
                        duplicates+=1;continue
                    text=text+'\n\n'+body+'\n' if text else block['text']
                    added.append(block['title'])
                if len(text)>1000000:raise ValueError('追記後の本文が保存上限を超えます。')
                targets.append(dict(kind=kind,revision=current['revision'],current=current['text'],text=text,
                    added=added,duplicates=duplicates,path=current['path']))
            if self.app.resources.read(room,'legacy_memory')['revision']!=source['revision']:
                raise EditConflict('読込中に旧主記憶が変更されました。')
            result=dict(room=room,source=source,targets=targets,
                unclassified=[b for b in blocks if b['kind']=='unclassified'],
                warning='見出しによる分類です。意味の判定はしません。元ファイルは残し、既存本文への追記案だけを作成します。未分類の本文は自動反映しません。')
            result['revision']=hashlib.sha256(encoded([source['revision'],[(t['kind'],t['revision']) for t in targets]]).encode()).hexdigest()
            return result

    def create(self,room,revision,request_id,checkpoint=lambda:None):
        room=self.store.room_path(room).resolve().name
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',request_id):
            raise ValueError('操作IDが必要です。')
        if not isinstance(revision,str) or not re.fullmatch(r'[0-9a-f]{64}',revision):
            raise ValueError('資料を確認してから作成してください。')
        with self.app.lock,self.store.lock,self.store.connect() as db:
            if self.app.closing or self.app.job:raise ValueError('生成・終了処理の完了を待ってください。')
            db.execute('BEGIN IMMEDIATE')
            receipt=db.execute('SELECT signature,result FROM memory_sort_receipts WHERE room=? AND request_id=?',(room,request_id)).fetchone()
            if receipt:
                if receipt[0]!=revision:raise EditConflict('同じ操作IDで別の資料を指定できません。')
                return dict(json.loads(receipt[1]),replayed=True)
            preview=self.preview(room)
            if preview['revision']!=revision:raise EditConflict('旧主記憶または追記先が変わりました。資料を確認し直してください。')
            dependency=encoded([dict(kind='legacy_memory',name='',revision=preview['source']['revision'])])
            drafts=[]
            checkpoint()
            for target in preview['targets']:
                if not target['added']:continue
                self.app.resources.validate(target['kind'],target['text'])
                old=db.execute("SELECT id FROM resource_drafts WHERE room=? AND kind=? AND name='' AND revision=? AND text=? AND dependencies=? AND status='pending' ORDER BY id LIMIT 1",(room,target['kind'],target['revision'],target['text'],dependency)).fetchone()
                key=old[0] if old else db.execute("INSERT INTO resource_drafts(room,kind,name,revision,source_hash,text,dependencies) VALUES(?,?,'',?,'',?,?)",(room,target['kind'],target['revision'],target['text'],dependency)).lastrowid
                drafts.append(dict(id=key,kind=target['kind'],reused=bool(old)))
            checkpoint()
            if self.preview(room)['revision']!=revision:raise EditConflict('案の確定前に資料が変更されました。原本は変更していません。')
            result=dict(room=room,drafts=drafts,unclassified=len(preview['unclassified']),revision=revision,replayed=False,
                message='確認待ち案を作成しました。原本はまだ変更していません。' if drafts else '追記する分類済みの本文がありません。未分類と重複を確認してください。')
            db.execute('INSERT INTO memory_sort_receipts(room,request_id,signature,result) VALUES(?,?,?,?)',(room,request_id,revision,encoded(result)))
            return result

    def history(self,room):
        room=self.store.room_path(room).resolve().name
        with self.store.connect() as db:
            rows=db.execute('SELECT request_id,result,created FROM memory_sort_receipts WHERE room=? ORDER BY rowid DESC LIMIT 20',(room,)).fetchall()
            results=[]
            for row in rows:
                item=json.loads(row[1]);item.update(request_id=row[0],created=row[2])
                for draft in item['drafts']:
                    status=db.execute('SELECT status FROM resource_drafts WHERE room=? AND id=?',(room,draft['id'])).fetchone()
                    draft['status']=status[0] if status else 'unavailable'
                results.append(item)
        return results
