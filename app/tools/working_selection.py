"""Opt-in per-run topic selection from existing activity links; never edit originals."""
import copy
import json
import os
import unicodedata
import uuid
import time
import sqlite3
import http.client
from litechat.file_editor import EditConflict
from litechat.retrieval import chunks
from litechat.semantic import digest, cached_cosine


def folded(value):
    return unicodedata.normalize('NFKC', value).casefold().strip()


class WorkingSelection:
    def __init__(self, app):
        self.app, self.store = app, app.store
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS working_selection_settings(room TEXT PRIMARY KEY, enabled INTEGER NOT NULL, revision TEXT NOT NULL)')
            if 'semantic' not in {r[1] for r in db.execute('PRAGMA table_info(working_selection_settings)')}:
                db.execute('ALTER TABLE working_selection_settings ADD COLUMN semantic INTEGER NOT NULL DEFAULT 0')

    def settings(self, room):
        room = self.store.room_path(room).resolve().name
        with self.store.connect() as db:
            row = db.execute('SELECT enabled,revision,semantic FROM working_selection_settings WHERE room=?', (room,)).fetchone()
        return dict(enabled=bool(row[0]), revision=row[1], semantic=bool(row[2])) if row else dict(enabled=False, revision='unconfigured', semantic=False)

    def save(self, room, enabled, revision, semantic=None):
        room = self.store.room_path(room).resolve().name
        if type(enabled) is not bool:
            raise ValueError('自動選択にはtrue/falseを指定してください。')
        if semantic is not None and type(semantic) is not bool:
            raise ValueError('意味による選択にはtrue/falseを指定してください。')
        with self.store.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT enabled,revision,semantic FROM working_selection_settings WHERE room=?', (room,)).fetchone()
            if revision != (row[1] if row else 'unconfigured'):
                raise EditConflict('話題の自動選択設定が変わりました。再読込してください。')
            semantic = semantic if semantic is not None else bool(row[2]) if row else False
            if row and bool(row[0]) == enabled and bool(row[2]) == semantic:
                return dict(enabled=enabled, revision=row[1], semantic=semantic)
            current = uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO working_selection_settings(room,enabled,revision,semantic) VALUES(?,?,?,?)', (room, int(enabled), current, int(semantic)))
        return dict(enabled=enabled, revision=current, semantic=semantic)

    def corpus(self, room):
        """Additional indexing corpus; explicit index rebuild only, no auto inference."""
        state = self.app.slots.inventory(room)
        documents, warnings = [], list(state['warnings'])
        total = 0
        for slot in state['slots']:
            if not slot['exists']:
                continue
            if len(documents) >= 3000:
                warnings.append('話題の索引上限3000断片に達しました。')
                break
            doc = self.app.slots.preview(room, slot['name'])
            total += len(doc['text'])
            if total > 2000000:
                warnings.append('話題の索引本文上限200万字に達しました。')
                break
            for part in chunks(doc['text'], {'path': doc['path']}, 'memory'):
                if len(documents) >= 3000:
                    warnings.append('話題の索引上限3000断片に達しました。')
                    break
                documents.append(part)
        return documents, warnings

    def semantic_candidates(self, candidates, query, decision):
        """Use only current cached topic vectors, with one bounded query embedding.

        Lock acquisition must not wait while holding the store lock: index search
        acquires these in the opposite order. Busy models simply keep main.
        """
        engine = self.app.semantic
        decision.update(method='semantic', embedding_calls=0, embedding_seconds=0., missing_chunks=0)
        if not 2 <= len(query.strip()) <= 200:
            decision['reason'] = '意味選択の対象は2～200文字の発言です'
            return []
        if not engine.lock.acquire(blocking=False):
            decision['reason'] = '記憶検索モデルが使用中のため手動の話題を維持しました'
            return []
        try:
            if engine.progress['state'] == 'building' or not engine.identity or engine.engine.status()['state'] != 'ready':
                decision['reason'] = '記憶検索モデルまたは索引が未準備です'
                return []
            cached = {}; count = 0
            with engine.connect() as db:
                for _, _, doc in candidates:
                    path = os.path.normcase(doc['path'])
                    if path in cached:
                        continue
                    vectors = []
                    for part in chunks(doc['text'], {}, 'memory'):
                        count += 1
                        if count > 3000:
                            decision['reason'] = '意味選択の断片上限を超えるため手動の話題を維持しました'
                            return []
                        row = db.execute('SELECT vector FROM vectors WHERE model=? AND body=?', (engine.identity, digest(part['text']))).fetchone()
                        if not row:
                            decision['missing_chunks'] += 1
                        else:
                            vectors.append(row[0])
                    cached[path] = vectors
            # Partial coverage could hide a better candidate. Never silently select
            # from a biased subset after another topic was changed or left unindexed.
            if decision['missing_chunks'] or not cached:
                decision['reason'] = '候補の索引が不足または更新済みです。索引を更新してください'
                return []
            decision['embedding_calls'] = 1
            start = time.perf_counter()
            try:
                vector = engine.embed([query], query=True)[0]
            finally:
                decision['embedding_seconds'] = time.perf_counter() - start
            ranked = sorted(((max(cached_cosine(raw, vector) for raw in vectors), path) for path, vectors in cached.items()), reverse=True)
            if not ranked or ranked[0][0] < engine.loaded_settings['threshold']:
                decision['reason'] = '意味の類似度が下限未満のため手動の話題を維持しました'
                return []
            if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.05:
                decision['reason'] = '意味の近い候補が複数あるため手動の話題を維持しました'
                return []
            decision.update(score=ranked[0][0], model=engine.identity)
            return [c for c in candidates if os.path.normcase(c[2]['path']) == ranked[0][1]]
        except (ValueError, OSError, sqlite3.Error, http.client.HTTPException, IndexError):
            decision['reason'] = '意味選択に失敗したため手動の話題を維持しました'
            return []
        finally:
            engine.lock.release()

    def apply(self, character, query='', activity_id='', background=False):
        room = self.store.room_path(character['id']).resolve().name
        policy = self.settings(room)
        original = character['memory_sources']['working']
        decision = dict(selected=False, slot=original['slot'], policy_revision=policy['revision'], reason='自動選択はオフです')
        if not policy['enabled']:
            return character, decision
        if original['slot'] != 'main':
            decision['reason'] = '手動で選択した個別の話題を優先しました'
            return character, decision
        # Hold application-managed writers across the read snapshot; recheck external
        # file changes below. Semantic model lock acquisition never waits here.
        with self.store.lock:
            snapshot, _ = self.app.slots.config(room)
            with self.store.connect() as db:
                rows = db.execute('SELECT payload FROM autonomy_documents WHERE room=? ORDER BY id LIMIT 1001', (room,)).fetchall()
            if len(rows) > 1000:
                decision['reason'] = '活動記録の上限を超えるため手動の話題を維持しました'
                return character, decision
            candidates = []
            documents = {}
            characters = 0
            original_query = query
            query = folded(query[:100000])
            semantic = policy['semantic'] and not background and not activity_id
            lexical = []
            for row in rows:
                item = json.loads(row[0])
                if item['state'] != 'active' or item['kind'] not in ('thread', 'goal', 'question'):
                    continue
                if activity_id:
                    if item['id'] != activity_id:
                        continue
                elif not background:
                    title = folded(item['title'])
                    if (len(title) < 2 or title not in query) and not semantic:
                        continue
                names = item.get('details', {}).get('working_slots', [])
                available = []
                for name in names[:3]:
                    identity = os.path.normcase(name)
                    if identity not in documents:
                        if len(documents) >= 100 or characters >= 2000000:
                            decision['reason'] = '候補確認の上限に達したため手動の話題を維持しました'
                            return character, decision
                        documents[identity] = self.app.slots.preview(room, name)
                        characters += len(documents[identity]['text'])
                        if characters > 2000000:
                            decision['reason'] = '候補本文の上限を超えるため手動の話題を維持しました'
                            return character, decision
                    doc = documents[identity]
                    if doc['exists'] and doc['state'] != 'dormant' and doc['text'].strip():
                        available.append(doc)
                # Multiple linked topics need a human decision, not arbitrary first-match.
                if len(available) != 1:
                    continue
                candidates.append((item.get('details', {}).get('priority', 0), item, available[0]))
                if len(folded(item['title'])) >= 2 and folded(item['title']) in query:
                    lexical.append(candidates[-1])
            if semantic and candidates:
                if lexical:
                    candidates = lexical
                    decision['method'] = 'title'
                else:
                    candidates = self.semantic_candidates(candidates, original_query, decision)
            if not candidates:
                if decision.get('method') != 'semantic':
                    decision['reason'] = '対応する活動と使用可能な話題がないため手動の話題を維持しました'
                return character, decision
            highest = max(c[0] for c in candidates)
            top = [c for c in candidates if c[0] == highest]
            if len({os.path.normcase(c[2]['path']) for c in top}) != 1:
                decision['reason'] = '同じ優先度に複数の話題があるため手動の話題を維持しました'
                return character, decision
            _, item, doc = top[0]
            if os.path.normcase(doc['slot']) == 'main':
                decision['reason'] = '活動に対応する話題は選択中のmainです'
                return character, decision
            latest = self.app.slots.preview(room, doc['slot'])
            if decision.get('method') == 'semantic':
                for candidate in documents.values():
                    if self.app.slots.preview(room, candidate['slot'])['state_revision'] != candidate['state_revision']:
                        raise EditConflict('意味選択の候補本文または状態が変更されました。再実行してください。')
            with self.store.connect() as db:
                latest_rows = db.execute('SELECT payload FROM autonomy_documents WHERE room=? ORDER BY id LIMIT 1001', (room,)).fetchall()
            if [r[0] for r in rows] != [r[0] for r in latest_rows]:
                raise EditConflict('活動記録が変更されました。再実行してください。')
            if latest['state_revision'] != doc['state_revision'] or self.app.slots.config(room)[0]['revision'] != snapshot['revision']:
                raise EditConflict('話題または選択設定が変更されました。再実行してください。')
            if self.settings(room)['revision'] != policy['revision']:
                raise EditConflict('話題の自動選択設定が変更されました。')
            # Also bind the room snapshot already loaded by the caller.
            current = self.store.room(room)['memory_sources']['working']
            if current != original:
                raise EditConflict('選択中の話題が変更されました。再読込してください。')
            result = copy.deepcopy(character)
            reason = ('本文の意味の近さと' if decision.get('method') == 'semantic' else '') + '活動「' + item['title'] + '」のリンクから、この実行だけ選択しました。参照・更新ツールではこのスロット名を明示してください。省略時は手動設定の話題が対象です'
            result['memory_sources']['working'] = dict(original, path=doc['path'], text=doc['text'].strip(), slot=doc['slot'],
                exists=True, encoding=doc['encoding'], selection_reason=reason, runtime_selection=True)
            decision.update(selected=True, slot=doc['slot'], path=doc['path'], revision=doc['revision'],
                            activity_id=item['id'], activity_revision=item['revision'], reason=reason)
            return result, decision
