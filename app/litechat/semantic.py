"""Local embeddings and a derived SQLite cache, independent of character originals."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import math
import sqlite3
import struct
import threading
import time
from operator import mul
from pathlib import Path

from .engine import Engine
from .model_catalog import metadata
from .retrieval import normalize, document_reference, check_page, ranked_page
from . import settings_io

PROFILES = {"embeddinggemma": ("mean", "task: search result | query: ", "title: none | text: ")}
METHOD = "ローカル・意味検索"


def checked_settings(value):
    result = {"model": "", "profile": "embeddinggemma", "auto_start": True,
              "auto_semantic": True, "threshold": 0.45}
    result.update({k: v for k, v in value.items() if k in result})
    if not isinstance(result["model"], str) or len(result["model"]) > 4096:
        raise ValueError("埋め込みGGUFのパスが不正です。")
    result["model"] = result["model"].strip().strip('"')
    if result["profile"] not in PROFILES:
        raise ValueError("埋め込みモデルの方式が不正です。")
    for key in ("auto_start", "auto_semantic"):
        if not isinstance(result[key], bool):
            raise ValueError(key + "にはtrue/falseを指定してください。")
    t = result["threshold"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not 0 <= t <= 1:
        raise ValueError("類似度の下限は0～1で指定してください。")
    return result


def unit(vector):
    if not isinstance(vector, list) or not 1 <= len(vector) <= 4096 or any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
        raise ValueError("埋め込み応答のベクトルが不正です。")
    norm = math.hypot(*(float(x) for x in vector))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("埋め込みがゼロベクトルです。")
    return [float(x) / norm for x in vector]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached_cosine(raw, query):
    if not isinstance(raw,(bytes,bytearray,memoryview)) or not raw or len(raw)%4 or len(raw)>4096*4:
        raise ValueError('検索キャッシュの形式が不正です。')
    # Unpacking guarantees float values; hypot checks all components in C.
    vector=struct.unpack('<%sf'%(len(raw)//4),raw)
    norm=math.hypot(*vector)
    if not math.isfinite(norm) or norm<1e-12:
        raise ValueError('検索キャッシュのベクトルが不正です。')
    if len(query)!=len(vector):
        raise ValueError('モデルと索引の次元数が一致しません。')
    return sum(map(mul,query,vector))/norm


class SemanticRetrieval:
    def __init__(self, root, install, corpus, additional_corpus=None):
        self.root, self.install, self.corpus = Path(root), Path(install), corpus
        self.additional_corpus=additional_corpus
        self.engine = Engine(self.root / "embedding")
        self.lock = threading.RLock()
        self.worker = None
        self.cancel = threading.Event()
        self.closed = False
        self.identity = None
        self.loaded_settings = None
        self.progress = {"state": "idle", "room": "", "done": 0, "total": 0, "error": "", "warnings": []}
        self.config_file = self.root / "embedding_settings.json"
        self.database = self.root / "retrieval.sqlite3"
        self.settings_warning = ''

    def settings(self):
        defaults=checked_settings({})
        default="Models/embeddings/embeddinggemma-300M-qat-Q4_0.gguf"
        if (self.install/default).is_file():defaults['model']=default
        with self.lock:
            settings,self.settings_warning=settings_io.load(self.config_file,defaults,checked_settings)
            return settings

    def model_path(self, settings):
        model = Path(settings["model"])
        model = model if model.is_absolute() else self.install / model
        if not settings["model"] or model.suffix.lower() != ".gguf" or not model.is_file():
            raise ValueError("埋め込み用GGUFを指定して設定を保存してください。")
        if metadata(model).get("general.architecture") != "gemma-embedding":
            raise ValueError("現在の意味検索はEmbeddingGemmaのGGUFに対応しています。会話用モデルとは別に指定してください。")
        return model

    def save(self, data, *, restart_executable=None):
        settings = checked_settings(data)
        with self.lock:
            if self.closed:
                raise ValueError("アプリは終了処理中です。")
            if self.worker and self.worker.is_alive():
                raise ValueError("索引作成中です。停止後に設定を変更してください。")
            if restart_executable is None:
                if self.engine.status()["state"] in ("ready", "loading"):
                    raise ValueError("検索モデルを停止してから設定を変更してください。")
            elif settings["model"]:
                self.model_path(settings)
                if not Path(restart_executable).is_file():
                    raise ValueError("検索モデル用のllama-serverが見つかりません。")
            self.root.mkdir(parents=True, exist_ok=True)
            settings=settings_io.portable(settings,self.install,('model',))
            settings_io.save(self.config_file,settings)
            self.settings_warning=''
            if restart_executable is not None:
                try:
                    self.stop()
                    if settings["model"]:
                        self.start(restart_executable)
                except (ValueError, OSError) as error:
                    raise ValueError(f"検索設定は保存しましたが、検索モデルの再起動に失敗しました。{error}") from error
        return settings

    def status(self):
        with self.lock:
            return {**self.progress, "engine": self.engine.status()}

    def start(self, executable):
        with self.lock:
            if self.closed:
                raise ValueError("アプリは終了処理中です。")
            if self.engine.status()["state"] in ("ready", "loading"):
                return
            if self.cancel.is_set() and threading.current_thread() is self.worker:
                raise ValueError("検索モデルの起動を中止しました。")
            self.cancel.clear()
            settings = self.settings()
            model = self.model_path(settings)
            sha = hashlib.sha256()
            with model.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(block)
            self.identity = digest(sha.hexdigest() + settings["profile"] + ":v1:900/750")
            self.loaded_settings = settings
            self.engine.start({"executable": executable, "model": str(model), "context": 2048,
                "gpu_layers": 0, "temperature": 0.7, "max_tokens": 1, "history_limit": 1,
                "extra_args": ["--embedding", "--pooling", PROFILES[settings["profile"]][0],
                               "-np", "1", "-b", "2048", "-ub", "2048", "-t", "4"]})

    def stop(self):
        self.cancel.set()
        with self.lock:
            self.engine.stop()
            self.identity = None
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=4)
        with self.lock:
            self.identity = None
            if not worker or not worker.is_alive():
                self.progress["state"] = "idle"

    def close(self):
        with self.lock:
            self.closed = True
        self.stop()

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database, timeout=15)
        db.execute("CREATE TABLE IF NOT EXISTS vectors (model TEXT, body TEXT, vector BLOB NOT NULL, PRIMARY KEY(model,body))")
        try:
            with db:
                yield db
        finally:
            db.close()

    def embed(self, texts, query=False):
        if self.engine.status()["state"] != "ready":
            raise ValueError("検索モデルの準備ができていません。索引作成・更新を実行してください。")
        prefix = PROFILES[self.loaded_settings["profile"]][1 if query else 2]
        conn = http.client.HTTPConnection("127.0.0.1", self.engine.port, timeout=30)
        try:
            conn.request("POST", "/v1/embeddings", json.dumps({"model": "Local_LLM",
                         "input": [prefix + t for t in texts], "encoding_format": "float"}).encode("utf-8"),
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                raise ValueError("埋め込みリクエストに失敗しました。検索モデルのログを確認してください。")
            rows = json.loads(raw).get("data")
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("埋め込み応答の件数が一致しません。")
            by_index = {row["index"]: row["embedding"] for row in rows}
            if set(by_index) != set(range(len(texts))):
                raise ValueError("埋め込み応答の順序が不正です。")
            vectors = [unit(by_index[i]) for i in range(len(texts))]
            if len({len(v) for v in vectors}) != 1:
                raise ValueError("埋め込みの次元数が一致しません。")
            return vectors
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("埋め込み応答の形式が不正です。") from error
        finally:
            conn.close()

    def build(self, room, executable, *, existing_only=False,idle_check=None):
        with self.lock:
            if self.closed:
                raise ValueError("アプリは終了処理中です。")
            if self.worker and self.worker.is_alive():
                raise ValueError("索引作成中です。完了を待つか停止してください。")
            if existing_only and (self.engine.status()['state']!='ready' or not self.identity):raise ValueError('自動更新には起動済みの記憶検索モデルが必要です。')
            self.cancel.clear()
            self.progress = {"state": "building", "room": room, "done": 0, "total": 0, "error": "", "warnings": []}
            args=(room,executable,True,idle_check) if existing_only else (room,executable)
            self.worker = threading.Thread(target=self._build, args=args, daemon=True)
            self.worker.start()
        return self.status()

    def _build(self, room, executable, existing_only=False,idle_check=None):
        try:
            if existing_only:
                if self.engine.status()['state']!='ready' or not self.identity:raise ValueError('起動済みの記憶検索モデルを確認できません。自動起動しません。')
            else:self.start(executable)
            deadline = time.monotonic() + 300
            while self.engine.status()["state"] == "loading":
                if self.cancel.wait(.1):
                    return
                if time.monotonic() > deadline:
                    raise ValueError("検索モデルの準備がタイムアウトしました。")
            if self.cancel.is_set():
                return
            if self.engine.status()["state"] != "ready":
                raise ValueError("検索モデルを起動できませんでした。ログを確認してください。")
            identity = self.identity
            documents, warnings = self.corpus(room)
            if self.additional_corpus is not None:
                additional,notes=self.additional_corpus(room);documents.extend(additional);warnings.extend(notes)
            bodies = list(dict.fromkeys(doc["text"] for doc in documents))
            with self.lock:
                self.progress.update(total=len(bodies), warnings=warnings[:10])
            with self.connect() as db:
                for i, body in enumerate(bodies):
                    while idle_check is not None and not idle_check():
                        if self.cancel.wait(.2):return
                    if self.cancel.is_set():
                        return
                    key = digest(body)
                    if not db.execute("SELECT 1 FROM vectors WHERE model=? AND body=?", (identity, key)).fetchone():
                        vector = self.embed([body])[0]
                        if self.cancel.is_set():
                            return
                        db.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?)",
                                   (identity, key, struct.pack('<%sf' % len(vector), *vector)))
                        db.commit()
                    with self.lock:
                        self.progress["done"] = i + 1
            with self.lock:
                self.progress["state"] = "ready"
        except (OSError, ValueError, sqlite3.Error, http.client.HTTPException) as error:
            with self.lock:
                self.progress.update(state="error", error=str(error))
        finally:
            if self.cancel.is_set():
                with self.lock:
                    self.progress["state"] = "idle"

    def search(self, room, query, scope="all", exclude=(), before=None, offset=0, limit=3,score_adjuster=None,threshold_adjuster=None):
        check_page(offset,limit)
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            raise ValueError("検索語は2～200文字で入力してください。")
        if scope not in ("all", "memory", "conversation", "knowledge", "memory+conversation"):
            raise ValueError("検索対象が不正です。")
        with self.lock:
            if self.progress["state"] == "building":
                raise ValueError("索引作成中です。完了を待って検索してください。")
            if not self.identity or self.engine.status()["state"] != "ready":
                raise ValueError("検索モデルの準備ができていません。索引作成・更新を実行してください。")
            documents, warnings = self.corpus(room)
            excluded = [normalize(t.strip()) for t in exclude if isinstance(t, str) and t.strip()]
            results, seen, missing, searched, q = [], set(), 0, 0, None
            with self.connect() as db:
                for document in documents:
                    if before is not None and document["category"]=="conversation" and ("message_id" not in document or document["message_id"]>=before):continue
                    if scope == "memory+conversation" and document["category"] == "knowledge":
                        continue
                    if scope not in ("all", "memory+conversation") and document["category"] != scope:
                        continue
                    text = document["normalized"]
                    if text in seen or any(text in old for old in excluded):
                        continue
                    seen.add(text)
                    row = db.execute("SELECT vector FROM vectors WHERE model=? AND body=?", (self.identity, digest(document["text"]))).fetchone()
                    if not row:
                        missing += 1
                        continue
                    raw = row[0]
                    if q is None:
                        q = self.embed([query], query=True)[0]
                    searched += 1
                    score = cached_cosine(raw,q)
                    threshold=threshold_adjuster(document,self.loaded_settings["threshold"]) if threshold_adjuster else self.loaded_settings["threshold"]
                    if score >= threshold:
                        result = {k:v for k,v in document.items() if k not in ("text", "normalized")}
                        result.update(score=score, eligibility_threshold=threshold, excerpt=document["text"][:400], excerpt_offset=0,reference=document_reference(document))
                        results.append(result)
            if missing:
                warnings.append(f"未索引または更新された断片が{missing}件あります。索引作成・更新を実行してください。")
            if score_adjuster:score_adjuster(results)
            return ranked_page(results,METHOD,query,scope,warnings,searched,offset,limit,self.identity)
