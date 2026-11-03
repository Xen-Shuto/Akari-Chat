"""Registration is separate from model-visible definitions."""
import copy
import logging
from dataclasses import replace


class ToolRegistry:
    def __init__(self, specs):
        self._specs={}
        for spec in specs:
            if spec.name in self._specs:raise ValueError('ツール名が重複しています。')
            self._specs[spec.name]=replace(spec,definition=copy.deepcopy(spec.definition))

    def get(self,name):return self._specs.get(name)

    def usable(self,spec,app,context):
        # Background and scheduled executors are added with their respective features.
        if not spec.enabled or spec.execution!='sync':return False
        if spec.available is not None and (app is None or context is None):return False
        try:return spec.available is None or spec.available(app,context) is True
        except Exception:
            logging.getLogger(__name__).exception('Tool availability check failed: %s',spec.name)
            return False

    def definitions(self,app=None,context=None,*,categories=None):
        if categories is not None and (not isinstance(categories,(set,frozenset)) or any(not isinstance(c,str) for c in categories)):
            raise ValueError('提示カテゴリは文字列の集合で指定してください。')
        definitions=[copy.deepcopy(s.definition) for s in self._specs.values()
                     if (categories is None or s.category in categories) and self.usable(s,app,context)]
        if getattr(app,'external',None) is not None and context is not None and app.external.enabled(context.room,'x'):
            for definition in definitions:
                fn=definition['function']
                if fn['name']!='propose_external':continue
                config=app.external.room_public(context.room,'x')['values']
                for key,label in [('posting_summary','投稿方針の要約'),('posting_guidelines','投稿ガイドライン')]:
                    if config.get(key):
                        fn['description']+=f"\nXに投稿する場合は次の{label}に従ってください。\n{config[key]}"
        return definitions

    def catalog(self):
        return [dict(name=s.name,category=s.category,operation=s.operation,execution=s.execution,
                     effects=list(s.effects),output=s.output,enabled=s.enabled,
                     timeout_s=s.timeout_s,max_result_chars=s.max_result_chars) for s in self._specs.values()]
