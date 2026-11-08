"""Room-scoped text generation for memory work; embeddings remain independent."""
import time
from .file_editor import EditConflict
from .engine import GenerationLengthError


_LENGTH_MESSAGE = '記憶生成が長さの上限に達しました。未完了の文章は保存していません。'


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
    from .context_budget import prepare_memory, MemoryLengthError
    request = prepare_memory(app,room,generation,payload,job_id)
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
        reason=getattr(generation,'finish_reason',None)
        if not generation.cancelled.is_set() and reason in ('length','max_tokens','MAX_TOKENS'):
            raise MemoryLengthError(_LENGTH_MESSAGE)
        if not generation.cancelled.is_set() and (reason not in (None,'stop') or getattr(generation,'tool_calls',[])):
            raise ValueError('記憶生成が未完了です（終了理由: '+str(reason)+'）。原本は保持しました。')
    except GenerationLengthError as error:
        check()
        if generation.cancelled.is_set():
            raise
        raise MemoryLengthError(_LENGTH_MESSAGE) from error
    finally:
        app.diagnostics.trace('memory.response',room,job_id,finish_reason=getattr(generation,'finish_reason',None),
                              cancelled=generation.cancelled.is_set(),tool_call_count=len(getattr(generation,'tool_calls',[])))
        if target:
            app.conversation_api.record(room, job_id, 'memory-generation-result',
                                        getattr(generation, 'api_outcome', 'unknown'))
