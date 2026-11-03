"use strict";
const A=id=>document.getElementById(id);
let autoRoom=null,autoConfig=null,autoDoc=null,autoBaseline='',autoSettingsBaseline='',autoPage=0,autoKind='purpose',autoSaving=false,autoOperation=null,autoRunOperation=null,autoSignature='';
let autoPurposeReview=null,autoSettingsEpoch=0;
const purposeLabels={core_values:'価値観',stable_interests:'長期的な関心',preferred_behaviors:'望む行動',avoid_behaviors:'避けたい行動',active_interests:'現在の関心',open_questions:'探求中の問い'};
const activityText={success_criteria:'success',resolution:'resolution',findings:'findings',preconditions:'preconditions',outcome:'outcome',lesson:'lesson'};
const activityFields={purpose:Object.keys(purposeLabels),goal:['progress','due_at','success_criteria'],question:['resolution'],thread:['findings','open_questions'],procedure:['preconditions','steps','success_criteria'],reflection:['outcome','lesson']};
function activitySelected(id){return Array.from(A(id).selectedOptions,option=>option.value);}
function activitySelect(id,values){const select=A(id);for(const value of values)if(!Array.from(select.options).some(option=>option.value===value))select.append(new Option(value,value));for(const option of select.options)option.selected=values.includes(option.value);}
function autoDocumentForm(){
  const details={priority:Number(A('activity-priority').value||0),links:activitySelected('activity-links'),working_slots:activitySelected('activity-slots')};
  for(const key of activityFields[autoKind]||[]){
    if(autoKind==='purpose')details[key]=A('purpose-'+key).value.split('\n').map(line=>line.trim()).filter(Boolean);
    else if(key==='progress')details[key]=Number(A('activity-progress').value||0);
    else if(key==='due_at')details[key]=A('activity-due').value?new Date(A('activity-due').value).toISOString():'';
    else if(key==='steps'||key==='open_questions')details[key]=A(key==='steps'?'activity-steps':'activity-questions').value.split('\n').map(line=>line.trim()).filter(Boolean);
    else details[key]=A('activity-'+activityText[key]).value;
  }
  return {title:A('autonomy-title').value,body:A('autonomy-body').value,state:A('autonomy-state').value,next_action:A('autonomy-next-action').value,details};
}
function autoSettingsForm(){return {enabled:A('autonomy-enabled').checked,interval_seconds:Number(A('autonomy-interval').value)*60,max_per_day:Number(A('autonomy-max-per-day').value),max_seconds:Number(A('autonomy-max-seconds').value),max_tokens:Number(A('autonomy-max-tokens').value),allow_external:A('autonomy-allow-external').checked,allow_web:A('autonomy-allow-web').checked,allow_proposals:A('autonomy-allow-proposals').checked,allow_updates:A('autonomy-allow-updates').checked};}
function autonomyDirty(){if(window.AkariSleep?.dirty())return true;if(window.AkariCharacterWizard?.dirty())return true;if(window.AkariMusic?.dirty())return true;if(window.AkariGateway?.dirty())return true;if(window.AkariExternal?.dirty())return true;if(window.AkariAppearance?.dirty())return true;if(window.AkariResonance?.dirty())return true;if(window.AkariSessions?.dirty())return true;return (!!window.AkariRetrievalPolicy&&window.AkariRetrievalPolicy.dirty())||(!!window.AkariPersona&&window.AkariPersona.dirty())||(!!window.AkariDiaryArchives&&window.AkariDiaryArchives.dirty())||(!!window.AkariRooms&&window.AkariRooms.dirty())||(typeof questionDirty==='function'&&questionDirty())||(typeof motivationDirty==='function'&&motivationDirty())||(!!autoRoom&&(JSON.stringify(autoDocumentForm())!==autoBaseline||JSON.stringify(autoSettingsForm())!==autoSettingsBaseline))||(typeof permissionDirty==='function'&&permissionDirty())||(typeof researchMergeDirty==='function'&&researchMergeDirty())||(typeof avatarDirty==='function'&&avatarDirty())||(typeof sharedDirty==='function'&&sharedDirty())||(typeof timelineDirty==='function'&&timelineDirty())||(typeof subscriptionDirty==='function'&&subscriptionDirty())||(typeof watchDirty==='function'&&watchDirty())||(typeof userMemoDirty==='function'&&userMemoDirty())||(typeof inboxSettingsDirty==='function'&&inboxSettingsDirty())||(typeof sleepScheduleDirty==='function'&&sleepScheduleDirty())||(typeof researchMaintDirty==='function'&&researchMaintDirty())||(typeof entityMaintDirty==='function'&&entityMaintDirty())||(typeof affectDirty==='function'&&affectDirty())||(typeof memorySaveDirty==='function'&&memorySaveDirty())||(typeof repetitionDirty==='function'&&repetitionDirty());}
function autoDocumentDirty(){return !!autoRoom&&JSON.stringify(autoDocumentForm())!==autoBaseline;}
function autoFillDocument(doc=null,review=null){
  autoPurposeReview=review;A('autonomy-save').textContent=review?'この提案を承認して保存':'記録を保存';A('purpose-review-mode').hidden=!review;
  autoDoc=doc;for(const key of ['title','body','state','next_action'])A('autonomy-'+key.replace('_','-')).value=doc?.[key]||(key==='state'?'active':'');
  const details=doc?.details||{};
  for(const key of Object.keys(purposeLabels))A('purpose-'+key).value=(details[key]||[]).join('\n');
  A('activity-priority').value=details.priority??0;A('activity-progress').value=details.progress??0;
  const date=details.due_at?new Date(details.due_at):null;A('activity-due').value=date?new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16):'';
  for(const [key,id] of Object.entries(activityText))A('activity-'+id).value=details[key]||'';
  A('activity-steps').value=(details.steps||[]).join('\n');A('activity-questions').value=(details.open_questions||[]).join('\n');
  activitySelect('activity-links',details.links||[]);activitySelect('activity-slots',details.working_slots||[]);
  for(const section of document.querySelectorAll('[data-activity-kind]'))section.hidden=!section.dataset.activityKind.split(' ').includes(autoKind);
  autoBaseline=JSON.stringify(autoDocumentForm());autoOperation=null;
}
function autoFillSettings(config){
  autoSettingsEpoch++;autoConfig=config;
  for(const key of ['enabled','allow_web','allow_proposals','allow_updates','allow_external'])A('autonomy-'+key.replaceAll('_','-')).checked=config[key];
  A('autonomy-interval').value=config.interval_seconds/60;
  for(const key of ['max_per_day','max_seconds','max_tokens'])A('autonomy-'+key.replaceAll('_','-')).value=config[key];
  autoSettingsBaseline=JSON.stringify(autoSettingsForm());
}
async function autoList(){
  if(!room)return;const selected=room.id,kind=autoKind,page=autoPage;
  const [data,refs]=await Promise.all([api('/api/autonomy-documents?'+new URLSearchParams({room:selected,kind,offset:page})),api('/api/autonomy-references?room='+encodeURIComponent(selected))]);
  if(room?.id!==selected||autoKind!==kind||autoPage!==page)return;
  const select=A('autonomy-documents');select.replaceChildren(new Option('新規作成',''));
  for(const item of data.entries)select.append(new Option(item.title+' / '+item.state,item.id));
  if(autoDoc?.id&&!data.entries.some(item=>item.id===autoDoc.id))select.append(new Option(autoDoc.title,autoDoc.id));
  select.value=autoDoc?.id||'';A('autonomy-prev').disabled=autoPage===0;A('autonomy-next').disabled=!data.more;
  const links=activitySelected('activity-links'),slots=activitySelected('activity-slots');
  A('activity-links').replaceChildren(...refs.documents.filter(item=>item.id!==autoDoc?.id).map(item=>new Option(item.title+' / '+item.kind,item.id)));
  A('activity-slots').replaceChildren(...refs.slots.map(name=>new Option(name,name)));
  activitySelect('activity-links',links);activitySelect('activity-slots',slots);
}
async function refreshAutonomy(force=false){
  if(!room)return;const selected=room.id,epoch=autoSettingsEpoch,data=await api('/api/autonomy?room='+encodeURIComponent(selected));if(room?.id!==selected||epoch!==autoSettingsEpoch||autoSaving)return;
  await window.AkariSleep?.load();
  if(room?.id!==selected||epoch!==autoSettingsEpoch||autoSaving)return;
  const changed=autoRoom!==selected;
  if(changed){autoRoom=selected;autoPage=0;autoKind='purpose';A('autonomy-kind').value=autoKind;autoFillDocument();autoFillSettings(data.settings);autoRunOperation=null;await autoList();}
  else if(force&&JSON.stringify(autoSettingsForm())===autoSettingsBaseline)autoFillSettings(data.settings);
  const connection=data.connection||{},provider={local:'ローカル',openai:'OpenAI',gemini:'Gemini',anthropic:'Anthropic',compatible:'OpenAI互換'}[connection.provider]||'未設定';
  A('autonomy-run-status').textContent=data.warning||'定期実行：'+(data.settings.enabled?'有効':'無効')+' / 実行接続：'+provider+(connection.model?' / '+connection.model:'')+'。'+(connection.state==='ready'?'会話中は待機します。':(connection.error||'接続先の準備を待っています。'));
  const signature=JSON.stringify([selected,data.runs]);if(signature===autoSignature)return;autoSignature=signature;
  const states={queued:'待機中',running:'実行中',succeeded:'完了',partial:'一部未完了',failed:'失敗',cancelled:'中止',unknown:'結果未確認'};A('autonomy-run-list').replaceChildren();
  for(const run of data.runs){
    const card=document.createElement('section'),title=document.createElement('h3'),content=document.createElement('pre'),meta=document.createElement('p');
    title.textContent=(states[run.state]||run.state)+' / '+({manual:'手動',scheduled:'定期',research:'研究購読',memory:'会話の記憶整理',memory_route:'会話草案の自動分類'}[run.trigger]||run.trigger);content.textContent=[run.text,run.error].filter(Boolean).join('\n\n')||'まだ結果はありません。';
    meta.className='muted small';meta.textContent=run.created_at+' / '+run.id;card.append(title,meta,content);
    if(run.timeline_id){const trace=document.createElement('button');trace.type='button';trace.textContent='この実行の過程を見る';trace.addEventListener('click',async()=>{try{document.querySelector('[data-panel="autonomy-timeline"]').click();await timelineLoad(run.timeline_id);}catch(error){report(error);}});card.append(trace);}
    if(['queued','running'].includes(run.state)){
      const cancel=document.createElement('button');cancel.type='button';cancel.textContent=run.cancel_requested?'中止を要求済み':'取り消す';cancel.disabled=!!run.cancel_requested;
      cancel.addEventListener('click',async()=>{cancel.disabled=true;try{await api('/api/autonomy-cancel',{room:selected,id:run.id,revision:run.revision});await refreshAutonomy();}catch(error){report(error);cancel.disabled=false;}});card.append(cancel);
    }
    if(run.state==='unknown'&&!run.reviewed){
      const ack=document.createElement('button');ack.type='button';ack.textContent='反映状況を確認した（定期実行を再開可能にする）';
      ack.addEventListener('click',async()=>{ack.disabled=true;try{await api('/api/autonomy-acknowledge',{room:selected,id:run.id,revision:run.revision});await refreshAutonomy();}catch(error){report(error);ack.disabled=false;}});card.append(ack);
    }A('autonomy-run-list').append(card);
  }
}
A('autonomy-kind').addEventListener('change',async()=>{if(autoDocumentDirty()){A('autonomy-kind').value=autoKind;notice('未保存の活動記録があります。保存または破棄してください。');return;}autoKind=A('autonomy-kind').value;autoPage=0;autoFillDocument();try{await autoList();}catch(error){report(error);}});
A('autonomy-documents').addEventListener('change',async()=>{
  if(autoDocumentDirty()){A('autonomy-documents').value=autoDoc?.id||'';notice('未保存の活動記録があります。');return;}
  const key=A('autonomy-documents').value,selected=room.id;if(!key){autoFillDocument();return;}
  try{const doc=await api('/api/autonomy-document?'+new URLSearchParams({room:selected,id:key}));if(room?.id===selected&&A('autonomy-documents').value===key)autoFillDocument(doc);}catch(error){report(error);}
});
A('autonomy-document-form').addEventListener('submit',async event=>{
  event.preventDefault();if(autoSaving||!room)return;autoSaving=true;A('autonomy-save').disabled=true;
  const selected=room.id,review=autoPurposeReview,form=autoDocumentForm(),data={room:selected,kind:autoKind,...form,id:autoDoc?.id||'',revision:autoDoc?.revision||''},signature=JSON.stringify([data,review?.id,review?.revision]);
  if(autoOperation?.signature!==signature)autoOperation={signature,id:crypto.randomUUID()};
  try{
    const result=review?await api('/api/purpose-review',{room:selected,id:review.id,revision:review.revision,action:'approve',edited:form,request_id:autoOperation.id}):await api('/api/autonomy-save',{...data,request_id:autoOperation.id});
    if(room.id===selected){
      if(review&&typeof purposeProposal!=='undefined'&&purposeProposal?.id===review.id){purposeProposal=result.proposal;purposeProposalPaint();}
      if(autoKind===data.kind&&(autoDoc?.id||'')===data.id&&autoPurposeReview?.id===review?.id&&JSON.stringify(autoDocumentForm())===JSON.stringify(form)){autoFillDocument(result.document);await autoList();A('autonomy-record-status').textContent=review?'提案を承認して目的を保存しました。':'記録を保存しました。';}
      else A('autonomy-record-status').textContent='保存は完了しました。保存待ちの間に編集された入力を保持しています。再読込して状態を確認してください。';
    }
  }
  catch(error){report(error);}finally{autoSaving=false;A('autonomy-save').disabled=false;}
});
A('autonomy-discard').addEventListener('click',()=>autoFillDocument(autoDoc,autoPurposeReview));
A('autonomy-document-reload').addEventListener('click',async()=>{const selected=room.id;if(autoPurposeReview){try{const data=await api('/api/purpose-profile?room='+encodeURIComponent(selected));if(room?.id===selected){autoFillDocument(data.document);await autoList();}}catch(error){report(error);}return;}if(!autoDoc){autoFillDocument();return;}const key=autoDoc.id;try{const doc=await api('/api/autonomy-document?'+new URLSearchParams({room:selected,id:key}));if(room?.id===selected&&autoDoc?.id===key){autoFillDocument(doc);A('autonomy-record-status').textContent='未保存の本文を破棄して最新の記録を読み込みました。';}}catch(error){report(error);}});
A('autonomy-new').addEventListener('click',()=>{if(autoDocumentDirty()){notice('保存または入力を破棄してください。');return;}autoFillDocument();A('autonomy-documents').value='';});
A('autonomy-list-refresh').addEventListener('click',()=>autoList().catch(report));
for(const [id,delta] of [['autonomy-prev',-20],['autonomy-next',20]])A(id).addEventListener('click',()=>{autoPage=Math.max(0,autoPage+delta);autoList().catch(report);});
A('autonomy-settings-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!autoConfig||autoSaving)return;autoSaving=true;A('autonomy-settings-save').disabled=true;
  const selected=room.id;
  try{const result=await api('/api/autonomy-settings',{room:selected,settings:autoSettingsForm(),revision:autoConfig.revision});if(room.id===selected){autoFillSettings(result);A('autonomy-settings-status').textContent='実行設定を保存しました。待機中の旧設定の処理は開始しません。';}}
  catch(error){report(error);}finally{autoSaving=false;A('autonomy-settings-save').disabled=false;}
});
A('autonomy-settings-reload').addEventListener('click',async()=>{const selected=room?.id;if(!selected||autoSaving)return;const epoch=++autoSettingsEpoch;try{const data=await api('/api/autonomy?room='+encodeURIComponent(selected));if(room?.id===selected&&epoch===autoSettingsEpoch&&!autoSaving){autoFillSettings(data.settings);A('autonomy-settings-status').textContent='未保存の設定を破棄して読み込みました。';}}catch(error){report(error);}});
A('autonomy-run').addEventListener('click',async()=>{if(!room)return;const button=A('autonomy-run');button.disabled=true;if(!autoRunOperation)autoRunOperation=crypto.randomUUID();
  try{await api('/api/autonomy-run',{room:room.id,request_id:autoRunOperation});autoRunOperation=null;await refreshAutonomy();}catch(error){report(error);}finally{button.disabled=false;}});
A('autonomy-refresh').addEventListener('click',()=>refreshAutonomy().catch(report));
A('tab-autonomy').addEventListener('click',()=>refreshAutonomy(true).catch(report));

// Shared procedures have no room-specific links or execution settings.
let sharedDoc=null,sharedBaseline='',sharedPage=0,sharedRevision='',sharedBusy=false,sharedOperation=null,sharedListRequest=0;
function sharedForm(){return {title:A('shared-title').value,body:A('shared-body').value,state:A('shared-state').value,details:{preconditions:A('shared-preconditions').value,steps:A('shared-steps').value.split('\n').map(s=>s.trim()).filter(Boolean),success_criteria:A('shared-success').value}};}
function sharedDirty(){return sharedBaseline!==''&&JSON.stringify(sharedForm())!==sharedBaseline;}
function sharedFill(doc=null){
  sharedDoc=doc;A('shared-title').value=doc?.title||'';A('shared-body').value=doc?.body||'';A('shared-state').value=doc?.state||'active';
  A('shared-preconditions').value=doc?.details?.preconditions||'';A('shared-steps').value=(doc?.details?.steps||[]).join('\n');A('shared-success').value=doc?.details?.success_criteria||'';
  sharedBaseline=JSON.stringify(sharedForm());sharedOperation=null;
}
async function sharedList(){
  const page=sharedPage,request=++sharedListRequest,data=await api('/api/shared-procedures?'+new URLSearchParams({offset:page,revision:sharedRevision}));
  if(sharedPage!==page||request!==sharedListRequest)return;sharedRevision=data.revision;
  A('shared-list').replaceChildren(new Option('新規作成',''),...data.entries.map(item=>new Option(item.title+(item.state==='archived'?' / アーカイブ':''),item.id)));
  if(sharedDoc&&!data.entries.some(item=>item.id===sharedDoc.id))A('shared-list').append(new Option(sharedDoc.title,sharedDoc.id));
  A('shared-list').value=sharedDoc?.id||'';A('shared-prev').disabled=page===0;A('shared-next').disabled=!data.more;
}
A('shared-list').addEventListener('change',async()=>{
  if(sharedBusy||sharedDirty()){A('shared-list').value=sharedDoc?.id||'';notice('共有手順を保存するか、未保存の入力を破棄してください。');return;}
  const id=A('shared-list').value,snapshot=JSON.stringify(sharedForm());
  try{const doc=id?await api('/api/shared-procedure?id='+encodeURIComponent(id)):null;if(A('shared-list').value!==id)return;
    if(JSON.stringify(sharedForm())!==snapshot){A('shared-list').value=sharedDoc?.id||'';notice('読み込み待ちの間の入力を保持しました。保存・破棄してから選び直してください。');return;}sharedFill(doc);
  }catch(error){A('shared-list').value=sharedDoc?.id||'';report(error);}
});
A('shared-new').addEventListener('click',()=>{if(sharedBusy||sharedDirty()){notice('共有手順を保存するか、未保存の入力を破棄してください。');return;}sharedFill();A('shared-list').value='';});
A('shared-reload').addEventListener('click',async()=>{if(sharedBusy)return;const id=sharedDoc?.id||'',snapshot=JSON.stringify(sharedForm());try{const doc=id?await api('/api/shared-procedure?id='+encodeURIComponent(id)):null;
  if((sharedDoc?.id||'')!==id||JSON.stringify(sharedForm())!==snapshot){notice('読み込み待ちの間の入力を保持しました。');return;}sharedFill(doc);A('shared-status').textContent='未保存の入力を破棄しました。';}catch(error){report(error);}});
A('shared-refresh').addEventListener('click',()=>{sharedPage=0;sharedRevision='';sharedList().catch(report);});
for(const [id,delta] of [['shared-prev',-20],['shared-next',20]])A(id).addEventListener('click',()=>{sharedPage=Math.max(0,sharedPage+delta);sharedList().catch(report);});
A('shared-form').addEventListener('submit',async event=>{
  event.preventDefault();if(sharedBusy)return;sharedBusy=true;A('shared-save').disabled=true;
  const form=sharedForm(),data={...form,id:sharedDoc?.id||'',revision:sharedDoc?.revision||''},signature=JSON.stringify(data);
  if(sharedOperation?.signature!==signature)sharedOperation={signature,id:crypto.randomUUID()};
  try{const result=await api('/api/shared-procedure-save',{...data,request_id:sharedOperation.id});
    if((sharedDoc?.id||'')===data.id&&JSON.stringify(sharedForm())===JSON.stringify(form))sharedFill(result.document);
    else {sharedDoc=result.document;sharedBaseline=JSON.stringify({title:result.document.title,body:result.document.body,state:result.document.state,details:result.document.details});}
    sharedRevision='';await sharedList();A('shared-status').textContent='全ルーム共通の手順を保存しました。実行はしていません。';
  }catch(error){report(error);}finally{sharedBusy=false;A('shared-save').disabled=false;}
});
A('shared-copy').addEventListener('click',()=>{
  if(!room||sharedBusy||sharedDirty()||autoDocumentDirty()){notice('未保存の入力を保存・破棄してからコピーしてください。');return;}
  if(!sharedDoc){notice('保存済みの共有手順を選択してください。');return;}
  autoKind='procedure';A('autonomy-kind').value=autoKind;autoPage=0;autoFillDocument();
  A('autonomy-title').value=sharedDoc.title;A('autonomy-body').value=sharedDoc.body;A('autonomy-state').value='paused';
  A('activity-preconditions').value=sharedDoc.details.preconditions||'';A('activity-steps').value=(sharedDoc.details.steps||[]).join('\n');A('activity-success').value=sharedDoc.details.success_criteria||'';
  showWorkspace('autonomy');revealPanel('autonomy-records');autoList().catch(report);A('autonomy-record-status').textContent='このルーム用の未保存の下書きです。内容を確認して保存してください。';
});
A('shared-from-private').addEventListener('click',()=>{
  if(sharedBusy||sharedDirty()){notice('共有手順を保存・破棄してからコピーしてください。');return;}
  const form=autoDocumentForm();sharedFill();A('shared-title').value=form.title;A('shared-body').value=form.body;
  A('shared-preconditions').value=form.details.preconditions||'';A('shared-steps').value=(form.details.steps||[]).join('\n');A('shared-success').value=form.details.success_criteria||'';
  showWorkspace('shared');revealPanel('shared-procedures-panel');A('shared-status').textContent='未保存の共有用下書きです。人格・秘密情報を除いてから、共通として保存してください。';
});
sharedFill();
const sharedTab=document.querySelector('[data-panel="shared-procedures-panel"]');
sharedTab.addEventListener('click',()=>{sharedPage=0;sharedRevision='';sharedList().catch(report);});
