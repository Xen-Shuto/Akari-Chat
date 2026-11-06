import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import hashlib
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlencode
from .memory_context import read_sources
from . import settings_io
from .engine import validate_settings


HEADER = re.compile(r"^## (USER|AGENT|SYSTEM):(.+)$")


def interrupted_text(content, status):
    marker = '（中断されました。）'
    if status != 'cancelled' or content.rstrip().endswith(marker):
        return content
    return content.rstrip() + ('\n' if content.strip() else '') + marker


def text_file(path):
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def read_json(path, default):
    return json.loads(text_file(path)) if path.is_file() else default


def parse_log(path):
    # Iterate by message, rather than loading every monthly log into memory.
    role, lines = None, []
    with path.open(encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            match = HEADER.match(line.rstrip("\r\n"))
            if match:
                if role and "".join(lines).strip():
                    yield role, "".join(lines).strip()
                role = {"USER": "user", "AGENT": "assistant", "SYSTEM": "system"}[match[1]]
                lines = []
            elif role:
                lines.append(line)
        if role and "".join(lines).strip():
            yield role, "".join(lines).strip()


def reject_links(root):
    # Junctions can escape a Windows character directory just like symlinks.
    for parent, dirs, files in os.walk(root, followlinks=False):
        for path in [Path(parent)] + [Path(parent) / name for name in dirs + files]:
            attrs = getattr(path.lstat(), "st_file_attributes", 0)
            if path.is_symlink() or attrs & 0x400:
                raise ValueError("リンクやジャンクションを含むフォルダは取り込めません。")


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.characters = self.root.parent / "characters"
        self.characters.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.episode_context_filter = None
        self.settings_warning = ''
        self.db = self.root / "chat.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS room_states (room TEXT PRIMARY KEY, archived INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, room TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'complete',
                    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS room_messages ON messages(room, id);
                CREATE TABLE IF NOT EXISTS indexed_rooms (room TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS initial_log_imports(room TEXT PRIMARY KEY,state TEXT NOT NULL,first_id INTEGER,last_id INTEGER,source_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, message_id INTEGER NOT NULL REFERENCES messages(id),
                    name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                    width INTEGER NOT NULL, height INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS message_images ON images(message_id);
                CREATE INDEX IF NOT EXISTS room_attachments ON images(room);
                CREATE TABLE IF NOT EXISTS attachment_states (
                    image_id TEXT PRIMARY KEY REFERENCES images(id),
                    hidden INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_attachment_links (
                    message_id INTEGER REFERENCES messages(id),image_id TEXT REFERENCES images(id),
                    PRIMARY KEY(message_id,image_id)
                );
                CREATE TABLE IF NOT EXISTS message_generated_images (
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    artifact_id TEXT NOT NULL, room TEXT NOT NULL,
                    PRIMARY KEY(message_id,artifact_id)
                );
            """)
        with self.connect() as db:
            if 'deleted' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            if 'model' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN model TEXT NOT NULL DEFAULT ''")
            if 'origin' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute("ALTER TABLE messages ADD COLUMN origin TEXT NOT NULL DEFAULT 'conversation'")
            if 'autonomy_run_id' not in {r['name'] for r in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN autonomy_run_id TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS autonomy_message_run ON messages(autonomy_run_id)')
        from .lifestyle import Lifestyle
        self.lifestyle = Lifestyle(self)
        from .character_bootstrap import copy_initial_characters
        from .initial_purpose import prepare
        self.character_bootstrap = uuid.uuid4().hex if copy_initial_characters(self.root, self.characters, reject_links,lambda stage,names:prepare(self,stage,names)) else ''
        if not any(path.is_dir() for path in self.characters.iterdir()):
            sample = self.characters / "Assistant"
            sample.mkdir()
            (sample / "room_config.json").write_text(
                json.dumps({"agent_display_name": "アシスタント"}, ensure_ascii=False), encoding="utf-8")
            (sample / "SystemPrompt.txt").write_text(
                "あなたは親しみやすい会話相手です。日本語で自然に話してください。"
                "与えられたキャラクター設定や記憶を尊重し、知らないことは作り話で補わないでください。",
                encoding="utf-8")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def room_path(self, room, *, include_archived=False, db=None):
        if not isinstance(room, str) or not room or room.startswith(".") or Path(room).name != room or "/" in room or "\\" in room:
            raise ValueError("キャラクター名が不正です。")
        path = self.characters / room
        if path.resolve().parent != self.characters or not path.is_dir():
            raise ValueError("キャラクターが見つかりません。")
        if not include_archived:
            with (self.connect() if db is None else nullcontext(db)) as connection:
                if any(row[0].casefold() == room.casefold() for row in connection.execute("SELECT room FROM room_states WHERE archived=1")):
                    raise ValueError("このルームは保管中です。ルーム管理から復元してください。")
        return path

    def room(self, room):
        path = self.room_path(room)
        config = read_json(path / "room_config.json", {})
        if not isinstance(config, dict):
            raise ValueError("room_config.jsonはオブジェクト形式である必要があります。")
        # Keep archive validity and the selected file snapshot under the same write lock.
        with self.lock:
            excluded, warnings = self.episode_context_filter(room) if self.episode_context_filter else ((), ())
            sources, memory_settings = read_sources(path, config,
                episode_excluded_paths=excluded, episode_warnings=warnings)
        sources["lifestyle"] = self.lifestyle.context(room)
        retrieval_settings = {}
        for name in ("enable_auto_retrieval", "include_knowledge_in_auto_retrieval"):
            value = config.get("override_settings", {}).get(name, False)
            if not isinstance(value, bool):
                raise ValueError(f"{name}にはtrue/falseを指定してください。")
            retrieval_settings[name] = value
        return {
            "id": room, "name": config.get("agent_display_name") or config.get("room_name") or room,
            "description": config.get("description") if isinstance(config.get("description"), str) else "",
            "prompt": sources["prompt"]["text"], "memory": sources["core"]["text"],
            "memory_sources": sources, "memory_settings": memory_settings,
            "retrieval_settings": retrieval_settings,
            "avatar": (path / "profile.png").is_file(),
            "compatibility": "記憶を設定に沿って送信。ローカルのキーワード検索・意味検索に対応。意味検索には専用モデルと索引が必要です。対応モデルは必要に応じてローカルツールを使用します。短期メモの更新案は確認・保存するまで反映されません。",
        }

    def rooms(self, *, include_archived=False):
        result = []
        for path in sorted(self.characters.iterdir()):
            if path.is_dir() and not path.name.startswith(".") and not path.is_symlink():
                try:
                    self.room_path(path.name, include_archived=include_archived)
                    if not (path / "room_config.json").is_file():
                        continue
                    config = read_json(path / "room_config.json", {})
                    result.append({"id": path.name,
                                   "name": config.get("agent_display_name") or config.get("room_name") or path.name})
                except (ValueError, OSError, AttributeError):
                    continue
        return result

    def index_room(self, room):
        path = self.room_path(room)
        with self.lock, self.connect() as db:
            from .initial_log import import_once,RELATIVE
            import_once(db,room,path)
            if db.execute("SELECT 1 FROM indexed_rooms WHERE room=?", (room,)).fetchone():
                return
            # NexusArk uses monthly logs; legacy log.txt is used only if none exist.
            logs = sorted((path / "logs").glob("????-??.txt"))
            if (path/RELATIVE).is_file():logs=[log for log in logs if log.name!='0000-00.txt']
            if not logs and (path / "log.txt").is_file():
                logs = [path / "log.txt"]
            for log in logs:
                db.executemany("INSERT INTO messages(room,role,content) VALUES(?,?,?)",
                               ((room, role, content) for role, content in parse_log(log)))
            db.execute("INSERT INTO indexed_rooms VALUES(?)", (room,))

    def history(self, room, limit=80, before=None):
        self.index_room(room)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE room=? AND deleted=0 AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
                              (room, before, before, limit)).fetchall()
            images={}
            if rows:
                marks=",".join("?" for _ in rows)
                for image in db.execute(f"SELECT * FROM images WHERE message_id IN ({marks}) AND NOT EXISTS (SELECT 1 FROM attachment_states s WHERE s.image_id=images.id AND s.hidden=1) ORDER BY rowid",[row["id"] for row in rows]):
                    item=dict(image)
                    item["url"]="/api/image?"+urlencode({"room":room,"id":item["id"]})
                    images.setdefault(item["message_id"],[]).append(item)
                for image in db.execute(f'''SELECT i.id,i.room,l.message_id,i.name,i.mime,i.size,i.width,i.height FROM images i
                    JOIN message_attachment_links l ON l.image_id=i.id WHERE i.room=? AND l.message_id IN ({marks})
                    AND NOT EXISTS (SELECT 1 FROM attachment_states s WHERE s.image_id=i.id AND s.hidden=1) ORDER BY l.rowid''',[room]+[row['id'] for row in rows]):
                    item=dict(image);item['url']='/api/image?'+urlencode({'room':room,'id':item['id']});item['linked']=True
                    images.setdefault(item['message_id'],[]).append(item)
                generated=db.execute(f'SELECT message_id,artifact_id FROM message_generated_images WHERE room=? AND message_id IN ({marks}) ORDER BY rowid',[room]+[row['id'] for row in rows]).fetchall()
                for link in generated:
                    artifact=db.execute('SELECT payload FROM image_artifacts WHERE room=? AND id=?',(room,link['artifact_id'])).fetchone()
                    if artifact:
                        doc=json.loads(artifact[0])
                        images.setdefault(link['message_id'],[]).append(dict(id=link['artifact_id'],room=room,name=Path(doc['path']).name,mime=doc['mime'],width=doc['width'],height=doc['height'],generated=True,url='/api/generated-image?'+urlencode(dict(room=room,id=link['artifact_id']))))
            from tools.discord_labels import present
            return present(db,room,[dict(row,images=images.get(row["id"],[])) for row in reversed(rows)])

    def retrieval_rows(self, room):
        """Stream existing visible rows newest-first without a silent 500-row cutoff."""
        self.room_path(room)
        with self.connect() as db:
            yield from db.execute("SELECT id,role,content,created FROM messages WHERE room=? AND deleted=0 "
                                  "AND role IN ('user','assistant') AND status!='no_response' ORDER BY id DESC",(room,))

    def append(self, room, role, content, status="complete", images=None,image_links=None,model="",generated_ids=None):
        if role == 'assistant':content = interrupted_text(content, status)
        self.index_room(room)
        created=[]
        try:
            with self.lock, self.connect() as db:
                message_id=db.execute("INSERT INTO messages(room,role,content,status,model) VALUES(?,?,?,?,?)",
                           (room, role, content, status,model)).lastrowid
                for image in images or []:
                    image_id=uuid.uuid4().hex
                    target=self.image_path(room,image_id,image["mime"])
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open("xb") as stream:
                        created.append(target)
                        stream.write(image["raw"])
                    db.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?)",
                               (image_id,room,message_id,image["name"],image["mime"],image["size"],image["width"],image["height"]))
                links=image_links or []
                if len(links)+len(images or [])>4:raise ValueError('参照を含む添付画像は4枚までです。')
                for link in links:
                    source=db.execute('SELECT i.id,m.deleted FROM images i JOIN messages m ON m.id=i.message_id WHERE i.room=? AND i.id=?',(room,link['source_id'])).fetchone()
                    if not source or source['deleted']:raise ValueError('継続添付の元の発言が参照できません。')
                    image=self.read_image(room,link['source_id'])
                    if hashlib.sha256(image['raw']).hexdigest()!=link['sha256']:raise ValueError('保存直前に継続添付が変わりました。')
                    db.execute('INSERT INTO message_attachment_links VALUES(?,?)',(message_id,link['source_id']))
                for artifact_id in dict.fromkeys(generated_ids or []):
                    if not db.execute('SELECT 1 FROM image_artifacts WHERE room=? AND id=?',(room,artifact_id)).fetchone():raise ValueError('このルームの生成画像が見つかりません。')
                    db.execute('INSERT INTO message_generated_images VALUES(?,?,?)',(message_id,artifact_id,room))
            return message_id
        except Exception:
            for target in created:
                target.unlink(missing_ok=True)
            raise

    def image_path(self, room, image_id, mime):
        folder=hashlib.sha256(room.encode("utf-8")).hexdigest()[:32]
        if not re.fullmatch(r"[0-9a-f]{32}",image_id) or mime not in ("image/png","image/jpeg"):
            raise ValueError("画像の識別子が不正です。")
        target=self.root/"attachments"/folder/(image_id+(".png" if mime=="image/png" else ".jpg"))
        for part in (target,*target.parents):
            if part==self.root:break
            try:attrs=getattr(part.lstat(),'st_file_attributes',0)
            except FileNotFoundError:continue
            if part.is_symlink() or attrs&0x400:raise ValueError('画像の保存先にリンクやジャンクションは使用できません。')
        return target

    def read_image(self, room, image_id, *, include_hidden=False):
        from .multimodal import MAX_IMAGE_BYTES
        self.room_path(room)
        with self.connect() as db:
            row=db.execute("SELECT * FROM images WHERE room=? AND id=?",(room,image_id)).fetchone()
            hidden=db.execute('SELECT hidden FROM attachment_states WHERE image_id=?',(image_id,)).fetchone()
        if not row: raise ValueError("画像が見つかりません。")
        if hidden and hidden[0] and not include_hidden:raise ValueError('この画像は会話から外されています。添付画像の管理から戻してください。')
        with self.image_path(room,row["id"],row["mime"]).open("rb") as stream:
            raw=stream.read(MAX_IMAGE_BYTES+1)
        if len(raw)!=row["size"] or len(raw)>MAX_IMAGE_BYTES:
            raise ValueError("保存した画像のサイズが一致しません。")
        return dict(row,raw=raw)

    def import_room(self, source):
        if not isinstance(source,(str,Path)) or not str(source).strip():
            raise ValueError("取り込むroom_config.jsonのパスを指定してください。")
        value=str(source).strip()
        if value.startswith('"') and value.endswith('"'):value=value[1:-1]
        selected=Path(value).expanduser()
        if selected.is_dir():
            source=selected.resolve()  # Retain existing callers, using the same character marker.
        elif selected.name=='room_config.json':
            source=selected.parent.resolve()
        else:
            raise ValueError("取り込むroom_config.jsonを指定してください。")
        config_path=source/'room_config.json'
        if not source.is_dir() or not config_path.is_file():
            raise ValueError("指定したキャラのroom_config.jsonが見つかりません。")
        app_root = self.root.parent
        if source == app_root or app_root.is_relative_to(source) or source.is_relative_to(app_root):
            raise ValueError("このアプリのデータフォルダ以外から取り込んでください。")
        reject_links(source)
        config=read_json(config_path,{})
        if not isinstance(config,dict):raise ValueError("room_config.jsonはオブジェクト形式である必要があります。")
        temporary = self.characters / (".import-" + uuid.uuid4().hex)
        name = source.name
        if name.startswith("."):
            raise ValueError("ドットから始まるフォルダ名には対応していません。")
        try:
            shutil.copytree(source, temporary)
            copied_config=read_json(temporary / "room_config.json", {})
            if not isinstance(copied_config,dict):raise ValueError("room_config.jsonはオブジェクト形式である必要があります。")
            read_sources(temporary,copied_config)
            for key in ("enable_auto_retrieval","include_knowledge_in_auto_retrieval"):
                if not isinstance(copied_config.get('override_settings',{}).get(key,False),bool):
                    raise ValueError(f"{key}にはtrue/falseを指定してください。")
            with self.lock:
                destination = self.characters / name
                if destination.exists():
                    name += "-" + uuid.uuid4().hex[:8]
                    destination = self.characters / name
                temporary.rename(destination)
            self.index_room(name)
            return self.room(name)
        finally:
            if temporary.exists() and temporary.parent == self.characters:
                shutil.rmtree(temporary)

    def settings(self):
        defaults = {"executable": "", "model": "", "mmproj": "", "context": 8192,
                    "gpu_layers": 99, "temperature": 0.7, "top_p": 0.95, "max_tokens": 0,
                    "history_limit": 40, "thinking_mode":"auto", "tool_calling": True, "extra_args": [], "mtp":"", "mtp_mode":"off",
                    "auto_aux":True, "draft_tokens":2, "auto_start":True}
        install = self.root.parent.parent
        for relative in ("llama.cpp/llama-server.exe", "llama.cpp/bin/llama-server.exe",
                         "llama.cpp/bin/Release/llama-server.exe"):
            if (install / relative).is_file():
                defaults["executable"] = relative
                break
        with self.lock:
            path=self.root/'settings.json'
            settings,self.settings_warning=settings_io.load(path,defaults,validate_settings)
            if not self.settings_warning and settings['max_tokens']==1024 and not settings.get('generation_limit_migrated'):
                migrated=dict(settings,max_tokens=0,generation_limit_migrated=True)
                try:
                    settings_io.save(path,migrated)
                except OSError:
                    self.settings_warning='最大生成tokensの移行を保存できませんでした。設定ファイルの書き込み権限を確認してください。'
                else:
                    settings=migrated
            return settings

    def save_settings(self, settings):
        with self.lock:
            settings=settings_io.portable(settings,self.root.parent.parent,('executable','model','mmproj','mtp'))
            settings_io.save(self.root/'settings.json',dict(settings,generation_limit_migrated=True))
            self.settings_warning=''

    def export_log(self, room):
        self.index_room(room)
        with self.connect() as db:
            rows = db.execute("SELECT id,role,content FROM messages WHERE room=? AND deleted=0 ORDER BY id", (room,)).fetchall()
            images = db.execute("SELECT message_id,name FROM images WHERE room=? AND NOT EXISTS (SELECT 1 FROM attachment_states s WHERE s.image_id=images.id AND s.hidden=1) ORDER BY rowid", (room,)).fetchall()
        names = {}
        for image in images:
            names.setdefault(image["message_id"], []).append(image["name"])
        return "\n\n".join(("## USER: user" if r["role"] == "user" else
                              "## AGENT: " + room if r["role"] == "assistant" else "## SYSTEM: system")
                             + "\n" + r["content"] + ("\n[添付画像: " + ", ".join(names[r["id"]]) + "]"
                             if r["id"] in names else "") for r in rows)
