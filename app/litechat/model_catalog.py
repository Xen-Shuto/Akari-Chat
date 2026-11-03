"""Bounded GGUF metadata inspection and conservative local sidecar discovery."""
import json
import re
import struct
from functools import lru_cache
from pathlib import Path


FAMILY = re.compile(r"(gemma|qwen|ministral|mistral|llama|deepseek)[-_ ]*(\d+(?:\.\d+)?)[-_ ]+(e?\d+(?:\.\d+)?b)", re.I)
QUANT = re.compile(r"[-_.](?:(?:iq\d|tq\d|[qit]\d)[^/]*|bf16|f16|f32)(?:-\d{5}-of-\d{5})?$", re.I)


def kind(path):
    name = path.stem.lower()
    if "mmproj" in name:
        return "mmproj"
    if name.startswith("mtp-") or name.startswith("mtp_") or name.endswith(".mtp") or "mtp-head" in name:
        return "mtp"
    if re.search(r"-\d{5}-of-\d{5}$", name) and not re.search(r"-00001-of-\d{5}$", name):
        return "shard"
    return "model"


def family(path):
    match = FAMILY.search(path.stem)
    return tuple(item.lower() for item in match.groups()) if match else None


def identity(path):
    stem = re.sub(r"(?i)^mmproj[-_.]?", "", path.stem)
    stem = re.sub(r"(?i)[-_.]?mmproj", "", stem)
    stem = QUANT.sub("", stem)
    return re.sub(r"[^a-z0-9]", "", stem.lower())


@lru_cache(maxsize=64)
def _metadata(path, size, modified):
    output = {}
    scalar = {0:"B", 1:"b", 2:"H", 3:"h", 4:"I", 5:"i", 6:"f", 7:"?", 10:"Q", 11:"q", 12:"d"}
    with Path(path).open("rb") as stream:
        def read(count):
            if count > 1024 * 1024 or stream.tell() + count > min(size, 4 * 1024 * 1024):
                raise ValueError("GGUFヘッダーの読取上限を超えました。")
            data = stream.read(count)
            if len(data) != count:
                raise ValueError("GGUFヘッダーが途中で終了しています。")
            return data

        def number(fmt):
            return struct.unpack("<" + fmt, read(struct.calcsize(fmt)))[0]

        def string():
            return read(number("Q")).decode("utf-8")

        def value(type_id):
            if type_id in scalar:
                return number(scalar[type_id])
            if type_id == 8:
                return string()
            if type_id == 9:
                subtype, count = number("I"), number("Q")
                if count > 4096 or subtype == 9:
                    raise ValueError("GGUF配列の読取上限を超えました。")
                for _ in range(count):
                    value(subtype)
                return None
            raise ValueError("未対応のGGUF値形式です。")

        if read(4) != b"GGUF" or number("I") not in (2,3):
            raise ValueError("GGUF v2/v3ヘッダーを確認できません。")
        number("Q")
        count = number("Q")
        if count > 4096:
            raise ValueError("GGUFメタデータ件数の上限を超えました。")
        for _ in range(count):
            key = string()
            # Model hyperparameters precede the tokenizer in standard exports.
            # Do not scan vocabulary arrays or read tensor/weight data.
            if key.startswith("tokenizer."):
                break
            result = value(number("I"))
            if key.startswith("general.") or key.endswith(".nextn_predict_layers"):
                output[key] = result
    return output


def metadata(path):
    info = path.stat()
    return _metadata(str(path.resolve()), info.st_size, info.st_mtime_ns)


def local_model_name(install, selected):
    """Display identity only; preserve the engine request model/alias."""
    if not selected:
        return 'Local_LLM'
    path = Path(selected)
    path = (path if path.is_absolute() else Path(install) / path).resolve()
    return path.stem if path.parent == (Path(install) / 'Models' / 'LLM').resolve() else (path.parent.name or path.stem)


def model_files(install):
    root = Path(install) / "Models" / "LLM"
    return [p.relative_to(install).as_posix() for p in sorted(root.rglob("*.gguf"))
            if p.is_file() and kind(p) == "model" and "embeddings" not in p.relative_to(root).parts] if root.is_dir() else []


def embedding_files(install):
    root = Path(install) / 'Models' / 'embeddings'
    return [p.relative_to(install).as_posix() for p in sorted(root.rglob('*.gguf'))
            if p.is_file() and kind(p) == 'model'] if root.is_dir() else []


def discover(install, selected):
    install = Path(install).resolve()
    path = Path(selected)
    path = (path if path.is_absolute() else install/path).resolve()
    result = {"mmproj":"", "mtp":"", "mtp_mode":"off", "notes":[], "candidates":{"mmproj":[],"mtp":[]}}
    if not selected or not path.is_file():
        result["notes"].append("モデルファイルが見つかりません。")
        return result
    if path.suffix.lower() != ".gguf" or kind(path) != "model":
        raise ValueError("本体モデルのGGUFを指定してください。")

    def display(file):
        return file.relative_to(install).as_posix() if file.is_relative_to(install) else str(file)

    try:
        info = metadata(path)
    except (OSError,ValueError,UnicodeError,struct.error) as error:
        info = {}
        result["notes"].append("本体のMTPメタデータは未確認です。必要なら手動で指定してください。")
    embedded = any(isinstance(value,int) and value > 0 for key,value in info.items()
                   if key.endswith(".nextn_predict_layers"))
    if embedded:
        result["mtp_mode"] = "embedded"
        result["notes"].append("本体GGUFにMTP層を検出しました。")

    manifest = path.parent / "akari-model.json"
    if manifest.is_file():
        mapping = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(mapping,dict) or not isinstance(mapping.get("models",{}),dict):
            raise ValueError("akari-model.jsonのmodelsはオブジェクトで指定してください。")
        mapping = mapping.get("models",{}).get(path.name,{})
        if not isinstance(mapping,dict):
            raise ValueError("akari-model.jsonのモデル設定が不正です。")
    else:
        mapping = {}
    explicit = set()
    for role in ("mmproj","mtp"):
        if role not in mapping:
            continue
        explicit.add(role)
        name = mapping[role]
        if name is None:
            if role == "mtp": result["mtp_mode"] = "off"
            continue
        if not isinstance(name,str) or not name:
            raise ValueError("akari-model.jsonにはファイル名またはnullを指定してください。")
        linked = (path.parent/name).resolve()
        if linked.parent != path.parent or linked.suffix.lower() != ".gguf" or not linked.is_file():
            raise ValueError("akari-model.jsonの対応ファイルが見つからないか、別フォルダを指しています。")
        result[role] = display(linked)
        if role == "mtp": result["mtp_mode"] = "external"
        result["notes"].append(role + "を対応表から選択しました。")

    siblings = [p for p in path.parent.glob("*.gguf") if p.is_file()]
    mains = [p for p in siblings if kind(p) == "model"]
    # A dedicated directory may contain several quantizations of the same model.
    dedicated = all(identity(p) == identity(path) for p in mains)
    for role in ("mmproj","mtp"):
        if role in explicit or (role == "mtp" and embedded):
            continue
        candidates = [p for p in siblings if kind(p) == role]
        result["candidates"][role] = [display(p) for p in candidates]
        exact = [p for p in candidates if role == "mmproj" and identity(p) == identity(path)]
        if len(exact) == 1:
            matched = exact
        else:
            key = family(path)
            matched = [p for p in candidates if key and family(p) == key] if dedicated else []
        if len(matched) == 1:
            result[role] = display(matched[0])
            if role == "mtp": result["mtp_mode"] = "external"
            result["notes"].append(role + "を同フォルダの名前・系列・サイズから選択しました（実動作は未検証）。")
        elif candidates:
            result["notes"].append(role + "の対応関係を一意に判断できないため、自動選択しませんでした。")
    if not result["mmproj"]:
        result["notes"].append("mmprojは未選択です。")
    if result["mtp_mode"] == "off":
        result["notes"].append("MTPは未選択です。")
    return result
