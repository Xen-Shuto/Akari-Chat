"""Shared preflight: input budgets, protected requests, and bounded memory batches."""
import copy
from . import autonomy_context

SAFETY=128
MEMORY_TIMEOUT=600


class MemoryLengthError(ValueError):
    """A completed generation exhausted its output/context budget; no text is saved."""


def measure(app,room,generation,payload,port=None):
    if port is None:port=app.conversation_api.target(room) or app.engine.port
    images=sum(1 for m in payload.get('messages',[]) if isinstance(m.get('content'),list)
               for part in m['content'] if part.get('type')=='image_url')
    counted=payload
    if images:
        counted=copy.deepcopy(payload)
        for message in counted['messages']:
            if isinstance(message.get('content'),list):
                message['content']=[dict(type='text',text='[image input]') if part.get('type')=='image_url' else part for part in message['content']]
    result=dict(autonomy_context.measure_prompt(port,counted,generation,app.store.settings()['context']))
    if images:
        # Template tokenization does not include model-specific image expansion.
        result['input_tokens']+=images*4096
        result.update(estimated=True,image_reserve=images*4096)
    return result


def memory_fits(app,room,generation,payload):
    result=measure(app,room,generation,payload)
    result['input_budget']=max(0,int(result['context_limit']*.6)-SAFETY)
    result['fits']=result['input_tokens']<=result['input_budget']
    return result


def prepare_memory(app,room,generation,payload,job_id=''):
    request=copy.deepcopy(payload)
    request.pop('max_tokens',None)
    request.pop('max_completion_tokens',None)
    result=memory_fits(app,room,generation,request)
    app.diagnostics.trace('memory.context',room,job_id,**result,input_count=result['input_tokens'],output_limit_unset=True)
    if not result['fits']:
        raise ValueError('記憶整理の入力がコンテキストの入力予算に収まりません。原本は保持しました。'
                         f" 入力{result['input_tokens']} / 入力予算{result['input_budget']} / 上限{result['context_limit']}トークン。")
    return request


def fit_chat(app,job):
    payload=job['payload'];removed=0
    while True:
        result=measure(app,job['room'],job['generation'],payload,job['port'])
        reserve=payload.get('max_tokens') or min(1024,max(128,int(result['context_limit']*.25)))
        result.update(output_reserve=reserve,safety_reserve=SAFETY,history_removed=removed)
        if result['input_tokens']+reserve+SAFETY<=result['context_limit']:
            app.diagnostics.trace('chat.context',job['room'],job['id'],**result,input_count=result['input_tokens'])
            return result
        remaining=job.get('chat_history_remaining',0)
        if remaining<=0:
            raise ValueError('キャラ設定・今回の発言・記憶・ツール結果と生成用の余裕がコンテキストに収まりません。'
                             '保存済みの会話は削除していません。入力や設定を確認してください。'
                             f" 入力{result['input_tokens']}＋生成枠{reserve}＋余裕{SAFETY} / 上限{result['context_limit']}。")
        # Drop a complete old turn; never touch the current user or current tool receipts.
        count=1
        while count<remaining and payload['messages'][1+count]['role']!='user':count+=1
        del payload['messages'][1:1+count]
        job['chat_history_remaining']-=count;removed+=count
