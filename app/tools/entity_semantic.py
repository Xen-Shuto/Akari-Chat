"""Compare indexed entity centroids. Read-only, no embedding or chat calls."""
import math
import struct

from litechat.file_editor import EditConflict
from litechat.retrieval import text_documents
from litechat.semantic import digest,unit


class EntitySemantic:
    def __init__(self,app):self.app=app

    def _centroid(self,db,identity,doc,checkpoint):
        keys=sorted({digest(part['text']) for part in text_documents(doc['text'],doc['path'],'memory')})
        if not keys:return None
        total=None
        for key in keys:
            checkpoint()
            row=db.execute('SELECT vector FROM vectors WHERE model=? AND body=?',(identity,key)).fetchone()
            if row is None:return None
            raw=row[0]
            if not isinstance(raw,bytes) or not raw or len(raw)%4 or len(raw)>4096*4:
                raise ValueError('検索キャッシュの形式が不正です。')
            vector=unit(list(struct.unpack('<%sf'%(len(raw)//4),raw)))
            if total is None:total=[0.]*len(vector)
            if len(total)!=len(vector):raise ValueError('検索キャッシュの次元が一致しません。')
            for i,value in enumerate(vector):total[i]+=value
        if math.hypot(*total)<1e-12:return None
        return unit(total)

    def candidates(self,room,name,checkpoint=lambda:None):
        app=self.app;sem=app.semantic
        room=app.store.room_path(room).resolve().name
        with app.lock,sem.lock,app.store.lock:
            checkpoint()
            target=app.entity_reviews.document(room,name)
            if target.get('state')!='active' or target.get('merge_pending'):
                raise ValueError('使用中のエンティティを対象にしてください。')
            if not sem.identity or sem.engine.status()['state']!='ready' or not sem.loaded_settings:
                raise ValueError('先に記憶検索モデルを準備し、索引を作成してください。')
            entries=app.resources.inventory(room,'entity')
            inactive=sum(e.get('state')=='dormant' for e in entries)
            blocked=sum(e.get('state')!='dormant' and (e.get('state')=='merged' or bool(e.get('merge_pending'))) for e in entries)
            eligible=[e for e in entries if e.get('state')=='active' and not e.get('merge_pending') and app.entity_states.key(e['path'])!=app.entity_states.key(target['path'])]
            rows=[];snapshots=[];missing=0;unreadable=0;used=0;scanned=0;limited=max(0,len(eligible)-500)
            with sem.connect() as db:
                query=self._centroid(db,sem.identity,target,checkpoint)
                if query is None:raise ValueError('対象本文の索引が未準備か、比較できる内容がありません。索引を確認してください。')
                for index,entry in enumerate(eligible[:500]):
                    checkpoint()
                    doc=app.editor.read_snapshot(room,'resource','エンティティ記憶',entry['path'])
                    if not doc['exists'] or not doc['editable'] or len(doc['text'])>32000:
                        unreadable+=1;continue
                    if used+len(doc['text'])>2000000:
                        limited+=len(eligible[:500])-index;break
                    used+=len(doc['text']);scanned+=1;snapshots.append((entry['path'],doc['revision']))
                    vector=self._centroid(db,sem.identity,doc,checkpoint)
                    if vector is None:missing+=1;continue
                    if len(query)!=len(vector):raise ValueError('検索キャッシュの次元が一致しません。')
                    score=max(-1.,min(1.,sum(a*b for a,b in zip(query,vector))))
                    if score>=sem.loaded_settings['threshold']:
                        rows.append(dict(name=entry['name'],score=round(score,4),reason='本文の索引ベクトルの平均を比較',revision=doc['revision']))
            checkpoint()
            current=app.resources.read(room,'entity',name)
            if current['revision']!=target['revision'] or current.get('state_revision')!=target.get('state_revision'):
                raise EditConflict('比較中に対象本文または状態が変更されました。再読込してください。')
            for path,revision in snapshots:
                checkpoint()
                if app.editor.read_snapshot(room,'resource','エンティティ記憶',path)['revision']!=revision:
                    raise EditConflict('比較中に候補本文が変更されました。再読込してください。')
            rows.sort(key=lambda r:(-r['score'],r['name']))
            warning='本文の類似候補であり、同一人物・同一概念という判定ではありません。両方の本文を確認してください。'
            if missing or unreadable or limited:warning+=f' 未索引/比較不能{missing}件、読取対象外{unreadable}件、走査上限による未確認{limited}件があります。'
            return dict(name=name,basis='indexed_centroid',scanned=scanned,skipped_dormant=inactive,skipped_merging=blocked,
                        skipped_unindexed=missing,skipped_unreadable=unreadable,skipped_limit=limited,total=len(rows),rows=rows[:20],
                        overflow=max(0,len(rows)-20)+missing+unreadable+limited,warning=warning)
