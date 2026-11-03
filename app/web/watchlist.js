"use strict";
let watchRoom=null,watchDoc=null,watchGroup=null,watchBaseline='',watchGroupBaseline='',watchBusy=false,watchData=null,watchOperation=null,watchTicket=0;
const watchFields=['title','url','group_id','interval','at','enabled','max_per_day'];
function watchForm(){return Object.fromEntries(watchFields.map(key=>[key,key==='enabled'?A('watch-'+key).checked:key==='max_per_day'?Number(A('watch-'+key).value):A('watch-'+key).value]));}
function watchGroupForm(){return {title:A('watch-group-title').value,interval:A('watch-group-interval').value};}
function watchDirty(){return watchOptionsDirty()||!!watchRoom&&(JSON.stringify(watchForm())!==watchBaseline||JSON.stringify(watchGroupForm())!==watchGroupBaseline);}
function watchFill(doc=null){
  watchDoc=doc;const defaults={title:'',url:'',group_id:'',interval:'manual',at:'09:00',enabled:false,max_per_day:24};
  for(const key of watchFields){if(key==='enabled')A('watch-'+key).checked=doc?.[key]??defaults[key];else A('watch-'+key).value=doc?.[key]??defaults[key];}
  A('watch-select').value=doc?.id||'';watchBaseline=JSON.stringify(watchForm());watchOperation=null;
  A('watch-run').disabled=!doc||doc.archived;A('watch-archive').disabled=!doc||doc.archived;
  A('watch-status').textContent=doc?[(doc.archived?'保管済み':doc.enabled?'有効':'停止中'),doc.next_due?'次回 '+new Date(doc.next_due*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})+' JST':'自動取得なし',doc.last_result,doc.last_error].filter(Boolean).join(' / '):'新規登録。保存だけではURLを取得しません。';
}
function watchFillGroup(group=null){watchGroup=group;A('watch-group-select').value=group?.id||'';A('watch-group-title').value=group?.title||'';A('watch-group-interval').value='';watchGroupBaseline=JSON.stringify(watchGroupForm());A('watch-group-archive').disabled=!group||group.archived;}
async function watchLoad(reset=false){
  if(!room)return;const selected=room.id,ticket=++watchTicket,formBefore=JSON.stringify(watchForm()),groupBefore=JSON.stringify(watchGroupForm());
  const data=await api('/api/url-watches?room='+encodeURIComponent(selected));if(room?.id!==selected||ticket!==watchTicket)return;
  const changed=watchRoom!==selected;watchRoom=selected;watchData=data;
  const savedId=watchDoc?.id||'',groupId=watchGroup?.id||'',assigned=A('watch-group_id').value;
  A('watch-select').replaceChildren(new Option('新規登録',''),...data.entries.map(d=>new Option((d.archived?'[保管] ':d.enabled?'[有効] ':'[停止] ')+d.title,d.id)));
  A('watch-group-select').replaceChildren(new Option('新規グループ',''),...data.groups.map(d=>new Option((d.archived?'[保管] ':'')+d.title,d.id)));
  A('watch-group_id').replaceChildren(new Option('未分類',''),...data.groups.filter(d=>!d.archived||d.id===assigned).map(d=>new Option(d.title,d.id)));
  A('watch-select').value=savedId;A('watch-group-select').value=groupId;A('watch-group_id').value=assigned;
  if(changed||reset&&JSON.stringify(watchForm())===formBefore)watchFill(changed?null:data.entries.find(d=>d.id===savedId));
  if(changed||reset&&JSON.stringify(watchGroupForm())===groupBefore)watchFillGroup(changed?null:data.groups.find(d=>d.id===groupId));
  await watchLoadOptions();if(room?.id!==selected||ticket!==watchTicket)return;
  A('watch-warning').textContent=data.warning;A('watch-checks').replaceChildren();
  const states={queued:'待機中',running:'取得中',succeeded:'取得完了',cancelled:'中止',failed:'失敗',conflict:'設定競合',unknown:'結果未確認'};
  const outcomes={baseline:'初回基準を保存',changed:'本文に変更あり',unchanged:'本文に変更なし'};
  for(const item of data.checks){
    const card=document.createElement('section'),title=document.createElement('h3'),meta=document.createElement('p');title.textContent=item.title+' / '+(states[item.state]||item.state);meta.textContent=[item.created_at,outcomes[item.result],item.error].filter(Boolean).join(' / ');card.append(title,meta);
    const details=document.createElement('button');details.type='button';details.textContent='取得結果・差分';details.addEventListener('click',()=>watchShowCheck(item.id).catch(report));card.append(details);
    if(['queued','running'].includes(item.state)||item.state==='unknown'&&!item.reviewed){
      const button=document.createElement('button');button.type='button';button.textContent=item.state==='unknown'?'反映状況を確認済みにする':'取得を取り消す';button.disabled=!!item.cancel_requested;
      button.addEventListener('click',async()=>{button.disabled=true;try{await api(item.state==='unknown'?'/api/url-watch-acknowledge':'/api/url-watch-cancel',{room:selected,id:item.id,revision:item.revision});if(room?.id===selected)await watchLoad();}catch(error){report(error);button.disabled=false;}});card.append(button);
    }A('watch-checks').append(card);
  }
}
async function watchShowCheck(id){if(!room)return;const selected=room.id,data=await api('/api/url-watch-check?'+new URLSearchParams({room:selected,id}));if(room?.id!==selected)return;A('watch-check-detail').textContent=[data.title,data.state,data.result,data.error,data.url,data.final_url,data.summary?'AI要約：\n'+data.summary:'',data.summary_error,data.diff,data.diff_truncated?'（差分表示は4000文字で省略）':''].filter(Boolean).join('\n');}
async function watchWrite(action){
  if(!room||watchBusy)return;const selected=room.id,form=watchForm(),groupForm=watchGroupForm();
  const group=action==='group'||action==='archive_group';
  const data=group?{room:selected,title:groupForm.title,interval:groupForm.interval,id:watchGroup?.id||'',revision:watchGroup?.revision||'',archive:action==='archive_group'}:{room:selected,config:form,id:watchDoc?.id||'',revision:watchDoc?.revision||'',archive:action==='archive'};
  const signature=JSON.stringify([action,data]);if(watchOperation?.signature!==signature)watchOperation={signature,id:crypto.randomUUID()};watchBusy=true;
  try{
    const result=await api(group?'/api/url-watch-group':'/api/url-watch-save',{...data,request_id:watchOperation.id});if(room?.id!==selected)return;
    if(group&&JSON.stringify(watchGroupForm())===JSON.stringify(groupForm)){watchGroup=result.document;watchGroupBaseline=JSON.stringify(watchGroupForm());}
    if(!group&&JSON.stringify(watchForm())===JSON.stringify(form)){watchDoc=result.document;watchBaseline=JSON.stringify(watchForm());}
    // A group change can invalidate watch revisions. Never replace unsaved fields.
    await watchLoad(!watchDirty());
  }catch(error){report(error);}finally{watchBusy=false;}
}
A('watch-form').addEventListener('submit',event=>{event.preventDefault();watchWrite('save').catch(report);});
A('watch-group-form').addEventListener('submit',event=>{event.preventDefault();watchWrite('group').catch(report);});
A('watch-archive').addEventListener('click',()=>watchWrite('archive').catch(report));
A('watch-group-archive').addEventListener('click',()=>watchWrite('archive_group').catch(report));
A('watch-select').addEventListener('change',()=>{if(watchBusy||watchDirty()){A('watch-select').value=watchDoc?.id||'';notice('未保存の監視設定があります。保存または破棄してください。');return;}watchFill(watchData.entries.find(d=>d.id===A('watch-select').value));});
A('watch-group-select').addEventListener('change',()=>{if(watchBusy||watchDirty()){A('watch-group-select').value=watchGroup?.id||'';notice('未保存の監視設定があります。');return;}watchFillGroup(watchData.groups.find(d=>d.id===A('watch-group-select').value));});
A('watch-reload').addEventListener('click',()=>{if(watchBusy)return;watchBaseline=JSON.stringify(watchForm());watchGroupBaseline=JSON.stringify(watchGroupForm());watchLoad(true).catch(report);});
A('watch-refresh').addEventListener('click',()=>watchLoad().catch(report));
let watchRunRequest=null;
A('watch-run').addEventListener('click',async()=>{
  if(!room||!watchDoc||watchBusy)return;if(watchDirty()){notice('先に監視設定を保存してください。');return;}
  const selected=room.id,signature=JSON.stringify([selected,watchDoc.id,watchDoc.revision]);if(watchRunRequest?.signature!==signature)watchRunRequest={signature,id:crypto.randomUUID()};watchBusy=true;
  try{await api('/api/url-watch-run',{room:selected,id:watchDoc.id,revision:watchDoc.revision,request_id:watchRunRequest.id});watchRunRequest=null;if(room?.id===selected){A('watch-status').textContent='取得待ちに追加しました。会話完了後に取得します。';await watchLoad();}}catch(error){report(error);}finally{watchBusy=false;}
});
document.querySelector('[data-panel="watchlist-panel"]').addEventListener('panelactivate',()=>watchLoad().catch(report));

let watchOptions=null,watchOptionsBase='',watchOptionsRoom=null,watchGroupRun=null;
function watchOptionValues(){return {summarize_changes:A('watch-summary-enabled').checked,native_notifications:A('watch-native-enabled').checked};}
function watchOptionsDirty(){return !!watchOptions&&JSON.stringify(watchOptionValues())!==watchOptionsBase;}
async function watchLoadOptions(force=false){if(!room)return;const id=room.id;if(watchOptionsRoom===id&&watchOptionsDirty()&&!force)return;const data=await api('/api/url-watch-options?room='+encodeURIComponent(id));if(room?.id!==id)return;watchOptionsRoom=id;watchOptions=data;A('watch-summary-enabled').checked=data.values.summarize_changes;A('watch-native-enabled').checked=data.values.native_notifications;watchOptionsBase=JSON.stringify(watchOptionValues());}
A('watch-options-save').onclick=async()=>{if(!room||!watchOptions)return;try{const id=room.id;await api('/api/url-watch-options',{room:id,values:watchOptionValues(),revision:watchOptions.revision});await watchLoadOptions(true);notice('取得後の設定を保存しました。');}catch(e){report(e);}};
A('watch-options-reset').onclick=()=>watchLoadOptions(true).catch(report);
A('watch-group-run').onclick=async()=>{if(!room||!watchGroup||watchBusy)return;if(watchDirty()){notice('先に設定を保存してください。');return;}const id=room.id,signature=JSON.stringify([id,watchGroup.id,watchGroup.revision]);if(watchGroupRun?.signature!==signature)watchGroupRun={signature,id:crypto.randomUUID()};watchBusy=true;try{const result=await api('/api/url-watch-group-run',{room:id,id:watchGroup.id,revision:watchGroup.revision,request_id:watchGroupRun.id});watchGroupRun=null;if(room?.id!==id)return;A('watch-check-detail').textContent=JSON.stringify(result,null,2);await watchLoad();notice(result.state==='queued'?'グループ内URLを取得待ちに追加しました。':'一括予約に未受付があります。結果を確認してください。');}catch(e){report(e);}finally{watchBusy=false;}};
