"""Room-local persona terms; checks are deterministic and do not claim tone understanding."""
import copy,json
from litechat.file_editor import EditConflict
from litechat.resources import strict_json
from .contracts import ToolSpec,ToolOutput
from .schema import schema
DEFAULT=dict(enabled=False,persona_name='',user_display_name='',address_terms=dict(preferred=[],forbidden=[]),required_terms=[],forbidden_terms=[],tone_rules=[],validation=dict(forbidden_terms='error',required_terms='warning',address_terms='warning',tone_rules='warning'))

def contract(raw):
 if raw is None:return copy.deepcopy(DEFAULT)
 if not isinstance(raw,dict):raise ValueError('Persona ContractはJSONオブジェクトです。')
 result=copy.deepcopy(raw)
 result.setdefault('enabled',True)
 if type(result['enabled']) is not bool:raise ValueError('enabledはtrue/falseです。')
 for key in ('persona_name','user_display_name'):
  result.setdefault(key,'')
  if not isinstance(result[key],str) or len(result[key])>200:raise ValueError('名前は200文字までです。')
 for key in ('address_terms','validation'):
  result.setdefault(key,{})
  if not isinstance(result[key],dict):raise ValueError(key+'はオブジェクトです。')
 pairs=[(result,key) for key in ('required_terms','forbidden_terms','tone_rules')]+[(result['address_terms'],key) for key in ('preferred','forbidden')]
 for owner,key in pairs:
  value=owner.get(key,[]);value=[value] if isinstance(value,str) else value
  if not isinstance(value,list) or len(value)>40 or any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in value):raise ValueError('用語/口調ルールは各200文字・40件以内の空でない文字列配列です。')
  owner[key]=list(dict.fromkeys(value))
 for key,severity in DEFAULT['validation'].items():
  result['validation'].setdefault(key,severity)
  if result['validation'][key] not in ('error','warning','info'):raise ValueError('重大度はerror/warning/infoです。')
 if len(json.dumps(result,ensure_ascii=False))>12000:raise ValueError('契約は12000文字以内にしてください。')
 return result

def active(value):
 return value['enabled'] and any([value['persona_name'],value['user_display_name'],value['required_terms'],value['forbidden_terms'],value['tone_rules'],value['address_terms']['preferred'],value['address_terms']['forbidden']])

class PersonaContracts:
 def __init__(self,app):self.app,self.store=app,app.store
 def read(self,room):
  doc=self.app.editor.read_snapshot(room,'persona_contract','Persona Contract','room_config.json');config=strict_json(doc['text']) if doc['exists'] else {}
  if not isinstance(config,dict) or not isinstance(config.get('override_settings',{}),dict):raise ValueError('ルーム設定が不正です。')
  return dict(room=room,revision=doc['revision'],contract=contract(config.get('override_settings',{}).get('persona_contract')))
 def save(self,room,value,revision):
  if not isinstance(value,dict):raise ValueError('保存する契約はJSONオブジェクトです。')
  value=contract(value)
  with self.app.lock,self.store.lock:
   if self.app.closing or self.app.job:raise ValueError('処理中です。')
   read=lambda:self.app.editor.read_snapshot(room,'persona_contract','Persona Contract','room_config.json')
   doc=read()
   if doc['revision']!=revision:raise EditConflict('ルーム設定が変わりました。再読込してください。')
   config=strict_json(doc['text']) if doc['exists'] else {};overrides=config.setdefault('override_settings',{})
   if not isinstance(overrides,dict):raise ValueError('override_settingsが不正です。')
   overrides['persona_contract']=value
   saved=self.app.editor.save_snapshot(room,'persona_contract',json.dumps(config,ensure_ascii=False,indent=2),revision,read)
   return dict(document=self.read(room),backup=saved['backup'],changed=saved['changed'])
 def check(self,room,text):
  if not isinstance(text,str) or len(text)>20000:raise ValueError('検査文は20000文字までです。')
  value=self.read(room)['contract'];violations=[]
  enabled=active(value)
  if enabled:
   for rule,terms,present in [('forbidden_terms',value['forbidden_terms'],True),('address_terms',value['address_terms']['forbidden'],True),('required_terms',value['required_terms'],False)]:
    for term in terms:
     if (term in text)==present:violations.append(dict(rule=rule,term=term,severity=value['validation'][rule]))
   preferred=value['address_terms']['preferred']
   if preferred and not any(term in text for term in preferred):violations.append(dict(rule='address_terms.preferred',term=', '.join(preferred),severity=value['validation']['address_terms']))
  return dict(enabled=enabled,violations=violations,passed=not any(v['severity']=='error' for v in violations),tone_review_required=value['tone_rules'] if enabled else [],scope='文字列の一致/不在のみ。口調の意味・自然さや人格一致は判定しません。本文や契約は変更しません。')

def read(app,context,args):context.checkpoint();return ToolOutput(data=app.persona_contracts.read(context.room),message='このルームだけのPersona Contractです。外部へ送信しません。')
def check(app,context,args):context.checkpoint();return ToolOutput(data=app.persona_contracts.check(context.room,args['text']),message='用語の機械検査です。口調や人格の意味的な一致は未判定です。')
ENTRIES=[ToolSpec(schema('read_persona_contract','現在ルームの人格契約をローカル参照。契約なしは無効の中立値。',{},[]),read,category='memory',operation='read'),ToolSpec(schema('check_text_against_persona_contract','禁止語・必須語・呼び名を文字一致で検査する。口調は手動確認の対象。本文を変更しない。',{'text':{'type':'string','maxLength':20000}},['text']),check,category='memory',operation='read')]
