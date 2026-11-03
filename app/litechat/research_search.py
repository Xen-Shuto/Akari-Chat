"""Current-room research search; never starts engines or builds missing vectors."""
import hashlib
import http.client
import json
import sqlite3
import time
from .autonomy import encoded
from .retrieval import chunks,normalize,terms
from .semantic import digest,cached_cosine
from .file_editor import EditConflict


class ResearchSearch:
    def __init__(self,store,semantic):self.store,self.semantic=store,semantic

    def corpus(self,room):
        self.store.room_path(room)
        with self.store.connect() as db:rows=db.execute('SELECT payload FROM autonomy_documents WHERE room=? AND kind="thread" ORDER BY id LIMIT 1000',(room,)).fetchall()
        documents=[]
        for row in rows:
            item=json.loads(row[0]);details=item.get('details',{})
            text='\n'.join([item['title'],item['body'],details.get('findings',''),*details.get('open_questions',[]),item['next_action']])
            reference={key:item[key] for key in ('id','title','state','revision')}
            documents.extend(chunks(text,reference,'research'))
        return documents,[]

    def search(self,room,query,method='auto',state='active',offset=0,limit=5,revision='',checkpoint=lambda:None):
        if not isinstance(query,str) or not 2<=len(query.strip())<=200:raise ValueError('検索語は2～200文字です。')
        if method not in ('auto','keyword','semantic') or state not in ('all','active','paused','completed','archived'):raise ValueError('検索方式または状態が不正です。')
        if type(offset) is not int or not 0<=offset<=1000 or type(limit) is not int or not 1<=limit<=10 or offset and not revision:raise ValueError('続きには検索結果のrevisionが必要です。')
        checkpoint();query=query.strip();documents,_=self.corpus(room)
        documents=[doc for doc in documents if state=='all' or doc['state']==state]
        identity=hashlib.sha256(encoded(sorted({(doc['id'],doc['revision']) for doc in documents})).encode()).hexdigest()
        selected='semantic' if method=='auto' and self.semantic.settings()['auto_semantic'] else 'keyword' if method=='auto' else method
        warnings=[];fallback=False;embedding_calls=0;embedding_seconds=0.;missing=0;searched=0;results=[];model=''
        try:
            if selected=='semantic':
                with self.semantic.lock:
                    if self.semantic.progress['state']=='building' or not self.semantic.identity or self.semantic.engine.status()['state']!='ready':raise ValueError('意味検索モデルまたは索引の準備ができていません。')
                    model=self.semantic.identity;vector=None
                    with self.semantic.connect() as db:
                        for doc in documents:
                            checkpoint();row=db.execute('SELECT vector FROM vectors WHERE model=? AND body=?',(model,digest(doc['text']))).fetchone()
                            if not row:missing+=1;continue
                            if vector is None:
                                embedding_calls+=1;start=time.perf_counter()
                                try:vector=self.semantic.embed([query],query=True)[0]
                                finally:embedding_seconds+=time.perf_counter()-start
                            score=cached_cosine(row[0],vector);searched+=1
                            if score>=self.semantic.loaded_settings['threshold']:results.append(self._result(doc,score))
                    if missing:warnings.append(str(missing)+'断片が未索引または更新済みです。設定の記憶検索モデルで索引を更新してください。')
                    if documents and not searched:raise ValueError('研究スレッドがまだ索引にありません。')
        except (ValueError,OSError,sqlite3.Error,http.client.HTTPException):
            if method!='auto':raise
            selected='keyword';fallback=True;results=[];searched=0;model=''
            warnings=['意味検索を利用できないため文字一致で検索しました。索引・モデルの状態を確認してください。']
        if selected=='keyword':
            tokens=terms(query)
            for doc in documents:
                checkpoint();searched+=1
                score=sum(min(doc['normalized'].count(token),5)+2*min(normalize(doc['title']).count(token),2) for token in tokens)
                if score:results.append(self._result(doc,float(score)))
        # Different chunks of a thread produce a single best matching result.
        best={}
        for item in sorted(results,key=lambda item:(-item['score'],item['id'],item['chunk_offset'])):best.setdefault(item['id'],item)
        ranked=list(best.values())
        # Fallback diagnostics are not part of the keyword ranking snapshot;
        # continuation pins keyword explicitly and must keep the same revision.
        current=digest(encoded([query,selected,state,identity,model,ranked,warnings if selected=='semantic' else []]))
        if revision and revision!=current:raise EditConflict('研究検索の結果が変わりました。先頭から検索し直してください。')
        if offset>len(ranked):raise ValueError('検索結果の範囲を超えています。')
        checkpoint()
        return dict(query=query,method=selected,fallback=fallback,state='current',filter_state=state,results=ranked[offset:offset+limit],
                    revision=current,offset=offset,total=len(ranked),warnings=warnings,searched_chunks=searched,embedding_calls=embedding_calls,embedding_seconds=embedding_seconds)

    def _result(self,doc,score):
        return {key:doc[key] for key in ('id','title','state','revision','chunk_offset')}|dict(score=score,excerpt=doc['text'][:400])
