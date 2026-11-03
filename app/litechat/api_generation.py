"""Bounded native API adapters; no SDK, proxy inheritance, redirects or automatic retry."""
import copy,http.client,json,re,socket,threading,time,uuid
from urllib.parse import urlsplit,quote

def image_parts(content,provider):
 if isinstance(content,str):return [{'text':content}] if provider=='gemini' else [{'type':'text','text':content}]
 if not isinstance(content,list):raise ValueError('メッセージ形式が不正です。')
 result=[]
 for part in content:
  if part.get('type')=='text':result.append({'text':part['text']} if provider=='gemini' else part)
  elif part.get('type')=='image_url':
   value=part.get('image_url',{}).get('url','');match=re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)',value)
   if not match:raise ValueError('APIへ送る画像は管理済みの埋込画像だけです。')
   result.append({'inlineData':{'mimeType':match[1],'data':match[2]}} if provider=='gemini' else {'type':'image','source':{'type':'base64','media_type':match[1],'data':match[2]}})
  else:raise ValueError('APIで扱えない入力形式です。')
 return result

def request(target,payload):
 payload=dict(payload)
 if payload.get('max_tokens')==0:payload.pop('max_tokens')
 p=target.provider;messages=payload['messages'];system='\n\n'.join(m['content'] for m in messages if m['role']=='system');headers={'Content-Type':'application/json','Accept':'text/event-stream'};sampling={k:payload[k] for k in ('temperature','top_p') if k in payload} if target.sampling else {}
 if p in ('openai','compatible'):
  body=copy.deepcopy(payload);body['model']=target.model
  if not target.sampling:body.pop('temperature',None);body.pop('top_p',None)
  if p=='openai' and 'max_tokens' in body:body['max_completion_tokens']=body.pop('max_tokens')
  headers['Authorization']='Bearer '+target.key
  return '/chat/completions',headers,body
 if p=='gemini':
  contents=[];names={call['id']:call['function']['name'] for m in messages for call in m.get('tool_calls',[])}
  native_ids={part['functionCall']['id'] for m in messages for part in m.get('_gemini_parts',[]) if part.get('functionCall',{}).get('id')}
  for m in messages:
   role=m['role']
   if role=='system':continue
   if role=='tool':
    response={'name':names[m['tool_call_id']],'response':{'result':m['content']}}
    if m['tool_call_id'] in native_ids:response['id']=m['tool_call_id']
    parts=[{'functionResponse':response}];role='user'
   elif '_gemini_parts' in m:parts=m['_gemini_parts'];role='model'
   else:
    parts=image_parts(m.get('content') or '',p);role='model' if role=='assistant' else 'user'
    for call in m.get('tool_calls',[]):parts.append({'functionCall':{'name':call['function']['name'],'args':json.loads(call['function']['arguments'])}})
   if contents and contents[-1]['role']==role:contents[-1]['parts']+=parts
   else:contents.append(dict(role=role,parts=parts))
  body={'contents':contents,'systemInstruction':{'parts':[{'text':system}]},'generationConfig':({'maxOutputTokens':payload['max_tokens']} if 'max_tokens' in payload else {})}
  if target.sampling:body['generationConfig'].update(temperature=payload.get('temperature',.7),topP=payload.get('top_p',.95))
  if payload.get('tools'):body['tools']=[{'functionDeclarations':[{'name':t['function']['name'],'description':t['function']['description'],'parametersJsonSchema':t['function']['parameters']} for t in payload['tools']]}]
  headers['x-goog-api-key']=target.key
  return '/models/'+quote(target.model,safe='')+':streamGenerateContent?alt=sse',headers,body
 if p=='anthropic':
  contents=[]
  for m in messages:
   role=m['role']
   if role=='system':continue
   if role=='tool':parts=[{'type':'tool_result','tool_use_id':m['tool_call_id'],'content':m['content']}];role='user'
   elif '_anthropic_blocks' in m:parts=m['_anthropic_blocks']
   else:
    parts=image_parts(m.get('content') or '',p)
    for call in m.get('tool_calls',[]):parts.append({'type':'tool_use','id':call['id'],'name':call['function']['name'],'input':json.loads(call['function']['arguments'])})
   if contents and contents[-1]['role']==role:contents[-1]['content']+=parts
   else:contents.append(dict(role=role,content=parts))
  maximum=payload.get('max_tokens') or target.max_output_tokens
  if not maximum:raise ValueError('Anthropic APIは出力上限が必須です。モデル一覧を再取得してモデルの出力上限を読み込むか、最大生成tokensに正の値を設定してください。')
  body=dict(model=target.model,system=system,messages=contents,max_tokens=maximum,stream=True,**sampling)
  # Claude models can reject simultaneous temperature/top_p; send one explicit knob.
  body.pop('top_p',None)
  if payload.get('tools'):body['tools']=[dict(name=t['function']['name'],description=t['function']['description'],input_schema=t['function']['parameters']) for t in payload['tools']]
  headers.update({'x-api-key':target.key,'anthropic-version':'2023-06-01'})
  return '/messages',headers,body
 raise ValueError('未対応の会話プロバイダーです。')

def validate_calls(calls):
 if len(calls)>4:raise ValueError('APIツール呼出数が上限を超えました。')
 ids=[]
 for call in calls:
  key=call.get('id');function=call.get('function',{})
  if not isinstance(key,str) or not 1<=len(key)<=256 or not isinstance(function.get('name'),str) or not 1<=len(function['name'])<=200 or not isinstance(function.get('arguments'),str) or len(function['arguments'])>16000:raise ValueError('APIツール呼出の形式が不正です。')
  json.loads(function['arguments']);ids.append(key)
 if len(ids)!=len(set(ids)):raise ValueError('APIツール呼出IDが重複しています。')

def stream(generation,target,payload):
 generation.tool_calls=[];generation.finish_reason=None;generation.assistant_message=None;generation.api_outcome='not_sent'
 if payload.get('tools') and not target.tools:raise ValueError('この接続ではツール対応が許可されていません。')
 if not target.vision and any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content']) for m in payload['messages']):raise ValueError('この接続では画像入力が許可されていません。')
 suffix,headers,body=request(target,payload);url=urlsplit(target.endpoint);path=url.path.rstrip('/')+suffix
 connection=(http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection)(url.hostname,url.port,timeout=15)
 chunks=[];calls={};blocks={};closed_blocks=set();gemini_parts=[];done=False;total=0;deadline=time.monotonic()+180;timed_out=threading.Event()
 def expire():
  timed_out.set()
  with generation.lock:
   if generation.socket:
    try:generation.socket.shutdown(socket.SHUT_RDWR)
    except OSError:pass
 timer=threading.Timer(180,expire);timer.daemon=True;timer.start()
 try:
  if generation.cancelled.is_set():return
  connection.connect()
  with generation.lock:generation.socket=connection.sock
  if generation.cancelled.is_set():return
  connection.sock.settimeout(120)
  raw=json.dumps(body,ensure_ascii=False).encode('utf8')
  if len(raw)>16*1048576:raise ValueError('API入力サイズの上限を超えました。')
  generation.api_outcome='unknown';connection.request('POST',path,raw,headers);response=connection.getresponse()
  if response.status!=200:generation.api_outcome='rejected'
  if response.status!=200:raise ValueError('会話APIからHTTP '+str(response.status)+'が返りました。認証・モデル・利用枠を確認してください。自動再送はしません。')
  pending=[]
  while not generation.cancelled.is_set():
   if time.monotonic()>deadline:raise ValueError('API応答の時間上限です。')
   line=response.readline(1048577);total+=len(line)
   if len(line)>1048576 or total>32*1048576:raise ValueError('API応答のサイズ上限です。')
   if not line:
    if pending:raise ValueError('APIイベントが途中で切断されました。')
    break
   if line.startswith(b'data:'):pending.append(line[5:].strip());continue
   if line.strip() or not pending:continue
   data=b'\n'.join(pending);pending=[]
   if data==b'[DONE]':done=True;break
   event=json.loads(data)
   if event.get('error') or event.get('type')=='error':raise ValueError('会話APIが処理エラーを返しました。自動再送はしません。')
   if target.provider in ('openai','compatible'):
    choices=event.get('choices',[])
    if not choices:continue
    choice=choices[0];delta=choice.get('delta',{})
    if choice.get('finish_reason') is not None:generation.finish_reason=choice['finish_reason']
    text=delta.get('content') or delta.get('refusal','')
    if isinstance(text,str) and text:chunks.append(text);yield text
    for piece in delta.get('tool_calls',[]):
     index=piece.get('index')
     if type(index) is not int or not 0<=index<4:raise ValueError('APIツール呼出番号が不正です。')
     call=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
     if piece.get('id'):call['id']=piece['id']
     for k in ('name','arguments'):
      part=piece.get('function',{}).get(k,'')
      if not isinstance(part,str):raise ValueError('APIツール引数が不正です。')
      call['function'][k]+=part
      if len(call['function'][k])>16000:raise ValueError('APIツール引数の上限です。')
   elif target.provider=='gemini':
    candidates=event.get('candidates',[])
    if event.get('promptFeedback',{}).get('blockReason'):raise ValueError('Geminiが入力を拒否しました。')
    if not candidates:continue
    candidate=candidates[0]
    for part in candidate.get('content',{}).get('parts',[]):
     gemini_parts.append(part)
     if part.get('text') and not part.get('thought'):chunks.append(part['text']);yield part['text']
     if part.get('functionCall'):
      f=part['functionCall'];calls[len(calls)]={'id':f.get('id') or 'gemini_'+uuid.uuid4().hex,'type':'function','function':{'name':f['name'],'arguments':json.dumps(f.get('args',{}),ensure_ascii=False)}}
    if candidate.get('finishReason'):
     generation.finish_reason=candidate['finishReason'];done=True
     if generation.finish_reason not in ('STOP','MAX_TOKENS'):raise ValueError('Geminiが応答を停止しました。')
   else:
    kind=event.get('type');index=event.get('index')
    if kind=='content_block_start':
     if type(index) is not int or not 0<=index<100:raise ValueError('Claudeブロック番号が不正です。')
     blocks[index]=event['content_block']
     if blocks[index]['type']=='tool_use':blocks[index]['_json']=''
     elif blocks[index]['type']=='text' and blocks[index].get('text'):text=blocks[index]['text'];chunks.append(text);yield text
    elif kind=='content_block_delta':
     block=blocks[index];delta=event['delta'];typ=delta['type']
     if typ=='text_delta':text=delta['text'];block['text']=block.get('text','')+text;chunks.append(text);yield text
     elif typ=='input_json_delta':block['_json']+=delta['partial_json']
     elif typ=='thinking_delta':block['thinking']=block.get('thinking','')+delta['thinking']
     elif typ=='signature_delta':block['signature']=delta['signature']
     if len(json.dumps(block))>100000:raise ValueError('Claudeブロックの上限です。')
    elif kind=='content_block_stop':closed_blocks.add(index)
    elif kind=='message_delta':generation.finish_reason=event.get('delta',{}).get('stop_reason')
    elif kind=='message_stop':done=True;break
   if len(calls)>4:raise ValueError('APIツール呼出数の上限です。')
  if generation.cancelled.is_set():return
  if timed_out.is_set():raise ValueError('会話APIの時間上限です。自動再送はしません。')
  if not done:raise ValueError('会話APIとの接続が完了前に切れました。自動再送はしません。')
  if target.provider=='anthropic':
   if set(blocks)!=closed_blocks:raise ValueError('Claudeの出力ブロックが未完了です。ツールは実行しません。')
   for block in blocks.values():
    if block['type']=='tool_use':
     args=block.pop('_json','');block['input']=json.loads(args) if args else block.get('input',{});calls[len(calls)]={'id':block['id'],'type':'function','function':{'name':block['name'],'arguments':json.dumps(block['input'],ensure_ascii=False)}}
  result=[calls[k] for k in sorted(calls)];validate_calls(result)
  if result and generation.finish_reason in ('length','max_tokens','MAX_TOKENS'):raise ValueError('APIツール呼出が生成上限で中断しました。実行しません。')
  generation.api_outcome='complete'
  generation.finish_reason={'STOP':'stop','MAX_TOKENS':'length','end_turn':'stop','stop_sequence':'stop','tool_use':'tool_calls','max_tokens':'length'}.get(generation.finish_reason,generation.finish_reason)
  if generation.finish_reason=='length':raise ValueError('最大生成tokensに達しました。途中の本文を保持し、自動再送はしません。')
  if generation.finish_reason not in (None,'stop','tool_calls'):raise ValueError('APIの応答終了理由を確認してください。途中本文は保持します。')
  generation.tool_calls=result
  generation.assistant_message=dict(role='assistant',content=''.join(chunks) or None,tool_calls=result)
  if target.provider=='gemini':generation.assistant_message['_gemini_parts']=gemini_parts
  if target.provider=='anthropic':generation.assistant_message['_anthropic_blocks']=[blocks[k] for k in sorted(blocks)]
 except (OSError,http.client.HTTPException,UnicodeError,json.JSONDecodeError,KeyError,TypeError):
  if timed_out.is_set():raise ValueError('会話APIの時間上限です。自動再送はしません。') from None
  if not generation.cancelled.is_set():raise ValueError('会話APIの通信・応答を確認できませんでした。自動再送はしません。') from None
 finally:
  timer.cancel()
  with generation.lock:generation.socket=None
  connection.close()
