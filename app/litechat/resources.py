"""Allowlisted memory documents and reviewed local generation."""
import hashlib
import json
import os
import re
from itertools import islice
from pathlib import Path
from .file_editor import safe_path, EditConflict
from .working_slots import slot_name, unique_object
from .world import world_relative

FIXED = {"scene": ("背景設定JSON", "cache/akari_scene.json"), "world": ("世界設定RAW", "spaces/world_settings.txt"), "location": ("現在地", "current_location.txt"), "temporary": ("一時的現在地JSON", "cache/temporary_location.json"), "notepad": ("短期メモ", "notes/notepad.md"), "diary": ("日記", "memory/memory_diary.txt"), "identity": ("永続記憶", "memory/memory_identity.txt"),
         "core": ("コアメモリ", "core_memory.txt"), "secret":("秘密日記","private/secret_diary.txt"),
         "creative": ("創作ノート", "notes/creative_notes.md"), "analysis": ("分析ノート", "notes/research_notes.md")}
FOLDERS = {"knowledge": ("知識資料", "knowledge", ".md"), "episode": ("エピソード", "memory/episodic", ".json"),
           "dream": ("夢日記", "memory/dreaming", ".json"),
           "entity": ("エンティティ記憶", "memory/entities", ".md"),
           "archive":("日記アーカイブ","memory",".txt")}

def strict_json(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError("有限のJSON値が必要です。")))

class Resources:
    def __init__(self, store, editor):
        self.store, self.editor = store, editor
        self.process_arousal = None
        self.entity_states = None
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS resource_drafts (id INTEGER PRIMARY KEY, room TEXT, kind TEXT, name TEXT, revision TEXT, source_hash TEXT, text TEXT, status TEXT DEFAULT 'pending', created TEXT DEFAULT CURRENT_TIMESTAMP)")
            if 'dependencies' not in {r[1] for r in db.execute('PRAGMA table_info(resource_drafts)')}:
                db.execute("ALTER TABLE resource_drafts ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]'")
            if 'message_revisions' not in {r[1] for r in db.execute('PRAGMA table_info(resource_drafts)')}:
                db.execute("ALTER TABLE resource_drafts ADD COLUMN message_revisions TEXT NOT NULL DEFAULT '[]'")

    def message_revisions(self, room, ids):
        if not ids:return []
        result=[]
        with self.store.connect() as db:
            for key in ids:
                row=db.execute("SELECT id,role,content,created,status FROM messages WHERE room=? AND id=? AND deleted=0 AND status='complete'",(room,key)).fetchone()
                if row is None:raise EditConflict('参照した会話が変更または削除されています。')
                result.append(dict(id=key,hash=hashlib.sha256(json.dumps(dict(row),ensure_ascii=False,sort_keys=True).encode()).hexdigest()))
        return result

    def check_messages(self, room, encoded):
        expected=strict_json(encoded)
        if not expected:return
        if expected != self.message_revisions(room,[m['id'] for m in expected]):
            raise EditConflict('参照した会話が変更されています。資料を確認し直してください。')

    def target(self, kind, name="", room=None):
        if kind=='legacy_memory':
            return '旧主記憶（参照専用）','memory/memory_main.txt'
        if kind in ('episode','dream') and name=='@legacy':
            return '旧記録','memory/episodic_memory.json' if kind=='episode' else 'memory/insights.json'
        if kind=='archive' and (not isinstance(name,str) or not re.fullmatch(r'memory_archived_[0-9]{1,6}',name)):
            raise ValueError('アーカイブ名はmemory_archived_001の形式で指定してください。')
        if kind == "world" and room is not None:
            return FIXED[kind][0], world_relative(self.store.room_path(room))
        if kind in FIXED:
            return FIXED[kind]
        if kind not in FOLDERS:
            raise ValueError("管理対象が不正です。")
        label, folder, suffix = FOLDERS[kind]
        if kind == "knowledge" and isinstance(name,str) and Path(name).suffix.lower() in (".txt",".md"):
            slot_name(Path(name).stem)
            if Path(name).name!=name:raise ValueError("ファイル名が不正です。")
            return label,folder+"/"+name
        return label, folder+"/"+slot_name(name)+suffix

    def read(self, room, kind, name=""):
        if kind=='episode_draft_source':
            room=self.store.room_path(room).resolve().name
            if not isinstance(name,str) or not re.fullmatch(r'[0-9a-f]{32}',name):raise ValueError('会話草案のIDが不正です。')
            with self.store.connect() as db:
                row=db.execute('SELECT * FROM episode_drafts WHERE room=? AND id=?',(room,name)).fetchone()
            if row is None:raise ValueError('会話草案が見つかりません。')
            source=strict_json(row['source'])
            material=dict(summary=row['summary'],review_date=row['day'],status=row['status'],
                conversation=[{k:m[k] for k in ('id','role','content','created')} for m in source['messages']])
            identity=dict(row)
            if row['status']=='approved':
                saved=self.editor.read_snapshot(room,'resource','保存済みエピソード',row['path'])
                records=strict_json(saved['text']) if saved['exists'] else None
                if not isinstance(records,list) or len(records)!=1 or not isinstance(records[0],dict) or any(records[0].get(k)!=v for k,v in dict(akari_draft_id=name,summary=row['summary'],date=row['day']).items()):
                    raise EditConflict('保存済みエピソードが草案の記録と一致しません。')
                identity['saved_revision']=saved['revision'];material['saved_path']=saved['path']
            raw=json.dumps(identity,ensure_ascii=False,sort_keys=True)
            return dict(room=room,key='resource',label='会話由来の草案（参照専用）',path='会話草案/'+name,
                text=json.dumps(material,ensure_ascii=False,indent=2),exists=True,editable=False,encoding='utf-8',
                bom=False,newline='LF',revision=hashlib.sha256(raw.encode()).hexdigest())
        label, path = self.target(kind, name, room)
        doc=self.editor.read_snapshot(room, "resource", label, path)
        if kind=='legacy_memory':doc.update(editable=False,edit_error='旧主記憶は参照専用です。')
        if kind=='entity' and self.entity_states:
            doc.update(self.entity_states.describe(room,name,doc))
            if doc.get('merge_pending') or doc['state']=='merged':doc.update(editable=False,edit_error='統合処理中または統合済みの退避元です。統合履歴を確認してください。')
        return doc

    def listing(self, room, kind):
        root = self.store.room_path(room)
        if kind in FIXED:
            return [dict(name="", path=self.target(kind, room=room)[1])]
        if kind not in FOLDERS:
            raise ValueError("管理対象が不正です。")
        _, folder, suffix = FOLDERS[kind]
        directory = safe_path(root, folder)
        entries=self._listed(root,kind,sorted(directory.glob("*" if kind=="knowledge" else "*"+suffix))[:500])
        return self.entity_states.annotate(room,entries) if kind=='entity' and self.entity_states else entries

    def inventory(self, room, kind):
        """Complete bounded catalog for AI paging; never silently omit candidates."""
        if kind not in FOLDERS:
            raise ValueError('一覧参照の対象ではありません。')
        root=self.store.room_path(room)
        directory=safe_path(root,FOLDERS[kind][1])
        candidates=[]
        if directory.exists():
            with os.scandir(directory) as stream:
                candidates=[directory/item.name for item in islice(stream,5001)]
        if len(candidates)>5000:
            raise ValueError('一覧対象フォルダの項目が5000件を超えています。')
        result=self._listed(root,kind,sorted(candidates))
        if kind in ('episode','dream'):
            result=[row for row in result if row['name']!='@legacy']
            relative='memory/episodic_memory.json' if kind=='episode' else 'memory/insights.json'
            if safe_path(root,relative).is_file():result.append({'name':'@legacy','path':relative})
        return self.entity_states.annotate(room,result) if kind=='entity' and self.entity_states else result

    def _listed(self, root, kind, paths):
        result=[]
        for path in paths:
            try:
                if kind=="knowledge" and path.suffix.lower() not in (".txt",".md"):continue
                if kind!='knowledge' and path.suffix.lower()!=FOLDERS[kind][2]:continue
                self.target(kind, path.name if kind=="knowledge" else path.stem)
                if safe_path(root, path.relative_to(root)).is_file():
                    result.append(dict(name=path.name if kind=="knowledge" else path.stem, path=path.relative_to(root).as_posix()))
            except ValueError:
                continue
        return result

    def validate(self, kind, text):
        if kind in ('legacy_memory','episode_draft_source'):raise ValueError('参照専用の資料は変更できません。')
        if kind == "scene":
            data=strict_json(text)
            if not isinstance(data,dict) or not isinstance(data.get('background',''),str):raise ValueError('background文字列が必要です。')
        if kind == "temporary":
            data=strict_json(text)
            if not isinstance(data,dict) or not isinstance(data.get('active'),bool) or not isinstance(data.get('current'),dict) or not isinstance(data['current'].get('scenery_text',''),str):
                raise ValueError('activeとcurrent.scenery_textを持つJSONが必要です。')
        if kind in ("episode", "dream"):
            records = strict_json(text)
            if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                raise ValueError("記録はオブジェクトのJSON配列にしてください。")
            from .episodes import episode_date
            from datetime import datetime
            for record in records:
                if kind == "episode":
                    episode_date(record.get("date"))
                    if isinstance(record.get('akari_compression'),dict) and record['akari_compression'].get('version')==1:
                        last=episode_date(record.get('period_end',record['date']))
                        if last<record['date']:raise ValueError('圧縮エピソードの終了日が開始日より前です。')
                    if not isinstance(record.get("summary"), str):
                        raise ValueError("summaryの本文が必要です。")
                else:
                    if not isinstance(record.get("strategy"), str):
                        raise ValueError("strategyの本文が必要です。")
                    datetime.fromisoformat(record.get("created_at", ""))
        return text

    def save(self, room, kind, name, text, revision):
        self.validate(kind, text)
        return self.editor.save_snapshot(room, "resource", text, revision, lambda: self.read(room, kind, name))

    def backups(self, room, kind, name, backup_id=None):
        _,relative=self.target(kind,name,room)
        if backup_id is None:
            return self.editor.backups(room,"resource",relative)
        return self.editor.backup(room,"resource",backup_id,relative)

    def source(self, room, kind, name):
        document = self.read(room, kind, name)
        if kind=='entity' and (document.get('merge_pending') or document.get('state')=='merged'):raise EditConflict('未完了の統合または統合済みの退避元です。統合履歴を確認してください。')
        parts = {"target":document["text"]}
        for key in ("core", "identity", "diary"):
            doc = self.read(room, key)
            parts[key] = doc["text"][-6000:]
        parts["conversation"] = [dict(id=m["id"], role=m["role"], content=m["content"][-1000:]) for m in self.store.history(room, 12)]
        raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
        if len(raw)>30000:
            raise ValueError("生成資料が長すぎます。先に対象を分割してください。")
        return document, raw, hashlib.sha256(raw.encode()).hexdigest()

    def draft(self, room, kind, name, revision, source_hash, text, dependencies=None):
        with self.store.connect() as db:
            key = db.execute("INSERT INTO resource_drafts(room,kind,name,revision,source_hash,text,dependencies) VALUES(?,?,?,?,?,?,?)",(room,kind,name,revision,source_hash,text,json.dumps(dependencies or [],ensure_ascii=False))).lastrowid
        return {"id":key,"text":text,"revision":revision,"kind":kind,"name":name}

    def drafts(self, room):
        self.store.room_path(room)
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM resource_drafts WHERE room=? AND status='pending' ORDER BY id DESC LIMIT 50",(room,))]

    def approve(self, room, key, text):
        with self.store.lock, self.store.connect() as db:
            row=db.execute("SELECT * FROM resource_drafts WHERE room=? AND id=? AND status='pending'",(room,key)).fetchone()
            if row is None:
                raise ValueError("確認待ちの草案がありません。")
            if row['source_hash']:
                _,_,current=self.source(room,row['kind'],row['name'])
                if current!=row['source_hash']:
                    raise EditConflict("生成資料が変わりました。草案を控え、資料を確認して生成し直してください。")
            dependencies=strict_json(row['dependencies'])
            def read():
                self.check_messages(room,row['message_revisions'])
                for dep in dependencies:
                    if self.read(room,dep['kind'],dep['name'])['revision']!=dep['revision']:
                        raise EditConflict('保存済みアーカイブ等の参照先が変わりました。草案を確認し直してください。')
                return self.read(room,row['kind'],row['name'])
            self.validate(row['kind'],text)
            saved=self.editor.save_snapshot(room,'resource',text,row['revision'],read,
                after_read=lambda:self.read(room,row['kind'],row['name']))
            if row['kind']=='episode' and self.process_arousal:
                for record in strict_json(text):self.process_arousal(db,room,record.get('akari_session_arousal'),'resource:'+str(key))
            db.execute("UPDATE resource_drafts SET status='approved',text=? WHERE id=?",(text,key))
            return saved

    def reviews(self, room):
        self.store.room_path(room)
        with self.store.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,kind,name,created,substr(text,1,100) AS preview FROM resource_drafts WHERE room=? AND status='pending' ORDER BY id DESC LIMIT 50",(room,))]

    def review(self, room, key):
        self.store.room_path(room)
        with self.store.connect() as db:
            row=db.execute("SELECT * FROM resource_drafts WHERE room=? AND id=? AND status='pending'",(room,key)).fetchone()
        if row is None:raise ValueError('確認待ちの草案がありません。')
        current=self.read(room,row['kind'],row['name'])
        dependencies=[{**dep,'document':self.read(room,dep['kind'],dep['name'])} for dep in strict_json(row['dependencies'])]
        conflict=current['revision']!=row['revision'] or current.get('merge_pending',False) or any(dep['document']['revision']!=dep['revision'] or dep['document'].get('merge_pending') for dep in dependencies)
        if row['source_hash']:
            try:
                conflict = conflict or self.source(room,row['kind'],row['name'])[2] != row['source_hash']
            except (ValueError,OSError):
                conflict = True
        try:
            self.check_messages(room,row['message_revisions'])
        except EditConflict:
            conflict = True
        return {**dict(row),'current':current,'conflict':conflict,'source_documents':dependencies}

    def discard(self, room, key):
        self.store.room_path(room)
        with self.store.connect() as db:
            if self.entity_states and self.entity_states.merges:
                pending=db.execute("SELECT 1 FROM entity_merges WHERE room=? AND draft_id=? AND phase IN ('prepared','undo_prepared','conflict')",(room,key)).fetchone()
                if pending:raise EditConflict('未完了の統合を先に復旧してください。')
            changed=db.execute("UPDATE resource_drafts SET status='discarded' WHERE room=? AND id=? AND status='pending'",(room,key)).rowcount
        if not changed:raise EditConflict('確認待ちの草案ではありません。')
        return {'id':key,'status':'discarded'}
