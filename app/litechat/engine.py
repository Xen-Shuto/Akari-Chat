import http.client
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.request import ProxyHandler, build_opener

from .process_job import ProcessJob


class GenerationLengthError(ValueError):
    """The model reported a length limit on a fully received response."""

    def __init__(self):
        super().__init__('最大生成tokensまたはコンテキスト上限に達し、生成が途中で終了しました。')


def validate_settings(values):
    values={"top_p":0.95,**values}
    token_value=values.get('max_tokens')
    try:
        if isinstance(token_value,bool) or not isinstance(token_value,(str,int,float)):raise ValueError()
        token_number=Decimal(str(token_value).strip() or '0')
        if not token_number.is_finite():raise ValueError()
        if token_number>=32769:raise ValueError()
        values['max_tokens']=0 if token_number<0 else int(token_number)
    except (InvalidOperation,ValueError,TypeError,OverflowError):
        raise ValueError('max_tokensは数値で指定してください（空欄・負数は0、小数は切り捨て。最大32768）。') from None
    if isinstance(values["top_p"],bool):raise ValueError("top_pの数値が不正です。")
    clean = {}
    clean['thinking_mode']=values.get('thinking_mode','auto')
    if clean['thinking_mode'] not in ('auto','on','off'):raise ValueError('思考モードはモデルに任せる・有効・無効から選んでください。')
    clean["tool_calling"] = values.get("tool_calling", True)
    if not isinstance(clean["tool_calling"], bool):
        raise ValueError("ツール使用にはtrue/falseを指定してください。")
    for name in ("executable", "model", "mmproj", "mtp"):
        value = values.get(name, "")
        if not isinstance(value, str):
            raise ValueError(name + "にはパスを指定してください。")
        clean[name] = value.strip().strip('"')
    clean["auto_aux"] = values.get("auto_aux", True)
    if not isinstance(clean["auto_aux"],bool):
        raise ValueError("自動選択にはtrue/falseを指定してください。")
    clean["auto_start"] = values.get("auto_start", True)
    if not isinstance(clean["auto_start"],bool):
        raise ValueError("自動起動にはtrue/falseを指定してください。")
    clean["mtp_mode"] = values.get("mtp_mode", "off")
    if clean["mtp_mode"] not in ("off","embedded","external"):
        raise ValueError("MTP方式が不正です。")
    if clean["mtp_mode"] == "external" and not clean["mtp"]:
        raise ValueError("別ファイルMTPのパスを指定してください。")
    bounds = {"context": (512, 131072, int), "gpu_layers": (0, 999, int),
              "temperature": (0, 2, float), "top_p": (0, 1, float), "max_tokens": (0, 32768, int),
              "history_limit": (1, 200, int)}
    for name, (low, high, convert) in bounds.items():
        try:
            value = convert(values[name])
        except (KeyError, ValueError, TypeError, OverflowError):
            raise ValueError(name + "の数値が不正です。") from None
        if not low <= value <= high:
            raise ValueError(f"{name}は{low}〜{high}で指定してください。")
        clean[name] = value
    try:
        clean["draft_tokens"] = int(values.get("draft_tokens",2))
    except (TypeError,ValueError,OverflowError):
        raise ValueError("MTP先読み数が不正です。") from None
    if not 1 <= clean["draft_tokens"] <= 16:
        raise ValueError("MTP先読み数は1〜16で指定してください。")
    extra = values.get("extra_args", [])
    if not isinstance(extra, list) or any(not isinstance(arg, str) for arg in extra):
        raise ValueError("追加引数はJSON配列で指定してください。")
    # Ownership of the endpoint and model paths remains with this manager.
    forbidden = {"--host", "--port", "--model", "-m", "--alias", "--api-key", "--api-key-file",
                 "--models-dir", "--models-preset", "--models-max", "--model-url", "-mu",
                 "--hf-repo", "-hf", "--hf-file", "-hff", "--mmproj", "--ssl-key-file", "--ssl-cert-file"}
    forbidden.update({"--spec-draft-model","--model-draft","-md","--spec-type","--spec-draft-n-max"})
    if any(arg.split("=", 1)[0] in forbidden for arg in extra):
        raise ValueError("接続先・モデル・認証に関する追加引数は使用できません。専用欄を使ってください。")
    clean["extra_args"] = extra
    return clean


class Engine:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.process = None
        self.port = None
        self.state = "stopped"
        self.error = ""
        self.log = None
        self.job = None
        self.vision = False

    def report_startup_error(self, error):
        with self.lock:
            self.state = "error"
            self.error = "保存済みモデルを自動起動できませんでした: " + str(error)

    def status(self):
        with self.lock:
            if self.process and self.process.poll() is not None and self.state in ("loading", "ready"):
                self.state = "error"
                self.error = f"llama-serverが終了しました（コード{self.process.returncode}）。ログを確認してください。"
            return {"state": self.state, "error": self.error,
                    "vision": self.vision,
                    "pid": self.process.pid if self.process and self.process.poll() is None else None}

    def start(self, settings):
        settings = validate_settings(settings)
        with self.lock:
            if self.process and self.process.poll() is None:
                raise ValueError("モデルは起動済みです。変更する場合は先に停止してください。")
            for key in ("executable", "model"):
                if not Path(settings[key]).is_file():
                    raise ValueError(f"{key}のファイルが見つかりません。")
            if Path(settings["model"]).suffix.lower() != ".gguf":
                raise ValueError("GGUFモデルを指定してください。")
            if settings["mmproj"] and not Path(settings["mmproj"]).is_file():
                raise ValueError("mmprojが見つかりません。")
            if settings["mtp_mode"] == "external" and not Path(settings["mtp"]).is_file():
                raise ValueError("MTPファイルが見つかりません。")
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                self.port = reservation.getsockname()[1]
            args = [settings["executable"], "-m", settings["model"], "--host", "127.0.0.1",
                    "--port", str(self.port), "--alias", "Local_LLM", "-c", str(settings["context"]),
                    "-ngl", str(settings["gpu_layers"])]
            if settings["mmproj"]:
                args += ["--mmproj", settings["mmproj"]]
            if settings["mtp_mode"] != "off":
                args += ["--spec-type","draft-mtp","--spec-draft-n-max",str(settings["draft_tokens"])]
                if settings["mtp_mode"] == "external":
                    args += ["--model-draft",settings["mtp"]]
            args += settings["extra_args"]
            self.root.mkdir(parents=True, exist_ok=True)
            if self.log:
                self.log.close()
            if self.job:
                self.job.close()
                self.job = None
            self.log = (self.root / "llama-server.log").open("w", encoding="utf-8")
            # Clear llama environment overrides; saved settings should determine launches.
            env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_") and k != "LLAMA_API_KEY"}
            try:
                self.job = ProcessJob()
                self.process = subprocess.Popen(args, cwd=str(Path(args[0]).resolve().parent),
                    stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.job.assign(self.process)
            except OSError:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                if self.job:
                    self.job.close()
                    self.job = None
                self.log.close()
                self.log = None
                self.process = None
                raise
            self.state, self.error = "loading", ""
            self.vision = False
            process, port = self.process, self.port
            threading.Thread(target=self._wait_ready, args=(process, port), daemon=True).start()
        return self.status()

    def _wait_ready(self, process, port):
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            with self.lock:
                if self.process is not process or self.state != "loading":
                    return
                if process.poll() is not None:
                    self.status()
                    return
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    healthy = response.status == 200 and json.load(response).get("status") == "ok"
                if healthy:
                    vision=False
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/props",timeout=2) as response:
                            vision=json.load(response).get("modalities",{}).get("vision") is True
                    except (OSError,ValueError,AttributeError):
                        pass
                    with self.lock:
                        if self.process is process and process.poll() is None and self.state == "loading":
                            self.state = "ready"
                            self.vision = vision
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.4)
        with self.lock:
            if self.process is process and self.state == "loading":
                self.stop()
                self.state, self.error = "error", "モデルの準備が5分以内に完了しませんでした。ログを確認してください。"

    def stop(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            if self.log:
                self.log.close()
                self.log = None
            if self.job:
                self.job.close()
                self.job = None
            self.process, self.port = None, None
            self.state, self.error = "stopped", ""
            self.vision = False
        return self.status()


class Generation:
    def __init__(self, thinking_mode='auto'):
        if thinking_mode not in ('auto','on','off'):raise ValueError('思考モードの設定が不正です。')
        self.thinking_mode=thinking_mode
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.socket = None

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def stream(self, port, payload):
        if payload.get('max_tokens')==0:
            payload={k:v for k,v in payload.items() if k!='max_tokens'}
        if not isinstance(port,(int,type(None))):
            from .api_generation import stream
            yield from stream(self,port,payload)
            return
        if self.thinking_mode!='auto':
            kwargs=payload.get('chat_template_kwargs',{})
            if not isinstance(kwargs,dict):raise ValueError('チャットテンプレートの設定が不正です。')
            payload=dict(payload,chat_template_kwargs={**kwargs,'enable_thinking':self.thinking_mode=='on'})
        self.assistant_message=None
        self.tool_calls = []
        self.finish_reason = None
        calls = {}
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
        try:
            connection.connect()
            with self.lock:
                self.socket = connection.sock
                if self.cancelled.is_set():
                    return
            connection.request("POST", "/v1/chat/completions", body=json.dumps(payload).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("LLMエラー: " + response.read(4096).decode("utf-8", errors="replace"))
            done = False
            while not self.cancelled.is_set():
                line = response.readline()
                if not line:
                    break
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                event = json.loads(data)
                if event.get("error"):
                    raise ValueError(str(event["error"]))
                choices = event.get("choices", [])
                if choices:
                    if choices[0].get('finish_reason') is not None:
                        self.finish_reason = choices[0]['finish_reason']
                    delta = choices[0].get("delta", {})
                    for piece in delta.get("tool_calls", []):
                        index = piece.get("index")
                        if type(index) is not int:
                            raise ValueError("ツール呼び出し番号が不正です。")
                        if not 0 <= index < 4:
                            raise ValueError("ツール呼び出し数が上限を超えました。")
                        call = calls.setdefault(index,{"id":"","type":"function","function":{"name":"","arguments":""}})
                        if piece.get("id"): call["id"] = piece["id"]
                        for key in ("name","arguments"):
                            part=piece.get("function",{}).get(key, "")
                            if not isinstance(part,str): raise ValueError("ツール引数が不正です。")
                            call["function"][key] += part
                            if len(call["function"][key])>16000: raise ValueError("ツール引数が長すぎます。")
                    content = choices[0].get("delta", {}).get("content")
                    if isinstance(content, str) and content:
                        yield content
            if not done and not self.cancelled.is_set():
                raise ValueError("LLMとの接続が応答完了前に切れました。")
            if self.finish_reason == 'length' and not self.cancelled.is_set():
                raise GenerationLengthError()
            self.tool_calls = [calls[k] for k in sorted(calls)]
            ids=[c['id'] for c in self.tool_calls]
            if any(not isinstance(key,str) or not 0<len(key)<=256 for key in ids):
                raise ValueError("ツール呼び出しIDが不正です。")
            if len(set(ids))!=len(ids):
                raise ValueError("同じ応答内でツール呼び出しIDが重複しています。実行しませんでした。")
        finally:
            with self.lock:
                self.socket = None
            connection.close()
