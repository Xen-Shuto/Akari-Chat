"""Turn-scoped tool presentation. Requests never grant execution permissions."""
import json
from .schema import schema
from .contracts import ToolSpec, ToolOutput, ToolFailure


CATEGORIES = {
    'external': '許可済みのカレンダー・X・Discordの参照と人による送信確認待ち案',
    'memory': '記憶・日記・知識・検索の続き・ワーキングメモリ',
    'notes': '短期メモの参照・確認待ち更新案',
    'world': '場所・現在地・世界設定',
    'items': '所持品・衣装・外見',
    'time': '現在時刻・タイマー・アプリ内通知・手紙箱',
    'web': 'Web検索・公開URLの本文取得',
    'images': '画像生成・結果確認・取消・画像参照',
    'autonomy': '活動目的の参照・目標・研究・専用/共有手順・振返り',
}
CORE = frozenset({'request_capability', 'search_memory', 'read_notepad', 'read_world', 'read_autonomy'})


def request_capability(app, context, args):
    context.checkpoint()
    definitions = app.local_tools.definitions(context, categories={args['category']})
    if context.capability_scope is not None:
        definitions=[d for d in definitions if d['function']['name'] in context.capability_scope]
    names = [definition['function']['name'] for definition in definitions]
    if not names:raise ToolFailure('unavailable','現在利用可能なツールがないカテゴリです。')
    selected=args.get('tool_names',[])
    if set(selected)-set(names):raise ToolFailure('unavailable','指定されたツールは今回の許可範囲にありません。')
    if context.capability_scope is not None and not selected:
        return ToolOutput(data={'category':args['category'],'intent':args['intent'],'tool_names':[],
                               'catalog_only':True,'catalog':[{'name':d['function']['name'],'description':d['function']['description'].split('\n')[0][:160]} for d in definitions]},
                          message='今回利用できる候補一覧です。必要な名前をtool_namesで最大3件指定して、同じカテゴリを要求してください。一覧取得は実処理の完了ではありません。')
    if selected:names=list(dict.fromkeys(selected))
    permissions=app.local_tools.permissions
    if context.interactive and permissions.settings(context.room)['levels'].get(args['category'])=='ask':
        targets=[item['name'] for item in permissions.registry.catalog() if item['category']==args['category'] and permissions.registry.usable(permissions.registry.get(item['name']),app,context)]
        if targets:return ToolOutput(data={'category':args['category'],'intent':args['intent'],'tool_names':names,'approval_targets':targets},message='このカテゴリは毎回確認です。request_tool_permissionのinspectで引数定義を確認し、requestで具体的な引数と理由を申請してください。承認待ちは未実行です。')
    if not names:
        raise ToolFailure('unavailable', '現在利用可能なツールがないカテゴリです。')
    return ToolOutput(data={'category': args['category'], 'intent':args['intent'], 'tool_names': names},
                      message='目的のカテゴリを受け付けました。'
                              '次の生成で、今回の許可範囲内から利用可能なツールを追加提示します。'
                              'ここまでは準備の段階です。続けて、提示されたツールで目的の処理を進めてください。'
                              '実行結果が返ったら、その内容を踏まえて応答できます。')


TOOL_LOOP_LIMIT=8


def tool_loop_weight(calls):
    names=[call.get('function',{}).get('name','') for call in calls]
    if not all(isinstance(name,str) for name in names):return 1.
    if names and (all(name=='request_capability' for name in names) or all(name.startswith(('read_','search_','list_','get_','find_')) for name in names)):return .5
    return 1.


class ToolPresentation:
    """A snapshot per chat job; definitions are refreshed only between batches."""
    def __init__(self, definitions, *, staged=False):
        self.pool = {definition['function']['name']: definition for definition in definitions}
        self.staged=staged
        self.base=set(self.pool) & ({'request_capability','read_autonomy','read_purpose'} if staged else CORE|{'request_tool_permission'})
        self.visible = set(self.base)
        self.requested = set()
        self.pending = {}
        self.seen_capability_calls = set()
        self.followup_blocked = False

    def definitions(self):
        return [definition for name, definition in self.pool.items() if name in self.visible]

    def accept(self, name, result, call_id=None):
        # Any non-broker call clears the org-style pending confirmation.
        # Never turn this mechanism into a retry of a write or an approval.
        if name != 'request_capability':
            self.pending={}
        if result.get('status') not in ('ok','no_change'):
            self.followup_blocked=True
        if name != 'request_capability' or result.get('status') != 'ok':return
        if call_id is not None:
            if call_id in self.seen_capability_calls:return
            self.seen_capability_calls.add(call_id)
        data=result.get('data',{});category=data.get('category')
        if category not in CATEGORIES:return
        if self.staged:
            self.visible=self.base | (set(data.get('tool_names',[])) & set(self.pool))
            return
        self.requested.add(category)
        intent=data.get('intent','')
        candidates=set(data.get('tool_names',[])) & set(self.pool) - {'request_capability','request_tool_permission'}
        if isinstance(intent,str) and intent.strip() and candidates:
            self.pending={category:dict(intent=intent[:300],names=candidates,reminded=False)}

    def finish_batch(self, calls):
        # org clears the pending request if any non-broker call appeared,
        # regardless of ordering within a parallel batch.
        if any(call.get('function',{}).get('name')!='request_capability' for call in calls):self.pending={}

    def followup(self, visible):
        if self.followup_blocked:return None
        pending={category:item for category,item in self.pending.items() if not item['reminded'] and item['names'] & set(visible)}
        if not pending:return None
        for item in pending.values():item['reminded']=True
        goals=json.dumps({category:item['intent'] for category,item in pending.items()},ensure_ascii=False)
        return dict(categories=sorted(pending),instruction=(
            '【アプリからの手順確認・この能力要求につき1回】'
            'これは、先ほどの能力要求から処理を続けるための案内です。\n'
            '要求時の目的（引用データ）: '+goals+'\n'
            'この目的のためのツールが提示されていますが、対応する実ツールの呼び出しはまだ確認されていません。\n'
            '引き続き操作が必要であれば、現在提示されている許可済みツールから、'
            '目的に必要なものを呼び出し、結果を受け取ってから応答してください。'
            '操作が不要になった場合は、その理由を自然に伝えて会話を終えられます。\n'
            '今回の権限と回数・枚数の範囲は、そのまま引き継がれます。'
            'すでに試した処理や確認待ちの処理は現在の状態を引き継ぎ、'
            '結果が不明なものは状態の確認を先に行ってください。'))


    def refresh(self, registry, available):
        # Intersect the original job scope with current availability; never broaden
        # an autonomous allowlist or resurrect a provider that became unavailable.
        live = {definition['function']['name'] for definition in available}
        if not self.staged:
            self.visible.update(name for name in self.pool
                                if registry.get(name).category in self.requested)
        self.visible.intersection_update(live)
        return self.definitions()


ENTRIES = [ToolSpec(schema('request_capability',
    '必要なツールのカテゴリを要求する。次の生成から追加提示。権限付与・更新・外部送信は行わない。'
    + ' '.join(key + '=' + label for key, label in CATEGORIES.items()),
    {'category': {'type': 'string', 'maxLength': 16, 'enum': list(CATEGORIES)},
     'intent': {'type': 'string', 'minLength': 1, 'maxLength': 300},
     'tool_names': {'type':'array','maxItems':3,'items':{'type':'string','maxLength':80},'default':[]}},
    ['category', 'intent']), request_capability, category='capabilities', operation='read', max_result_chars=6000)]
