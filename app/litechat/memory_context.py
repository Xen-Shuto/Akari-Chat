"""Read character memory without migrations, updates, or index initialization."""
from pathlib import Path
from .temporal_memory import episodic_source, dream_source

SOURCES = {
    "prompt": ("キャラクター指示", "SystemPrompt.txt"),
    "core": ("コアメモリ", "core_memory.txt"),
    "identity": ("永続記憶の原本", "memory/memory_identity.txt"),
    "diary": ("日記の原本", "memory/memory_diary.txt"),
    "notepad": ("短期メモ", "notes/notepad.md"),
}


def working_location(root, overrides):
    slot = overrides.get("active_working_memory_slot", "main")
    if not isinstance(slot, str) or not slot.strip():
        slot = "main"
    slot = slot.strip()
    if slot.endswith(".md"):
        slot = slot[:-3]
    if not slot or ".." in slot or any(c in slot for c in '/\\:<>"|?*') or slot.endswith((".", " ")):
        raise ValueError("ワーキングメモリのスロット名が不正です。")
    working_path = f"notes/working_memories/{slot}.md"
    if slot == "main" and not (root / working_path).exists() and (root / "notes/working_memory.md").is_file():
        working_path = "notes/working_memory.md"
    return slot, working_path


def read_sources(room_path, config, *, episode_excluded_paths=(), episode_warnings=()):
    root = Path(room_path).resolve()
    overrides = config.get("override_settings", {})
    if not isinstance(overrides, dict):
        raise ValueError("override_settingsはオブジェクト形式である必要があります。")
    slot, working_path = working_location(root, overrides)
    sources = {}
    source_paths = dict(SOURCES, working=("ワーキングメモリ", working_path))
    for key, (label, relative) in source_paths.items():
        path = root / relative
        # A copy may contain manually-added junctions after it was imported.
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"{relative}はキャラフォルダ内のファイルを指定してください。")
        exists = path.is_file()
        text, encoding = "", None
        if exists:
            raw = path.read_bytes()
            try:
                text, encoding = raw.decode("utf-8-sig"), "UTF-8"
            except UnicodeDecodeError:
                text, encoding = raw.decode("cp932"), "CP932"
        sources[key] = {"label": label, "path": relative, "exists": exists,
                        "text": text.strip(), "encoding": encoding}
        if key == "working":
            sources[key]["slot"] = slot
    from .world import source as world_source
    sources["world"] = world_source(root)
    sources["episodic"] = episodic_source(root, overrides, excluded_paths=episode_excluded_paths, selection_warnings=episode_warnings)
    sources["dream"] = dream_source(root, overrides)
    settings = {}
    for name in ("send_core_memory", "send_notepad"):
        value = overrides.get(name, True)
        if not isinstance(value, bool):
            raise ValueError(f"{name}にはtrue/falseを指定してください。")
        settings[name] = {"value": value, "origin": "ルーム設定" if name in overrides else "あかりの既定値"}
    return sources, settings


def build_context(character):
    sources = character["memory_sources"]
    settings = character["memory_settings"]
    parts, report = [], []
    for key, source in sources.items():
        enabled = key in ("prompt", "working", "episodic", "dream", "world", "lifestyle") or (key == "core" and settings["send_core_memory"]["value"]) or (
            key == "notepad" and settings["send_notepad"]["value"])
        if source.get("disabled"):
            status, reason = "excluded", "送信しない指定です"
        elif not source["exists"]:
            status, reason = "missing", "ファイルがありません"
        elif not source["text"]:
            status, reason = "empty", "空のファイルです"
        elif key in ("identity", "diary"):
            status, reason = "excluded", "原本として保持。全文の自動送信・コアへの自動反映は行いません"
        elif not enabled:
            status, reason = "excluded", "ルーム設定で送信しない指定です"
        else:
            status, reason = "included", "送信対象です"
        if key == "working":
            reason += f'（選択スロット: {source["slot"]}。他のスロットは送信しません）'
        if source.get("selection_reason"):
            reason += "。" + source["selection_reason"]
        if status == "included":
            parts.append(source["text"] if key == "prompt" else f'【{source["label"]}】\n{source["text"]}')
            if key == 'working' and source.get('runtime_selection'):
                import json
                parts.append('【今回だけ使用する話題】スロット名: '+json.dumps(source['slot'],ensure_ascii=False)+
                             '。ワーキングメモリの参照・更新ツールには、このスロット名を明示してください。名前を省略したツールは手動設定の話題を対象とします。本文の自動保存や手動の選択設定の変更は行っていません。')
        report.append({"key": key, "label": source["label"], "path": source["path"],
                       "characters": len(source["text"]), "sent_characters": len(source["text"]) if status == "included" else 0,
                       "status": status, "reason": reason, "encoding": source["encoding"]})
        if "records" in source:
            report[-1]["records"] = source["records"]
    instructions = "\n\n".join(parts)
    auto = character.get("retrieval_settings", {}).get("enable_auto_retrieval", False)
    return instructions, {"room": character["id"], "sources": report, "settings": settings,
                          "system_characters": len(instructions),
                          "retrieval": {"method": "ローカル・検索（方式は送信時に確定）", "results": [],
                                        "reason": "送信時に検索します" if auto else "自動検索はオフです"},
                          "scope": "指示・記憶のみ。会話履歴・画像・テンプレートのトークン数は含みません。"}
