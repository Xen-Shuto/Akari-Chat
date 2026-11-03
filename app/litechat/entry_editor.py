"""Edit one NexusArk diary/note section or episode record with file-level revisions."""
import json
import re
from datetime import date, datetime

from .file_editor import EditConflict, normalize
from .resources import strict_json

KINDS = {"diary", "episode", "dream", "creative", "analysis"}
DREAM_FIELDS = ('trigger_topic', 'insight', 'log_entry')
DAY = r"\d{4}-\d{2}-\d{2}"
DIARY = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(?:\#{1,6}[ \t]*)?(?:\*{1,2}[ \t]*)?("+DAY+r")[^\r\n]*(?:\r\n|\n|\r|$)", re.M)
NOTE = re.compile(r"^[ \t]*(?:\#{1,6}[ \t]*)?(?:📝[ \t]*("+DAY+r")[ \t]+\d{2}:\d{2}|\[("+DAY+r")[ \t]+\d{2}:\d{2}\])", re.M)


def dates(value):
    if not isinstance(value, str):
        raise ValueError("エントリの日付が不正です。")
    parts = re.split(r"[~～]", value)
    if len(parts) > 2:
        raise ValueError("エントリの日付範囲が不正です。")
    first, last = date.fromisoformat(parts[0].strip()), date.fromisoformat(parts[-1].strip())
    if first > last:
        raise ValueError("エントリの日付範囲が逆順です。")
    return first.isoformat(), last.isoformat()


def section(text, start, end, day=""):
    body = text[start:end]
    left = len(body)-len(body.lstrip())
    right = len(body.rstrip())
    start, end = start+left, start+max(left, right)
    return {"text": text[start:end], "start": start, "end": end,
            "date": day, "end_date": day}


def text_entries(text, kind):
    matches = list((DIARY if kind == "diary" else NOTE).finditer(text))
    if not matches:
        return [section(text, 0, len(text))] if text.strip() else []
    # Keep separators outside the editable body, including leading file prose.
    boundaries = []
    for match in matches:
        start = match.start()
        if kind != "diary":
            separator = re.search(r"(?:^|\n)[ \t]*---+[ \t]*(?:\r?\n[ \t]*)*$", text[:start])
            if separator:
                start = separator.start()
        boundaries.append(start)
    result = []
    if text[:boundaries[0]].strip():
        result.append(section(text, 0, boundaries[0]))
    for index, match in enumerate(matches):
        day = next(value for value in match.groups() if value)
        dates(day)
        end = boundaries[index+1] if index+1 < len(matches) else len(text)
        result.append(section(text, match.end(), end, day))
    return result


def episode_entries(text, kind='episode'):
    records = strict_json(text)
    if not isinstance(records, list):
        raise ValueError("エピソードJSONが配列ではありません。")
    decoder, position, result = json.JSONDecoder(), text.index('[')+1, []
    for record in records:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        _, end = decoder.raw_decode(text, position)
        field='strategy' if kind=='dream' else 'summary'
        if not isinstance(record, dict) or not isinstance(record.get(field), str):
            raise ValueError("記録の"+field+"が不正です。")
        if kind=='dream':
            stamp=record.get('created_at','')
            if not isinstance(stamp,str) or any(not isinstance(record.get(key,''),str) for key in DREAM_FIELDS):
                raise ValueError('夢日記の日時または本文項目が不正です。')
            first=last=datetime.fromisoformat(stamp.replace('Z','+00:00')).date().isoformat()
        else:
            first,last=dates(record.get('date'));stamp=first
            if isinstance(record.get('akari_compression'),dict) and record['akari_compression'].get('version')==1:
                period_end=record.get('period_end',first)
                if not isinstance(period_end,str):raise ValueError('圧縮エピソードの終了日が不正です。')
                first,last=dates(first+'~'+period_end)
        result.append({"text": record[field], "date": first, "end_date": last,
                       "timestamp":stamp,"start": position, "end": end, "record": record})
        position = end
    return result


class EntryEditor:
    def __init__(self, resources):
        self.resources = resources

    def document(self, room, kind, name=""):
        if kind not in KINDS:
            raise ValueError("個別編集の対象ではありません。")
        if kind in ('episode','dream') and name == '@legacy':
            relative='memory/insights.json' if kind=='dream' else 'memory/episodic_memory.json'
            return self.resources.editor.read_snapshot(room, 'resource', '旧記録', relative)
        return self.resources.read(room, kind, name)

    def parse(self, document, kind):
        if not document['text'].strip():
            return []
        return episode_entries(document['text'],kind) if kind in ('episode','dream') else text_entries(document['text'], kind)

    def listing(self, room, kind):
        if kind not in KINDS:
            raise ValueError("個別編集の対象ではありません。")
        files = self.resources.listing(room, kind)
        if kind in ('episode','dream'):
            old = self.document(room, kind, '@legacy')
            if old['exists']:
                files.append({"name": '@legacy'})
        result, warnings = [], []
        for file in files:
            name = file['name']
            try:
                doc = self.document(room, kind, name)
                for index, entry in enumerate(self.parse(doc, kind)):
                    result.append({"id": str(index), "name": name, "date": entry['date'],
                                   "end_date": entry['end_date'], "preview": entry['text'][:100],
                                   "timestamp":entry.get('timestamp',entry['date']),"path": doc['path'], "revision": doc['revision']})
                    if len(result) > 20000:
                        raise ValueError("エントリ数が多すぎるため個別編集の一覧を作成できません。")
            except (ValueError, OSError) as error:
                warnings.append(str(name or kind)+": "+str(error))
        result.sort(key=lambda item: (item['timestamp'], item['path'], int(item['id'])), reverse=True)
        return {"entries": result, "warnings": warnings}

    def read(self, room, kind, name, key, expected=None):
        if not isinstance(key, str) or not re.fullmatch(r"0|[1-9]\d{0,5}", key):
            raise ValueError("エントリの指定が不正です。")
        doc = self.document(room, kind, name)
        if expected is not None and doc['revision'] != expected:
            raise EditConflict('記録が変更されました。一覧を取得し直してください。')
        entries = self.parse(doc, kind)
        index = int(key)
        if index >= len(entries):
            raise ValueError("エントリがありません。一覧を更新してください。")
        item = entries[index]
        result={"room": room, "kind": kind, "name": name, "id": key, "path": doc['path'],
                "revision": doc['revision'], "editable": doc['editable'], "text": item['text'],
                "date": item['date'], "end_date": item['end_date'],"timestamp":item.get('timestamp',item['date'])}
        if kind=='dream':result['fields']={key:item['record'].get(key,'') for key in DREAM_FIELDS}
        return result

    def prepare(self, room, kind, name, key, text, revision, fields=None):
        if not isinstance(text, str) or len(text) > 1024*1024:
            raise ValueError("エントリ本文が不正または長すぎます。")
        with self.resources.store.lock:
            selected = self.read(room, kind, name, key)
            if selected['revision'] != revision:
                raise EditConflict("読み込み後に元ファイルが変わりました。本文を控えて一覧を更新してください。")
            doc = self.document(room, kind, name)
            item = self.parse(doc, kind)[int(key)]
            fields_changed=False
            if kind=='dream':
                if fields is None:fields=selected['fields']
                if not isinstance(fields,dict) or set(fields)-set(DREAM_FIELDS) or any(not isinstance(value,str) or len(value)>1024*1024 for value in fields.values()):
                    raise ValueError('夢日記の編集項目が不正です。')
                fields_changed=any(normalize(value)!=normalize(item['record'].get(key,'')) for key,value in fields.items())
            if normalize(text) == normalize(item['text']) and not fields_changed:
                return {"entry": selected, "changed": False, "document": doc, "text": doc["text"]}
            if kind in ('episode','dream'):
                record = dict(item['record'])
                if normalize(text)!=normalize(item['text']):record['strategy' if kind=='dream' else 'summary'] = text
                if kind=='dream':
                    for field_key,value in fields.items():
                        if normalize(value)!=normalize(record.get(field_key,'')):record[field_key]=value
                replacement = json.dumps(record, ensure_ascii=False, indent=2)
            else:
                replacement = text
                # A new header inside the body would change section boundaries.
                pattern = DIARY if kind == 'diary' else NOTE
                if pattern.search(replacement):
                    raise ValueError("本文内に別エントリの日付見出しを追加できません。")
                if item['start'] and not item['text'] and doc['text'][item['start']-1] not in '\r\n':
                    replacement = '\n'+replacement
            whole = doc['text'][:item['start']]+replacement+doc['text'][item['end']:]
            return {"entry":selected,"document":doc,"text":whole,"changed":True}

    def save(self, room, kind, name, key, text, revision, fields=None):
        with self.resources.store.lock:
            prepared=self.prepare(room,kind,name,key,text,revision,fields)
            if not prepared['changed']:
                return {'entry':prepared['entry'],'changed':False,'backup':None}
            saved=self.resources.editor.save_snapshot(room,'resource',prepared['text'],revision,
                lambda:self.document(room,kind,name))
            return {'entry':self.read(room,kind,name,key),'changed':saved['changed'],'backup':saved['backup']}
