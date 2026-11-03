"""Automatic capabilities from explicit provider metadata and conservative known IDs."""
import re

def detect(provider,model,row=None):
    # None means unknown, not proof of lack of support.
    result={'vision':None,'tools':None,'source':'unknown'}
    known={
      'openai':r'(?:gpt-4[.]1(?:-mini|-nano)?|gpt-4o(?:-mini)?|gpt-5|gpt-6[.]1-sol|gpt-6-astra)(?:-\d{4}-\d{2}-\d{2})?',
      'gemini':r'(?:gemini-2[.]5-(?:flash|pro)|gemini-3[.]8-flash|gemini-3[.]5-flash-lite)(?:-preview-\d{2}-\d{2})?',
      'anthropic':r'claude-(?:3(?:-5|-7)?-(?:sonnet|haiku|opus)|(?:sonnet|opus|haiku)-4(?:-\d+)?)(?:-\d{8}|-latest)?',
    }
    if provider in known and re.fullmatch(known[provider],model):result.update(vision=True,tools=True,source='known-model')
    # Compatible endpoints are not assumed to implement a model from its name.
    if not isinstance(row,dict):return result
    maximum=row.get('max_tokens') if provider=='anthropic' else None
    if type(maximum) is int and 0<maximum<=10000000:result['max_output_tokens']=maximum
    capabilities=row.get('capabilities',{})
    if not isinstance(capabilities,dict):capabilities={}
    for field,names in [('vision',('vision','image_input')),('tools',('function_calling','tool_calling'))]:
        for name in names:
            value=capabilities.get(name)
            if isinstance(value,dict):value=value.get('supported')
            if type(value) is bool:result[field]=value;result['source']='api-metadata';break
    architecture=row.get('architecture',{})
    modalities=row.get('input_modalities',architecture.get('input_modalities') if isinstance(architecture,dict) else None)
    if isinstance(modalities,list) and all(isinstance(m,str) for m in modalities):result['vision']='image' in modalities;result['source']='api-metadata'
    params=row.get('supported_parameters')
    if isinstance(params,list) and all(isinstance(p,str) for p in params):result['tools']='tools' in params or 'tool_choice' in params;result['source']='api-metadata'
    return result
