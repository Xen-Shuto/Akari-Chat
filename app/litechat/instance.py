"""OS-owned lock: stale files never prevent restart after a crash."""
import os
from pathlib import Path


class InstanceBusy(ValueError):
    pass


class InstanceLock:
    def __init__(self, app_dir):
        self.path = Path(app_dir) / '.akari.lock'
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        try:
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise InstanceBusy('このAkari-Chatは既に起動中、またはバックアップ操作中です。先に終了してください。') from error
        except BaseException:
            stream.close()
            raise
        self.stream = stream
        return self

    def __exit__(self, *args):
        if self.stream:
            # Closing releases the OS lock, including after abnormal termination.
            self.stream.close()
            self.stream = None
        # Keep the inode: deleting it can allow two locks on POSIX.
