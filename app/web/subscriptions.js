"use strict";
let subRoom=null,subDoc=null,subBaseline='',subData=[],subBusy=false,subOperation=null,subRefresh=0;
const subFields=['thread_id','focus','source','frequency','at','weekday','enabled','auto_apply','max_per_day'];
function subscriptionForm(){return Object.fromEntries(subFields.map(key=>[key,['enabled','auto_apply'].includes(key)?A('sub-'+key).checked:['weekday','max_per_day'].includes(key)?Number(A('sub-'+key).value):A('sub-'+key).value]));}
function subscriptionDirty(){return !!subRoom&&JSON.stringify(subscriptionForm())!==subBaseline;}
function subscriptionFill(doc=null){
  subDoc=doc;const defaults={thread_id:A('sub-thread_id').options[0]?.value||'',focus:'',source:'local',frequency:'manual',at:'09:00',weekday:0,enabled:false,auto_apply:false,max_per_day:1};
  for(const key of subFields){const field=A('sub-'+key);if(['enabled','auto_apply'].includes(key))field.checked=doc?.[key]??defaults[key];else field.value=doc?.[key]??defaults[key];}
  A('sub-auto_apply').disabled=A('sub-source').value!=='local';
  A('sub-select').value=doc?.id||'';subBaseline=JSON.stringify(subscriptionForm());subOperation=null;
  A('sub-run').disabled=!doc||!!doc.archived;A('sub-archive').disabled=!doc||!!doc.archived;
  A('sub-status').textContent=doc?[(doc.archived?'保管済み':doc.enabled?'有効':'停止中'),doc.next_due?'次回 '+new Date(doc.next_due*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})+' JST':'自動実行なし',doc.last_error].filter(Boolean).join(' / '):'新規購読。保存するまで実行しません。';
}
async function subscriptionLoad(reset=false){
  if(!room||subBusy&&!reset)return;const selected=room.id,ticket=++subRefresh,inputBefore=JSON.stringify(subscriptionForm());
  const [data,refs]=await Promise.all([api('/api/research-subscriptions?room='+encodeURIComponent(selected)),api('/api/autonomy-references?room='+encodeURIComponent(selected))]);
  if(room?.id!==selected||ticket!==subRefresh)return;
  if(subRoom!==selected||reset&&JSON.stringify(subscriptionForm())===inputBefore){
    subRoom=selected;subData=data.subscriptions;
    A('sub-select').replaceChildren(new Option('新規作成',''),...subData.map(doc=>new Option((doc.archived?'[保管] ':doc.enabled?'[有効] ':'[停止] ')+(refs.documents.find(t=>t.id===doc.thread_id)?.title||doc.thread_id),doc.id)));
    A('sub-thread_id').replaceChildren(...refs.documents.filter(d=>d.kind==='thread'&&d.state==='active').map(d=>new Option(d.title,d.id)));
    if(subDoc&&!A('sub-thread_id').querySelector('option[value="'+subDoc.thread_id+'"]'))A('sub-thread_id').append(new Option('以前の研究（停止・削除済み）',subDoc.thread_id));
    subscriptionFill(reset&&subDoc?subData.find(d=>d.id===subDoc.id)||null:null);
  }
  A('sub-reports').replaceChildren();
  for(const item of data.reports){
    const card=document.createElement('section'),title=document.createElement('h3'),text=document.createElement('pre'),sources=document.createElement('p');
    title.textContent=item.title+' / '+({ready:'確認待ち',applied:'追記済み',unverified:'未検証・未完了'}[item.state]||item.state)+' / '+item.run_state;
    text.textContent=item.text||'本文なし';sources.textContent=item.created_at+' / 参照: '+item.sources.map(s=>s.url||s.reference||s.path||s.title||s.id).join('、');card.append(title,sources,text);if(item.auto_apply_state){const note=document.createElement('p');note.textContent=item.auto_apply_state==='applied'?'設定に従って自動追記しました。':item.auto_apply_error;card.append(note);}
    if(item.state==='ready'){
      const apply=document.createElement('button');apply.type='button';apply.textContent='確認して研究本文へ追記';const request=crypto.randomUUID();
      apply.addEventListener('click',async()=>{if(autoDocumentDirty()){notice('活動記録の未保存入力を保存または破棄してください。');return;}apply.disabled=true;try{await api('/api/research-report-apply',{room:selected,id:item.id,revision:item.revision,request_id:request});if(room?.id===selected){await subscriptionLoad();await autoList();}}catch(error){report(error);apply.disabled=false;}});card.append(apply);
    }A('sub-reports').append(card);
  }
}
async function subscriptionSave(archive=false){
  if(!room||subBusy)return;const selected=room.id,form=subscriptionForm(),payload={room:selected,config:form,id:subDoc?.id||'',revision:subDoc?.revision||'',archive};
  const signature=JSON.stringify(payload);if(subOperation?.signature!==signature)subOperation={signature,id:crypto.randomUUID()};
  subBusy=true;
  try{const result=await api('/api/research-subscription-save',{...payload,request_id:subOperation.id});if(room?.id===selected&&JSON.stringify(subscriptionForm())===JSON.stringify(form)){subDoc=result.document;subBaseline=JSON.stringify(subscriptionForm());await subscriptionLoad(true);}else notice('購読の保存は完了しました。入力の変更は保持しています。');}catch(error){report(error);}finally{subBusy=false;}
}
A('sub-form').addEventListener('submit',event=>{event.preventDefault();subscriptionSave().catch(report);});
A('sub-archive').addEventListener('click',()=>{if(subscriptionDirty()){notice('先に未保存の購読設定を保存または破棄してください。');return;}subscriptionSave(true).catch(report);});
A('sub-select').addEventListener('change',()=>{if(subBusy||subscriptionDirty()){A('sub-select').value=subDoc?.id||'';notice('未保存の購読設定があります。保存または破棄してください。');return;}subscriptionFill(subData.find(d=>d.id===A('sub-select').value)||null);});
A('sub-reload').addEventListener('click',()=>{if(subBusy)return;subBaseline=JSON.stringify(subscriptionForm());subscriptionLoad(true).catch(report);});
let subRunRequest=null;
A('sub-run').addEventListener('click',async()=>{
  if(!room||!subDoc||subBusy)return;if(subscriptionDirty()){notice('先に購読設定を保存してください。');return;}
  const selected=room.id,key=subDoc.id,revision=subDoc.revision,signature=JSON.stringify([selected,key,revision]);if(subRunRequest?.signature!==signature)subRunRequest={signature,id:crypto.randomUUID()};subBusy=true;A('sub-run').disabled=true;
  try{await api('/api/research-subscription-run',{room:selected,id:key,revision,request_id:subRunRequest.id});subRunRequest=null;if(room?.id===selected){A('sub-status').textContent='実行待ちに追加しました。状態と取り消しは実行履歴から確認できます。';await refreshAutonomy();}}catch(error){report(error);}finally{subBusy=false;A('sub-run').disabled=false;}
});
document.querySelector('[data-panel="subscriptions-panel"]').addEventListener('panelactivate',()=>subscriptionLoad().catch(report));
A('sub-report-refresh').addEventListener('click',()=>subscriptionLoad().catch(report));

A('sub-source').addEventListener('change',()=>{if(A('sub-source').value!=='local')A('sub-auto_apply').checked=false;A('sub-auto_apply').disabled=A('sub-source').value!=='local';});
