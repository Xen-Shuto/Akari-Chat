"""NexusArk-aligned provider protocols without SDKs, retries or hidden caption calls."""
import base64
import binascii
import json
import math
import uuid
from urllib.parse import quote,urlsplit
from .public_http import request,parsed_url,RemoteError
from .image_formats import dimensions,detected_mime,EXTENSIONS

PROVIDERS=('disabled','local','gemini','openai','pollinations','huggingface')
DEFAULTS={
 'local':dict(url='http://127.0.0.1:7861/sdapi/v1/txt2img',sampler='Euler a',steps=25,cfg_scale=7.,positive_prefix='',positive_append='',negative_prompt=''),
 'gemini':dict(base_url='https://generativelanguage.googleapis.com/v1beta',model='',api_key=''),
 'openai':dict(base_url='https://api.openai.com/v1',model='',api_key=''),
 'pollinations':dict(base_url='https://gen.pollinations.ai/v1',model='flux',api_key=''),
 'huggingface':dict(base_url='https://router.huggingface.co/hf-inference/models',model='',api_key='')}
for _provider,_profile in DEFAULTS.items():_profile['timeout_seconds']=600 if _provider=='local' else 180


def checked_profile(provider,source):
    if provider not in DEFAULTS or not isinstance(source,dict):raise ValueError('画像生成の設定形式が不正です。')
    if set(source)-set(DEFAULTS[provider]):raise ValueError('画像生成に未定義の設定項目があります。')
    result={**DEFAULTS[provider],**source}
    for key,value in result.items():
        if key in ('steps','cfg_scale','timeout_seconds'):continue
        if not isinstance(value,str) or len(value)> (4096 if key in ('positive_prefix','positive_append','negative_prompt') else 2048):raise ValueError('設定文字列の形式または長さが不正です。')
        if key not in ('positive_prefix','positive_append','negative_prompt'):result[key]=value.strip()
    if type(result['timeout_seconds']) is not int or not 30<=result['timeout_seconds']<=1800:raise ValueError('画像生成の待機上限は30～1800秒の整数です。')
    url=result['url'] if provider=='local' else result['base_url'];p=parsed_url(url)
    if p.query or p.fragment:raise ValueError('接続先URLにクエリやフラグメントは含めないでください。')
    if provider!='local' and p.scheme!='https' and p.hostname not in ('localhost','127.0.0.1','::1'):raise ValueError('外部サービスの接続先はHTTPSを指定してください。')
    if provider=='local':
        if type(result['steps']) is not int or not 1<=result['steps']<=150:raise ValueError('Stepsは1～150の整数です。')
        cfg=result['cfg_scale']
        if type(cfg) not in (int,float) or not math.isfinite(cfg) or not 0<=cfg<=30:raise ValueError('CFGは0～30です。')
        if not result['sampler']:raise ValueError('サンプラーが必要です。')
    elif result['model'] and (len(result['model'])>200 or '..' in result['model'] or result['model'].startswith('/')):raise ValueError('モデル名の形式が不正です。')
    return result


def ready(provider,settings):
    if provider=='disabled':return False
    p=checked_profile(provider,settings)
    return provider=='local' or bool(p['model'] and p['api_key'])


def supports_reference(provider,model,base_url=''):
    model=model.lower()
    if provider=='gemini':return 'image' in model
    if provider=='openai':
        if urlsplit(base_url).hostname=='openrouter.ai':return 'gemini' in model and 'image' in model
        return 'gpt-image' in model or 'gptimage' in model
    return False


def image_bytes(raw):
    mime=detected_mime(raw)
    if not mime or len(raw)>8*1048576:raise ValueError('生成結果は8MiB以内のPNG/JPEG/静止WebPが必要です。')
    w,h=dimensions(raw,mime)
    if not 0<w<=8192 or not 0<h<=8192 or w*h>16777216:raise ValueError('生成画像の寸法が上限を超えています。')
    return raw,mime


def unbase64(value):
    if not isinstance(value,str) or len(value)>12*1048576:raise ValueError('画像応答の形式または長さが不正です。')
    if value.startswith('data:'):value=value.split(';base64,',1)[-1]
    try:return image_bytes(base64.b64decode(value,validate=True))
    except binascii.Error as error:raise ValueError('画像応答のbase64が不正です。') from error


def multipart(fields,references):
    boundary='akari'+uuid.uuid4().hex;parts=[]
    for key,value in fields.items():parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+str(value)+'\r\n').encode())
    for index,image in enumerate(references):
        ext=EXTENSIONS[image['mime']][1:]
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="image[]"; filename="reference'+str(index)+'.'+ext+'"\r\nContent-Type: '+image['mime']+'\r\n\r\n').encode()+image['raw']+b'\r\n')
    parts.append(('--'+boundary+'--\r\n').encode())
    return b''.join(parts),'multipart/form-data; boundary='+boundary


def generate(provider,settings,prompt,aspect='square',references=(),fetch=request):
    p=checked_profile(provider,settings)
    if not ready(provider,p):raise ValueError('画像生成のモデル/キーまたは接続先を設定してください。')
    if aspect not in ('square','portrait','landscape'):raise ValueError('画像の縦横比が不正です。')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>6000:raise ValueError('画像の指示は1～6000文字です。')
    if references and not supports_reference(provider,p.get('model',''),p.get('base_url','')):raise ValueError('選択中の方式は参照画像を直接受け取れません。')
    sizes={'square':(1024,1024),'portrait':(832,1216),'landscape':(1216,832)};width,height=sizes[aspect]
    headers={'Content-Type':'application/json','Accept-Encoding':'identity','User-Agent':'Akari-Chat/1.0'}
    if p.get('api_key'):headers['Authorization']='Bearer '+p['api_key']
    content_type=None
    if provider=='local':
        url=p['url'];payload={'prompt':', '.join(x for x in (p['positive_prefix'],prompt,p['positive_append']) if x),'negative_prompt':p['negative_prompt'],
            'sampler_name':p['sampler'],'steps':p['steps'],'cfg_scale':p['cfg_scale'],'width':width,'height':height,'batch_size':1,'n_iter':1}
    elif provider=='gemini':
        url=p['base_url'].rstrip('/')+'/models/'+quote(p['model'],safe='-_.')+':generateContent'
        headers.pop('Authorization',None);headers['x-goog-api-key']=p['api_key']
        parts=[{'inlineData':{'mimeType':i['mime'],'data':base64.b64encode(i['raw']).decode()}} for i in references]+[{'text':prompt}]
        payload={'contents':[{'role':'user','parts':parts}],'generationConfig':{'responseModalities':['TEXT','IMAGE'],'imageConfig':{'aspectRatio':{'square':'1:1','portrait':'2:3','landscape':'3:2'}[aspect]}}}
    elif provider=='huggingface':
        url=p['base_url'].rstrip('/')+'/'+quote(p['model'],safe='/-_.');payload={'inputs':prompt}
    elif provider=='openai' and urlsplit(p['base_url']).hostname=='openrouter.ai':
        url=p['base_url'].rstrip('/')+'/chat/completions'
        content=[{'type':'text','text':prompt}]+[{'type':'image_url','image_url':{'url':'data:'+i['mime']+';base64,'+base64.b64encode(i['raw']).decode()}} for i in references] if references else prompt
        payload={'model':p['model'],'messages':[{'role':'user','content':content}],'modalities':['image','text'],'image_config':{'aspect_ratio':{'square':'1:1','portrait':'2:3','landscape':'3:2'}[aspect],'image_size':'1K'}}
    else:
        url=p['base_url'].rstrip('/')+('/images/edits' if references else '/images/generations')
        payload={'model':p['model'],'prompt':prompt,'n':1}
        if 'grok' in p['model'].lower():payload.update(aspect_ratio={'square':'1:1','portrait':'2:3','landscape':'3:2'}[aspect],resolution='1k')
        elif 'dall-e-3' in p['model'].lower():payload['size']={'square':'1024x1024','portrait':'1024x1792','landscape':'1792x1024'}[aspect]
        else:payload['size']='1024x1024' if aspect=='square' else '1024x1536' if aspect=='portrait' else '1536x1024'
        if not supports_reference(provider,p['model'],p['base_url']):payload['response_format']='b64_json'
        if references:
            body,content_type=multipart(payload,references)
    if content_type:headers['Content-Type']=content_type
    else:body=json.dumps(payload,ensure_ascii=False).encode()
    raw,mime,_=fetch(url,method='POST',body=body,headers=headers,timeout=p['timeout_seconds'],limit=12*1048576,allow_local=True,redirects=0)
    if mime.split(';')[0].startswith('image/'):
        image,mime=image_bytes(raw);return image,mime,''
    data=json.loads(raw);comment=''
    if provider=='local':encoded=data['images'][0]
    elif provider=='gemini':
        parts=data['candidates'][0]['content']['parts'];encoded=next(part.get('inlineData',part.get('inline_data',{}))['data'] for part in parts if 'inlineData' in part or 'inline_data' in part)
        comment=' '.join(part.get('text','') for part in parts)[:2000]
    elif provider=='openai' and urlsplit(p['base_url']).hostname=='openrouter.ai':
        message=data['choices'][0]['message'];value=message['images'][0]['image_url'];encoded=value['url'] if isinstance(value,dict) else value;comment=str(message.get('content') or '')[:2000]
    else:
        first=data['data'][0];encoded=first.get('b64_json','');comment=str(first.get('revised_prompt') or '')[:2000]
        if not encoded and first.get('url'):
            image_raw,_,_=fetch(first['url'],timeout=30,limit=8*1048576)
            image,mime=image_bytes(image_raw);return image,mime,comment
    image,mime=unbase64(encoded)
    return image,mime,comment
