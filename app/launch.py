"""Prefer the bundled runtime, with a development fallback if writing is denied."""
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    try:
        data = root / "data"
        data.mkdir(exist_ok=True)
        probe = data / ".startup-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        fallback = shutil.which("python")
        if not fallback or Path(fallback).resolve() == Path(sys.executable).resolve():
            print("このフォルダへの書き込みを確認できませんでした。書き込み可能な場所へ配置してください。")
            return 1
        # Inherit the same console and wait, so the batch stays open on either path.
        print("同梱Pythonから書き込めないため、導入済みPythonで起動します。", flush=True)
        try:
            return subprocess.call([fallback, "-u", str(root / "app.py")] + sys.argv[1:], cwd=str(root))
        except KeyboardInterrupt:
            return 0
    from litechat.lifecycle import supervise
    return supervise()


if __name__ == "__main__":
    sys.exit(main())
