"""Offline update transaction, run only by the supervisor after its worker exits."""
import hashlib
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath
from .file_editor import safe_path
from .instance import InstanceLock


def digest(path):
    if not Path(path).is_file():return None
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def managed(name):
    parts=PurePosixPath(name).parts
    if not parts or '\\' in name or any(p in ('.','..') or ':' in p or p.endswith((' ','.')) for p in parts):
        raise ValueError('更新ファイルのパスが不正です。')
    if name.startswith('/') or any(p.lower() in ('.git','__pycache__') for p in parts):return False
    if Path(name).suffix.lower() in ('.gguf','.pyc','.pyo'):return False
    lower=name.lower()
    if lower.startswith('app/web/splash.'):return False
    return (name in ('app/app.py','app/launch.py','app/VERSION.txt','README.md','LICENSE','CHANGELOG.md','NOTICE','THIRD_PARTY_NOTICES.md')
            or name.startswith(('app/litechat/','app/tools/')) and name.endswith('.py')
            or name.startswith(('app/web/','Docs/','app/data/CharacterDrafts/')))


def update_root(install):
    root=safe_path(Path(install),'app/data/updates/.anchor',create=True).parent
    return root


def load_plan(root, pointer):
    key=pointer.get('id','')
    if not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('更新IDが不正です。')
    path=safe_path(root,key+'/plan.json')
    if digest(path)!=pointer.get('hash'):raise ValueError('更新計画の照合に失敗しました。')
    plan=json.loads(path.read_text('utf8'))
    if not plan.get('files') or not isinstance(plan['files'],list):raise ValueError('更新内容がありません。')
    seen=set()
    for entry in plan['files']:
        name=entry['path']
        if not managed(name) or name.lower() in seen:raise ValueError('更新対象が不正または重複しています。')
        seen.add(name.lower())
    if 'app/version.txt' not in seen:raise ValueError('更新バージョンがありません。')
    return root/key,plan


def replace_file(source, target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name('.akari-update-'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as out:
            with source.open('rb') as stream:
                import shutil
                shutil.copyfileobj(stream,out)
            out.flush();os.fsync(out.fileno())
        os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)


def restore(install, folder, journal):
    # Verify *all* backups before attempting any recovery.
    for entry in journal['files']:
        if not managed(entry['path']):raise ValueError('復元対象が不正です。')
        backup=safe_path(folder,'backup/'+entry['path'])
        if entry['before'] is not None and digest(backup)!=entry['before']:
            raise ValueError('退避ファイルを照合できません。自動復元を停止しました。')
    for entry in reversed(journal['files']):
        target=safe_path(install,entry['path'],create=True)
        current=digest(target)
        if current not in (entry['before'],entry['hash']):
            raise ValueError('更新途中のファイルが別途変更されています。自動復元を停止しました。')
        if entry['before'] is None:target.unlink(missing_ok=True)
        else:replace_file(safe_path(folder,'backup/'+entry['path']),target)


def apply_pending(install):
    """Return False only if an interrupted/failed update cannot be safely recovered."""
    install=Path(install)
    root=update_root(install);pending=root/'pending.json'
    if not pending.exists():return True
    with InstanceLock(install/'app'):
        pointer=json.loads(pending.read_text('utf8'))
        folder,plan=load_plan(root,pointer)
        journal_path=folder/'journal.json'
        journal=json.loads(journal_path.read_text('utf8')) if journal_path.exists() else None
        result=dict(id=pointer['id'],version=plan['version'],state='failed',error='')
        try:
            if journal and journal['state']=='applying':
                restore(install,folder,journal)
                journal['state']='rolled_back';write_json(journal_path,journal)
                result['error']='前回の更新が中断されたため、退避した更新前のファイルへ戻しました。'
            elif journal and journal['state']=='complete':
                result.update(state='complete',error='')
            elif journal and journal['state']=='unknown':
                raise ValueError('前回の復元結果が未確認です。退避先とコンソールを確認してください。')
            elif journal and journal['state'] in ('rolled_back','failed'):
                result['error']='前回の更新は適用されていません。更新前のファイルを保持しています。'
            else:
                entries=sorted(plan['files'],key=lambda e:e['path']=='app/VERSION.txt')
                for entry in entries:
                    target=safe_path(install,entry['path'])
                    if target.exists() and not target.is_file():raise ValueError('更新先がファイルではありません。')
                    if digest(target)!=entry['before']:raise ValueError('準備後に更新対象が変更されました。再確認してください。')
                    if digest(safe_path(folder,'stage/'+entry['path']))!=entry['hash']:
                        raise ValueError('取得ファイルの照合に失敗しました。')
                # Back up and verify all originals before replacing the first file.
                for entry in entries:
                    if entry['before'] is not None:
                        backup=safe_path(folder,'backup/'+entry['path'],create=True)
                        replace_file(safe_path(install,entry['path']),backup)
                        if digest(backup)!=entry['before']:raise ValueError('更新前の退避に失敗しました。')
                journal=dict(state='applying',files=entries)
                write_json(journal_path,journal)
                for entry in entries:
                    target=safe_path(install,entry['path'],create=True)
                    if digest(target)!=entry['before']:raise ValueError('適用中に更新先が変更されました。')
                    replace_file(safe_path(folder,'stage/'+entry['path']),target)
                    if digest(target)!=entry['hash']:raise ValueError('適用後のファイル照合に失敗しました。')
                write_json(journal_path,dict(journal,state='complete'))
                journal['state']='complete'
                result.update(state='complete')
        except Exception as error:
            result['error']=str(error)[:500]
            if journal and journal['state']=='applying':
                try:
                    restore(install,folder,journal)
                    journal['state']='rolled_back';write_json(journal_path,journal)
                except Exception as failure:
                    journal['state']='unknown';write_json(journal_path,journal)
                    result.update(state='unknown',error='自動復元を確認できません: '+str(failure)[:300])
            elif journal and journal['state']=='unknown':result['state']='unknown'
            elif journal is None:
                write_json(journal_path,dict(state='failed',files=[]))
        write_json(root/'last.json',result)
        if result['state']=='unknown':return False
        pending.unlink()
        print('アップデート: '+('完了しました。' if result['state']=='complete' else result['error']),flush=True)
        return True
