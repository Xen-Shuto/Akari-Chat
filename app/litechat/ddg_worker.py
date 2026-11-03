"""One DuckDuckGo-only request in an isolated interpreter (no app imports)."""
import json
import sys
from pathlib import Path

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/135.0.0.0 Safari/537.36')


def fetch(query):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/web_packages'))
    from ddgs.engines.duckduckgo import Duckduckgo
    from importlib.metadata import version
    if version('ddgs') != '9.9.1':
        raise ValueError('Unsupported ddgs version')
    engine = Duckduckgo(timeout=10)
    try:
        response = engine.http_client.request(
            engine.search_method, engine.search_url,
            data=engine.build_payload(query=query, region='us-en', safesearch='moderate', timelimit=None, page=1),
            headers={**engine.search_headers, 'User-Agent': USER_AGENT},
        )
        if len(response.content) > 1048576:
            return {'error': '検索応答のサイズ上限を超えました。'}
        return {'status': response.status_code, 'html': response.text,
                'url': engine.search_url}
    finally:
        engine.http_client.client.close()


def main():
    try:
        data = json.loads(sys.stdin.buffer.read(16385).decode('utf-8'))
        query = data['query']
        if not isinstance(query, str) or not 1 <= len(query) <= 1000:
            raise ValueError('Invalid query')
        result = fetch(query)
    except Exception as error:
        # No query, response, proxy credentials or traceback in diagnostics.
        result = {'error': 'DuckDuckGo専用エンジンの実行に失敗しました（' + type(error).__name__ + '）。'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
