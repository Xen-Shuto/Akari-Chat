"""Recoverable configuration writes and installation-relative model paths."""
import json
import os
import time
import uuid
from pathlib import Path


def load(path, defaults, validate):
    path=Path(path)
    if not path.is_file():return dict(defaults), ''
    try:
        data=json.loads(path.read_text('utf-8-sig'))
        if not isinstance(data,dict):raise ValueError('JSONオブジェクトではありません。')
        merged={**defaults,**data}
        merged.update(validate(merged))
        return merged, ''
    except (OSError,UnicodeError,ValueError,TypeError) as error:
        safe=dict(defaults);safe['auto_start']=False
        if 'auto_semantic' in safe:safe['auto_semantic']=False
        return safe, f'{path.name}を読み込めませんでした。自動起動を止めて設定画面を開きます。原本は保持しています: {error}'


def portable(settings, install, keys):
    result=dict(settings);install=Path(install).resolve()
    for key in keys:
        value=result.get(key,'')
        if not isinstance(value,str) or not value:continue
        path=Path(value)
        if path.is_absolute():
            try:result[key]=path.resolve().relative_to(install).as_posix()
            except ValueError:pass
    return result


def save(path, settings):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    body=json.dumps(settings,ensure_ascii=False,indent=2).encode('utf8')
    if path.exists():
        backups=path.parent/'settings_backups';backups.mkdir(exist_ok=True)
        backup=backups/f'{path.name}.{time.time_ns()}.{uuid.uuid4().hex}.bak'
        with backup.open('xb') as stream:
            stream.write(path.read_bytes());stream.flush();os.fsync(stream.fileno())
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
