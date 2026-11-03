"""Explicit working-memory slot creation and selection, without migrations."""
import json
import os
from itertools import islice
from pathlib import PureWindowsPath

from .file_editor import EditConflict, safe_path, normalize
from .memory_context import working_location


def slot_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 80 or value != value.strip()
            or value.startswith(".") or ".." in value or value.lower().endswith(".md")
            or value.endswith(".") or any(ord(c) < 32 or c in '/\\:<>"|?*' for c in value)
            or PureWindowsPath(value).is_reserved()):
        raise ValueError("スロット名は拡張子なしの1～80文字にしてください。パス・予約名・末尾の空白やドットは使えません。")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("room_config.jsonに重複キーがあります。自動修復せず切替を中止します。")
        result[key] = value
    return result


class WorkingSlots:
    def __init__(self, store, editor):
        self.store, self.editor = store, editor
        self.states = None

    def config(self, room):
        snapshot = self.editor.read_snapshot(room, "working_config", "スロット選択設定", "room_config.json")
        data = json.loads(snapshot["text"], object_pairs_hook=unique_object) if snapshot["exists"] else {}
        if not isinstance(data, dict) or not isinstance(data.get("override_settings", {}), dict):
            raise ValueError("room_config.jsonとoverride_settingsはオブジェクト形式で指定してください。")
        return snapshot, data

    def location(self, room, slot):
        slot = slot_name(slot)
        return working_location(self.store.room_path(room), {"active_working_memory_slot": slot})[1]

    def preview(self, room, slot):
        slot = slot_name(slot)
        doc=self.editor.read_snapshot(room, "working_slot", "ワーキングメモリ", self.location(room, slot), slot)
        if self.states:doc.update(self.states.describe(room,slot,doc))
        return doc

    def listing(self, room):
        return self._listing(room)

    def inventory(self, room):
        """Bounded complete AI catalog; leave the UI's listing limit unchanged."""
        return self._listing(room, complete=True)

    def readonly(self, room, name='', expected=''):
        """Read a named or active slot and retain the selection snapshot."""
        if name:slot_name(name)
        config,data=self.config(room)
        if expected and expected!=config['revision']:
            raise EditConflict('ルーム設定が変更されました。スロットの一覧を取得し直してください。')
        active,_=working_location(self.store.room_path(room),data.get('override_settings',{}))
        slot_name(active)
        doc=self.preview(room,name or active)
        if self.config(room)[0]['revision']!=config['revision']:
            raise EditConflict('読取中にルーム設定が変更されました。もう一度確認してください。')
        return {'document':doc,'active':active,'config_revision':config['revision']}

    def _listing(self, room, complete=False):
        root = self.store.room_path(room)
        config, data = self.config(room)
        active, active_path = working_location(root, data.get("override_settings", {}))
        if complete:slot_name(active)
        directory = safe_path(root, "notes/working_memories")
        names, warnings = set(), []
        if directory.is_dir():
            if complete:
                with os.scandir(directory) as stream:
                    paths=[directory/item.name for item in islice(stream,5001)]
                if len(paths)>5000:
                    raise ValueError('スロット一覧の走査対象が5000項目を超えています。')
                paths=sorted(paths)
            else:paths=sorted(directory.glob('*.md'))
            for path in paths:
                if complete and path.suffix.lower()!='.md':continue
                if not complete and len(names) >= 500:
                    warnings.append("スロット一覧の上限500件に達しました。")
                    break
                try:
                    name = slot_name(path.stem)
                    safe = safe_path(root, path.relative_to(root))
                    if safe.is_file():
                        names.add(name)
                except ValueError:
                    warnings.append("扱えない名前またはリンクを含むスロットは一覧から除外しています。")
        legacy = safe_path(root, "notes/working_memory.md")
        if legacy.is_file():
            names.add("main")
        names.add(active)
        result = []
        for name in sorted(names):
            try:
                relative = self.location(room, name)
                path = safe_path(root, relative)
                exists = path.is_file()
                result.append({"name": name, "path": relative, "exists": exists,
                               "selectable": exists, "bytes": path.stat().st_size if exists else 0})
            except ValueError:
                warnings.append("現在の選択先または既存名の一部は管理画面では扱えません。原本は変更していません。")
        if complete and self.config(room)[0]['revision']!=config['revision']:
            raise EditConflict('一覧取得中にルーム設定が変更されました。もう一度確認してください。')
        if self.states:
            result=self.states.annotate(room,result)
            if any(s['state']=='dormant' and self.states.key(s['path'])==self.states.key(active_path) for s in result):
                warnings.append('選択中の話題が休眠状態です。外部で選択設定が変更された可能性があります。復帰または別の話題への切替を確認してください。')
        return {"room": room, "active": active, "active_path": active_path,
                "config_revision": config["revision"], "slots": result, "warnings": list(dict.fromkeys(warnings))[:10]}

    def create(self, room, name, text, config_revision, checkpoint=lambda: None):
        name = slot_name(name)
        if not isinstance(text, str):
            raise ValueError("初期本文は文字列で指定してください。")
        with self.store.lock:
            state = self.listing(room)
            if state["config_revision"] != config_revision:
                raise EditConflict("読み込み後にルーム設定が変わりました。一覧を更新してください。")
            if any(s["name"].casefold() == name.casefold() and s["exists"] for s in state["slots"]):
                raise ValueError("同じ名前のスロットがあります。既存の本文は上書きしません。")
            relative = f"notes/working_memories/{name}.md"
            def read():
                checkpoint()
                if self.config(room)[0]["revision"] != config_revision:
                    raise EditConflict("作成前にルーム設定が変わりました。選択先は更新していません。")
                return self.editor.read_snapshot(room, "working_slot", "ワーキングメモリ", relative, name)
            document = read()
            if document["exists"]:
                raise ValueError("同名のファイルがあります。上書きしません。")
            saved = self.editor.save_snapshot(room, "working_slot", normalize(text), document["revision"], read,
                after_read=lambda: self.editor.read_snapshot(room, "working_slot", "ワーキングメモリ", relative, name))
            return {"state": self.listing(room), "document": saved["document"], "backup": saved["backup"]}

    def select(self, room, name, config_revision, slot_revision, checkpoint=lambda: None):
        name = slot_name(name)
        with self.store.lock:
            original, data = self.config(room)
            if original["revision"] != config_revision:
                raise EditConflict("読み込み後にルーム設定が変わりました。一覧を更新してください。")
            document = self.preview(room, name)
            if document.get('state')=='dormant':raise ValueError('休眠中の話題です。復帰してから切り替えてください。')
            if not document["exists"]:
                raise ValueError("本文ファイルのあるスロットを選んでください。未配置のスロットは先に作成してください。")
            if document["revision"] != slot_revision:
                raise EditConflict("確認後に対象スロットの本文が変わりました。もう一度プレビューしてください。")
            active, _ = working_location(self.store.room_path(room), data.get("override_settings", {}))
            if active == name:
                return {"state": self.listing(room), "changed": False, "backup": None}
            overrides = dict(data.get("override_settings", {}))
            overrides["active_working_memory_slot"] = name
            updated = dict(data, override_settings=overrides)
            text = json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            def read():
                checkpoint()
                current=self.preview(room,name)
                if current.get('state')=='dormant':raise EditConflict('話題が休眠になりました。再読込してください。')
                if current["revision"] != slot_revision:
                    raise EditConflict("切替前に本文が変わりました。選択設定は更新していません。")
                return self.config(room)[0]
            saved = self.editor.save_snapshot(room, "working_config", text, original["revision"], read, after_read=lambda: self.config(room)[0])
            return {"state": self.listing(room), "changed": saved["changed"], "backup": saved["backup"]}

    def replace(self, room, name, text, config_revision, revision, old_text=None, checkpoint=lambda: None):
        """Use the same guarded file writer as the slot editor, including legacy main."""
        with self.store.lock:
            state = self.readonly(room, name, config_revision)
            doc = state['document']
            if doc['revision'] != revision:
                raise EditConflict('本文が変更されました。読み直してください。')
            if not doc['exists']:
                raise ValueError('未配置のスロットは先に作成してください。')
            text = normalize(text)
            if old_text is not None:
                source, needle = normalize(doc['text']), normalize(old_text)
                if not needle or source.count(needle) != 1:
                    raise EditConflict('置換元は空でなく、本文に一度だけ現れる必要があります。')
                text = source.replace(needle, text, 1)
            def read():
                checkpoint()
                current = self.readonly(room, name, config_revision)['document']
                if current['path'] != doc['path']:
                    raise EditConflict('スロットの参照先が変更されました。')
                return current
            return self.editor.save_snapshot(room, 'working_slot', text, revision, read,
                after_read=lambda: self.editor.read_snapshot(room, 'working_slot', 'ワーキングメモリ', doc['path'], name))
