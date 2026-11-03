"""Copy bundled sample characters only into a directory-free characters folder."""
import json
import shutil
import tempfile
from pathlib import Path


def copy_initial_characters(data, characters, reject_links, prepare=None):
    data, characters = Path(data), Path(characters)
    if any(path.is_dir() for path in characters.iterdir()):
        return False
    drafts = data / 'CharacterDrafts'
    if not drafts.exists():
        # Preserve source-only/dev installs without bundled character resources.
        return False
    names = ('Assistant', 'Akari')
    for name in names:
        source = drafts / name
        if not source.is_dir():
            raise ValueError('初回配置用のCharacterDrafts/' + name + 'が見つかりません。')
        reject_links(source)
        config = json.loads((source / 'room_config.json').read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('初回配置用room_config.jsonの形式が不正です。')
        if (characters / name).exists():
            raise ValueError('初回配置先に同名のファイルが存在します: ' + name)
    # Complete both copies before publishing either room; never merge/overwrite.
    with tempfile.TemporaryDirectory(prefix='.initial-characters-', dir=data) as temp:
        stage = Path(temp)
        for name in names:
            shutil.copytree(drafts / name, stage / name)
        if any(path.is_dir() for path in characters.iterdir()):
            return False
        if prepare is not None:prepare(stage,names)
        published = []
        try:
            for name in names:
                (stage / name).rename(characters / name)
                published.append(name)
        except OSError:
            for name in reversed(published):
                (characters / name).rename(stage / name)
            raise
    return True
