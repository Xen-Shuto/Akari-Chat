"use strict";
let timelineRoom=null,timelineDoc=null,timelinePage=0,timelineStepPage=0,timelineBaseline='',timelineOperation=null,timelineBusy=false,timelineSelectedStep='';
let timelineCandidate=null,timelineCandidateOperation=null,timelineCandidateBusy=false;
function timelineForm(){return {title:A('timeline-title').value,phase:A('timeline-phase').value,summary:A('timeline-summary').value,step_id:A('timeline-step').value};}
function timelineDirty(){return !!timelineRoom&&JSON.stringify(timelineForm())!==timelineBaseline;}
function timelineFill(){
  A('timeline-title').value=timelineDoc?.title||'';A('timeline-summary').value='';A('timeline-phase').value='observe';
  A('timeline-title').readOnly=!!timelineDoc;timelineSelectedStep='';
  for(const id of ['timeline-summary','timeline-phase','timeline-step','timeline-submit'])A(id).disabled=!!timelineDoc&&timelineDoc.state!=='active';
  A('timeline-step').replaceChildren(new Option('新しいステップ',''));
  for(const step of timelineDoc?.steps||[])if(step.actor!=='system')A('timeline-step').append(new Option(step.phase+' / '+step.summary.slice(0,50),step.id));
  timelineBaseline=JSON.stringify(timelineForm());timelineOperation=null;
}
async function timelineList(){
  if(!room)return;const selected=room.id,page=timelinePage;
  const data=await api('/api/timelines?'+new URLSearchParams({room:selected,offset:page}));if(room?.id!==selected||timelinePage!==page)return;
  const select=A('timeline-list');select.replaceChildren(new Option('新規作成',''));
  for(const item of data.entries)select.append(new Option(item.title+' / '+item.state,item.id));
  if(timelineDoc&&!data.entries.some(item=>item.id===timelineDoc.id))select.append(new Option(timelineDoc.title,timelineDoc.id));
  select.value=timelineDoc?.id||'';A('timeline-prev').disabled=page===0;A('timeline-next').disabled=!data.more;
}
function timelinePaint(){
  const area=A('timeline-steps');area.replaceChildren();
  for(const step of timelineDoc?.steps||[]){
    const card=document.createElement('section'),title=document.createElement('h3'),body=document.createElement('p'),meta=document.createElement('p');
    title.textContent=step.phase+' / '+(step.actor==='system'?'アプリの確認記録':step.actor==='user'?'ユーザーの記録':'AIの自己申告');
    body.textContent=step.summary;meta.className='muted small';meta.textContent=step.created_at+(step.edited_at?' / 訂正あり':'');card.append(title,body,meta);
    if(step.evidence?.tool){const receipt=document.createElement('p');receipt.textContent=step.evidence.tool+' / '+step.evidence.status;card.append(receipt);}
    if(step.evidence?.draft_id){const open=document.createElement('button');open.type='button';open.textContent='作成した案を確認';open.addEventListener('click',async()=>{
      if(updateReviewDirty()||resourceDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
      try{await updateReviewList();const id=String(step.evidence.draft_id);
        if(!Array.from(A('updates-list').options).some(o=>o.value===id)){notice('この案は確認待ち一覧にありません。保存済み・見送り済みの可能性があります。');return;}
        showWorkspace('memory');revealPanel('updates-panel');A('updates-list').value=id;A('updates-list').dispatchEvent(new Event('change'));
      }catch(e){report(e);}
    });card.append(open);}area.append(card);
  }
  A('timeline-status').textContent=timelineDoc?timelineDoc.state+' / '+timelineDoc.step_count+'件。'+(timelineDoc.source_type==='sleep'?(timelineDoc.auto_save_state==='saved'?'夜間整理案の原本保存を記録しました。索引反映の完了とは別です。':timelineDoc.auto_save_state==='unknown'?'原本の自動保存結果は未確認です。草案・本文・バックアップを確認してください。':'夜間整理の案作成の記録です。原本の保存完了とは異なります。'):timelineDoc.source_type==='memory'?'会話からの記憶草案の記録です。原本の保存完了とは異なります。':timelineDoc.source_run?'自律実行の終了状態はアプリが記録します。':'記録・終了の申告は、実際の行動完了の証明ではありません。'):'新しいタイムラインを作成できます。';
  const input=timelineDoc?.input_references;
  if(input){const note=document.createElement('p');note.className='muted small';note.textContent='開始時の入力：'+(input.activity?input.activity.title+(input.activity.changed?'（現在は変更済み／未配置）':''):'活動の指定なし')+(input.working?' / 作業メモ：'+input.working.slot:'')+'。'+input.notice;area.prepend(note);}
  A('timeline-step-prev').disabled=timelineStepPage===0;A('timeline-step-next').disabled=!timelineDoc?.more;
  A('timeline-finish').disabled=!timelineDoc||!!timelineDoc.source_run||timelineDoc.state!=='active';
  A('timeline-procedure-create').disabled=!timelineDoc?.source_run||timelineDoc.state!=='completed'||['sleep','memory','question'].includes(timelineDoc.source_type);
}
async function timelineLoad(key,offset=0){
  if(timelineDirty()){notice('タイムラインの未保存入力があります。保存または破棄してください。');return;}
  const selected=room.id;
  const data=key?await api('/api/timeline?'+new URLSearchParams({room:selected,id:key,offset,revision:offset?timelineDoc?.revision||'':''})):null;
  if(room?.id!==selected)return;timelineRoom=selected;timelineDoc=data;timelineStepPage=offset;timelineFill();timelinePaint();await timelineList();
  timelineCandidate=null;A('timeline-procedure-open').disabled=true;A('timeline-procedure-status').textContent='';
}
async function timelineRefreshRoom(){
  if(!room)return;if(timelineRoom!==room.id){timelineRoom=room.id;timelineDoc=null;timelinePage=0;timelineStepPage=0;timelineCandidate=null;timelineCandidateOperation=null;A('timeline-procedure-title').value='';A('timeline-procedure-open').disabled=true;A('timeline-procedure-status').textContent='';timelineFill();timelinePaint();}await timelineList();
}
async function timelineChange(action){
  if(timelineBusy||!room)return;timelineBusy=true;const selected=room.id;
  const data={room:selected,action,id:action==='start'?'':timelineDoc?.id||'',revision:action==='start'?'':timelineDoc?.revision||'',...timelineForm()},signature=JSON.stringify(data);
  if(timelineOperation?.signature!==signature)timelineOperation={signature,id:crypto.randomUUID()};
  try{
    const result=await api('/api/timeline-change',{...data,request_id:timelineOperation.id});
    if(room?.id!==selected)return;
    timelineBaseline=JSON.stringify(timelineForm());await timelineLoad(result.document.id);
  }catch(error){report(error);}finally{timelineBusy=false;}
}
A('timeline-list').addEventListener('change',()=>{const key=A('timeline-list').value;if(timelineDirty()){A('timeline-list').value=timelineDoc?.id||'';notice('未保存のタイムラインがあります。');return;}timelineLoad(key).catch(report);});
A('timeline-step').addEventListener('change',()=>{
  const previous={...timelineForm(),step_id:timelineSelectedStep};
  if(JSON.stringify(previous)!==timelineBaseline){A('timeline-step').value=timelineSelectedStep;notice('未保存のステップがあります。保存または破棄してください。');return;}
  timelineSelectedStep=A('timeline-step').value;
  const step=timelineDoc?.steps.find(item=>item.id===A('timeline-step').value);A('timeline-summary').value=step?.summary||'';A('timeline-phase').value=step?.phase||'observe';
  timelineBaseline=JSON.stringify(timelineForm());
});
A('timeline-form').addEventListener('submit',event=>{event.preventDefault();timelineChange(!timelineDoc?'start':A('timeline-step').value?'edit':'append').catch(report);});
A('timeline-finish').addEventListener('click',()=>{if(timelineDirty()){notice('先にステップを保存または破棄してください。');return;}timelineChange('finish').catch(report);});
A('timeline-new').addEventListener('click',()=>timelineLoad('').catch(report));
A('timeline-discard').addEventListener('click',()=>timelineFill());
A('timeline-reload').addEventListener('click',async()=>{const key=timelineDoc?.id;timelineFill();try{await timelineLoad(key||'');}catch(error){report(error);}});
A('timeline-refresh').addEventListener('click',()=>timelineList().catch(report));
for(const [id,delta] of [['timeline-prev',-20],['timeline-next',20]])A(id).addEventListener('click',()=>{timelinePage=Math.max(0,timelinePage+delta);timelineList().catch(report);});
for(const [id,delta] of [['timeline-step-prev',-20],['timeline-step-next',20]])A(id).addEventListener('click',()=>timelineLoad(timelineDoc?.id,Math.max(0,timelineStepPage+delta)).catch(report));
document.querySelector('[data-panel="autonomy-timeline"]').addEventListener('panelactivate',()=>timelineRefreshRoom().catch(report));
A('timeline-procedure-form').addEventListener('submit',async event=>{
  event.preventDefault();if(timelineCandidateBusy||!timelineDoc)return;timelineCandidateBusy=true;A('timeline-procedure-create').disabled=true;
  const selected=room.id,key=timelineDoc.id,data={room:selected,id:key,revision:timelineDoc.revision,title:A('timeline-procedure-title').value},signature=JSON.stringify(data);
  if(timelineCandidateOperation?.signature!==signature)timelineCandidateOperation={signature,id:crypto.randomUUID()};
  try{
    const result=await api('/api/timeline-procedure',{...data,request_id:timelineCandidateOperation.id});
    if(room?.id===selected&&timelineDoc?.id===key){timelineCandidate=result.document;A('timeline-procedure-open').disabled=false;A('timeline-procedure-status').textContent='保留状態の手順候補を作成しました。適用条件・成功条件を確認してください。'+(result.truncated?'先頭10操作のみ抽出しています。':'');}
  }catch(error){report(error);}finally{timelineCandidateBusy=false;timelinePaint();}
});
A('timeline-procedure-open').addEventListener('click',async()=>{
  if(!timelineCandidate)return;if(autoDocumentDirty()){notice('活動記録の未保存入力があります。保存または破棄してください。');return;}
  autoKind='procedure';A('autonomy-kind').value=autoKind;autoPage=0;autoFillDocument(timelineCandidate);revealPanel('autonomy-records');try{await autoList();}catch(error){report(error);}
});
