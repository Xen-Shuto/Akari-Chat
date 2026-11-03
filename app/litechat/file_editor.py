"""Allowlisted character text editing, revision checks and original-byte backups."""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .memory_context import working_location

MAX_BYTES = 1024 * 1024
FILES = {"prompt": ("キャラクター指示", "SystemPrompt.txt"),
         "core": ("コアメモリ", "core_memory.txt"),
         "notepad": ("短期メモ", "notes/notepad.md"),
         "working": ("選択中のワーキングメモリ", None)}


class EditConflict(ValueError):
    pass


def normalize(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def decode(raw):
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text, encoding = raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        try:
            text, encoding = raw.decode("cp932"), "cp932"
        except UnicodeDecodeError as error:
            raise ValueError("UTF-8またはCP932として読めないため編集できません。") from error
    endings = set()
    if "\r\n" in text:
        endings.add("CRLF")
    remaining = text.replace("\r\n", "")
    if "\n" in remaining:
        endings.add("LF")
    if "\r" in remaining:
        endings.add("CR")
    newline = next(iter(endings)) if len(endings) == 1 else "mixed" if endings else "LF"
    return {"text": text, "encoding": encoding, "bom": bom, "newline": newline, "editable": newline != "mixed"}


def safe_path(root, relative, create=False):
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or any(p in ("", ".") for p in relative.parts):
        raise ValueError("編集対象のパスが不正です。")
    paths = [root] + [root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts)+1)]
    for index, path in enumerate(paths):
        if path.is_reserved():
            raise ValueError("予約されたファイル名は使用できません。")
        if path.exists() or path.is_symlink():
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("編集対象やバックアップ先にリンク・ジャンクションは使用できません。")
            if index < len(paths)-1 and not path.is_dir():
                raise ValueError("保存先の途中にフォルダ以外の項目があります。")
        elif create and index < len(paths)-1:
            path.mkdir()
    target = paths[-1]
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("編集対象は許可されたフォルダ内に置いてください。")
    return target


class FileEditor:
    def __init__(self, store):
        self.store = store

    def target(self, room, key):
        if not isinstance(key, str) or key not in FILES:
            raise ValueError("編集できる項目ではありません。")
        root = self.store.room_path(room)
        relative, slot = FILES[key][1], None
        if key == "working":
            config = safe_path(root, "room_config.json")
            data = json.loads(decode(self.read_bytes(config))["text"]) if config.is_file() else {}
            if not isinstance(data, dict):
                raise ValueError("room_config.jsonはオブジェクト形式である必要があります。")
            overrides = data.get("override_settings", {})
            if not isinstance(overrides, dict):
                raise ValueError("override_settingsが不正です。")
            slot, relative = working_location(root, overrides)
        return root, relative, slot

    @staticmethod
    def read_bytes(path):
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            raise ValueError("編集できるファイルは1MiBまでです。")
        return raw

    def read(self, room, key):
        root, relative, slot = self.target(room, key)
        return self.read_snapshot(room, key, FILES[key][0], relative, slot)

    def read_snapshot(self, room, key, label, relative, slot=None):
        root = self.store.room_path(room)
        path = safe_path(root, relative)
        exists = path.exists()
        if exists and not path.is_file():
            raise ValueError("編集対象がファイルではありません。")
        raw = self.read_bytes(path) if exists else b""
        revision = hashlib.sha256(relative.encode("utf-8") + bytes([exists]) + raw).hexdigest()
        return {"room": room, "key": key, "label": label, "path": relative,
                "slot": slot, "exists": exists, "revision": revision, "bytes": len(raw), **decode(raw)}

    def backup_root(self, room, key):
        # Only a hash of the room name is used as a backup directory component.
        room_hash = hashlib.sha256(room.encode("utf-8")).hexdigest()
        return f"file_backups/{room_hash}/{key}"

    def save(self, room, key, text, expected):
        return self.save_snapshot(room, key, text, expected, lambda: self.read(room, key))

    def save_snapshot(self, room, key, text, expected, read, after_read=None):
        """Internal writer; only trusted resolvers may select non-editor paths."""
        if not isinstance(text, str) or len(text) > MAX_BYTES:
            raise ValueError("本文が長すぎるか、文字列ではありません。")
        with self.store.lock:
            current = read()
            if not isinstance(expected, str) or expected != current["revision"]:
                raise EditConflict("読み込み後にファイルまたは選択スロットが変わりました。編集中の本文を控え、最新の内容を確認してください。")
            if not current["editable"]:
                raise ValueError(current.get('edit_error',"改行コードが混在しているため、この画面では保存できません。"))
            if current["exists"] and normalize(text) == normalize(current["text"]):
                return {"document": current, "changed": False, "backup": None}
            newline = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}[current["newline"]]
            try:
                raw = normalize(text).replace("\n", newline).encode(current["encoding"])
            except UnicodeEncodeError as error:
                raise ValueError("元の文字コードでは保存できない文字があります。本文を修正してください。") from error
            if current["bom"]:
                raw = b"\xef\xbb\xbf" + raw
            if len(raw) > MAX_BYTES:
                raise ValueError("保存後のファイルが1MiBを超えます。")
            root, relative = self.store.room_path(room), current["path"]
            target = safe_path(root, relative)
            old_raw = self.read_bytes(target) if current["exists"] else b""
            if hashlib.sha256(relative.encode("utf-8") + bytes([current["exists"]]) + old_raw).hexdigest() != current["revision"] or read()["revision"] != current["revision"]:
                raise EditConflict("保存前に内容が変わりました。上書きしていません。")
            backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex
            prefix = self.backup_root(room, key) + "/" + backup_id
            before = safe_path(self.store.root, prefix + "/before.bin", create=True)
            with before.open("xb") as stream:
                stream.write(old_raw)
                stream.flush()
                os.fsync(stream.fileno())
            metadata = {k:current[k] for k in ("room", "key", "path", "exists", "revision", "encoding", "bom", "newline")}
            metadata.update(id=backup_id, sha256=hashlib.sha256(old_raw).hexdigest(),
                            new_sha256=hashlib.sha256(raw).hexdigest(), created=datetime.now(timezone.utc).isoformat())
            info = safe_path(self.store.root, prefix + "/info.json")
            with info.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            target = safe_path(root, relative, create=True)
            temporary = target.with_name("." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if read()["revision"] != current["revision"]:
                    raise EditConflict("書き込み直前に内容が変わりました。バックアップは残し、上書きはしていません。")
                # Optimistic concurrency: an external editor need not obey our app lock.
                if current["exists"]:
                    os.replace(temporary, target)
                elif os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return {"document": (after_read or read)(), "changed": True, "backup": backup_id}

    def backups(self, room, key, relative=None):
        if relative is None:
            self.target(room, key)
        directory = safe_path(self.store.root, self.backup_root(room, key))
        if not directory.exists():
            return []
        result = []
        for child in sorted(directory.iterdir(), reverse=True):
            if len(result) >= 20:
                break
            try:
                backup = self.backup(room, key, child.name, relative)
                result.append({k:backup[k] for k in ("id", "created", "path", "exists")})
            except (OSError, ValueError):
                continue
        return result

    def backup(self, room, key, backup_id, relative=None):
        if relative is None:
            self.target(room, key)
        if not isinstance(backup_id, str) or not re.fullmatch(r"\d{8}T\d{12}Z_[0-9a-f]{32}", backup_id):
            raise ValueError("バックアップの識別子が不正です。")
        prefix = self.backup_root(room, key) + "/" + backup_id
        info = safe_path(self.store.root, prefix + "/info.json")
        metadata = json.loads(self.read_bytes(info).decode("utf-8"))
        raw = self.read_bytes(safe_path(self.store.root, prefix + "/before.bin"))
        if metadata.get("room") != room or metadata.get("key") != key or metadata.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("バックアップの内容を確認できません。")
        if relative is not None and metadata.get("path") != relative:
            raise ValueError("対象の異なるバックアップです。")
        return {**metadata, **decode(raw)}
