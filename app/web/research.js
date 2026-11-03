"use strict";
let researchResults=null,researchRoom=null,researchSignature='',researchBusy=false;
function researchForm(){return {query:A('research-query').value,method:A('research-method').value,state:A('research-state').value};}
async function researchFind(offset=0){
  if(!room||researchBusy)return;researchBusy=true;A('research-search').disabled=true;
  const selected=room.id,form=researchForm(),signature=JSON.stringify(form);
  const page=offset&&researchRoom===selected&&researchSignature===signature&&researchResults?offset:0;
  try{
    const data=await api('/api/research-search?'+new URLSearchParams({room:selected,...form,method:page?researchResults.method:form.method,offset:page,revision:page?researchResults.revision:''}));
    if(room?.id!==selected||JSON.stringify(researchForm())!==signature)return;
    researchRoom=selected;researchResults=data;researchSignature=signature;
    A('research-search-status').textContent=data.query+' / '+(data.method==='semantic'?'意味検索':'文字一致')+' / '+data.total+'件。'+data.warnings.join(' ');
    A('research-prev').disabled=data.offset===0;A('research-next').disabled=data.offset+data.results.length>=data.total;A('research-results').replaceChildren();
    for(const item of data.results){
      const card=document.createElement('section'),title=document.createElement('h3'),text=document.createElement('p'),open=document.createElement('button');
      title.textContent=item.title+' / '+item.state;text.textContent=item.excerpt;open.type='button';open.textContent='この研究を開く';
      open.addEventListener('click',async()=>{
        if(autoDocumentDirty()){notice('活動記録の未保存入力があります。保存または破棄してください。');return;}
        try{const doc=await api('/api/autonomy-document?'+new URLSearchParams({room:selected,id:item.id}));if(room?.id!==selected||autoDocumentDirty())return;if(doc.revision!==item.revision){notice('検索後に研究が変わりました。検索し直してください。');return;}autoKind='thread';A('autonomy-kind').value='thread';autoPage=0;autoFillDocument(doc);revealPanel('autonomy-records');await autoList();}catch(error){report(error);}
      });const merge=document.createElement('button');merge.type='button';merge.textContent='統合元の候補に選ぶ';merge.addEventListener('click',async()=>{
        if(researchMergeDirty()){notice('編集中の統合案を保存・破棄してください。');return;}
        try{await researchMergeLoad();if(room?.id!==selected)return;A('research-merge-source').value=item.id;revealPanel('research-merge-panel');A('research-merge-status').textContent='統合先を選び、現在の内容を比較してください。';}catch(error){report(error);}
      });card.append(title,text,open,merge);A('research-results').append(card);
    }
  }catch(error){report(error);}finally{researchBusy=false;A('research-search').disabled=false;}
}
A('research-search-form').addEventListener('submit',event=>{event.preventDefault();researchFind().catch(report);});
A('research-prev').addEventListener('click',()=>researchFind(Math.max(0,(researchResults?.offset||0)-5)).catch(report));
A('research-next').addEventListener('click',()=>researchFind((researchResults?.offset||0)+5).catch(report));
document.querySelector('[data-panel="research-panel"]').addEventListener('panelactivate',()=>{
  if(room?.id!==researchRoom){researchRoom=room?.id;researchResults=null;researchSignature='';A('research-results').replaceChildren();A('research-search-status').textContent='';A('research-prev').disabled=true;A('research-next').disabled=true;}
});

let researchMergePreview=null,researchMergeBaseline='',researchMergeBusy=false,researchMergeOperation=null,researchMergeRoom=null,researchMergeReferences=null,researchMergeNext=0;
function researchMergeForm(){return {title:A('research-merge-title').value,body:A('research-merge-body').value,next_action:A('research-merge-next-action').value,state:A('research-merge-state').value,
  details:{...(researchMergePreview?.proposal.details||{}),findings:A('research-merge-findings').value,open_questions:A('research-merge-questions').value.split('\n').map(s=>s.trim()).filter(Boolean),links:activitySelected('research-merge-links'),working_slots:activitySelected('research-merge-slots')}};}
function researchMergeInput(){return {edited:researchMergeForm(),transfers:activitySelected('research-merge-subscriptions')};}
function researchMergeDirty(){return !!researchMergePreview&&JSON.stringify(researchMergeInput())!==researchMergeBaseline;}
function researchMergeControls(){

  syncRoomSelector();
  const blocked=researchMergeBusy||busy||!room;
  for(const id of ['research-merge-target','research-merge-source','research-merge-compare','research-merge-discard','research-merge-history-refresh'])A(id).disabled=blocked;
  for(const field of A('research-merge-form').querySelectorAll('input,textarea,select,button'))field.disabled=blocked||!researchMergePreview||researchMergePreview.room!==room?.id;
  A('research-merge-history-older').disabled=blocked||!researchMergeNext;
}
function researchMergeClear(){
  researchMergePreview=null;researchMergeBaseline='';researchMergeOperation=null;
  for(const id of ['research-merge-before','research-merge-original','research-merge-warnings'])A(id).textContent='';
  for(const id of ['title','body','findings','questions','next-action'])A('research-merge-'+id).value='';
  A('research-merge-subscriptions').replaceChildren();A('research-merge-links').replaceChildren();A('research-merge-slots').replaceChildren();researchMergeControls();
}
async function researchMergeHistory(before=0){
  if(!room)return;const selected=room.id,data=await api('/api/research-merges?'+new URLSearchParams({room:selected,before}));if(room?.id!==selected)return;
  researchMergeNext=data.next_before;A('research-merge-history').replaceChildren();
  for(const item of data.entries){
    const card=document.createElement('section'),label=document.createElement('p');label.textContent=(item.moved_subscriptions?'ローカル購読 '+item.moved_subscriptions+'件移管 / ':'')+item.created_at+' / '+item.title+' ← '+item.source_title+' / '+(item.state==='applied'?'反映済み':'取消済み');card.append(label);
    if(item.state==='applied'){const undo=document.createElement('button');undo.type='button';undo.textContent='この統合を取り消す';undo.addEventListener('click',async()=>{
      if(researchMergeBusy||busy||researchMergeDirty()||autoDocumentDirty()){notice('編集中の内容を保存・破棄してから取り消してください。');return;}
      researchMergeBusy=true;researchMergeControls();
      try{const result=await api('/api/research-merge-undo',{room:selected,id:item.id,revision:item.revision});if(room?.id!==selected)return;researchMergeClear();
        if(autoDoc?.id===result.operation.restored.id)autoFillDocument(result.operation.restored);await autoList();await refreshContext();await researchMergeHistory();A('research-merge-status').textContent=result.operation.restored_subscriptions?.length?'研究本文と購読先を戻しました。購読は停止を維持しています。':'統合先を編集前へ戻しました。元の研究と購読設定は変更していません。';
      }catch(error){report(error);}finally{researchMergeBusy=false;researchMergeControls();}
    });card.append(undo);}A('research-merge-history').append(card);
  }researchMergeControls();
}
async function researchMergeLoad(){
  if(!room||researchMergeBusy)return;
  if(researchMergeDirty()){notice('編集中の統合案を保存・破棄してください。');return;}
  const selected=room.id;researchMergeBusy=true;researchMergeControls();
  try{const refs=await api('/api/autonomy-references?room='+encodeURIComponent(selected));if(room?.id!==selected)return;
    researchMergeReferences=refs;researchMergeRoom=selected;researchMergeClear();
    for(const id of ['research-merge-target','research-merge-source']){const value=A(id).value;A(id).replaceChildren(new Option('選択…',''),...refs.documents.filter(item=>item.kind==='thread').map(item=>new Option(item.title+' / '+item.state,item.id)));if(refs.documents.some(item=>item.id===value))A(id).value=value;}
    await researchMergeHistory();
  }finally{researchMergeBusy=false;researchMergeControls();}
}
A('research-merge-compare').addEventListener('click',async()=>{
  if(!room||researchMergeBusy)return;if(researchMergeDirty()){notice('編集中の統合案を保存・破棄してください。');return;}
  const selected=room.id,target=A('research-merge-target').value,source=A('research-merge-source').value;researchMergeBusy=true;researchMergeControls();
  try{const data=await api('/api/research-merge-preview?'+new URLSearchParams({room:selected,target,source}));if(room?.id!==selected)return;
    researchMergePreview=data;researchMergeOperation=null;const form=data.proposal;
    A('research-merge-before').textContent=JSON.stringify(data.target,null,2);A('research-merge-original').textContent=JSON.stringify(data.source,null,2);A('research-merge-warnings').textContent=data.warnings.join('\n');
    for(const key of ['title','body','state','next_action'])A('research-merge-'+key.replace('_','-')).value=form[key];
    A('research-merge-findings').value=form.details.findings||'';A('research-merge-questions').value=(form.details.open_questions||[]).join('\n');
    A('research-merge-links').replaceChildren(...researchMergeReferences.documents.filter(item=>item.id!==target).map(item=>new Option(item.title,item.id)));activitySelect('research-merge-links',form.details.links||[]);
    A('research-merge-slots').replaceChildren(...researchMergeReferences.slots.map(slot=>new Option(slot,slot)));activitySelect('research-merge-slots',form.details.working_slots||[]);
    A('research-merge-subscriptions').replaceChildren(...data.references.source.subscriptions.filter(s=>s.source==='local'&&!s.archived).map(s=>new Option(s.focus+' / '+(s.enabled?'有効 → 移管後停止':'停止中'),s.id)));
    researchMergeBaseline=JSON.stringify(researchMergeInput());A('research-merge-status').textContent='内容はまだ反映していません。比較して修正し、確認後に保存してください。';
  }catch(error){report(error);}finally{researchMergeBusy=false;researchMergeControls();}
});
for(const [id,key] of [['research-merge-target','target'],['research-merge-source','source']])A(id).addEventListener('change',()=>{
  if(researchMergeDirty()){A(id).value=researchMergePreview[key].id;notice('編集中の統合案を保存・破棄してください。');return;}researchMergeClear();
});
A('research-merge-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!researchMergePreview||researchMergeBusy||busy)return;if(autoDocumentDirty()){notice('活動記録の未保存入力を保存・破棄してください。');return;}
  const snapshot=researchMergePreview,edited=researchMergeForm(),transfers=activitySelected('research-merge-subscriptions'),signature=JSON.stringify([snapshot.revision,edited,transfers]);if(researchMergeOperation?.signature!==signature)researchMergeOperation={signature,id:crypto.randomUUID()};
  researchMergeBusy=true;researchMergeControls();
  try{const result=await api('/api/research-merge-apply',{room:snapshot.room,target:snapshot.target.id,source:snapshot.source.id,revision:snapshot.revision,edited,transfer_subscription_ids:transfers,request_id:researchMergeOperation.id});if(room?.id!==snapshot.room)return;
    researchMergeClear();if(autoDoc?.id===snapshot.target.id){const doc=await api('/api/autonomy-document?'+new URLSearchParams({room:snapshot.room,id:snapshot.target.id}));if(room?.id===snapshot.room)autoFillDocument(doc);}
    await autoList();await refreshContext();await researchMergeHistory();A('research-merge-status').textContent=result.operation.state==='undone'?'この操作は既に取消済みです。再反映はしていません。':result.operation.subscription_moves?.length?'統合先へ保存し、選択したローカル購読を停止して移管しました。過去のレポートは保持します。':'統合先へ保存しました。元の研究・購読先・参照IDは保持しています。意味検索には索引の更新が必要な場合があります。';
  }catch(error){report(error);}finally{researchMergeBusy=false;researchMergeControls();}
});
A('research-merge-discard').addEventListener('click',()=>{researchMergeClear();researchMergeLoad().catch(report);});
A('research-merge-history-refresh').addEventListener('click',()=>researchMergeHistory().catch(report));
A('research-merge-history-older').addEventListener('click',()=>researchMergeHistory(researchMergeNext).catch(report));
const researchMergeTab=document.createElement('button');researchMergeTab.type='button';researchMergeTab.dataset.panel='research-merge-panel';researchMergeTab.textContent='研究の整理';A('workspace-autonomy').querySelector('.workspace-shortcuts').append(researchMergeTab);
researchMergeTab.addEventListener('click',()=>researchMergeLoad().catch(report));
