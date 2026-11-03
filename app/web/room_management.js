/* User-operated room management; no AI tool exposes these lifecycle actions. */
(()=>{
 const el=id=>window.document.getElementById(id);let documents=[],selectedRoom=null,baseline=null,saving=false,loading=false,ignoringDirty=false,operation=null,initialSettings=false,previewSerial=0,previewing=false;
 const values=()=>({room:el('room-manage-id').value,name:el('room-manage-name').value,description:el('room-manage-description').value,...(initialSettings?{first_message:el('room-manage-first-message').value}:{}),prompt:el('room-manage-prompt').value});
 const dirty=()=>!ignoringDirty&&baseline!==null&&JSON.stringify(values())!==baseline;
 function controls(){
  const locked=saving||loading||busy;
  el('room-manage-first-preview').hidden=!initialSettings;
  el('room-manage-first-preview').disabled=!initialSettings||locked||previewing||!room;
  const archived=!!selectedRoom?.archived,confirmed=selectedRoom&&el('room-manage-confirm').value===selectedRoom.id;
  el('room-manage-id').disabled=!!selectedRoom||locked;
  for(const id of ['room-manage-name','room-manage-description','room-manage-first-message'])el(id).disabled=archived||locked;
  el('room-manage-first-label').hidden=!initialSettings;
  el('room-manage-first-help').hidden=!initialSettings;
  el('room-manage-first-message').disabled=!initialSettings||archived||locked;
  el('room-manage-prompt-label').hidden=!!selectedRoom;el('room-manage-prompt-help').hidden=!selectedRoom;
  el('room-manage-save').textContent=selectedRoom?'変更を保存':'ルームを作成';
  el('room-manage-save').disabled=locked||archived||!!selectedRoom&&!dirty();
  el('room-manage-archive-area').hidden=!selectedRoom;
  el('room-manage-archive').hidden=archived;el('room-manage-restore').hidden=!archived;
  el('room-manage-archive').disabled=el('room-manage-restore').disabled=locked||!confirmed||dirty();
  for(const id of ['room-manage-new','room-manage-reload','room-manage-list'])el(id).disabled=locked;
  el('room-manage-confirm').disabled=locked;el('room-manage-prompt').disabled=locked;
 }
 function fill(doc){
  clearPreview();
  selectedRoom=doc;operation=null;
  el('room-manage-id').value=doc?.id||'';el('room-manage-name').value=doc?.name||'';
  el('room-manage-description').value=doc?.description||'';el('room-manage-prompt').value='';el('room-manage-confirm').value='';
  el('room-manage-first-message').value=doc?.first_message||'';
  el('room-manage-status').textContent=doc?(doc.archived?'保管中':'利用中')+' · 保存先：characters/'+doc.id:'新しいルームの情報を入力してください。';
  baseline=JSON.stringify(values());controls();
 }
 function clearPreview(){previewSerial++;previewing=false;el('room-manage-first-preview-panel').hidden=true;el('room-manage-first-preview-messages').replaceChildren();el('room-manage-first-preview-status').textContent='';}
 async function previewInitial(){
  if(!initialSettings||!room||loading||saving||busy||previewing)return;
  const text=el('room-manage-first-message').value,name=el('room-manage-name').value.trim()||'キャラクター',serial=++previewSerial;
  previewing=true;controls();
  el('room-manage-first-preview-panel').hidden=false;el('room-manage-first-preview-messages').replaceChildren();el('room-manage-first-preview-status').textContent='プレビューを準備しています…';
  try{
   const result=await api('/api/initial-log-preview',{text});
   if(serial!==previewSerial)return;
   for(const item of result.messages){
    const body=message(item.role,item.content,'complete',[],null,el('room-manage-first-preview-messages'));
    body.closest('article').querySelector('.speaker').textContent=item.role==='assistant'?name:item.role==='user'?'YOU':'SYSTEM';
   }
   el('room-manage-first-preview-status').textContent=result.messages.length?'プレビューのみ表示しています。':'初期ログは空です。';
  }catch(error){if(serial===previewSerial)el('room-manage-first-preview-status').textContent=error.message;}
  finally{if(serial===previewSerial){previewing=false;controls();}}
 }
 el('room-manage-first-preview').addEventListener('click',previewInitial);
 for(const id of ['room-manage-first-message','room-manage-name'])el(id).addEventListener('input',()=>{clearPreview();controls();});
 async function load(preferred){
  loading=true;controls();
  try{
  const result=await api('/api/room-management');documents=result.rooms;setInitialSettings(result.initial_settings_enabled);
  el('room-manage-list').replaceChildren(...documents.map(doc=>new Option((doc.archived?'[保管中] ':'')+doc.name+' ('+doc.id+')',doc.id)));
  const doc=documents.find(d=>d.id===(preferred||room?.id))||documents[0];
  el('room-manage-list').value=doc?.id||'';fill(doc||null);
  }finally{loading=false;controls();}
 }
 function show(){
  if(!dirty()&&!saving&&!loading&&!el('room-management-panel').hidden)load(selectedRoom?.id).catch(report);
 }
 function setInitialSettings(enabled){
  if(typeof enabled!=='boolean'||enabled===initialSettings)return;
  initialSettings=enabled;
  if(!enabled)clearPreview();
  if(baseline!==null){
   const old=JSON.parse(baseline);
   baseline=JSON.stringify({room:old.room,name:old.name,description:old.description,...(enabled?{first_message:selectedRoom?.first_message||''}:{}),prompt:old.prompt});
  }
  controls();
 }
 function otherEdits(){
  ignoringDirty=true;
  try{return conversationEdit||editorDirty()||slotsDraftDirty()||resourceDirty()||worldDirty()||lifeDirty()||autonomyDirty()||appearanceDraftDirty()||itemDraftDirty()||avatarDirty()||window.AkariGroups?.dirty()||window.AkariIndexRefresh?.dirty();}
  finally{ignoringDirty=false;}
 }
 async function change(action){
  if(saving||loading||busy||generating){notice('処理中です。完了してから操作してください。');return;}
  if(otherEdits()){notice('ほかの画面に未保存の変更があります。保存または破棄してからルームを管理してください。');return;}
  if(['archive','restore'].includes(action)&&(!selectedRoom||dirty()||el('room-manage-confirm').value!==selectedRoom.id))return;
  const data=action==='create'||action==='update'?values():{room:selectedRoom.id};
  const payload={action,...data,revision:selectedRoom?.revision||''};
  const key=JSON.stringify(payload);if(!operation||operation.key!==key)operation={key,id:crypto.randomUUID()};
  saving=true;controls();
  try{
   const result=await api('/api/room-management',{...payload,request_id:operation.id});
   fill(result.room);await loadState(action==='archive'?room?.id:result.room.id);await load(result.room.id);
   notice(({create:'ルームを作成しました。',update:'ルーム設定を保存しました。',archive:'ルームを保管しました。',restore:'ルームを復元しました。'})[action]);
  }catch(error){report(error);}finally{saving=false;controls();}
 }
 el('room-manage-form').addEventListener('submit',e=>{e.preventDefault();change(selectedRoom?'update':'create');});
 el('room-manage-new').addEventListener('click',()=>{if(dirty()){notice('未保存のルーム設定があります。保存または破棄してください。');return;}el('room-manage-list').value='';fill(null);el('room-manage-id').focus();});
 el('room-manage-list').addEventListener('change',()=>{if(dirty()){el('room-manage-list').value=selectedRoom?.id||'';notice('未保存のルーム設定があります。保存または破棄してください。');return;}fill(documents.find(d=>d.id===el('room-manage-list').value));});
 el('room-manage-reload').addEventListener('click',()=>load(selectedRoom?.id).catch(report));
 for(const id of ['room-manage-id','room-manage-name','room-manage-description','room-manage-first-message','room-manage-prompt','room-manage-confirm'])el(id).addEventListener('input',controls);
 el('room-manage-archive').addEventListener('click',()=>change('archive'));el('room-manage-restore').addEventListener('click',()=>change('restore'));
 window.document.querySelector('.workspace-shortcuts [data-panel="room-management-panel"]').addEventListener('panelactivate',show);
 window.AkariRooms={dirty,controls,show,setInitialSettings};
 if(typeof state!=='undefined')setInitialSettings(state?.initial_settings_enabled);
})();

/* Folder export uses its own reviewed source revision, never a shared account selector. */
(()=>{const el=id=>document.getElementById(id);let snapshot=null,running=false,request=null,serial=0;
function controls(){el('nexus-export-room').disabled=running;el('nexus-export-preview').disabled=running;el('nexus-export-write').disabled=running||!snapshot||!el('nexus-export-confirm').checked;el('nexus-export-cancel').disabled=!running||!request;}
async function load(){if(running)return;const version=++serial;const result=await api('/api/room-management');if(version!==serial)return;el('nexus-export-room').replaceChildren(...result.rooms.filter(r=>!r.archived).map(r=>new Option(r.name+' ('+r.id+')',r.id)));el('nexus-export-room').value=room?.id||el('nexus-export-room').value;snapshot=null;request=null;el('nexus-export-confirm').checked=false;controls();}
el('nexus-export-room').onchange=()=>{serial++;snapshot=null;request=null;el('nexus-export-confirm').checked=false;el('nexus-export-summary').textContent='対象を変更しました。内容を再確認してください。';controls();};
el('nexus-export-confirm').onchange=controls;
el('nexus-export-preview').onclick=async()=>{if(running)return;const target=el('nexus-export-room').value,version=++serial;running=true;controls();try{const data=await api('/api/nexus-export-preview',{room:target});if(version!==serial||el('nexus-export-room').value!==target)return;snapshot=data;request=null;el('nexus-export-confirm').checked=false;el('nexus-export-summary').textContent=target+' / '+data.files+'ファイル / '+(data.bytes/1048576).toFixed(2)+' MiB / 会話 '+data.messages+'発言\n\n'+data.warnings.join('\n')+'\n\n除外：'+data.excluded.length+'ファイル';}catch(e){snapshot=null;report(e);}finally{running=false;controls();}};
el('nexus-export-write').onclick=async()=>{if(running||!snapshot||!el('nexus-export-confirm').checked)return;request=request||crypto.randomUUID();running=true;controls();el('nexus-export-status').textContent='構築・全ファイル照合中…';try{const result=await api('/api/nexus-export',{room:snapshot.room,revision:snapshot.revision,request_id:request});el('nexus-export-status').textContent='照合成功：'+result.path+'\nマニフェスト：'+result.manifest;notice('NexusArk用フォルダを書き出しました。');}catch(e){el('nexus-export-status').textContent='書出しは完了確認できませんでした。未完成フォルダは取り込まないでください。';report(e);}finally{running=false;controls();}};
el('nexus-export-cancel').onclick=()=>api('/api/nexus-export-cancel',{request_id:request}).then(()=>notice('書出しの中止を要求しました。')).catch(report);
document.querySelector('[data-panel="nexus-export-panel"]').addEventListener('panelactivate',()=>{if(!snapshot)load().catch(report);});controls();})();
