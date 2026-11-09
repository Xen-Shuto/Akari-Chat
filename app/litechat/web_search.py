"""Shared provider settings, search budgets and API response parsing."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.external_secrets import protect
from .file_editor import EditConflict
from .public_http import RemoteError, RemoteHTTPError, parsed_url

PROVIDERS = {
    'duckduckgo': ('DuckDuckGo', 'duckduckgo-html-only'),
    'google_grounding': ('Google検索（Gemini）', 'gemini-grounding-google-search'),
    'tavily': ('Tavily', 'tavily-search'),
}
GOOGLE_MODEL = 'gemini-2.5-flash-lite'
GOOGLE_BILLING_MODEL = 'gemini-3.5-flash-lite'
GOOGLE_MODELS = (GOOGLE_MODEL, GOOGLE_BILLING_MODEL)
LIMITS = {'google_grounding': 200, 'tavily': 1000}


class SearchError(RemoteError):
    def __init__(self, message, reason, http_status=None):
        super().__init__(message)
        self.reason, self.http_status = reason, http_status


def period(provider, timestamp):
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    if provider == 'tavily':
        return moment.strftime('%Y-%m')
    try:
        return moment.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
    except ZoneInfoNotFoundError:
        # Windows installations without tzdata use the current US Pacific DST rules.
        march = datetime(moment.year, 3, 8, 10, tzinfo=timezone.utc)
        november = datetime(moment.year, 11, 1, 9, tzinfo=timezone.utc)
        start = march + timedelta(days=(6 - march.weekday()) % 7)
        end = november + timedelta(days=(6 - november.weekday()) % 7)
        return (moment - timedelta(hours=7 if start <= moment < end else 8)).date().isoformat()


class SearchSettings:
    def __init__(self, store, clock):
        self.store, self.clock = store, clock
        model_column = f"google_model TEXT NOT NULL DEFAULT '{GOOGLE_MODEL}'"
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(f'CREATE TABLE IF NOT EXISTS web_search_config(id INTEGER PRIMARY KEY,provider TEXT,tavily_key TEXT,revision TEXT,{model_column})')
            if 'google_model' not in {row['name'] for row in db.execute('PRAGMA table_info(web_search_config)')}:
                db.execute(f'ALTER TABLE web_search_config ADD COLUMN {model_column}')
            db.execute('CREATE TABLE IF NOT EXISTS web_search_usage(provider TEXT,period TEXT,count INTEGER,PRIMARY KEY(provider,period))')
            db.execute('CREATE TABLE IF NOT EXISTS ddg_search_pause(id INTEGER PRIMARY KEY,until REAL,strikes INTEGER)')

    @staticmethod
    def _get(db):
        row = db.execute('SELECT provider,tavily_key,revision,google_model FROM web_search_config WHERE id=1').fetchone()
        return dict(row) if row else dict(provider='duckduckgo', tavily_key='', revision='0', google_model=GOOGLE_MODEL)

    def get(self):
        with self.store.connect() as db:
            return self._get(db)

    def status(self):
        with self.store.connect() as db:
            saved = self._get(db)
            usage = {}
            for provider, limit in LIMITS.items():
                key = period(provider, self.clock())
                row = db.execute('SELECT count FROM web_search_usage WHERE provider=? AND period=?', (provider, key)).fetchone()
                usage[provider] = dict(period=key, count=row[0] if row else 0, limit=limit)
        return dict(provider=saved['provider'], revision=saved['revision'], tavily_key_saved=bool(saved['tavily_key']),
                    google_model=saved['google_model'], usage=usage, ddg_pause_until=self.ddg_pause())

    def save(self, provider, revision, tavily_key='', clear_key=False, google_model=None):
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ValueError('検索先を選択してください。')
        if (not isinstance(tavily_key, str) or len(tavily_key) > 512 or
                any(not 33 <= ord(c) <= 126 for c in tavily_key) or type(clear_key) is not bool or (tavily_key and clear_key)):
            raise ValueError('TavilyのAPIキーまたは消去設定が不正です。')
        if google_model is not None and google_model not in GOOGLE_MODELS:
            raise ValueError('Google検索モデルを一覧から選択してください。')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved = self._get(db)
            if revision != saved['revision']:
                raise EditConflict('Web検索設定が変更されています。再読込してください。')
            key = '' if clear_key else protect(tavily_key) if tavily_key else saved['tavily_key']
            model = saved['google_model'] if google_model is None else google_model
            db.execute('INSERT OR REPLACE INTO web_search_config(id,provider,tavily_key,revision,google_model) VALUES(1,?,?,?,?)',
                       (provider, key, uuid.uuid4().hex, model))
        return self.status()

    def reserve(self, provider):
        key = period(provider, self.clock())
        limit = LIMITS[provider]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cursor = db.execute('INSERT INTO web_search_usage VALUES(?,?,1) ON CONFLICT(provider,period) '
                                'DO UPDATE SET count=count+1 WHERE count<?', (provider, key, limit))
            if not cursor.rowcount:
                unit = '本日（太平洋時間）' if provider == 'google_grounding' else '今月（UTC）'
                raise SearchError(f'{unit}のあかりの検索上限（{limit}回）に達しました。', 'local_limit')
            db.execute('DELETE FROM web_search_usage WHERE provider=? AND period<?', (provider, key))

    def ddg_pause(self):
        with self.store.connect() as db:
            row = db.execute('SELECT until FROM ddg_search_pause WHERE id=1').fetchone()
        return row[0] if row and row[0] > self.clock() else None

    def update_ddg_pause(self, challenge):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if challenge:
                row = db.execute('SELECT strikes FROM ddg_search_pause WHERE id=1').fetchone()
                strikes = row[0] + 1 if row else 1
                until = self.clock() + (1, 6, 24)[min(strikes, 3) - 1] * 3600
                db.execute('INSERT OR REPLACE INTO ddg_search_pause VALUES(1,?,?)', (until, strikes))
            else:
                db.execute('DELETE FROM ddg_search_pause WHERE id=1')


def api_search(provider, query, limit, key, fetch, checkpoint, reserve, *, google_model=GOOGLE_MODEL):
    if not key:
        setting = '共通設定の会話モデルにGemini' if provider == 'google_grounding' else 'Web検索設定にTavily'
        raise SearchError(f'{setting}のAPIキーを登録してください。', 'not_configured')
    if provider == 'google_grounding':
        endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{google_model}:generateContent'
        auth = {'x-goog-api-key': key}
        payload = {'contents': [{'parts': [{'text': '公開Webを検索して、検索結果を根拠に答えてください。\n' + query}]}],
                   'tools': [{'google_search': {}}]}
    else:
        endpoint = 'https://api.tavily.com/search'
        auth = {'Authorization': 'Bearer ' + key}
        payload = dict(query=query, search_depth='basic', max_results=limit, auto_parameters=False,
                       include_answer=False, include_raw_content=False)
    checkpoint()
    reserve(provider)
    try:
        raw, _, _ = fetch(endpoint, method='POST', body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={**auth, 'Content-Type': 'application/json', 'Accept-Encoding': 'identity'},
                          timeout=24, limit=2 * 1048576, checkpoint=checkpoint)
    except RemoteHTTPError as error:
        label = PROVIDERS[provider][0]
        if provider == 'google_grounding' and error.status == 404:
            hint = '2.5系は利用実績によるアクセス制限があります。' if google_model == GOOGLE_MODEL else '共通のGeminiキーで利用可能なモデルを確認してください。'
            raise SearchError(f'共通のGeminiキーでは検索モデル {google_model} を利用できません（HTTP 404）。{hint}',
                              'model_unavailable', 404) from error
        if error.status == 429 or (provider == 'tavily' and error.status in (432, 433)):
            hint = 'この検索モデルには課金を有効にしたGoogleプロジェクトが必要です。' if provider == 'google_grounding' and google_model == GOOGLE_BILLING_MODEL else ''
            raise SearchError(f'{label}の利用制限に達しました（HTTP {error.status}）。{hint}', 'quota_exceeded', error.status) from error
        raise SearchError(f'{label}の検索に失敗しました（HTTP {error.status}）。キー、モデル、利用条件を確認してください。',
                          'api_error', error.status) from error
    except RemoteError as error:
        raise SearchError('検索先との通信を完了できませんでした。', 'transport_error') from error
    checkpoint()
    try:
        response = json.loads(raw)
        if provider == 'tavily':
            results = response['results']
            if not isinstance(results, list):
                raise TypeError
            rows = search_rows((item['title'], item['url'], item.get('content', '')) for item in results)
            if results and not rows:
                raise ValueError
            return dict(results=rows[:limit])
        candidate = response['candidates'][0]
        if candidate.get('finishReason', 'STOP') != 'STOP':
            raise SearchError('Google検索の回答が完了しませんでした。', 'incomplete', 200)
        metadata = candidate.get('groundingMetadata') or {}
        answer = ''.join(part.get('text', '') for part in candidate['content']['parts'] if not part.get('thought')).strip()
        chunks = (item['web'] for item in metadata.get('groundingChunks', []) if item.get('web'))
        rows = search_rows((item.get('title', ''), item['uri'], '') for item in chunks)
        if not answer or not rows:
            raise SearchError('Google検索の根拠が確認できませんでした。', 'no_grounding', 200)
        return dict(answer=answer[:3600], results=rows[:limit], model=google_model)
    except SearchError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise SearchError('検索先の応答形式を確認できませんでした。', 'parse_error', 200) from error


def search_rows(items):
    rows, seen = [], set()
    for title, url, snippet in items:
        try:
            parsed_url(url)
        except RemoteError:
            continue
        if url not in seen:
            rows.append(dict(title=str(title or '')[:200], url=url, snippet=str(snippet or '')[:500]))
            seen.add(url)
    return rows
