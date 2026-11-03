"""Room-scoped text generation for memory work; embeddings remain independent."""
import time
from .file_editor import EditConflict


def ready(app, room):
    return app.conversation_api.status(room)['state'] == 'ready'


def snapshot(app, room):
    doc = app.conversation_api.resolved(room)
    settings = app.store.settings()
    provider = doc['values']['provider']
    return [doc['revision'], provider,
            app.engine.status().get('pid') if provider == 'local' else doc['values'].get('model'),
            {key: settings.get(key) for key in ('model', 'mmproj', 'context', 'max_tokens', 'thinking_mode')}]


def bind_job(app, room, job):
    """Reuse the executor's existing revision guard, adapters, audit and cancellation."""
    if not ready(app, room):
        raise ValueError('このルームの会話接続を準備してください。')
    target = app.conversation_api.target(room)
    job['port'] = target or app.engine.port
    job['conversation_api_revision'] = target.revision if target else None
    if target:
        job['payload']['model'] = target.model
        job['display_model'] = target.model
    return job


def stream(app, room, generation, payload, *, expected=None, job_id=''):
    """Never retry or switch endpoints after a request. Check settings before saving."""
    expected = snapshot(app, room) if expected is None else expected
    if not ready(app, room):
        raise ValueError('このルームの会話接続を準備してください。')
    target = app.conversation_api.target(room)
    def check():
        if snapshot(app, room) != expected:
            raise EditConflict('生成中に会話接続またはモデル設定が変わりました。再送せず、原本を保持して停止します。')
    check()
    request = dict(payload)
    if target:
        request['model'] = target.model
        app.conversation_api.record(room, job_id, 'memory-generation', 'attempted')
    last = time.monotonic()
    try:
        for chunk in generation.stream(target or app.engine.port, request):
            if time.monotonic() - last >= .25:
                check()
                last = time.monotonic()
            yield chunk
        check()
    finally:
        if target:
            app.conversation_api.record(room, job_id, 'memory-generation-result',
                                        getattr(generation, 'api_outcome', 'unknown'))
