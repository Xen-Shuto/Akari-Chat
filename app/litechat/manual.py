"""Read only the packaged manual; never expose arbitrary workspace files."""
import re
from pathlib import Path

MAX_BYTES = 1024 * 1024


def document(install, name='MANUAL.md'):
    if not isinstance(name, str) or not re.fullmatch(r'(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.md', name):
        raise ValueError('マニュアルの指定が不正です。')
    root = Path(install) / 'Docs' / 'manual'
    path = root / name
    # Inspect every component below the installation, including Docs/manual.
    current = Path(install)
    for part in ('Docs', 'manual', *name.split('/')):
        current = current / part
        try:
            stat = current.lstat()
        except FileNotFoundError:
            raise FileNotFoundError('マニュアルが見つかりません。') from None
        if current.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
            raise ValueError('リンクされたマニュアルは開けません。')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('マニュアルの指定が不正です。')
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('マニュアルが大きすぎます。')
        text = raw.decode('utf-8-sig')
    except (OSError, UnicodeError):
        raise ValueError('マニュアルを読み込めません。') from None
    return {'path': name, 'text': text}
