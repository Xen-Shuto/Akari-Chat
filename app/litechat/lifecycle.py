"""Keep the console open and replace the app worker after a clean restart request."""
import socket
import subprocess
import sys
from pathlib import Path
from .process_job import ProcessJob

RESTART_EXIT = 75
UPDATE_EXIT = 76


def supervise(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    # Pin an ephemeral port once, so the existing browser can reconnect after restart.
    for i,value in enumerate(arguments):
        if value=='--port=0' or value=='--port' and i+1<len(arguments) and arguments[i+1]=='0':
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            if value=='--port=0':arguments[i]=f'--port={port}'
            else:arguments[i+1]=str(port)
            break
    root=Path(__file__).resolve().parents[1]
    from .update_files import apply_pending
    while True:
        try:
            if not apply_pending(root.parent):
                print('更新の復元結果が未確認です。app/data/updatesの退避先と履歴を確認してください。',flush=True)
                return 1
        except (OSError,ValueError) as error:
            print('更新を安全に適用できませんでした: '+str(error),flush=True)
            return 1
        owner=ProcessJob();child=None
        try:
            child=subprocess.Popen([sys.executable,'-u',str(root/'app.py'),'--akari-worker',*arguments],cwd=root)
            try:owner.assign(child)
            except OSError:
                child.terminate();child.wait(timeout=5);raise
            try:code=child.wait()
            except KeyboardInterrupt:
                # Ctrl+C reaches the child in the same console; allow its normal cleanup first.
                try:child.wait(timeout=25)
                except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
                return 0
        finally:owner.close()
        if code not in (RESTART_EXIT,UPDATE_EXIT):return code
        print('アプリを再起動しています。',flush=True)
        arguments=[value for value in arguments if value != '--startup-splash']
        if '--no-browser' not in arguments:arguments.append('--no-browser')
