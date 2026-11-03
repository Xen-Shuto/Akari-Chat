"""Exact-argument permission requests; approval never executes an action."""
from .schema import schema
from .contracts import ToolSpec,ToolOutput


def permission_request(app,context,args):
    return app.local_tools.permissions.request(context,args)


ENTRIES=[ToolSpec(schema('request_tool_permission',
    '毎回確認カテゴリのツールを個別申請する。inspectで対象の引数定義を確認し、requestでtool_name/arguments_json/intentを指定。'
    'statusで最新2件を確認する。pendingなら人の承認待ちを伝えて回答を終え、同じ会話で照会を繰り返さない。'
    '承認は1時間内の通常会話で同じ引数の1呼出だけ。承認だけでは実行しない。',
    {'action':{'type':'string','enum':['inspect','request','status'],'maxLength':10},
     'tool_name':{'type':'string','maxLength':80,'default':''},
     'arguments_json':{'type':'string','maxLength':6000,'default':'{}'},
     'intent':{'type':'string','maxLength':300,'default':''}},['action']),
    permission_request,category='capabilities',operation='propose',effects=('permission_request',),
    available=lambda app,context:context.interactive and 'ask' in app.local_tools.permissions.settings(context.room)['levels'].values())]
