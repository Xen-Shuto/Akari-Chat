"use strict";
(() => {
 let doc=null,history=null,members=[],baseline='',running=false,saving=false,saveOperation=null,sendOperation=null,rows=[],editing=null,historyOperation=null,loadSequence=0;
 const form=()=>JSON.stringify([$("group-title").value,members,$("group-archived").checked]);
 const settingsDirty=()=>baseline?form()!==baseline:!!($("group-title").value||members.length);
 function dirty(){return settingsDirty()||!!$("group-message").value||!!editing;}
 function groupControls(){
  const blocked=busy||running||saving||lifecycleBusy;
  for(const element of $("workspace-groups").querySelectorAll('button,input,select,textarea'))element.disabled=blocked||element.dataset.unavailable==='true'||!!editing&&!element.closest('[data-group-history-editor]');
  $("group-discard").disabled=blocked;$("group-cancel").disabled=!running;$("group-send").disabled=blocked||!doc||doc.archived||settingsDirty()||!!editing;$("group-older").disabled=blocked||!!editing||!history?.before;$("group-history-restore").disabled=blocked||!!editing||!history?.changes?.[0]?.can_restore;
 }
 function name(id){return state?.rooms.find(r=>r.id===id)?.name||id;}
 function renderMembers(){
  $("group-members").replaceChildren();
  members.forEach((id,index)=>{const li=document.createElement('li'),label=document.createElement('span');label.textContent=name(id);li.append(label);
   for(const [text,offset] of [['↑',-1],['↓',1],['外す',0]]){const b=document.createElement('button');b.type='button';b.textContent=text;b.setAttribute('aria-label',name(id)+'を'+(offset===-1?'前へ':offset===1?'後ろへ':'外す'));b.dataset.unavailable=String(!!offset&&!(index+offset>=0&&index+offset<members.length));b.addEventListener('click',()=>{if(busy||running||saving||offset&&!(index+offset>=0&&index+offset<members.length))return;if(offset)[members[index],members[index+offset]]=[members[index+offset],members[index]];else members.splice(index,1);renderMembers();saveOperation=null;groupControls();});li.append(b);}$("group-members").append(li);
  });
  $("group-speaker").replaceChildren(new Option('参加者全員（登録順）',''));for(const id of members)$("group-speaker").append(new Option(name(id),id));
 }
 function paintSettings(next){doc=next;members=[...(next?.members||[])];$("group-title").value=next?.title||'';$("group-archived").checked=!!next?.archived;renderMembers();baseline=form();saveOperation=null;groupControls();}
 function addMessage(role,title,text,status='complete',row=null){
  const a=document.createElement('article');a.className='message '+role;const label=document.createElement('div');label.className='speaker';label.textContent=title;const body=document.createElement('div');body.className='group-message-text';body.textContent=text;a.append(label,body);
  if(status!=='complete'){const note=document.createElement('small');note.textContent=status;a.append(note);}if(row){a.dataset.groupMessageId=row.id;const actions=document.createElement('div');actions.className='message-actions';
   for(const action of [role==='user'?'edit':'regenerate','remove']){const button=document.createElement('button');button.type='button';button.className='message-icon-button';button.dataset.groupAction=action;
    const label={edit:'ユーザー発言を編集して再生成',regenerate:'この回答以降を退避して再生成',remove:'この発言以降を削除'}[action];button.title=label;button.setAttribute('aria-label',label);button.textContent={edit:'✎',regenerate:'↻',remove:'×'}[action];button.dataset.unavailable=String(action!=='remove'&&!!doc?.archived);button.addEventListener('click',()=>openHistory(row,action,a));actions.append(button);
   }a.append(actions);}
  $("group-messages").append(a);return body;
 }
 async function listing(){
  const list=await api('/api/groups');rows=list;const selected=doc?.id||'';$("group-list").replaceChildren(new Option('新規グループ',''));for(const row of list)$("group-list").append(new Option((row.archived?'保管中 · ':'')+row.title,row.id));$("group-list").value=selected;
  const prior=$("group-room").value;$("group-room").replaceChildren(...(state?.rooms||[]).map(r=>new Option(r.name,r.id)));if(prior)$("group-room").value=prior;
 }
 async function load(key,before=0,discard=false){
  if(editing&&!discard){notice('共有ログの編集中です。確定または取消してください。');return;}
  const sequence=++loadSequence,result=await api('/api/group-history?'+new URLSearchParams({id:key,before}));if(sequence!==loadSequence||doc&&doc.id!==key||editing&&!discard)return;
  history=result;if(discard||!settingsDirty())paintSettings(result.group);$("group-messages").replaceChildren();for(const row of result.messages)addMessage(row.role,row.name,row.content,row.status,row);
  $("group-history-info").textContent=(before?'過去の':'最新の')+result.messages.length+'件。'+(result.runs[0]?'直近の実行: '+result.runs[0].state+(result.runs[0].error?' · '+result.runs[0].error:''):'')+(result.changes?.length?' / 最新のログ操作: '+({edit:'ユーザー編集',regenerate:'回答の再生成',remove:'選択以降の削除',restore:'最新操作の復元'}[result.changes[0].action]||result.changes[0].action)+'（変更前はDBに保持）':'');groupControls();
 }
 $("tab-groups").addEventListener('click',()=>{listing().catch(report);groupControls();});
 $("group-refresh").addEventListener('click',()=>listing().catch(report));
 $("group-add").addEventListener('click',()=>{const id=$("group-room").value;if(!id||members.includes(id))return;if(members.length>=8){notice('参加者は8人までです。');return;}members.push(id);renderMembers();groupControls();});
 $("group-new").addEventListener('click',()=>{if(dirty()){notice('設定や発言の編集を保存・送信するか、編集を破棄してください。');return;}paintSettings(null);history=null;$("group-messages").replaceChildren();$("group-list").value='';});
 $("group-discard").addEventListener('click',()=>{editing?.panel.remove();editing=null;historyOperation=null;paintSettings(doc);$("group-message").value='';sendOperation=null;});
 $("group-list").addEventListener('change',async()=>{if(dirty()){$("group-list").value=doc?.id||'';notice('設定や発言の編集を保存・送信するか、編集を破棄してください。');return;}const key=$("group-list").value;if(!key){paintSettings(null);history=null;$("group-messages").replaceChildren();return;}doc=null;try{await load(key,0,true);}catch(error){report(error);}});
 for(const id of ['group-title','group-archived'])$(id).addEventListener('input',()=>{saveOperation=null;groupControls();});
 $("group-save").addEventListener('click',async()=>{
  if(busy||running||saving)return;const payload={id:doc?.id||'',title:$("group-title").value,members,archived:$("group-archived").checked,revision:doc?.revision||''},signature=JSON.stringify(payload);if(saveOperation?.signature!==signature)saveOperation={signature,id:crypto.randomUUID()};
  saving=true;groupControls();try{const next=await api('/api/group-save',{...payload,request_id:saveOperation.id});paintSettings(next);await listing();await load(next.id,0,true);$("group-status").textContent='グループを保存しました。個別の会話履歴は変更していません。';}catch(error){report(error);}finally{saving=false;groupControls();}
 });
 $("group-history-refresh").addEventListener('click',()=>{if(doc)load(doc.id).catch(report);});
 $("group-older").addEventListener('click',()=>{if(doc&&history?.before)load(doc.id,history.before).catch(report);});
 $("group-cancel").addEventListener('click',()=>api('/api/cancel',{}).catch(report));
 function openHistory(row,action,article){
  if(busy||running||saving||editing||settingsDirty()||$("group-message").value){notice('未保存の設定や入力を先に保存・送信・破棄してください。');return;}
  const panel=document.createElement('div');panel.dataset.groupHistoryEditor='';const note=document.createElement('p');
  note.textContent=action==='restore'?'最新のログ操作を戻します。現在の本文も退避して保持します。後から会話や設定が変わっている場合は戻せません。':action==='edit'?'このユーザー発言を変更し、後続を退避して現在選択中の話者で再生成します。':action==='regenerate'?'この回答以降を退避し、この回答の話者1人だけを再生成します。':'この発言以降を共有ログから除外します。変更前の本文はDBに保持します。';panel.append(note);
  const input=document.createElement('textarea');input.rows=5;input.maxLength=4000;input.setAttribute('aria-label','グループのユーザー発言を編集');input.value=row.content||'';if(action==='edit')panel.append(input);
  const buttons=document.createElement('div');buttons.className='message-actions';const confirm=document.createElement('button'),cancel=document.createElement('button');confirm.type=cancel.type='button';confirm.textContent=action==='restore'?'変更前の共有ログへ戻す':action==='remove'?'ここから削除を確定':action==='edit'?'変更を確定して再生成':'この話者で再生成';cancel.textContent='編集・操作を取り消す';buttons.append(confirm,cancel);panel.append(buttons);article.append(panel);
  editing={row,action,panel,input,group:doc,history:history.history_revision,speakers:$("group-speaker").value?[$("group-speaker").value]:[...doc.members]};historyOperation=null;
  cancel.addEventListener('click',()=>{panel.remove();editing=null;historyOperation=null;groupControls();});confirm.addEventListener('click',()=>confirmHistory().catch(report));groupControls();if(action==='edit')input.focus();
 }
 async function confirmHistory(){
  if(!editing||busy||running||saving)return;const edit=editing,text=edit.action==='edit'?edit.input.value:'';
  if(edit.action==='edit'&&(!text.trim()||text.trim()===edit.row.content)){notice('本文を変更してから確定してください。');return;}
  const payload={id:edit.group.id,revision:edit.group.revision,history_revision:edit.history,...(edit.action==='remove'?{message_id:edit.row.id}:edit.action==='restore'?{archive_id:edit.row.id}:{message:text,speakers:edit.action==='regenerate'?[edit.row.speaker]:edit.speakers,replacement:{action:edit.action,id:edit.row.id}})},signature=JSON.stringify(payload);
  if(historyOperation?.signature!==signature)historyOperation={signature,payload:{...payload,request_id:crypto.randomUUID()}};
  if(!['remove','restore'].includes(edit.action)){await streamGroup(historyOperation.payload,text);return;}
  saving=true;busy=true;controls();try{const result=await api(edit.action==='restore'?'/api/group-history-restore':'/api/group-history-remove',historyOperation.payload);edit.panel.remove();editing=null;historyOperation=null;await load(payload.id);$("group-status").textContent=edit.action==='restore'?'変更前の共有ログへ戻しました。今回の本文もDBに保持しています。':result.retired+'件を共有ログから除外しました。変更前の本文はDBに保持しています。';}catch(error){report(error);}finally{saving=false;busy=false;controls();}
 }
 function acceptReplacement(payload,text){
  const change=payload.replacement;if(!change)return;
  editing?.panel.remove();editing=null;
  for(const article of $("group-messages").querySelectorAll('article[data-group-message-id]')){const id=Number(article.dataset.groupMessageId);if(id>change.id||change.action==='regenerate'&&id===change.id)article.remove();else if(id===change.id&&change.action==='edit')article.querySelector('.group-message-text').textContent=text.trim();}
 }
 async function streamGroup(payload,text){
  running=true;busy=true;controls();let output=null,accepted=false,done=false;
  try{
   const response=await fetch('/api/group-chat',{method:'POST',headers:{'Content-Type':'application/json','X-App-Token':state.token},body:JSON.stringify(payload)});if(!response.ok)throw new Error((await response.json()).error);
   const consume=item=>{if(item.type==='start'){accepted=true;$("group-message").value='';if(payload.replacement)acceptReplacement(payload,text);else addMessage('user','YOU',text);}else if(item.type==='speaker'){output=addMessage('assistant',item.name,'');$("group-status").textContent=item.name+'が回答中…'+(item.omitted?'（今回の文脈から古い履歴'+item.omitted+'件を省略）':'');}else if(item.type==='token'&&output){output.textContent+=item.text;$("group-messages").scrollTop=$("group-messages").scrollHeight;}else if(item.type==='done'){done=true;if(item.replayed&&payload.replacement){editing?.panel.remove();editing=null;}$("group-status").textContent='実行結果: '+item.status+(item.replayed?'（保存済みの受付結果）':'')+(item.error?' · '+item.error:'');}};
   const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const r=await reader.read();buffer+=decoder.decode(r.value||new Uint8Array(),{stream:!r.done});let n;while((n=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);if(line)consume(JSON.parse(line));}if(r.done)break;}if(buffer.trim())consume(JSON.parse(buffer));
   if(!done)throw new Error('完了イベントを確認できません。同じ操作の再送か履歴で確認してください。');
   if(!accepted)$("group-message").value='';sendOperation=null;historyOperation=null;
  }catch(error){$("group-status").textContent=error.message+' 履歴で受付と完了状況を確認してください。';report(error);}
  finally{running=false;busy=false;controls();if(!editing)await load(payload.id).catch(report);}
 }
 $("group-history-restore").addEventListener('click',()=>{const change=history?.changes?.[0];if(change?.can_restore)openHistory({id:change.id,content:''},'restore',$("group-messages"));});
 $("group-form").addEventListener('submit',async event=>{
  event.preventDefault();if(busy||running||editing||!doc||doc.archived||settingsDirty()||!history)return;
  const text=$("group-message").value;if(!text.trim())return;const speakers=$("group-speaker").value?[$("group-speaker").value]:doc.members;
  const signature=JSON.stringify([doc.id,text,speakers]);if(sendOperation?.signature!==signature)sendOperation={signature,payload:{id:doc.id,revision:doc.revision,history_revision:history.history_revision,message:text,speakers,request_id:crypto.randomUUID()}};
  await streamGroup(sendOperation.payload,text);
 });
 window.AkariGroups={controls:groupControls,dirty};
})();
