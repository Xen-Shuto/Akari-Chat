"""Small, dependency-free contracts shared by explicitly registered AI tools."""
from dataclasses import dataclass, field
import copy
import math
import re
import time


class ToolFailure(Exception):
    def __init__(self, code, message, status='error'):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ToolContext:
    room: str
    job_id: str
    call_id: str = ''
    before: int | None = None
    cancelled: object = None
    deadline: float | None = None
    interactive: bool = False
    capability_scope: frozenset | None = None

    def checkpoint(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise ToolFailure('cancelled', '実行を中止しました。', 'cancelled')
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ToolFailure('timeout', '処理の待機上限に達しました。')


@dataclass
class ToolOutput:
    data: dict = field(default_factory=dict)
    status: str = 'ok'
    message: str = ''
    changes: list = field(default_factory=list)
    error: dict | None = None
    truncated: bool = False
    continuation: object = None

    def as_dict(self):
        if self.status not in {'ok','no_change','pending','conflict','cancelled','error','partial','unknown'}:
            raise ValueError('不正なツール結果です。')
        if not isinstance(self.data,dict) or not isinstance(self.changes,list) or not isinstance(self.message,str):
            raise ValueError('不正なツール結果です。')
        if type(self.truncated) is not bool or self.error is not None and (
                not isinstance(self.error,dict) or not isinstance(self.error.get('code'),str)
                or not isinstance(self.error.get('message'),str)):
            raise ValueError('不正なツール結果です。')
        if self.status in ('error','conflict','cancelled','unknown') and self.error is None:
            raise ValueError('失敗結果には理由が必要です。')
        return dict(version=1,status=self.status,message=self.message,data=self.data,
                    changes=self.changes,error=self.error,truncated=self.truncated,continuation=self.continuation)


def failure(code, message, status='error'):
    return ToolOutput(status=status,message=message,error={'code':code,'message':message}).as_dict()


def validate(value, spec, path='引数'):
    kind=spec['type']
    valid={'object':lambda:isinstance(value,dict),'array':lambda:isinstance(value,list),
           'string':lambda:isinstance(value,str),'boolean':lambda:isinstance(value,bool),
           'integer':lambda:isinstance(value,int) and not isinstance(value,bool),
           'number':lambda:isinstance(value,(int,float)) and not isinstance(value,bool) and (isinstance(value,int) or math.isfinite(value)),
           'null':lambda:value is None}[kind]()
    if not valid:raise ToolFailure('invalid_arguments',path+'の型が不正です。')
    if 'enum' in spec and value not in spec['enum']:
        raise ToolFailure('invalid_arguments',path+'の値が選択肢にありません。')
    if kind=='object':
        props=spec['properties']
        if set(value)-set(props):raise ToolFailure('invalid_arguments','未定義の引数があります。')
        if set(spec['required'])-set(value):raise ToolFailure('invalid_arguments','必須の引数がありません。')
        result={k:validate(v,props[k],path+'.'+k) for k,v in value.items()}
        for k,prop in props.items():
            if k not in result and 'default' in prop:result[k]=validate(copy.deepcopy(prop['default']),prop,path+'.'+k)
        return result
    if kind=='array':
        if len(value)>spec['maxItems']:raise ToolFailure('invalid_arguments',path+'の件数が多すぎます。')
        return [validate(v,spec['items'],path+'[]') for v in value]
    if kind=='string':
        if len(value)<spec.get('minLength',0) or len(value)>spec['maxLength']:
            raise ToolFailure('invalid_arguments',path+'の文字数が範囲外です。')
    if kind in ('integer','number'):
        if 'minimum' in spec and value<spec['minimum'] or 'maximum' in spec and value>spec['maximum']:
            raise ToolFailure('invalid_arguments',path+'の数値が範囲外です。')
    return value


def check_schema(spec):
    if not isinstance(spec,dict):raise ValueError('引数定義はオブジェクトが必要です。')
    kind=spec.get('type')
    allowed={'type','description','enum','default'}
    if kind=='object':
        allowed|={'properties','required','additionalProperties'}
        props=spec.get('properties');required=spec.get('required')
        if not isinstance(props,dict) or not isinstance(required,list) or spec.get('additionalProperties') is not False:
            raise ValueError('オブジェクト定義にはproperties/required/additionalProperties:falseが必要です。')
        if any(not isinstance(k,str) for k in props):raise ValueError('引数名が不正です。')
        if any(not isinstance(k,str) for k in required) or len(set(required))!=len(required) or set(required)-set(props):
            raise ValueError('必須引数の定義が不正です。')
        for child in props.values():check_schema(child)
    elif kind=='array':
        allowed|={'items','maxItems'}
        if type(spec.get('maxItems')) is not int or spec['maxItems']<0:raise ValueError('配列の上限が必要です。')
        check_schema(spec['items'])
    elif kind=='string':
        allowed|={'minLength','maxLength'}
        if type(spec.get('maxLength')) is not int or spec['maxLength']<0:raise ValueError('文字列の上限が必要です。')
        if type(spec.get('minLength',0)) is not int or not 0<=spec.get('minLength',0)<=spec['maxLength']:
            raise ValueError('文字数の範囲が不正です。')
    elif kind in ('integer','number'):
        allowed|={'minimum','maximum'}
        for key in ('minimum','maximum'):
            if key in spec and (type(spec[key]) not in (int,float) or not math.isfinite(spec[key])):
                raise ValueError('数値の範囲が不正です。')
        if spec.get('minimum',float('-inf'))>spec.get('maximum',float('inf')):
            raise ValueError('数値の範囲が逆転しています。')
    elif kind not in ('boolean','null'):raise ValueError('未対応の引数定義です。')
    if set(spec)-allowed:raise ValueError('未対応のスキーマ指定があります。')
    if 'enum' in spec and (not isinstance(spec['enum'],list) or not spec['enum']):
        raise ValueError('選択肢の定義が不正です。')
    try:
        if 'enum' in spec:
            base={key:value for key,value in spec.items() if key not in ('enum','default')}
            for choice in spec['enum']:validate(choice,base)
        if 'default' in spec:validate(spec['default'],spec)
    except ToolFailure as error:raise ValueError('選択肢または既定値が定義と一致しません。') from error


@dataclass(frozen=True)
class ToolSpec:
    definition: dict
    handler: object
    category: str
    operation: str
    execution: str = 'sync'
    effects: tuple = ()
    output: str = 'structured'
    timeout_s: float = 15
    max_result_chars: int = 16000
    enabled: bool = True
    available: object = None

    @property
    def name(self):return self.definition['function']['name']

    def __post_init__(self):
        if not isinstance(self.definition,dict):raise ValueError('ツール定義はオブジェクトが必要です。')
        fn=self.definition.get('function',{})
        if not isinstance(fn,dict):raise ValueError('ツール定義が不正です。')
        if self.definition.get('type')!='function' or not re.fullmatch('[a-z][a-z0-9_]*',fn.get('name','')):
            raise ValueError('ツール名または定義形式が不正です。')
        if not callable(self.handler) or not isinstance(self.category,str) or not self.category.strip() or not isinstance(fn.get('description'),str) or not fn['description'].strip():
            raise ValueError('ツールの実行関数・カテゴリ・説明が必要です。')
        if self.operation not in ('read','propose','write') or self.execution not in ('sync','background','scheduled'):
            raise ValueError('ツールの操作区分または実行形態が不正です。')
        if type(self.enabled) is not bool or self.available is not None and not callable(self.available):
            raise ValueError('有効条件が不正です。')
        if not isinstance(self.effects,tuple) or any(not isinstance(effect,str) for effect in self.effects):
            raise ValueError('作用は文字列のタプルで指定してください。')
        if self.output not in ('structured','artifact','job','reservation'):
            raise ValueError('出力種別が不正です。')
        if not isinstance(self.timeout_s,(int,float)) or isinstance(self.timeout_s,bool) or not math.isfinite(self.timeout_s) or self.timeout_s<=0:
            raise ValueError('処理時間の上限が不正です。')
        if type(self.max_result_chars) is not int or self.max_result_chars<512:
            raise ValueError('結果サイズの上限が不正です。')
        if not isinstance(fn.get('parameters'),dict) or fn['parameters'].get('type')!='object':
            raise ValueError('ツール引数のルートはオブジェクトが必要です。')
        check_schema(fn['parameters'])
