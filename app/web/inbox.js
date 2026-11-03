"use strict";
let inboxRoom=null,inboxPage=null,inboxDoc=null,inboxBusy=false,inboxTicket=0,inboxConfig=null,inboxSettingsRoom=null,inboxSettingsBaseline='',inboxSettingsBusy=false,inboxSettingsTicket=0;
const inboxStateNames={stored:'手紙箱に保存済み',queued:'表示待ち',delivered:'通知一覧へ保存済み',cancelled:'取消済み'};
function inboxSettingsForm(){return {autonomous_letters:A('inbox-auto-letters').checked,autonomous_notifications:A('inbox-auto-notices').checked};}
function inboxSettingsDirty(){return !!inboxSettingsRoom&&JSON.stringify(inboxSettingsForm())!==inboxSettingsBaseline;}
function inboxFillSettings(config){inboxConfig=config;for(const [key,id] of Object.entries({autonomous_letters:'inbox-auto-letters',autonomous_notifications:'inbox-auto-notices'}))A(id).checked=config[key];inboxSettingsBaseline=JSON.stringify(inboxSettingsForm());}
async function inboxLoadSettings(discard=false){
  if(!room)return;const selected=room.id,before=JSON.stringify(inboxSettingsForm()),ticket=++inboxSettingsTicket;
  const config=await api('/api/inbox-settings?room='+encodeURIComponent(selected));if(room?.id!==selected||ticket!==inboxSettingsTicket)return;
  if(inboxSettingsRoom===selected&&!discard&&inboxSettingsDirty())return;if(JSON.stringify(inboxSettingsForm())!==before)return;
  inboxSettingsRoom=selected;inboxFillSettings(config);A('inbox-policy-status').textContent='自律からの保存は個別の許可を使います。OS/外部通知は行いません。';
}
function inboxPaintDocument(doc){
  inboxDoc=doc;A('inbox-document-title').textContent=doc.title;A('inbox-document-meta').textContent=doc.created_at+' / '+(inboxStateNames[doc.state]||doc.state)+(doc.kind==='letter'?' / '+(doc.read_at?'既読':'未読'):' / 表示完了やユーザー既読を保証しません');
  A('inbox-body').textContent=doc.body;A('inbox-more').disabled=!doc.more;A('inbox-read').hidden=doc.kind!=='letter';A('inbox-read').textContent=doc.read_at?'未読に戻す':'既読にする';A('inbox-archive').hidden=doc.state==='queued';A('inbox-archive').textContent=doc.archived?'保管から戻す':'保管する';A('inbox-cancel').hidden=doc.kind!=='notice'||doc.state!=='queued';
}
async function inboxOpen(id){
  if(!room)return;const selected=room.id,ticket=++inboxTicket,data=await api('/api/inbox-document?'+new URLSearchParams({room:selected,id}));if(room?.id===selected&&ticket===inboxTicket)inboxPaintDocument(data);
}
async function inboxLoad(offset=0){
  if(!room)return;const selected=room.id,kind=A('inbox-kind').value,filter=A('inbox-filter').value,ticket=++inboxTicket;
  const revision=offset&&inboxPage?.kind===kind&&inboxPage?.filter===filter?inboxPage.revision:'';
  const data=await api('/api/inbox?'+new URLSearchParams({room:selected,kind,filter,offset,revision}));
  if(room?.id!==selected||ticket!==inboxTicket)return;
  if(inboxRoom!==selected||inboxPage?.kind!==kind){inboxDoc=null;A('inbox-body').textContent='';A('inbox-document-title').textContent='';A('inbox-document-meta').textContent='';for(const id of ['inbox-read','inbox-cancel','inbox-archive'])A(id).hidden=true;A('inbox-more').disabled=true;}
  inboxRoom=selected;inboxPage={...data,kind,filter};A('inbox-prev').disabled=!offset;A('inbox-next').disabled=!data.more;A('inbox-list').replaceChildren();
  for(const item of data.entries){const card=document.createElement('section'),open=document.createElement('button'),meta=document.createElement('p');open.type='button';open.textContent=item.title;meta.textContent=item.created_at+' / '+(inboxStateNames[item.state]||item.state)+(item.kind==='letter'?' / '+(item.read_at?'既読':'未読'):'');open.addEventListener('click',()=>inboxOpen(item.id).catch(report));card.append(open,meta);A('inbox-list').append(card);}
  A('inbox-list-status').textContent=data.entries.length?offset+1+'件目から表示。本文の参照だけでは既読になりません。':'該当する手紙/お知らせはありません。';
}
async function inboxChange(action){
  if(!room||!inboxDoc||inboxRoom!==room.id||inboxBusy)return;const selected=room.id,doc=inboxDoc;inboxBusy=true;
  try{const result=await api('/api/inbox-change',{room:selected,id:doc.id,revision:doc.revision,action,request_id:crypto.randomUUID()});if(room?.id===selected){if(inboxDoc?.id===doc.id)inboxPaintDocument({...result.document,more:false});await inboxLoad();await refreshSchedules();}}catch(error){report(error);}finally{inboxBusy=false;}
}
A('inbox-read').addEventListener('click',()=>inboxChange(inboxDoc?.read_at?'unread':'read').catch(report));A('inbox-archive').addEventListener('click',()=>inboxChange(inboxDoc?.archived?'restore':'archive').catch(report));A('inbox-cancel').addEventListener('click',()=>inboxChange('cancel').catch(report));
A('inbox-refresh').addEventListener('click',()=>inboxLoad().catch(report));A('inbox-prev').addEventListener('click',()=>inboxLoad(Math.max(0,(inboxPage?.offset||0)-20)).catch(report));A('inbox-next').addEventListener('click',()=>inboxLoad((inboxPage?.offset||0)+20).catch(report));
A('inbox-kind').addEventListener('change',()=>{if(A('inbox-kind').value==='notice'&&A('inbox-filter').value==='unread')A('inbox-filter').value='all';A('inbox-filter').querySelector('[value="unread"]').disabled=A('inbox-kind').value==='notice';inboxLoad().catch(report);});A('inbox-filter').addEventListener('change',()=>inboxLoad().catch(report));
A('inbox-more').addEventListener('click',async()=>{
  if(!room||!inboxDoc?.more)return;const selected=room.id,doc=inboxDoc;
  try{const data=await api('/api/inbox-document?'+new URLSearchParams({room:selected,id:doc.id,offset:doc.body.length,revision:doc.revision}));if(room?.id===selected&&inboxDoc?.id===doc.id&&inboxDoc.revision===doc.revision)inboxPaintDocument({...data,body:doc.body+data.body});}catch(error){report(error);}
});
A('inbox-settings-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!room||!inboxConfig||inboxSettingsRoom!==room.id||inboxSettingsBusy)return;const selected=room.id,form=inboxSettingsForm();inboxSettingsBusy=true;
  try{const data=await api('/api/inbox-settings',{room:selected,settings:form,revision:inboxConfig.revision});if(room?.id===selected){inboxConfig=data;inboxSettingsBaseline=JSON.stringify(form);if(JSON.stringify(inboxSettingsForm())===JSON.stringify(form))inboxFillSettings(data);A('inbox-policy-status').textContent='お知らせ設定を保存しました。';}}catch(error){report(error);}finally{inboxSettingsBusy=false;}
});
A('inbox-policy-reload').addEventListener('click',()=>inboxLoadSettings(true).catch(report));
document.querySelector('[data-panel="inbox-panel"]').addEventListener('panelactivate',()=>inboxLoad().catch(report));document.querySelector('[data-panel="inbox-settings-panel"]').addEventListener('panelactivate',()=>inboxLoadSettings().catch(report));
A('open-letters').addEventListener('click',()=>{showWorkspace('world');revealPanel('inbox-panel');A('inbox-kind').value='letter';A('inbox-filter').value='all';A('inbox-filter').querySelector('[value="unread"]').disabled=false;inboxLoad().catch(report);});
