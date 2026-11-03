"""Cancellable child-process boundary around ddgs' global HTTP/2 patch."""
import json
import subprocess
import sys
import time
from pathlib import Path
from .public_http import RemoteError


def search_response(query, checkpoint=lambda: None):
    app = Path(__file__).resolve().parents[1]
    python = app / 'runtime/python.exe'
    if not python.is_file():
        python = Path(sys.executable)
    checkpoint()
    try:
        child = subprocess.Popen(
            [str(python), '-I', '-B', str(Path(__file__).with_name('ddg_worker.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except OSError as error:
        raise RemoteError('DuckDuckGo検索プロセスを起動できませんでした。') from error
    try:
        deadline = time.monotonic() + 13
        payload = json.dumps({'query': query}, ensure_ascii=False).encode('utf-8')
        while True:
            checkpoint()
            if time.monotonic() >= deadline:
                raise RemoteError('DuckDuckGo検索の待機時間を超えました。自動再試行はしません。')
            try:
                output, _ = child.communicate(payload, timeout=.1)
                break
            except subprocess.TimeoutExpired:
                payload = None
        checkpoint()
        if child.returncode or len(output) > 8 * 1048576:
            raise RemoteError('DuckDuckGo検索プロセスが正常に完了しませんでした。')
        try:
            data = json.loads(output)
            if 'error' in data:
                raise RemoteError(data['error'])
            if not isinstance(data['status'], int) or not isinstance(data['html'], str):
                raise ValueError()
            return data['status'], data['html'], 'https://html.duckduckgo.com/html/'
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, RemoteError):
                raise
            raise RemoteError('DuckDuckGo検索プロセスの応答を確認できませんでした。') from error
    finally:
        # Only this operation's own isolated child; never another app/server.
        if child.poll() is None:
            child.kill()
        child.communicate()
