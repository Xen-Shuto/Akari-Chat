"""Human-reviewed episode creation; originals are never overwritten."""
import hashlib
import json
import os
import re
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def today():
    return datetime.now(JST).date().isoformat()


def summary_text(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 4000:
        raise ValueError("記憶の本文は1～4000文字で入力してください。")
    return value.strip()


def episode_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("記憶の日付はYYYY-MM-DDで指定してください。")
    date.fromisoformat(value)
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class Episodes:
    def __init__(self, store):
        self.store = store
        self.enrich_source = None
        self.process_arousal = None
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS episode_drafts (
                id TEXT PRIMARY KEY, room TEXT NOT NULL, day TEXT NOT NULL, summary TEXT NOT NULL,
                source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, path TEXT NOT NULL DEFAULT '')""")
            db.execute("CREATE INDEX IF NOT EXISTS room_episode_drafts ON episode_drafts(room, created)")

    def source(self, room, limit=20, budget=8000):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 80:
            raise ValueError("対象の発言数は2～80の整数で指定してください。")
        self.store.room_path(room)
        self.store.index_room(room)
        with self.store.connect() as db:
            rows = db.execute("SELECT id,role,content,created FROM messages WHERE room=? AND deleted=0 AND status='complete' "
                "AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?", (room, limit)).fetchall()
        messages, remaining, shortened = [], budget, False
        for row in rows:
            body = row["content"].strip()
            if not body:
                continue
            if remaining <= 0:
                shortened = True
                break
            used = body[-remaining:]
            shortened |= len(used) != len(body)
            messages.append({"id": row["id"], "role": row["role"], "content": used,
                             "created": row["created"], "original_hash": fingerprint(dict(row))})
            remaining -= len(used)
        messages.reverse()
        if {m["role"] for m in messages} != {"user", "assistant"}:
            raise ValueError("記憶を作るには、完了したユーザーとキャラクターの発言が必要です。画像の内容は対象に含みません。")
        snapshot = {"room": room, "messages": messages, "limit": limit, "budget": budget}
        snapshot["hash"] = fingerprint(snapshot)
        snapshot["warnings"] = ["文字数上限により、古い発言または長い発言の先頭を省略しています。"] if shortened else []
        return snapshot

    def create(self, room, day, summary, source, *, _db=None):
        self.store.room_path(room)
        day, summary = episode_date(day), summary_text(summary)
        if source["room"] != room:
            raise ValueError("元の会話のルームが一致しません。")
        key = uuid.uuid4().hex
        with self.store.lock, (self.store.connect() if _db is None else nullcontext(_db)) as db:
            if self.enrich_source:source=self.enrich_source(room,source,db)
            db.execute("INSERT INTO episode_drafts(id,room,day,summary,source) VALUES(?,?,?,?,?)",
                       (key, room, day, summary, json.dumps(source, ensure_ascii=False)))
            if _db is not None:
                row = dict(db.execute('SELECT * FROM episode_drafts WHERE room=? AND id=?',(room,key)).fetchone())
                return dict(row, source=json.loads(row['source']))
        return self.get(room, key)

    def get(self, room, key):
        self.store.room_path(room)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM episode_drafts WHERE room=? AND id=?", (room, key)).fetchone()
        if row is None:
            raise ValueError("記憶の草案が見つかりません。")
        return dict(row, source=json.loads(row["source"]))

    def listing(self, room):
        self.store.room_path(room)
        with self.store.connect() as db:
            rows = db.execute("SELECT id,day,summary,status,created,path FROM episode_drafts "
                              "WHERE room=? ORDER BY created DESC,rowid DESC LIMIT 50", (room,)).fetchall()
        return [dict(row) for row in rows]

    def discard(self, room, key):
        self.store.room_path(room)
        with self.store.lock, self.store.connect() as db:
            count = db.execute("UPDATE episode_drafts SET status='discarded' WHERE room=? AND id=? AND status='pending'",
                               (room, key)).rowcount
            if not count:
                raise ValueError("保留中の草案だけを見送れます。保存済みの記憶は削除しません。")
        return self.get(room, key)

    def _target(self, room, key, day):
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("草案の識別子が不正です。")
        root = self.store.room_path(room)
        for path in (root, root / "memory", root / "memory/episodic"):
            if path.exists() or path.is_symlink():
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError("記憶の保存先にリンクやジャンクションは使えません。")
                if not path.is_dir():
                    raise ValueError("記憶の保存先がフォルダではありません。")
            else:
                path.mkdir()
        return root / "memory/episodic" / f"akari-{day}-{key}.json"

    def approve(self, room, key, day, summary):
        day, summary = episode_date(day), summary_text(summary)
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = self.get(room, key)
            if draft["status"] == "approved":
                if (draft["day"], draft["summary"]) != (day, summary):
                    raise ValueError("保存済みの草案の内容は変更できません。")
                return draft
            if draft["status"] != "pending":
                raise ValueError("見送った草案は記憶に保存できません。")
            for message in draft["source"]["messages"]:
                row = db.execute("SELECT id,role,content,created FROM messages WHERE room=? AND id=? AND deleted=0 AND status='complete'",
                                 (room, message["id"])).fetchone()
                if row is None or fingerprint(dict(row)) != message["original_hash"]:
                    raise ValueError("元の会話が変更されています。対象を確認して草案を作り直してください。")
            record = {"id": f"episode_{day}_akari{key}", "date": day, "summary": summary,
                      "created_at": datetime.now(JST).isoformat(timespec="seconds"),
                      "akari_draft_id": key, "source_message_ids": [m["id"] for m in draft["source"]["messages"]]}
            if draft['source'].get('akari_arousal'):record['akari_arousal']=draft['source']['akari_arousal']
            if draft['source'].get('akari_session_arousal'):record['akari_session_arousal']=draft['source']['akari_session_arousal']
            target = self._target(room, key, day)
            if target.is_symlink() or (target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
                raise ValueError("記憶ファイルにリンクは使えません。")
            if target.exists():
                existing = json.loads(target.read_text(encoding="utf-8"))
                # Recover an interrupted SQLite commit without duplicating or rewriting the file.
                expected = {k:v for k,v in record.items() if k != "created_at"}
                if not isinstance(existing, list) or len(existing) != 1 or not isinstance(existing[0], dict) or {
                        k:v for k,v in existing[0].items() if k != "created_at"} != expected:
                    raise ValueError("同名の記憶ファイルがあります。既存ファイルは上書きしません。")
            else:
                temporary = target.with_name("." + key + "-" + uuid.uuid4().hex + ".tmp")
                try:
                    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                        stream.write(json.dumps([record], ensure_ascii=False, indent=2) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    if os.name == "nt":
                        os.rename(temporary, target)  # Windows refuses an existing destination.
                    else:
                        os.link(temporary, target)  # Atomic create without replacement.
                finally:
                    temporary.unlink(missing_ok=True)
            relative = target.relative_to(self.store.room_path(room)).as_posix()
            if self.process_arousal:self.process_arousal(db,room,draft['source'].get('akari_session_arousal'),'episode:'+key)
            db.execute("UPDATE episode_drafts SET day=?,summary=?,status='approved',path=? WHERE room=? AND id=?",
                       (day, summary, relative, room, key))
        return self.get(room, key)
