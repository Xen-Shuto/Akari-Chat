"""Split source material, never an unfinished model answer."""
import copy

MIN_CHARACTERS=256
MAX_DEPTH=10


def split_conversation(source):
    depth=source.get('split_depth',0)
    lengths=[]
    for message in source['messages']:
        marker=message.get('source_fragment',{})
        lengths.append(0 if marker and marker.get('end',0)<=marker.get('start',0) else len(message['content']))
    total=sum(lengths)
    if depth>=MAX_DEPTH or total<=MIN_CHARACTERS:
        raise ValueError('最小の資料に分割しても生成が長さの上限に達しました。モデル設定を確認して続行するか、この整理を終了してください。保存済みの記憶は保持しています。')
    midpoint=total//2
    results=[]
    for lower,upper in ((0,midpoint),(midpoint,total)):
        part=copy.deepcopy(source);part['split_depth']=depth+1;position=0
        for message,length in zip(part['messages'],lengths):
            old=message.get('source_fragment',{})
            begin=old.get('start',0);start=min(length,max(0,lower-position));end=min(length,max(0,upper-position))
            message['source_fragment']=dict(start=begin+start,end=begin+end,total=old.get('total',length),conversation_continues=True)
            message['content']=message['content'][start:end] if end>start else '[本文は別の分割に含まれます]'
            position+=length
        results.append(part)
    return results
