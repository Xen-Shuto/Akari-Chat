"""Select dated character memory without migrations or model calls."""
import json
from .episodes import fingerprint
import os
import re
from datetime import date, datetime, timedelta, timezone


def read_records(root, folder, legacy, *, excluded_paths=()):
    directory = root / "memory" / folder
    if not directory.resolve().is_relative_to(root):
        raise ValueError("記憶フォルダはキャラフォルダ内に置いてください。")
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    old = root / "memory" / legacy
    if old.is_file():
        files.append(old)
    excluded = {os.path.normcase(p).replace("\\", "/") for p in excluded_paths}
    records, warnings = [], []
    for path in files:
        if not path.resolve().is_relative_to(root):
            raise ValueError("記憶ファイルはキャラフォルダ内に置いてください。")
        relative = path.relative_to(root).as_posix()
        if os.path.normcase(relative).replace("\\", "/") in excluded:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, list):
                raise ValueError("配列ではありません")
            for index, record in enumerate(data):
                if isinstance(record, dict):
                    records.append((record, {"path": relative, "record_index": index, "record_hash": fingerprint(record)}))
                else:
                    warnings.append(f"{relative}のレコード{index}を読めません")
        except (OSError, ValueError):
            warnings.append(f"{relative}を読めません（原本は変更していません）")
    return records, warnings, bool(files)


def episodic_source(root, overrides, today=None, *, excluded_paths=(), selection_warnings=()):
    value = overrides.get("episode_memory_lookback_days", "14")
    try:
        if isinstance(value, bool):
            raise ValueError()
        days = int(value)
        if str(days) != str(value).strip() or not 0 <= days <= 3660:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("エピソード記憶の日数は0～3660の整数で指定してください。") from None
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    start = today - timedelta(days=days)
    source = {"label": "エピソード記憶", "path": "memory/episodic/*.json", "exists": False,
              "text": "", "encoding": "UTF-8", "records": [],
              "selection_reason": f"{start.isoformat()}から{today.isoformat()}より前の{days}日間。今日の記録は含みません"}
    if days == 0:
        source.update(disabled=True, selection_reason="ルーム設定でエピソード記憶は無効（0日）です")
        return source
    records, warnings, source["exists"] = read_records(root, "episodic", "episodic_memory.json", excluded_paths=excluded_paths)
    warnings.extend(selection_warnings)
    seen, selected = set(), []
    for record, reference in records:
        try:
            parts = re.split(r"[~～]", record["date"])
            first, last = date.fromisoformat(parts[0].strip()), date.fromisoformat(parts[-1].strip())
            if "period_end" in record:
                explicit_end = date.fromisoformat(record["period_end"])
                if len(parts) == 2 and explicit_end != last:
                    raise ValueError()
                last = explicit_end
            summary = record["summary"]
            if len(parts) > 2 or first > last or not isinstance(summary, str) or not summary.strip():
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            warnings.append(f'{reference["path"]}のレコード{reference["record_index"]}の日付・本文を読めません')
            continue
        if (last - first).days + 1 > days or last < start or first >= today:
            continue
        day = first.isoformat() if first == last else f"{first.isoformat()}~{last.isoformat()}"
        identity = str(record.get("id") or (day, summary))
        if identity in seen:
            continue
        seen.add(identity)
        selected.append((day, summary.strip(), reference, identity))
    selected.sort(key=lambda item: item[0])
    source["text"] = "\n\n".join(f"[{day}] {summary}" for day, summary, _, _ in selected)
    source["records"] = [dict(reference, date=day, id=identity) for day, _, reference, identity in selected]
    if not selected:
        source["selection_reason"] += "。期間内の記憶はありません"
    if warnings:
        source["selection_reason"] += "。" + "／".join(warnings)
    return source


def dream_source(root, overrides):
    enabled = overrides.get("enable_self_awareness", True)
    if not isinstance(enabled, bool):
        raise ValueError("enable_self_awarenessにはtrue/falseを指定してください。")
    source = {"label": "夢の指針", "path": "memory/dreaming/*.json", "exists": False,
              "text": "", "encoding": "UTF-8", "records": [],
              "selection_reason": "最新1件の指針だけを使用。夢日記の全文は送信しません"}
    if not enabled:
        source.update(disabled=True, selection_reason="ルーム設定で自己意識は無効です")
        return source
    records, warnings, source["exists"] = read_records(root, "dreaming", "insights.json")
    candidates = []
    for record, reference in records:
        try:
            timestamp = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone(timedelta(hours=9)))
            candidates.append((timestamp, record, reference))
        except (KeyError, TypeError, ValueError, AttributeError):
            warnings.append(f'{reference["path"]}のレコード{reference["record_index"]}の日時を読めません')
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates:
        _, record, reference = candidates[0]
        strategy = record.get("strategy", "")
        if isinstance(strategy, str) and strategy.strip():
            kind = "最近の気づき（問いの解決）" if "解決された問い:" in str(record.get("trigger_topic", "")) else "深層意識の指針"
            source["text"] = f"{kind}: {strategy.strip()}"
            source["records"] = [dict(reference, created_at=record["created_at"])]
        else:
            source["selection_reason"] += "。最新の記録に指針がありません"
    if warnings:
        source["selection_reason"] += "。" + "／".join(warnings)
    return source
