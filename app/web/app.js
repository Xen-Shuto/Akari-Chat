let importPicking=false;
"use strict";
const $ = id => document.getElementById(id);
let state, room, busy = false, generating = false, jobId, ready = false, localReady = false, streamController, cancelRequestPending=false;
let pendingReview=null, pendingReviewOriginal="", pendingReviewBusy=false, pendingReviewListRequest=0;
const fields = ["model", "executable", "mmproj", "mtp", "mtp_mode", "draft_tokens", "context", "gpu_layers", "temperature", "top_p", "max_tokens", "history_limit", "thinking_mode"];
let auxiliaryRequest = 0;
let episodeSource=null, episodeDraft=null;
let sleepSource=null, sleepRequest=null, sleepIndex=null, sleepCrossRoom=null;
let editorDocument=null, editorBusy=false, editorBackup=null, editorRequest=0;
let slotsState=null, slotsPreview=null, slotsBusy=false, slotsRequest=0, slotsRoom=null, slotsListRequest=0;
let workingStateOperation=null;
let pendingImages = [], processingImages = false, vision = false;
let conversationRevision=null, conversationEdit=null, lifecycleBusy=false, lifecycleDisabled=null;
const labels = {stopped:"Inactive",loading:"Starting",starting:"Starting",stopping:"Stopping",ready:"Active",error:"Inactive"};
let noticeTimer=null,noticeHideTimer=null,lastEngineError="";
function notice(message = "", duration = 5000) {
  clearTimeout(noticeTimer);clearTimeout(noticeHideTimer);
  const element=$("notice");element.classList.remove("notice-fading");element.textContent=message;element.hidden=!message;
  if(!message)return;
  const displayTime=Math.max(duration,Math.min(15000,message.length*60));
  noticeTimer=setTimeout(()=>{
    element.classList.add("notice-fading");
    noticeHideTimer=setTimeout(()=>{element.hidden=true;element.textContent="";element.classList.remove("notice-fading");},400);
  },displayTime);
}
async function api(path, data) {
  if(window.AkariStartup?.active)await window.AkariStartup.ready;
  const response = await fetch(path, data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-App-Token":state.token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作に失敗しました。");
  if(result.index_warning)window.AkariIndexRefresh?.saved(result);
  return result;
}
function report(error) { notice(error.message || String(error),10000); }
function modelCaption() {
  const path = $("model").value.trim();
  $("model-current").textContent = path ? path.split(/[\\/]/).pop() : "モデル未選択";
  $("model-current").title = path;
}
function showWorkspace(name, focus = false) {
  const tab = $("tab-" + name);
  if (!tab) return;
  for (const button of document.querySelectorAll('.workspace-tabs [role="tab"]')) {
    const selected = button === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(button.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) tab.focus();
  ensureSubView(name);
  if (!focus && compactSettings.matches && !document.body.classList.contains("settings-hidden")) {
    const wasInMenu = $("settings-panel").contains(document.activeElement);
    document.body.classList.add("settings-hidden");syncSettingsToggle();
    if (wasInMenu) {
      const panel = $(tab.getAttribute("aria-controls"));
      panel.tabIndex = -1;panel.focus({preventScroll:true});
    }
  }
  if(name === "rooms")window.AkariRooms?.show();
  if(name === "room-settings")window.AkariPersona?.refresh();
  if(name === "shared"){
    if(!$("shared-procedures-panel").hidden&&!sharedBusy&&!sharedDirty())sharedList().catch(report);
    if(!$("shared-closet-panel").hidden&&!lifeDirty())lifeLoad().catch(report);
  }
  if(name === "memory"){window.AkariRetrievalPolicy?.refresh();window.AkariDiaryArchives?.refresh();}
  if (name === "chat") scrollDown();
  if(name==="logs"&&room&&!conversationEdit&&(!chatLogState||chatLogState.room!==room.id||chatLogState.revision!==conversationRevision))chatLogLoad().catch(report);
}
document.querySelectorAll("[data-workspace]").forEach(button => button.addEventListener("click", () => showWorkspace(button.dataset.workspace)));
document.querySelector('.workspace-tabs').addEventListener("keydown", event => {
  const tabs = Array.from(document.querySelectorAll('.workspace-tabs [role="tab"]'));
  const index = tabs.indexOf(document.activeElement);
  if (index < 0 || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (["ArrowRight","ArrowDown"].includes(event.key) ? 1 : -1) + tabs.length) % tabs.length;
  showWorkspace(tabs[next].dataset.workspace, true);
});
function roomSwitchBlocked() {
  return !!conversationEdit || busy || editorBusy || slotsBusy || lifecycleBusy ||
    researchMergeBusy || permissionBusy || motivationBusy || questionBusy;
}
function syncRoomSelector() {
  const blocked = roomSwitchBlocked();
  $("rooms").disabled = blocked;
  if(autonomySettingsPending&&!blocked)queueMicrotask(loadAutonomySettings);
}
function controls() {
  window.AkariRooms?.controls();
  window.AkariGroups?.controls();
  window.AkariIndexRefresh?.controls();
  window.AkariLifestyleImages?.controls();
  window.AkariImageLinkJobs?.controls();
  memoryRouteControls();
  memorySortControls();
  memoryPipelineControls();
  sleepScheduleControls();
  researchMaintControls();
  entityMaintControls();
  memorySaveControls();
  if(typeof affectControls==='function')affectControls();
  window.AkariSleep?.controls();
  window.AkariSessions?.controls();
  window.AkariResonance?.controls();
  window.AkariAppearance?.controls();
  if(typeof repetitionControls==='function')repetitionControls();
  sleepControls();
  if(!lifecycleBusy&&lifecycleDisabled){for(const [element,disabled] of lifecycleDisabled)element.disabled=disabled;lifecycleDisabled=null;}
  const send=$("send");
  send.type=generating?'button':'submit';send.textContent=generating?'中止 ■':'送信 ↗';
  send.title=generating?'回答生成を中止（Esc）':'メッセージを送信（Enter）';
  send.setAttribute('aria-label',generating?'生成を中止':'送信');
  send.disabled=generating?(!jobId||cancelRequestPending||lifecycleBusy):!!conversationEdit || busy || slotsBusy || !ready || !room || processingImages || (pendingImages.length > 0 && !vision);
  $("attach-image").disabled = busy || processingImages || (ready && !vision);
  $("attach-image").title = ready&&!vision?(window.AkariConversation?.status()?.remote?"このルームの会話APIの画像対応設定を確認してください。":"画像対応モデルとmmprojでモデルを再起動してください。"):"画像を選ぶ（貼り付け・ドロップも可）";
  $("cancel").hidden = !generating;
  $("cancel").disabled = !jobId||cancelRequestPending||lifecycleBusy;
  $("generation-bar").hidden = !generating;
  syncRoomSelector();
  $("import-room").disabled = busy||editorBusy||slotsBusy||importPicking;$("import-pick").disabled=busy||editorBusy||slotsBusy||importPicking;$("import-path").readOnly=importPicking;
  episodeControls();
  editorControls();
  slotsControls();
  resourceControls();
  chatLogControls();
  attachmentControls();
  avatarControls();
  researchMergeControls();
  permissionControls();
  if(typeof motivationControls==='function')motivationControls();
  if(typeof questionControls==='function')questionControls();
  conversationControls();
  worldControls();
  lifeControls();
  updateReviewControls();
  if(lifecycleBusy){
    lifecycleDisabled ||= new Map();
    for(const element of document.querySelectorAll("button,input,select,textarea")){if(!lifecycleDisabled.has(element))lifecycleDisabled.set(element,element.disabled);element.disabled=true;}
  }
}
function engineState(engine) {
  const local=engine;engine=window.AkariConversation?.status()||engine;
  localReady=local.state==="ready";if(state)state.engine=local;
  ready = engine.state === "ready";
  vision = engine.vision === true;
  $("engine-state").textContent = (labels[engine.state] || "Inactive")+(engine.remote&&engine.provider?"（"+({gemini:"Gemini",openai:"OpenAI",anthropic:"Anthropic",compatible:"OpenAI互換"}[engine.provider]||"API")+"）":"");
  $("model-status").textContent = labels[local.state] || local.state;
  modelCaption();
  $("engine-state").className = "pill " + engine.state;
  $("start-engine").disabled = ["loading", "ready"].includes(local.state);
  $("stop-engine").disabled = local.state === "stopped";
  $("engine-state").title = engine.error || "";
  if (engine.error&&engine.error!==lastEngineError) notice(engine.error,10000);
  lastEngineError=engine.error||"";
  controls();
}
function conversationDisplay(content,role='assistant'){
  if(role==='assistant'){content=content.replace(/<(?:memory_trace|entity_memory_trace|persona_emotion)\b[^>]*\/>/g,'');const pending=content.search(/<(?:memory_trace|entity_memory_trace|persona_emotion)\b[^>]*$/);if(pending>=0)content=content.slice(0,pending);}
  if(role!=='assistant')return content;
  // Discord route labels belong to the verified metadata rendered in the speaker line.
  content=content.replace(/^(?:\s*【(?:From|To): Discord \/ [^】\r\n]*】[ \t]*(?:\r?\n|$)[ \t\r\n]*)+/u,'');
  content=content.replace(/【表情】…[\p{L}\p{N}_-]+…/gu,'');
  const expressionStart=content.indexOf('【表情】');if(expressionStart>=0)content=content.slice(0,expressionStart);
  content=content.trimEnd();
  const index=content.lastIndexOf('<user_emotion>');
  if(index<0)return content;
  const tail=content.slice(index),end=tail.indexOf('</user_emotion>');
  return end<0||!tail.slice(end+'</user_emotion>'.length).trim()?content.slice(0,index).trimEnd():content;
}
function setThinking(body,active){
  const old=body.querySelector('.thinking-indicator');
  if(!active){old?.remove();body.classList.remove('is-thinking');return;}
  if(old)return;body.classList.add('is-thinking');const indicator=document.createElement('span');indicator.className='thinking-indicator';indicator.setAttribute('role','status');indicator.setAttribute('aria-label','応答を生成中');
  for(let i=0;i<3;i++){const dot=document.createElement('span');dot.textContent='.';dot.setAttribute('aria-hidden','true');indicator.append(dot);}body.append(indicator);
}
function renderMessageBody(body,content,role='assistant',streaming=false){window.AkariMarkdown.render(body,conversationDisplay(content,role),{streaming});}
function renderAssistant(body,content,waiting=true){const text=conversationDisplay(content);if(!text.trim()){window.AkariMarkdown.render(body,'');setThinking(body,waiting);return;}setThinking(body,false);window.AkariMarkdown.render(body,text,{streaming:waiting});}
function setMessageMetadata(body,role,metadata){
  const wrapper=body.closest('article');let meta=wrapper.querySelector('.message-meta');
  if(!meta){meta=document.createElement('div');meta.className='message-meta';wrapper.append(meta);}
  const stamp=metadata.created,parsed=new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(stamp)?stamp:stamp.replace(' ','T')+'Z');
  meta.textContent=(isNaN(parsed)?stamp:new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(parsed)+' JST')+(role==='assistant'?' ｜ '+(metadata.model||'モデル記録なし'):'');
}
function message(role, content, status = "complete", images = [], id = null, container = $("messages"), log = false, metadata = null) {
  const wrapper = document.createElement("article"); wrapper.className = "message " + role;
  const speaker = document.createElement("div"); speaker.className = "speaker";
  speaker.textContent = role === "user" ? (metadata?.speaker || "YOU") : role === "assistant" ? room.name : "SYSTEM";
  if(metadata?.origin==='autonomy'){speaker.textContent+=' · 自律行動';wrapper.dataset.origin='autonomy';}
  if(metadata?.origin==='tool_result'){speaker.textContent+=' · ツール結果';wrapper.dataset.origin='tool_result';}
  let displayContent=content;
  if(metadata?.origin==='discord'&&metadata.discord_label){
    const route=document.createElement('span');route.className='discord-route';route.textContent=metadata.discord_label;
    speaker.append(document.createTextNode(' '),route);
    const prefix=metadata.discord_label+'\n\n';
    if(content.startsWith(prefix))displayContent=content.slice(prefix.length);
  }
  const body = document.createElement("div"); body.className = "body"; renderMessageBody(body,displayContent,role);
  wrapper.append(speaker, body);
  if(images.length){const gallery=document.createElement("div");gallery.className="message-images";for(const image of images)gallery.append(imageElement(image));wrapper.append(gallery);}
  if (status !== "complete" && status !== "no_response") { const note = document.createElement("div");note.className="status";note.textContent=status==="cancelled"?"途中で停止した応答":metadata?.origin==="autonomy"?({partial:"自律行動の一部の処理は未完了です",unknown:"処理結果に未確認の部分があります",error:"自律行動の生成でエラーがありました"}[status]||"自律行動の実行状態を確認してください"):"途中で接続が終了した応答";wrapper.append(note); }
  if(metadata?.created)setMessageMetadata(body,role,metadata);
  if(id!==null){
    wrapper.dataset.messageId=id;
    const actions=document.createElement("div");actions.className="message-actions";
    const commands=(role==="user"||log&&role==="assistant")?[["edit",log?"本文を編集":"編集して再生成"],...(log&&role==="assistant"?[["regenerate","再生成"]]:[]),["remove","ここから削除"]]:role==="assistant"?[["regenerate","再生成"],["remove","ここから削除"]]:[["remove","ここから削除"]];
    if(role==="user"||role==="assistant")commands.unshift(["copy","本文をコピー"]);
    for(const [action,label] of commands){
      if(['autonomy','opening','tool_result'].includes(metadata?.origin)&&action==='regenerate')continue;
      const button=document.createElement("button");button.type="button";button.dataset.conversationAction=action;button.className="message-icon-button";button.setAttribute("aria-label",label);
      button.title=label+(action==="remove"?"（この発言以降）":action==="regenerate"?"（過去の返答では、それ以降を退避）":log?"（本文のみ修正し、後続を退避）":"（ユーザー本文）");
      const icon=document.createElementNS("http://www.w3.org/2000/svg","svg");
      for(const [key,value] of Object.entries({viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":"1.7","stroke-linecap":"round","stroke-linejoin":"round","aria-hidden":"true",focusable:"false"}))icon.setAttribute(key,value);
      const path=document.createElementNS("http://www.w3.org/2000/svg","path");
      path.setAttribute("d",{copy:"M8 8h12v12H8zM16 8V4H4v12h4",edit:"M16 3a2.1 2.1 0 0 1 3 3L8 17l-4 1 1-4L16 3ZM14 5l3 3M4 21h16",remove:"M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",regenerate:"M20 7v5h-5M20 12a8 8 0 1 0-2 5M20 12l-4-4"}[action]);icon.append(path);button.append(icon);
      if(action==="copy"){
        button.title=label;
        button.removeAttribute("data-conversation-action");
        button.addEventListener("click",async()=>{
          try{await navigator.clipboard.writeText(body.innerText);notice("本文をコピーしました。");}
          catch(error){report(error);}
        });
      }else button.addEventListener("click",()=>openConversationAction(wrapper,{id,role,content},action,log?"logs":"chat"));actions.append(button);
    }
    wrapper.append(actions);
  }
  container.append(wrapper);if(role==="assistant"&&!content&&id===null)setThinking(body,true);return body;
}
function conversationControls(){
  const blocked=busy||editorBusy||slotsBusy||chatLogLoading;
  for(const button of document.querySelectorAll("#messages [data-conversation-action], #chat-log-messages [data-conversation-action]"))button.disabled=blocked||!!conversationEdit;
  if(conversationEdit){
    conversationEdit.confirm.disabled=blocked||((conversationEdit.action==="regenerate"||conversationEdit.action==="edit"&&conversationEdit.scope!=="logs")&&!ready);
    conversationEdit.cancel.disabled=busy; if(conversationEdit.input)conversationEdit.input.readOnly=busy;
  }
}
function closeConversationAction(){
  if(!conversationEdit)return;
  const {panel,wrapper,action}=conversationEdit;panel.remove();conversationEdit=null;controls();
  wrapper.querySelector(`[data-conversation-action="${action}"]`)?.focus();
}
function openConversationAction(wrapper,item,action,scope="chat"){
  if(busy||editorBusy||slotsBusy||chatLogLoading||conversationEdit)return;
  const panel=document.createElement("div");panel.className="conversation-edit";
  const info=document.createElement("p");info.className="muted small";
  info.textContent=action==="edit"&&scope==="logs"?"この本文を変更し、それより後の会話を退避します。返答は自動生成しません。":action==="edit"?"ユーザー本文を変更し、この発言より後を退避して返答を作り直します。添付画像は引き継ぎます。":action==="remove"?"この発言を含む、それ以降の会話を削除します。会話ログの変更履歴から復元できます。":"この返答を含む、それ以降の会話を退避して、直前のユーザー発言から作り直します。";
  panel.append(info);
  let input=null;
  if(action==="edit"){input=document.createElement("textarea");input.rows=5;input.maxLength=100000;input.setAttribute("aria-label",scope==="logs"?"会話ログの本文を編集":"ユーザー本文を編集");input.value=item.content;panel.append(input);}
  const buttons=document.createElement("div");buttons.className="message-actions";
  const confirm=document.createElement("button");confirm.type="button";confirm.textContent=action==="edit"&&scope==="logs"?"本文を変更して以降を退避":action==="edit"?"変更を確定して再生成":action==="remove"?"この発言以降を削除":"ここから再生成";
  const cancel=document.createElement("button");cancel.type="button";cancel.textContent="キャンセル";
  cancel.addEventListener("click",closeConversationAction);confirm.addEventListener("click",()=>runConversationAction().catch(report));
  buttons.append(confirm,cancel);panel.append(buttons);wrapper.append(panel);
  conversationEdit={...item,action,scope,panel,wrapper,input,confirm,cancel,revision:scope==="logs"?chatLogState.revision:conversationRevision};controls();
  (input||confirm).focus();panel.scrollIntoView({block:"nearest"});
}
async function refreshConversation(){
  const selected=room.id,snapshot=await api("/api/conversation?room="+encodeURIComponent(selected));
  if(room.id!==selected)return;
  conversationRevision=snapshot.revision;$("messages").replaceChildren();
  for(const item of snapshot.messages)message(item.role,item.content,item.status,item.images||[],item.id,$("messages"),false,item);
  await refreshPortrait();
  controls();scrollDown();
}
async function runConversationAction(){
  const edit=conversationEdit;if(!edit||busy||editorBusy||slotsBusy)return;
  if(edit.action==="edit"&&!edit.input.value.trim()){notice("本文を入力してください。");return;}
  const selected=room.id,payload={room:selected,id:edit.id,revision:edit.revision,text:edit.input?.value};
  busy=true;generating=edit.action==="regenerate"||edit.action==="edit"&&edit.scope!=="logs";jobId=null;controls();notice();const effects=new Set();let started=false,output=null,full="",done=false;
  function consume(event){
    collectToolEffects(effects,event);
    if(event.type==="start"){
      started=true;jobId=event.id;conversationEdit=null;edit.panel.remove();
      for(const node of (edit.scope==="logs"?$("chat-log-messages"):$("messages")).querySelectorAll("[data-message-id]"))if(Number(node.dataset.messageId)>=edit.id&&(edit.action!=="edit"||Number(node.dataset.messageId)!==edit.id))node.remove();
      if(edit.action==="edit")renderMessageBody(edit.wrapper.querySelector(".body"),payload.text,edit.role);
      output=message("assistant","","complete",[],null,edit.scope==="logs"?$("chat-log-messages"):$("messages"));controls();if(event.context)$("context-last").textContent=describeContext(event.context);
    }
    if(event.type==="replace"){full=event.text;if(output)renderAssistant(output,full);}
    if(event.type==="tool_start"&&output)setThinking(output,true);
    if(event.type==="generated_image"&&output)showGeneratedImage(output,event.image);
    if(event.type==="delta"){full+=event.text;if(output)renderAssistant(output,full);if(edit.scope!=="logs")scrollDown();}
    if(event.type==="error")notice(event.message);
    if(event.type==="done"){done=true;if(output){setThinking(output,false);if(event.message?.status==="no_response"){full=event.message.content;renderAssistant(output,full,false);}if(event.message)setMessageMetadata(output,"assistant",event.message);}$("metrics").textContent="全体 "+event.total_seconds.toFixed(2)+"秒";if(event.status==="cancelled")notice("生成を停止しました。途中の返答も保存しています。");}
  }
  try{
    if(edit.action==="remove"||edit.action==="edit"&&edit.scope==="logs"){
      await api(edit.action==="remove"?"/api/chat-remove":"/api/chat-edit",payload);started=true;conversationEdit=null;notice(edit.action==="remove"?"選択した発言以降を削除しました。会話ログの変更履歴から復元できます。":"本文を変更し、それより後の会話を退避しました。返答は生成していません。");
    }else{
      streamController=new AbortController();
      const response=await fetch(edit.action==="edit"?"/api/chat-edit-regenerate":"/api/regenerate",{method:"POST",headers:{"Content-Type":"application/json","X-App-Token":state.token},body:JSON.stringify(payload),signal:streamController.signal});
      if(!response.ok)throw new Error((await response.json()).error);
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";
      while(true){const r=await reader.read();buffer+=decoder.decode(r.value||new Uint8Array(),{stream:!r.done});let n;while((n=buffer.indexOf("\n"))>=0){const line=buffer.slice(0,n);buffer=buffer.slice(n+1);if(line)consume(JSON.parse(line));}if(r.done)break;}
      if(buffer.trim())consume(JSON.parse(buffer));
      if(!done)throw new Error("通信が途中で切れました。履歴を再読み込みして確認してください。");
    }
  }catch(error){report(error);}finally{
    if(output)setThinking(output,false);generating=false;jobId=null;
    try{if(started){await refreshConversation();await refreshContext();if(chatLogState?.room===selected)await chatLogLoad();}}
    finally{busy=false;try{if(started)await syncToolEffects(effects,selected);}catch(error){report(error);}finally{controls();}}
  }
}
function collectToolEffects(effects,event){
  if(event.type==='start'||event.type==='done')refreshPortrait().catch(report);
  if(event.type!=="tool")return;
  if(event.result?.status==="unknown"&&["generate_image","cancel_image_job"].includes(event.name))effects.add("image_job");
  if(event.result?.status==="pending"||event.result?.status==="unknown"&&["propose_document","propose_notepad"].includes(event.name))effects.add("draft");
  for(const change of event.result?.changes||[])if(change.kind)effects.add(change.kind);
  if(event.result?.status==="unknown"&&["change_working_memory","change_location","change_lifestyle"].includes(event.name))
    for(const kind of ["working","location","lifestyle"])effects.add(kind);
}
async function syncToolEffects(effects,selected){
  if(room?.id!==selected||!effects.size)return;
  if(effects.has("location")||effects.has("temporary")||effects.has("world"))await worldRefresh(false);
  if(effects.has("lifestyle")&&lifeState?.room===selected){
    if(lifeDirty())$("life-status").textContent="会話中に生活データが更新されました。未保存の入力を保持しています。保存前に最新内容を確認してください。";
    else await lifeLoad();
  }
  if(effects.has("working")){
    await workingDisplay();
    if(slotsState)await refreshSlots();
    if(editorDocument?.key==="working"){
      if(editorDirty())$("editor-status").textContent="会話中にワーキングメモリが更新されました。未保存の本文を保持しています。最新内容と照合してください。";
      else await loadEditor(true);
    }
  }
  if(effects.has("image_job"))await refreshImageJobs();
  if(effects.has("draft"))await updateReviewList();
}
function scrollDown() { $("messages").scrollTop = $("messages").scrollHeight; }
function describeContext(report) {
  if (!report) return "この起動中の送信記録はありません。";
  const statuses={included:"送信",excluded:"省略",missing:"未配置",empty:"空"};
  const lines=["system本文 合計 "+report.system_characters.toLocaleString()+"文字",report.scope];
  for(const setting of Object.entries(report.settings))lines.push((setting[0]==="send_core_memory"?"コアメモリ":"短期メモ")+": "+(setting[1].value?"送信する":"送信しない")+"（"+setting[1].origin+"）");
  for(const source of report.sources)lines.push("\n"+source.label+" · "+statuses[source.status]+"\n"+source.path+"\n原文 "+source.characters.toLocaleString()+"文字 ／ 送信 "+source.sent_characters.toLocaleString()+"文字\n"+source.reason);
  for(const source of report.sources)if(source.records?.length){lines.push("\n"+source.label+"の出典");for(const record of source.records)lines.push(record.path+" #"+record.record_index+" · "+(record.date||record.created_at||""));}
  if(report.retrieval){lines.push("\n"+report.retrieval.method,report.retrieval.reason||"");for(const result of report.retrieval.results)lines.push("\n"+searchReference(result)+"\n"+result.excerpt);for(const warning of report.retrieval.warnings||[])lines.push(warning);}
  if(report.active_attachments)lines.push("\n継続添付: "+report.active_attachments.ids.length+"枚送信 / "+report.active_attachments.skipped+"枚省略\n"+report.active_attachments.reason);
  return lines.join("\n");
}
async function refreshContext(){
  if(!room)return;
  const selected=room.id,result=await api("/api/context?room="+encodeURIComponent(selected));
  if(room.id!==selected)return;
  $("context-preview").textContent=describeContext(result.preview);
  $("context-last").textContent=describeContext(result.last_sent);
  await refreshActiveAttachments();
}
async function selectRoom(id) {
  if(room?.id===id&&(busy||conversationEdit))return;
  if(room?.id!==id && roomSwitchBlocked()){
    if(room)$("rooms").value=room.id;
    notice(conversationEdit?"会話の編集中です。確定またはキャンセルしてください。":"処理の完了を待ってからルームを切り替えてください。");
    return;
  }
  if(room?.id!==id&&(appearanceDraftDirty()||itemDraftDirty()||avatarDirty()||window.AkariGroups?.dirty()||window.AkariIndexRefresh?.dirty()||window.AkariConversation?.dirty()||editorDirty()||slotsDraftDirty()||resourceDirty()||worldDirty()||lifeDirty()||updateReviewDirty()||autonomyDirty())){$("rooms").value=room.id;notice("編集中の本文があります。保存するか、編集内容を破棄してからルームを切り替えてください。");return;}
  if(memoryRouteSource?.room!==id){memoryRouteSource=null;memoryRouteOperation=null;$("memory-route-source").value='';for(const key of ['memory-route-status','memory-route-unclassified','memory-route-history-text'])$(key).textContent='';}
  if(memorySortSource?.room!==id){memorySortSource=null;memorySortOperation=null;memorySortRequest++;for(const key of ['memory-sort-source','memory-sort-unclassified'])$(key).value='';for(const key of ['memory-sort-result','memory-sort-status','memory-sort-history-text'])$(key).textContent='';}
  if(room?.id!==id){resetItemDraft();resetAppearanceDraft();appearanceTemplateDoc=null;appearancePresetDoc=null;appearancePresetOperation=null;appearancePresetSelected="";$("appearance-preset-name").value="";$("appearance-preset-list").replaceChildren(new Option("新規登録",""));appearanceTemplateOperation=null;appearanceTemplateRequest++;appearanceTemplateBusy=false;$("appearance-template-body").value="";$("appearance-template-enabled").checked=false;$("appearance-template-status").textContent="";$("appearance-template-history").replaceChildren(new Option("履歴を選択…",""));}
  if(pendingReview?.room!==id)resetUpdateReview();
  resetSleepSchedule(id);
  researchMaintReset(id);
  entityMaintReset(id);
  memorySaveReset(id);
  if(typeof affectReset==='function')affectReset(id);
  if(typeof repetitionReset==='function')repetitionReset(id);
  if(sleepSource?.room!==id){sleepSource=null;sleepRequest=null;sleepIndex=null;$("sleep-source").value='';$("sleep-status").textContent='資料を確認してください。';$("sleep-history-text").textContent='';$("sleep-index-status").textContent='';$("sleep-generate").disabled=true;$("sleep-index").disabled=true;}
  if(mergeRoom!==id)resetEntityMerges(id);
  if(editorDocument?.room!==id)resetEditor();
  if(lifeState?.room!==id){lifeState=null;sharedClosetDoc=null;sharedClosetRequest=null;lifeBaseline="";lifeFields.forEach(key=>$(key).value="");$("life-items").replaceChildren(new Option("新規作成",""));$("life-clothes").replaceChildren(new Option("衣装を選択…",""));$("life-status").textContent="生活データを読み込んでください。";}
  if((resourceDoc?.room||entryState?.room)!==id)resetResource();
  if(slotsRoom!==id)resetSlots(id);
  room = state.rooms.find(item => item.id === id);
  clearDiscordThinking();
  if (!room) return;
  const selected = room.id;
  const conversationLoading=window.AkariConversation?.roomChanged(selected);
  window.AkariGateway?.roomChanging(selected);
  const refreshAttachmentsAtStart=!$("attachments-panel").hidden&&!$("workspace-room-settings").hidden;
  const character = await api("/api/character?room=" + encodeURIComponent(selected));
  if (room.id !== selected) return;
  room = character;
  await window.AkariMusic?.refresh();
  if(room.id!==selected)return;
  await window.AkariGateway?.refresh();
  if(room.id!==selected)return;
  await window.AkariExternal?.roomChanged(selected);
  await conversationLoading;
  if(room.id!==selected)return;
  await window.AkariAppearance?.roomChanged(selected);
  if(room.id!==selected)return;
  window.AkariIndexRefresh?.roomChanged();
  window.AkariLifestyleImages?.roomChanged();
  window.AkariImageLinkJobs?.roomChanged();
  window.AkariAttachmentDisposal?.reset();
  if(sleepCrossRoom!==selected){sleepCrossRoom=selected;$("sleep-crossfile-sources").replaceChildren();$("sleep-crossfile-target").value="";}
  attachmentUsageRequest++;attachmentUsageBusy=false;$("attachments-usage-check").disabled=false;$("attachments-usage").textContent="まだ確認していません。";
  $("search-results").textContent="";
  episodeSource=null;episodeDraft=null;
  $("episode-source").textContent="";$("episode-summary").value="";$("episode-status").textContent="";
  $("episode-day").value=new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Tokyo",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  await refreshEpisodeDrafts();
  if(!$("episode-panel").hidden)await refreshMemoryPipeline();
  if(room.id!==selected)return;
  $("character-name").textContent = room.name;
  $("character-description").textContent = room.description || "";
  $("character-description").hidden = !room.description?.trim();
  await worldRefresh(false);
  $("compatibility").textContent = room.compatibility;
  $("avatar").hidden = !room.avatar; $("avatar-placeholder").hidden = room.avatar;
  if (room.avatar) $("avatar").src = "/api/avatar?room=" + encodeURIComponent(room.id);
  await avatarLoad();
  $("export").href = "/api/export?room=" + encodeURIComponent(room.id);
  localStorage.setItem("lastRoom", room.id);
  await refreshContext();
  const snapshot = await api("/api/conversation?room=" + encodeURIComponent(selected));
  const history=snapshot.messages;
  if (room.id !== selected) return;
  conversationRevision=snapshot.revision;
  if (history.length) { $("messages").replaceChildren(); for (const item of history) message(item.role, item.content, item.status, item.images || [], item.id,$("messages"),false,item); }
  else { $("messages").replaceChildren();const welcome=document.createElement("div");welcome.className="welcome";const title=document.createElement("h2");title.textContent=room.name+"と話してみよう。";const hint=document.createElement("p");hint.textContent="モデルを起動してメッセージを送ってください。";welcome.append(title,hint);$("messages").append(welcome); }
  await updateReviewList();
  await refreshSchedules();
  if(window.AkariActionReservations)await window.AkariActionReservations.refresh();
  await refreshImageJobs();
  if($("appearance-image-panel").open)await loadAppearanceTemplate();
  await refreshAutonomy(true);
  window.AkariPersona?.refresh();window.AkariRetrievalPolicy?.refresh();window.AkariDiaryArchives?.refresh();
  scrollDown(); controls();
  if(!$("workspace-logs").hidden)await chatLogLoad();
  if(!$("slots-panel").hidden&&!$("workspace-memory").hidden&&!slotsState)await refreshSlots();
  if(!$("sleep-schedule-panel").hidden&&!$("workspace-room-settings").hidden&&!nightConfig)await loadSleepSchedule();
  if(refreshAttachmentsAtStart&&!attachmentBusy&&!$("attachments-panel").hidden&&!$("workspace-room-settings").hidden)await attachmentLoad(0,attachmentDoc?.room===selected?attachmentDoc.id:'');
  if(!$("diagnostics-panel").hidden&&!$("workspace-settings").hidden)await refreshDiagnostics();
  if(!$("research-merge-panel").hidden&&!$("workspace-autonomy").hidden)await researchMergeLoad();
  if(!$("tool-permissions-panel").hidden&&!$("workspace-room-settings").hidden)await loadPermissions();
  await loadAutonomySettings();
  if(!$("workspace-shared").hidden&&!$("shared-closet-panel").hidden)await lifeLoad();
  if(typeof questionList==='function'&&!$("question-progress-panel").hidden&&!$("workspace-autonomy").hidden)await questionList();
  if(!history.length&&room.opening?.error)notice(room.opening.error,10000);
  await ensureFirstMessage();
}
let firstMessageBusy=false;
async function ensureFirstMessage(retry=false,targetRoom=null){
  if(!room||busy||firstMessageBusy||conversationEdit||!retry&&(!['pending','running'].includes(room.opening?.state)||room.opening?.mode==='ai'&&!ready))return;
  const selected=targetRoom||room.id;let opening=selected===room.id?room.opening:null,name=room.name;firstMessageBusy=true;
  busy=true;generating=false;jobId=null;controls();
  try{
    if(selected!==room.id){const character=await api('/api/character?room='+encodeURIComponent(selected));opening=character.opening;name=character.name;}
    generating=opening?.mode==='ai';jobId=opening?.job_id||crypto.randomUUID();controls();
    if(generating)notice('「'+name+'」の最初のメッセージを準備しています…');
    const result=await api('/api/first-message',{room:selected,retry,request_id:jobId});
    if(room?.id===selected){
      if(result.state!=='waiting')room.opening=result;
      if(result.state==='complete'){await refreshConversation();notice();}
    }else if(result.state==='complete')notice('「'+name+'」の最初のメッセージを保存しました。');
    if(retry&&result.state==='skipped')notice('「'+name+'」には既存の会話があるため、最初のメッセージは追加しません。');
    if(retry&&result.state==='unconfigured')notice('「'+name+'」の固定文は未設定です。固定文を保存するか、「キャラ作成」で作った新規ルームを使用してください。');
    if(retry&&result.state==='waiting')notice('ほかの処理の完了または会話モデルの準備を待って、再試行してください。');
    if(result.error)notice(result.error,10000);
  }catch(error){if(room?.id===selected)room.opening={...room.opening,state:'error'};report(error);}
  finally{firstMessageBusy=false;busy=false;generating=false;jobId=null;controls();}
}
window.ensureFirstMessage=ensureFirstMessage;
async function loadState(preferred) {
  const initial=!state;
  state = await api("/api/state");
  $("app-version").textContent=state.version||"";
  document.body.dataset.debugMode=state.debug_mode===true?'true':'false';
  window.AkariRooms?.setInitialSettings(state.initial_settings_enabled);
  const embedding = await api("/api/embedding-settings");
  const catalog = await api('/api/model-catalog');
  fillModelChoices('embedding-model',catalog.embeddings,embedding.model,'Models/embeddings/');
  $("embedding-profile").value=embedding.profile;
  $("embedding-threshold").value=embedding.threshold;
  $("embedding-auto-start").checked=embedding.auto_start;
  $("embedding-auto-semantic").checked=embedding.auto_semantic;
  semanticState(state.semantic);
  $("rooms").replaceChildren();
  for (const item of state.rooms) { const option = new Option(item.name,item.id);$("rooms").append(option); }
  await loadLocalModelFields(state.settings,state.models);
  if(initial)await window.AkariConversation?.loadCommon();
  if(!imageSettingsDoc)await loadImageSettings();
  const firstSamples=state.character_bootstrap&&localStorage.getItem('characterBootstrap')!==state.character_bootstrap;
  const id = preferred || (firstSamples?'Assistant':localStorage.getItem("lastRoom"));
  $("rooms").value = state.rooms.some(r=>r.id===id)?id:state.rooms.find(r=>r.id==='Assistant')?.id || state.rooms[0]?.id || "";
  engineState(state.engine);
  await selectRoom($("rooms").value);
  if(firstSamples)localStorage.setItem('characterBootstrap',state.character_bootstrap);
  if(state.warnings?.length)notice(state.warnings.join("\n"));
}
async function loadLocalModelFields(settings,models){
  for (const key of fields) $(key).value = settings[key];
  $("tool_calling").checked = settings.tool_calling !== false;
  $("auto_aux").checked = settings.auto_aux;
  $("auto_start").checked = settings.auto_start;
  $("extra_args").value = JSON.stringify(settings.extra_args);
  fillModelChoices('model-list',models,settings.model,'Models/LLM/');
  await refreshAux();
}
function normalizeGenerationTokens(){
  const input=$("max_tokens"),raw=input.value.trim();
  if(input.validity.badInput)return;
  if(raw===""){input.value="0";return;}
  if(raw.startsWith("-")){input.value="0";return;}
  const number=Number(raw);
  if(Number.isFinite(number)){
    // Keep ordinary decimal input exact at integer boundaries.
    const value=/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(raw)?Number(raw.split('.')[0].replace(/^[+]$/,'')||0):number;
    input.value=String(Math.max(0,Math.trunc(value)));
  }
}
$("max_tokens").addEventListener('change',normalizeGenerationTokens);
async function saveGenerationSettings() {
  const keys = ["context", "gpu_layers", "temperature", "top_p", "max_tokens", "history_limit", "thinking_mode"];
  const settings = Object.fromEntries(keys.map(key => [key, $(key).value]));
  settings.tool_calling = $("tool_calling").checked;
  const saved = await api("/api/settings/generation", settings);
  $("max_tokens").value = saved.max_tokens;
}
async function saveSettings() {
  normalizeGenerationTokens();
  await refreshAux();
  const settings = Object.fromEntries(fields.map(key => [key, $(key).value]));
  settings.max_tokens=$("max_tokens").value.trim()===""?0:$("max_tokens").value;
  settings.tool_calling = $("tool_calling").checked;
  settings.auto_aux = $("auto_aux").checked;
  settings.auto_start = $("auto_start").checked;
  try { settings.extra_args = JSON.parse($("extra_args").value || "[]"); }
  catch { throw new Error("追加引数をJSON配列で入力してください。例: [\"--jinja\"]"); }
  const saved=await api("/api/settings", settings);
  $("max_tokens").value=saved.max_tokens;
}
async function refreshAux() {
  modelCaption();
  const request = ++auxiliaryRequest, automatic = $("auto_aux").checked;
  $("mmproj").readOnly = automatic; $("mtp").readOnly = automatic; $("mtp_mode").disabled = automatic;
  if(!automatic){$("aux-status").textContent="手動設定を使用します。";return;}
  if(!$("model").value.trim()){
    $("mmproj").value="";$("mtp").value="";$("mtp_mode").value="off";
    $("aux-status").textContent="モデルを選ぶと対応ファイルを調べます。";return;
  }
  $("aux-status").textContent="対応ファイルを確認中…";
  const result=await api("/api/model-options?model="+encodeURIComponent($("model").value.trim()));
  if(request!==auxiliaryRequest)return;
  for(const key of ["mmproj","mtp","mtp_mode"])$(key).value=result[key];
  $("aux-status").textContent=result.notes.join("\n");
}
for (const id of ["settings-form", "generation-settings-form"]) {
  $(id).addEventListener("submit", event => {
    event.preventDefault();
    $("conversation-api-common-form").requestSubmit($("conversation-api-common-save"));
  });
}
$("rooms").addEventListener("change",()=>selectRoom($("rooms").value).catch(report));
$("model-list").addEventListener("change",()=>{if($("model-list").value){$("model").value=$("model-list").value;refreshAux().catch(report);}});
function fillModelChoices(id,models,selected,prefix){
  const select=$(id);
  select.replaceChildren(new Option('モデルを選択…',''));
  for(const path of models)select.append(new Option(path.startsWith(prefix)?path.slice(prefix.length):path,path));
  // Saved paths outside the catalog remain usable; refreshing never changes selection.
  if(selected&&!models.includes(selected))select.append(new Option(selected+'（一覧外）',selected));
  select.value=selected||'';
}
async function refreshModelChoices(embedding){
  const button=$(embedding?'embedding-model-refresh':'model-list-refresh');
  button.disabled=true;
  try{
    const catalog=await api('/api/model-catalog');
    const models=embedding?catalog.embeddings:catalog.models;
    fillModelChoices(embedding?'embedding-model':'model-list',models,embedding?$('embedding-model').value:$('model').value,embedding?'Models/embeddings/':'Models/LLM/');
    notice((embedding?'記憶検索':'会話')+'モデルの一覧を再取得しました（'+models.length+'件）。');
  }catch(error){report(error);}finally{button.disabled=false;}
}
$('embedding-model-refresh').addEventListener('click',()=>refreshModelChoices(true));
$('model-list-refresh').addEventListener('click',()=>refreshModelChoices(false));
$("model").addEventListener("change",()=>refreshAux().catch(report));
$("auto_aux").addEventListener("change",()=>refreshAux().catch(report));
function syncSettingsToggle() {
  $("toggle-settings").setAttribute("aria-expanded", String(!document.body.classList.contains("settings-hidden")));
}
$("toggle-settings").setAttribute("aria-controls", "settings-panel");
$("toggle-settings").addEventListener("click",()=>{document.body.classList.toggle("settings-hidden");syncSettingsToggle();});
$("import-pick").addEventListener('click',async()=>{if(importPicking||busy||editorBusy||slotsBusy)return;importPicking=true;controls();try{const selected=await api('/api/import-pick',{});if(selected.path)$("import-path").value=selected.path;}catch(error){report(error);}finally{importPicking=false;controls();}});
$("import-room").addEventListener("click",async()=>{const button=$("import-room");button.disabled=true;notice("キャラフォルダをコピーしています…");try{const imported=await api("/api/import",{path:$("import-path").value});await loadState(imported.id);notice("取り込みました。原本は変更していません。");}catch(error){report(error);}finally{controls();}});
$("start-engine").addEventListener("click",async()=>{try{await saveSettings();engineState(await api("/api/start",{}));notice("モデルを準備しています。起動ログから進捗を確認できます。");}catch(error){report(error);}});
$("stop-engine").addEventListener("click",async()=>{try{if(!window.AkariConversation?.status()?.remote)$("engine-state").textContent="Stopping";engineState(await api("/api/stop",{}));notice("モデルを停止しました。");}catch(error){report(error);}});
$("refresh-log").addEventListener("click",async()=>{try{$("engine-log").textContent=(await api("/api/engine-log")).text||"ログはまだありません。";}catch(error){report(error);}});
$("refresh-context").addEventListener("click",()=>refreshContext().catch(report));
function searchReference(result){
  let score='';
  if(result.score!==undefined){
    score=(result.intent?'複合点 ':'スコア ')+result.score.toFixed(3);
    if(result.base_score!==undefined)score+='（元 '+result.base_score.toFixed(3)+' / Arousal補助 +'+(100*result.arousal_bonus).toFixed(1)+'%）';
    else if(result.intent&&result.retrieval_base_score!==undefined)score+='（元 '+result.retrieval_base_score.toFixed(3)+'）';
    if(result.intent)score+=' / 意図 '+result.intent+' ×'+result.intent_weight.toFixed(2)+' / 新しさ ×'+result.temporal_weight.toFixed(3);
    score+=' · ';
  }
  return score+result.path+(result.record_index===undefined?'':' #record='+result.record_index)+(result.message_id===undefined?'':' #message='+result.message_id)+(result.message_index===undefined?'':' #entry='+result.message_index)+' · '+({memory:'記憶',conversation:'過去会話',knowledge:'知識資料'}[result.category]||result.category);
}
$("search-form").addEventListener("submit",async event=>{
  event.preventDefault();if(!room)return;const selected=room.id,button=$("search-button");button.disabled=true;$("search-results").textContent="検索中…";
  try{const result=await api("/api/search?room="+encodeURIComponent(selected)+"&q="+encodeURIComponent($("search-query").value)+"&scope="+$("search-scope").value+"&method="+$("search-method").value);if(room.id!==selected)return;const lines=[result.method,result.results.length?result.results.length+"件を表示":"条件に合う記録はありません。言葉や類似度の下限を変えて検索できます。"];for(const item of result.results)lines.push("\n"+searchReference(item)+"\n"+item.excerpt);lines.push(...result.warnings);$("search-results").textContent=lines.join("\n");}catch(error){if(room.id===selected)$("search-results").textContent=error.message;}finally{button.disabled=false;}
});
const HOLD_DURATION=1000;
function bindHold(button,action){
  let timer=null,holding=false;
  function reset(){clearTimeout(timer);timer=null;holding=false;button.classList.remove("holding");}
  function start(){
    if(button.disabled||holding||lifecycleBusy)return;
    holding=true;button.classList.add("holding");
    timer=setTimeout(()=>{timer=null;button.classList.remove("holding");action().catch(report);},HOLD_DURATION);
  }
  button.addEventListener("pointerdown",event=>{
    if(event.button!==0)return;
    event.preventDefault();button.focus({preventScroll:true});button.setPointerCapture(event.pointerId);start();
  });
  button.addEventListener("pointermove",event=>{if(!holding)return;const r=button.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)reset();});
  for(const event of ["pointerup","pointercancel","lostpointercapture","blur"])button.addEventListener(event,reset);
  button.addEventListener("keydown",event=>{if([" ","Enter"].includes(event.key)){event.preventDefault();if(!event.repeat)start();}});
  button.addEventListener("keyup",event=>{if([" ","Enter"].includes(event.key)){event.preventDefault();reset();}});
  button.addEventListener("click",event=>event.preventDefault());
  button.addEventListener("contextmenu",event=>event.preventDefault());
  window.addEventListener("blur",reset);
  document.addEventListener("visibilitychange",()=>{if(document.hidden)reset();});
}
async function finishApplication(restarting,updateRevision=null){
  if(lifecycleBusy)return;
  lifecycleBusy=true;controls();
  const oldToken=state.token;
  $("engine-state").textContent="Stopping";
  notice(updateRevision?"更新を適用して再起動しています…":restarting?"生成と推論サーバーを停止して再起動しています…":"アプリを終了しています…",15000);
  try{
    await api(updateRevision?"/api/update/apply":restarting?"/api/restart":"/api/shutdown",updateRevision?{revision:updateRevision}:{});
    ready=false;localReady=false;busy=false;generating=false;clearInterval(poll);controls();
    $("engine-state").textContent=restarting?"Starting":"Inactive";
    if(!restarting){notice("アプリと管理中の推論サーバーを終了しました。このタブを閉じてください。",15000);return;}
    const deadline=Date.now()+(updateRevision?300000:60000);
    while(Date.now()<deadline){
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),1500);
      try{
        const response=await fetch("/api/state",{cache:"no-store",signal:controller.signal});
        if(response.ok){const next=await response.json();if(next.token&&next.token!==oldToken){location.reload();return;}}
      }catch{}finally{clearTimeout(timeout);}
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    notice("再起動を確認できませんでした。起動コンソールを確認し、起動済みなら画面を再読み込みしてください。",15000);
  }catch(error){lifecycleBusy=false;controls();report(error);}
}
bindHold($("shutdown"),()=>finishApplication(false));
bindHold($("restart"),()=>finishApplication(true));
async function cancelConversationGeneration(){
  if(!generating||!jobId||cancelRequestPending||lifecycleBusy)return;
  const id=jobId;cancelRequestPending=true;controls();
  try{await api('/api/cancel',{id});notice('生成を停止しています…');}catch(error){report(error);}finally{cancelRequestPending=false;controls();}
}
$("cancel").addEventListener("click",cancelConversationGeneration);
$("send").addEventListener('click',event=>{if(generating){event.preventDefault();cancelConversationGeneration();}});
function imageElement(image){
  const element=document.createElement("img");element.src=image.url||image.data;element.alt=image.name||"添付画像";element.loading="lazy";element.decoding="async";
  element.addEventListener("click",()=>{$("large-image").src=element.src;$("image-viewer").showModal();});return element;
}
function showGeneratedImage(output,image){
  if(!image?.id||!image.url?.startsWith('/api/generated-image?'))return;
  const article=output.closest('article');if(!article||article.querySelector('[data-generated-image="'+CSS.escape(image.id)+'"]'))return;
  let gallery=article.querySelector('.message-images');if(!gallery){gallery=document.createElement('div');gallery.className='message-images';article.append(gallery);}
  const preview=imageElement(image);preview.dataset.generatedImage=image.id;gallery.append(preview);
}
function renderImages(){
  $("image-drafts").replaceChildren();
  pendingImages.forEach((image,index)=>{const card=document.createElement("div");card.className="image-draft";const remove=document.createElement("button");remove.type="button";remove.textContent="×";remove.setAttribute("aria-label",image.name+"を外す");remove.disabled=busy;remove.addEventListener("click",()=>{if(!busy){pendingImages.splice(index,1);renderImages();}});const name=document.createElement("small");name.textContent=image.name;card.append(imageElement(image),remove,name);$("image-drafts").append(card);});controls();
}
async function addImages(files){
  if(busy||processingImages){notice("画像の処理や生成が終わってから添付してください。");return;}
  processingImages=true;controls();
  try{
    for(const file of Array.from(files)){
      if(pendingImages.length>=4)throw new Error("画像は1回に4枚までです。");
      if(!["image/png","image/jpeg","image/webp"].includes(file.type))throw new Error("PNG・JPEG・WebPの画像を選んでください。");
      if(file.size>20*1024*1024)throw new Error("元画像は1枚20MiBまでです。");
      const bitmap=await createImageBitmap(file);
      try{
        if(bitmap.width*bitmap.height>32*1024*1024)throw new Error("画像の解像度が大きすぎます。縮小してから添付してください。");
        const scale=Math.min(1,2048/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement("canvas");
        canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
        const context=canvas.getContext("2d");context.drawImage(bitmap,0,0,canvas.width,canvas.height);
        let data=canvas.toDataURL(file.type==="image/jpeg"?"image/jpeg":"image/png",.9);
        if(data.length>5*1024*1024*4/3){context.fillStyle="#fff";context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);data=canvas.toDataURL("image/jpeg",.85);}
        const size=Math.ceil(data.split(",")[1].length*3/4);
        if(size>5*1024*1024)throw new Error("縮小後も画像が5MiBを超えています。");
        if(pendingImages.reduce((sum,image)=>sum+image.size,0)+size>12*1024*1024)throw new Error("画像の合計は12MiBまでです。");
        pendingImages.push({name:file.name||"貼り付け画像",data,size});
      }finally{bitmap.close();}
    }
    notice();
  }catch(error){report(error);}finally{processingImages=false;$("image-files").value="";renderImages();}
}
$("attach-image").addEventListener("click",()=>$("image-files").click());
$("image-files").addEventListener("change",()=>addImages($("image-files").files));
$("message").addEventListener("paste",event=>{const files=Array.from(event.clipboardData?.files||[]);if(files.length){event.preventDefault();addImages(files);const text=event.clipboardData.getData("text/plain");if(text)$("message").setRangeText(text,$("message").selectionStart,$("message").selectionEnd,"end");}});
$("chat-form").addEventListener("dragover",event=>{if(event.dataTransfer.types.includes("Files")){event.preventDefault();$("chat-form").classList.add("drag-over");}});
$("chat-form").addEventListener("dragleave",()=>$("chat-form").classList.remove("drag-over"));
$("chat-form").addEventListener("drop",event=>{event.preventDefault();$("chat-form").classList.remove("drag-over");addImages(event.dataTransfer.files);});
$("close-image").addEventListener("click",()=>$("image-viewer").close());
$("message").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();if(!generating&&!$("send").disabled)$("chat-form").requestSubmit();}});
$("chat-form").addEventListener("submit",async event=>{
  event.preventDefault();const input=$("message"),text=input.value.trim();if((!text&&!pendingImages.length)||busy||conversationEdit||!ready||processingImages||(pendingImages.length&&!vision))return;
  const images=pendingImages.map(image=>({...image})),sentText=text||(images.length?"この画像について教えて。":"");
  busy=true;generating=true;jobId=null;controls();notice();$("metrics").textContent="";
  if($("messages").querySelector(".welcome"))$("messages").replaceChildren();
  const sentAt=new Date().toISOString();const userBody=message("user",sentText,"complete",images,null,$("messages"),false,{created:sentAt});const output=message("assistant","");scrollDown();
  const effects=new Set(),effectRoom=room.id;
  let buffer="",full="",paintPending=false,sawDone=false;
  function paint(){renderAssistant(output,full,generating&&!sawDone);scrollDown();paintPending=false;}
  function consume(event){
    collectToolEffects(effects,event);
    if(event.type==="start"){jobId=event.id;input.value="";pendingImages=[];renderImages();controls();if(event.context)$("context-last").textContent=describeContext(event.context);}
    if(event.type==="replace"){full=event.text;renderAssistant(output,full);}
    if(event.type==="tool_start")setThinking(output,true);
    if(event.type==="generated_image")showGeneratedImage(output,event.image);
    if(event.type==="delta"){full+=event.text;if(!paintPending){paintPending=true;requestAnimationFrame(paint);}}
    if(event.type==="error")notice(event.message);
    if(event.type==="done"){sawDone=true;setThinking(output,false);if(event.message?.status==="no_response"){full=event.message.content;renderAssistant(output,full,false);}if(event.message)setMessageMetadata(output,"assistant",event.message);$("metrics").textContent=(event.first_seconds===null?"":"最初の本文まで "+event.first_seconds.toFixed(2)+"秒 · ")+"全体 "+event.total_seconds.toFixed(2)+"秒";if(event.status==="cancelled")notice("生成を停止しました。途中の返答も保存しています。");}
  }
  try{
    streamController=new AbortController();
    const response=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json","X-App-Token":state.token},body:JSON.stringify({room:room.id,message:sentText,images:images.map(image=>({name:image.name,data:image.data}))}),signal:streamController.signal});
    if(!response.ok)throw new Error((await response.json()).error);
    const reader=response.body.getReader(),decoder=new TextDecoder();
    while(true){const {value,done}=await reader.read();if(done){buffer+=decoder.decode();break;}buffer+=decoder.decode(value,{stream:true});let boundary;while((boundary=buffer.indexOf("\n"))>=0){const line=buffer.slice(0,boundary);buffer=buffer.slice(boundary+1);if(line)consume(JSON.parse(line));}}
    if(buffer.trim())consume(JSON.parse(buffer));
    if(!sawDone)throw new Error("通信が途中で切れました。履歴を再読み込みして確認してください。");
  }catch(error){report(error);if(!jobId){userBody.closest("article").remove();output.closest("article").remove();}}finally{generating=false;setThinking(output,false);paint();busy=false;const started=!!jobId;jobId=null;renderImages();if(started)try{await selectRoom(effectRoom);await syncToolEffects(effects,effectRoom);}catch(error){report(error);}controls();input.focus();}
});
const compactSettings = matchMedia("(max-width:1100px)");
function fitSettings() { document.body.classList.toggle("settings-hidden", compactSettings.matches); syncSettingsToggle(); }
compactSettings.addEventListener("change", fitSettings);
fitSettings();
document.addEventListener("DOMContentLoaded",()=>{
  loadState().then(()=>window.AkariStartup?.mainReady()).catch(error=>{report(error);window.AkariStartup?.mainFailed();});
},{once:true});
let autonomyLiveBody=null,autonomyPollBusy=false;const autonomyOutcomeSeen=new Map(),autonomyViewStarted=Date.now();
async function syncAutonomyConversation(snapshot,selected){
  if(!snapshot||!room||room.id!==selected||busy||conversationEdit||autonomyPollBusy)return;
  autonomyPollBusy=true;
  try{
    const outcome=snapshot.latest_result,key=outcome?outcome.id+':'+outcome.state:'';
    const changed=autonomyOutcomeSeen.has(selected)?autonomyOutcomeSeen.get(selected)!==key:Date.parse(outcome?.completed_at||'')>=autonomyViewStarted;
    if(changed&&outcome?.error&&['failed','partial','unknown'].includes(outcome.state))notice('自律行動：'+outcome.error,10000);
    autonomyOutcomeSeen.set(selected,key);
    const ids=[...$("messages").querySelectorAll('[data-message-id]')].map(e=>Number(e.dataset.messageId));
    if(snapshot.latest_message_id>Math.max(0,...ids)){
      const pane=$("messages"),top=pane.scrollTop,nearBottom=pane.scrollHeight-pane.clientHeight-top<80;
      await refreshConversation();autonomyLiveBody=null;if(!nearBottom)pane.scrollTop=top;
    }
    if(room?.id!==selected||busy||conversationEdit)return;
    if(snapshot.active){
      const active=snapshot.active;
      if(!autonomyLiveBody?.isConnected||autonomyLiveBody.closest('article').dataset.autonomyRun!==active.id){
        autonomyLiveBody?.closest('article')?.remove();$("messages").querySelector('.welcome')?.remove();
        autonomyLiveBody=message('assistant','', 'complete',[],null,$("messages"),false,{origin:'autonomy'});
        autonomyLiveBody.closest('article').dataset.autonomyRun=active.id;
      }
      renderAssistant(autonomyLiveBody,active.text||'');setThinking(autonomyLiveBody,true);
    }else if(autonomyLiveBody){autonomyLiveBody.closest('article')?.remove();autonomyLiveBody=null;}
  }finally{autonomyPollBusy=false;}
}
const imageCompletionSeen=new Map();
function syncImageCompletion(items,selected){
 if(room?.id!==selected)return;
 for(const item of items||[]){
  const key=selected+':'+item.id,signature=item.state+':'+item.error_code,previous=imageCompletionSeen.get(key);imageCompletionSeen.set(key,signature);
  if(previous===signature)continue;
  if(item.state==='unknown')notice(item.error_code==='timeout'?'画像生成がタイムアウトしました。生成先の結果は未確認です。画像生成の履歴を確認してください。':'画像生成の結果を確認できません。画像生成の履歴と生成先を確認してください。',12000);
  else if(previous&&item.state==='failed')notice('画像生成は実行されませんでした。画像生成の履歴を確認してください。',12000);
  else if(previous&&item.state==='succeeded')notice('画像生成が完了し、画像を保存しました。',8000);
 }
 if(imageCompletionSeen.size>200)for(const key of [...imageCompletionSeen.keys()].slice(0,100))imageCompletionSeen.delete(key);
}
let toolCompletionPolling=false;
async function syncToolCompletion(snapshot,selected){
 if(!snapshot||room?.id!==selected||busy||conversationEdit||toolCompletionPolling)return;
 const ids=[...$("messages").querySelectorAll('[data-message-id]')].map(e=>Number(e.dataset.messageId));
 if(snapshot.latest_message_id<=Math.max(0,...ids))return;
 toolCompletionPolling=true;
 try{const pane=$("messages"),top=pane.scrollTop,nearBottom=pane.scrollHeight-pane.clientHeight-top<80;await refreshConversation();if(room?.id===selected&&!nearBottom)pane.scrollTop=top;}
 finally{toolCompletionPolling=false;}
}
const discordChatRevisions=new Map();
let discordThinkingBody=null,discordThinkingKey='';
function clearDiscordThinking(){discordThinkingBody?.closest('article')?.remove();discordThinkingBody=null;}
function syncDiscordThinking(activity,selected){
 if(selected!==room?.id)return;
 const key=selected+':'+(activity?activity.id+':'+activity.state:'');
 if(key!==discordThinkingKey){discordThinkingKey=key;refreshPortrait().catch(report);}
 if(!activity||activity.room!==selected||busy||generating||conversationEdit||chatLogLoading){clearDiscordThinking();return;}
 let followBottom=false;
 if(!discordThinkingBody?.isConnected||discordThinkingBody.closest('article').dataset.discordReply!==activity.id){
  const pane=$('messages');followBottom=pane.scrollHeight-pane.clientHeight-pane.scrollTop<80;
  clearDiscordThinking();$('messages').querySelector('.welcome')?.remove();
  discordThinkingBody=message('assistant','');
  const article=discordThinkingBody.closest('article');article.dataset.discordReply=activity.id;article.dataset.origin='discord';
 }
 const label={queued:'Discordの返信待ち',generating:'Discordへの応答を生成中',sending:'Discordへ送信中'}[activity.state];
 discordThinkingBody.closest('article').querySelector('.speaker').textContent=room.name+' · '+label;
 setThinking(discordThinkingBody,true);discordThinkingBody.querySelector('.thinking-indicator')?.setAttribute('aria-label',label);
 if(followBottom)scrollDown();
}
async function syncDiscordChat(revision,selected){
 if(!selected||selected!==room?.id||busy||generating||conversationEdit||chatLogLoading)return;
 if(revision==null||discordChatRevisions.get(selected)===revision)return;
 await refreshConversation();
 if(selected!==room?.id)return;
 if(chatLogState?.room===selected&&!$('workspace-logs').hidden)await chatLogLoad();
 discordChatRevisions.set(selected,revision);
}
const poll=setInterval(async()=>{if(!state)return;try{const result=await api("/api/status?room="+encodeURIComponent(room?.id||""));window.AkariRooms?.setInitialSettings(result.initial_settings_enabled);window.AkariConversation?.reportStatus(result.conversation,result.conversation_room);syncImageCompletion(result.image_jobs,result.conversation_room);await syncAutonomyConversation(result.autonomy_chat,result.conversation_room);await syncDiscordChat(result.discord_chat_revision,result.conversation_room);await syncToolCompletion(result.tool_completion,result.conversation_room);syncDiscordThinking(result.discord_reply_activity,result.conversation_room);const wasReady=ready;engineState(result.engine);semanticState(result.semantic);if(!wasReady&&ready)notice("モデルの準備ができました。");if(room){await refreshSchedules();await ensureFirstMessage();if(!$("workspace-world").hidden&&!$("images-panel").hidden)await refreshImageJobs();if(!$("workspace-autonomy").hidden)await refreshAutonomy();}}catch{clearDiscordThinking();}},1500);

function semanticState(status){
  if(!status)return;
  const phases={idle:"未作成 / 停止",building:"索引作成中",ready:"索引作成完了",error:"エラー"};
  $("embedding-status").textContent="検索モデル: "+({stopped:"停止中",loading:"準備中",ready:"検索可能",error:"エラー"}[status.engine.state]||status.engine.state)+"\n"+(phases[status.state]||status.state)+(status.room?" · "+status.room:"")+"\n"+status.done+" / "+status.total+"断片"+(status.error?"\n"+status.error:"")+(status.engine.error?"\n"+status.engine.error:"")+"\n"+(status.warnings||[]).join("\n");
  $("embedding-build").disabled=status.state==="building";
  $("room-index-status").textContent=$("embedding-status").textContent;
}
$("embedding-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const saveButton=event.currentTarget.querySelector('button[type="submit"]');
  if(saveButton.disabled)return;
  saveButton.disabled=true;
  try{
    const saved=await api("/api/embedding-settings",{model:$("embedding-model").value,profile:$("embedding-profile").value,threshold:Number($("embedding-threshold").value),auto_start:$("embedding-auto-start").checked,auto_semantic:$("embedding-auto-semantic").checked});
    semanticState(saved.status);
    notice(saved.model?"検索設定を保存し、検索モデルの起動を開始しました。":"検索設定を保存しました。");
  }catch(error){report(error);}finally{saveButton.disabled=false;}
});
$("embedding-build").addEventListener("click",async()=>{try{semanticState(await api("/api/embedding-build",{room:room.id}));notice("このルームの索引を準備しています。");}catch(error){report(error);}});

function episodeControls(){
  memoryRouteControls();
  $("episode-preview").disabled=busy||!room;
  $("episode-generate").disabled=busy||!localReady||!episodeSource||episodeSource.room!==room?.id;
  const pending=episodeDraft?.status==="pending";
  $("episode-approve").disabled=busy||!pending;
  $("episode-discard").disabled=busy||!pending;
  $("episode-drafts").disabled=busy;
  $("episode-limit").disabled=busy;$("episode-day").disabled=busy;
  $("episode-summary").readOnly=busy||!pending;
}
function episodeSourceText(source){
  return source.messages.map(m=>"#"+m.id+" · "+(m.role==="user"?"YOU":"CHARACTER")+" · "+m.created+" UTC\n"+m.content).join("\n\n")+(source.warnings.length?"\n\n"+source.warnings.join("\n"):"");
}
async function refreshEpisodeDrafts(selected=""){
  if(!room)return;const id=room.id,items=await api("/api/episode-drafts?room="+encodeURIComponent(id));if(room.id!==id)return;
  $("episode-drafts").replaceChildren(new Option("草案を選択…",""));
  const labels={pending:"確認待ち",approved:"保存済み",discarded:"見送り"};
  for(const item of items)$("episode-drafts").append(new Option(item.day+" · "+labels[item.status]+" · "+item.summary.slice(0,24),item.id));
  if(selected){$("episode-drafts").value=selected;await loadEpisodeDraft(selected);}
  episodeControls();
}
async function loadEpisodeDraft(id){
  if(!id){episodeDraft=null;episodeControls();return;}
  const selected=room.id,draft=await api("/api/episode-draft?room="+encodeURIComponent(selected)+"&id="+encodeURIComponent(id));if(room.id!==selected||$("episode-drafts").value!==id)return;
  episodeSource=null;episodeDraft=draft;$("episode-summary").value=draft.summary;$("episode-day").value=draft.day;
  $("episode-source").textContent=episodeSourceText(draft.source);
  $("episode-status").textContent=draft.status==="approved"?"記憶に保存済み: "+draft.path:draft.status==="discarded"?"この草案は見送りました。":"確認待ちです。本文と日付を修正してから保存できます。";
  episodeControls();
}
$("episode-drafts").addEventListener("change",()=>loadEpisodeDraft($("episode-drafts").value).catch(report));
$("episode-limit").addEventListener("input",()=>{episodeSource=null;episodeControls();});
$("episode-preview").addEventListener("click",async()=>{
  const selected=room.id;try{const source=await api("/api/episode-source?room="+encodeURIComponent(selected)+"&limit="+Number($("episode-limit").value));if(room.id!==selected)return;episodeDraft=null;$("episode-drafts").value="";$("episode-summary").value="";episodeSource=source;$("episode-source").textContent=episodeSourceText(source);$("episode-status").textContent="この会話から草案を作ります。生成中は通常の会話を待ってください。";episodeControls();}catch(error){episodeSource=null;episodeControls();report(error);}
});
$("episode-generate").addEventListener("click",async()=>{
  if(busy||!episodeSource)return;
  const selected=room.id,source=episodeSource,day=$("episode-day").value;
  busy=true;generating=true;jobId=null;episodeDraft=null;$("episode-summary").value="";controls();notice();
  let full="",buffer="",sawDone=false,created=null;
  function consume(event){
    if(event.type==="start"){jobId=event.id;controls();}
    if(event.type==="delta"){full+=event.text;$("episode-summary").value=full;}
    if(event.type==="draft")created=event.draft;
    if(event.type==="error")report(new Error(event.message));
    if(event.type==="done"){sawDone=true;$("episode-status").textContent=event.status==="complete"?"草案を保存しました。内容を確認してください。":event.status==="cancelled"?"草案の生成を停止しました。記憶には保存していません。":"草案を保存できませんでした。";}
  }
  try{
    streamController=new AbortController();
    const response=await fetch("/api/episode-generate",{method:"POST",headers:{"Content-Type":"application/json","X-App-Token":state.token},body:JSON.stringify({room:selected,day,limit:source.limit,source_hash:source.hash}),signal:streamController.signal});
    if(!response.ok)throw new Error((await response.json()).error);
    const reader=response.body.getReader(),decoder=new TextDecoder();
    while(true){const {value,done}=await reader.read();if(done){buffer+=decoder.decode();break;}buffer+=decoder.decode(value,{stream:true});let boundary;while((boundary=buffer.indexOf("\n"))>=0){const line=buffer.slice(0,boundary);buffer=buffer.slice(boundary+1);if(line)consume(JSON.parse(line));}}
    if(buffer.trim())consume(JSON.parse(buffer));
    if(!sawDone)throw new Error("通信が途中で切れました。保存した草案の一覧を確認してください。");
  }catch(error){report(error);}finally{busy=false;generating=false;jobId=null;episodeSource=null;controls();if(room.id===selected)try{await refreshEpisodeDrafts(created?.id||"");}catch(error){report(error);}}
});
async function finishEpisode(action){
  if(!episodeDraft||busy)return;const selected=room.id,id=episodeDraft.id;
  try{const draft=await api("/api/episode-"+action,{room:selected,id,day:$("episode-day").value,summary:$("episode-summary").value});if(room.id!==selected)return;await refreshEpisodeDrafts(draft.id);await refreshContext();notice(action==="approve"?"確認した内容を新しいエピソード記憶に保存しました。意味検索には索引更新が必要です。":"草案を見送りました。");}catch(error){report(error);}
}
$("episode-approve").addEventListener("click",()=>finishEpisode("approve"));
$("episode-discard").addEventListener("click",()=>finishEpisode("discard"));

function editorCanonical(text){return text.replace(/\r\n?/g,"\n");}
function editorDirty(){return !!editorDocument&&editorCanonical($("editor-text").value)!==editorCanonical(editorDocument.text);}
function editorControls(){
  const blocked=busy||editorBusy||slotsBusy;
  $("editor-key").disabled=blocked;$("editor-load").disabled=blocked||!room;
  $("editor-reload").disabled=blocked||!room;
  $("editor-text").readOnly=blocked||!editorDocument||!editorDocument.editable;
  $("editor-save").disabled=blocked||!editorDocument||!editorDocument.editable||(!editorDirty()&&editorDocument.exists);
  $("editor-check").disabled=blocked||!editorDocument;
  $("editor-backups").disabled=blocked;
  $("editor-backup-load").disabled=blocked||!editorBackup||!editorBackup.exists||editorBackup.path!==editorDocument?.path;
}
function resetEditor(){
  editorRequest++;editorBusy=false;editorDocument=null;editorBackup=null;
  $("editor-text").value="";$("editor-info").textContent="";$("editor-status").textContent="";
  $("editor-latest").hidden=true;$("editor-backup-preview").textContent="";
  $("editor-backups").replaceChildren(new Option("バックアップなし",""));
}
function showEditorDocument(doc){
  editorDocument=doc;$("editor-text").value=doc.text;
  $("editor-info").textContent=doc.path+" · "+doc.encoding.toUpperCase()+(doc.bom?" BOMあり":"")+" · "+doc.newline+" · "+(doc.exists?doc.bytes.toLocaleString()+" bytes":"未配置（保存すると作成します）");
  $("editor-status").textContent=doc.editable?"読み込み済みです。変更後に保存してください。":"改行コードが混在しているため、表示だけに対応しています。";
  $("editor-latest").hidden=true;editorControls();
}
async function refreshEditorBackups(){
  const doc=editorDocument;if(!doc)return;
  const rows=await api("/api/document-backups?room="+encodeURIComponent(doc.room)+"&key="+doc.key);
  if(editorDocument!==doc)return;
  editorBackup=null;$("editor-backup-preview").textContent="";
  $("editor-backups").replaceChildren(new Option(rows.length?"バックアップを選択…":"バックアップなし",""));
  for(const item of rows)$("editor-backups").append(new Option(new Date(item.created).toLocaleString("ja-JP")+" · "+item.path+(item.exists?"":"（更新前は未配置）"),item.id));
}
async function loadEditor(discard=false){
  if(!room||editorBusy||busy)return;
  if(editorDirty()&&!discard){$("editor-status").textContent="未保存の本文があります。保存するか、編集内容を破棄して再読込してください。";return;}
  const selected=room.id,key=$("editor-key").value,request=++editorRequest;
  editorBusy=true;controls();
  try{const doc=await api("/api/document?room="+encodeURIComponent(selected)+"&key="+key);if(room.id!==selected||request!==editorRequest)return;showEditorDocument(doc);await refreshEditorBackups();}
  catch(error){if(editorDocument)$("editor-key").value=editorDocument.key;$("editor-status").textContent=error.message;report(error);}
  finally{if(request===editorRequest){editorBusy=false;controls();}}
}
$("editor-text").addEventListener("input",()=>{editorControls();$("editor-status").textContent=editorDirty()?"未保存の変更があります。":"読み込み時と同じ内容です。";});
$("editor-load").addEventListener("click",()=>loadEditor().catch(report));
$("editor-reload").addEventListener("click",()=>loadEditor(true).catch(report));
$("editor-check").addEventListener("click",async()=>{
  const doc=editorDocument;if(!doc)return;
  try{const latest=await api("/api/document?room="+encodeURIComponent(doc.room)+"&key="+doc.key);if(editorDocument!==doc)return;$("editor-latest").textContent="最新の内容（編集中の本文は保持しています）\n"+latest.path+"\n\n"+latest.text;$("editor-latest").hidden=false;}catch(error){report(error);}
});
$("editor-save").addEventListener("click",async()=>{
  const doc=editorDocument;if(!doc||editorBusy||busy)return;
  editorBusy=true;controls();
  try{
    const result=await api("/api/document-save",{room:doc.room,key:doc.key,revision:doc.revision,text:$("editor-text").value});
    if(room.id!==doc.room)return;showEditorDocument(result.document);await refreshEditorBackups();
    if(doc.key==="prompt")room.prompt=result.document.text.trim();
    if(doc.key==="core")room.memory=result.document.text.trim();
    try{await refreshContext();}catch(error){report(new Error("保存は完了しましたが、送信内訳の更新に失敗しました: "+error.message));}
    $("editor-status").textContent=result.changed?"更新前のバックアップを作成して保存しました。":"変更がないため、ファイルは書き換えていません。";
  }catch(error){$("editor-status").textContent=error.message+" 編集中の本文は残しています。";report(error);}
  finally{editorBusy=false;controls();}
});
$("editor-backups").addEventListener("change",async()=>{
  const doc=editorDocument,id=$("editor-backups").value;editorBackup=null;editorControls();
  if(!doc||!id){$("editor-backup-preview").textContent="";return;}
  try{const backup=await api("/api/document-backup?room="+encodeURIComponent(doc.room)+"&key="+doc.key+"&id="+encodeURIComponent(id));if(editorDocument!==doc||$("editor-backups").value!==id)return;editorBackup=backup;$("editor-backup-preview").textContent=backup.exists?backup.text:"更新前はファイルがありませんでした。";editorControls();}catch(error){report(error);}
});
$("editor-backup-load").addEventListener("click",()=>{if(!editorBackup||!editorDocument||editorBackup.path!==editorDocument.path)return;$("editor-text").value=editorBackup.text;$("editor-status").textContent="バックアップの本文を編集欄に読み込みました。確認して保存してください。";editorControls();});
function applicationDraftDirty(){return appearanceDraftDirty()||itemDraftDirty()||avatarDirty()||window.AkariGroups?.dirty()||window.AkariIndexRefresh?.dirty()||window.AkariConversation?.dirty()||editorDirty()||slotsDraftDirty()||resourceDirty()||worldDirty()||lifeDirty()||autonomyDirty();}
window.addEventListener("beforeunload",event=>{if(lifecycleBusy)return;if(applicationDraftDirty()){event.preventDefault();event.returnValue="";}});

let memoryRouteSource=null,memoryRouteOperation=null,memoryRouteBusy=false;
function memoryRouteControls(){
  const locked=busy||editorBusy||slotsBusy||!room;
  const selected=!!episodeDraft&&episodeDraft.id===$("episode-drafts").value;
  $("memory-route-preview").disabled=locked||!selected||!['pending','approved'].includes(episodeDraft?.status);
  $("memory-route-generate").disabled=locked||!selected||!localReady||memoryRouteSource?.room!==room?.id||memoryRouteSource?.id!==episodeDraft?.id;
  $("memory-route-stop").disabled=!memoryRouteBusy;
  $("memory-route-history").disabled=locked;
}
async function memoryRouteHistory(){
  if(!room)return;const selected=room.id,rows=await api('/api/memory-route-history?'+new URLSearchParams({room:selected}));
  if(room?.id!==selected)return;
  $("memory-route-history-text").textContent=rows.map(r=>r.created+' UTC / '+r.state+' / '+r.source_id+(r.error?' / '+r.error:'')+(r.result?' / '+r.result.drafts.map(d=>d.kind+' #'+d.id+' '+d.status).join(' / '):'')).join('\n')||'分類履歴はありません（最新20件）。';
}
$("memory-route-preview").addEventListener('click',async()=>{
  if(!room||busy||!episodeDraft||episodeDraft.id!==$("episode-drafts").value)return;
  if($("episode-summary").value!==episodeDraft.summary||$("episode-day").value!==episodeDraft.day){notice('分類は保存済みの草案本文が対象です。編集内容を控えるか元に戻してから確認してください。');return;}
  const selected=room.id,key=episodeDraft.id;memoryRouteSource=null;memoryRouteOperation=null;busy=true;controls();
  $("memory-route-source").value='';$("memory-route-unclassified").textContent='';
  try{const data=await api('/api/memory-route-preview?'+new URLSearchParams({room:selected,id:key}));if(room?.id!==selected||episodeDraft?.id!==key)return;
    memoryRouteSource=data;memoryRouteOperation=crypto.randomUUID();$("memory-route-source").value=data.source.text;$("memory-route-status").textContent='分類する本文は草案のsummaryだけです。元の会話は保存時の照合と人の確認に使います。';await memoryRouteHistory();
  }catch(error){$("memory-route-status").textContent=error.message;report(error);}finally{busy=false;controls();}
});
$("memory-route-generate").addEventListener('click',async()=>{
  if(!memoryRouteSource||busy||memoryRouteSource.id!==episodeDraft?.id||episodeDraft.id!==$("episode-drafts").value||memoryRouteSource.room!==room?.id)return;
  if(resourceDirty()||updateReviewDirty()){notice('未保存の本文を先に保存または破棄してください。');return;}
  if($("episode-summary").value!==episodeDraft.summary||$("episode-day").value!==episodeDraft.day){notice('草案の編集欄に変更があります。分類元を再確認してください。');return;}
  const source=memoryRouteSource;busy=true;generating=true;memoryRouteBusy=true;controls();$("memory-route-status").textContent='分類中…120秒まで。原本は変更しません。';
  try{const result=await api('/api/memory-route-generate',{room:source.room,id:source.id,revision:source.revision,request_id:memoryRouteOperation});
    if(room?.id!==source.room)return;
    $("memory-route-status").textContent=(result.replayed?'保存済みの結果を確認しました。':'分類が完了しました。')+' 追記案 '+result.drafts.length+'件。AI更新案で内容を確認してください。';
    $("memory-route-unclassified").textContent=result.unclassified.join('\n\n')||'分類対象外として残った本文はありません（分類の正しさを保証するものではありません）。';
    await memoryRouteHistory();await updateReviewList();
  }catch(error){$("memory-route-status").textContent='完了未確認です。履歴を確認してください。同じ操作の再送では再生成しません。';report(error);}
  finally{memoryRouteBusy=false;busy=false;generating=false;controls();}
});
$("memory-route-stop").addEventListener('click',()=>api('/api/memory-route-cancel',{room:memoryRouteSource?.room,request_id:memoryRouteOperation}).catch(report));
$("memory-route-history").addEventListener('click',()=>memoryRouteHistory().catch(report));
$("episode-drafts").addEventListener('change',()=>{memoryRouteSource=null;memoryRouteOperation=null;$("memory-route-source").value='';$("memory-route-status").textContent='';$("memory-route-unclassified").textContent='';memoryRouteControls();});

let memorySortSource=null,memorySortOperation=null,memorySortRequest=0;
function memorySortControls(){
  const locked=busy||editorBusy||slotsBusy||!room;
  $("memory-sort-preview").disabled=locked;$("memory-sort-history").disabled=locked;
  $("memory-sort-create").disabled=locked||memorySortSource?.room!==room?.id||!memorySortSource?.targets.some(t=>t.added.length);
}
async function memorySortHistory(){
  if(!room)return;const selected=room.id;
  const rows=await api('/api/memory-sort-history?'+new URLSearchParams({room:selected}));
  if(room?.id!==selected)return;
  $("memory-sort-history-text").textContent=rows.map(r=>r.created+' UTC / '+r.drafts.map(d=>(d.kind==='identity'?'永続記憶':'日記')+' #'+d.id+' '+d.status).join(' / ')+' / 未分類 '+r.unclassified+'件').join('\n')||'作成履歴はありません（最新20件）。';
}
$("memory-sort-preview").addEventListener('click',async()=>{
  if(!room||busy)return;const selected=room.id,request=++memorySortRequest;
  memorySortSource=null;memorySortOperation=null;busy=true;controls();
  for(const id of ['memory-sort-source','memory-sort-unclassified'])$(id).value='';
  $("memory-sort-result").textContent='';
  try{
    const data=await api('/api/memory-sort-preview?'+new URLSearchParams({room:selected}));
    if(room?.id!==selected||request!==memorySortRequest)return;
    memorySortSource=data;memorySortOperation=crypto.randomUUID();$("memory-sort-source").value=data.source.text;
    $("memory-sort-unclassified").value=data.unclassified.map(b=>b.text).join('\n');
    $("memory-sort-result").textContent=data.targets.map(t=>(t.kind==='identity'?'永続記憶':'日記')+' → '+t.path+' / 追記 '+t.added.length+'件 / 重複 '+t.duplicates+'件\n'+t.added.join(' / ')).join('\n\n');
    $("memory-sort-status").textContent=data.warning;await memorySortHistory();
  }catch(error){$("memory-sort-status").textContent=error.message;report(error);}finally{busy=false;controls();}
});
$("memory-sort-create").addEventListener('click',async()=>{
  if(!memorySortSource||busy||memorySortSource.room!==room?.id)return;
  if(resourceDirty()||updateReviewDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const source=memorySortSource;busy=true;controls();
  try{const result=await api('/api/memory-sort-create',{room:source.room,revision:source.revision,request_id:memorySortOperation});
    if(room?.id!==source.room)return;
    $("memory-sort-status").textContent=(result.replayed?'保存済みの作成結果を確認しました。現在の状態は履歴を確認してください。':result.message)+' '+result.drafts.map(d=>'#'+d.id).join(' / ');
    await memorySortHistory();await updateReviewList();
  }catch(error){$("memory-sort-status").textContent=error.message+' 同じ操作の再送では案を増やしません。';report(error);}
  finally{busy=false;controls();}
});
$("memory-sort-history").addEventListener('click',()=>memorySortHistory().catch(report));
document.querySelector('[data-panel="memory-sort-panel"]').addEventListener('panelactivate',()=>memorySortHistory().catch(report));

let pipelineData=null,pipelineRoom=null,pipelineRequest=0;
function memoryPipelineControls(){
  $("pipeline-toggle").disabled=busy||editorBusy||slotsBusy||pipelineRoom!==room?.id||!pipelineData;
  $("pipeline-routing-toggle").disabled=busy||editorBusy||slotsBusy||pipelineRoom!==room?.id||!pipelineData;
  $("pipeline-routing-toggle").textContent='草案後の自動分類: '+(pipelineRoom===room?.id&&pipelineData?.settings.routing_enabled?'オン':'オフ');
  $("pipeline-routing-toggle").setAttribute('aria-pressed',String(pipelineRoom===room?.id&&!!pipelineData?.settings.routing_enabled));
  $("pipeline-toggle").textContent='自動草案: '+(pipelineRoom===room?.id&&pipelineData?.settings.enabled?'オン':'オフ');
  $("pipeline-toggle").setAttribute('aria-pressed',String(pipelineRoom===room?.id&&!!pipelineData?.settings.enabled));
  $("pipeline-refresh").disabled=busy||!room;
}
async function refreshMemoryPipeline(){
  if(!room)return;
  const selected=room.id,request=++pipelineRequest;
  const data=await api('/api/memory-pipeline?'+new URLSearchParams({room:selected}));
  if(room?.id!==selected||request!==pipelineRequest)return;
  pipelineData=data;pipelineRoom=selected;$("pipeline-source").textContent='';
  $("pipeline-status").textContent=data.settings.enabled?'次の草案までの完了回答: '+data.waiting_replies+' / 5（待機中の処理がある場合は、その終了後に受付）':'自動草案はオフです。有効化前のログは自動処理しません。';
  $("pipeline-runs").replaceChildren();
  for(const run of data.runs){
    const card=document.createElement('section'),meta=document.createElement('p');
    const names={queued:'待機中',running:'生成中',succeeded:'草案作成完了',failed:'失敗',cancelled:'取り消し',unknown:'結果未確認'};
    const draftNames={pending:'確認待ち（原本未保存）',approved:'確認保存済み',discarded:'見送り'};
    meta.textContent=(names[run.state]||run.state)+' / '+run.created_at+(run.draft_status?' / 草案: '+(draftNames[run.draft_status]||run.draft_status):'')+(run.error?' / '+run.error:'');card.append(meta);
    function button(label,action){const b=document.createElement('button');b.type='button';b.textContent=label;b.addEventListener('click',()=>action().catch(report));card.append(b);}
    button('対象会話を確認',async()=>{const source=await api('/api/memory-pipeline-source?'+new URLSearchParams({room:selected,id:run.id}));if(room?.id===selected)$("pipeline-source").textContent=episodeSourceText(source);});
    if(run.timeline_id)button('この処理の過程を見る',async()=>{$("tab-autonomy").click();document.querySelector('[data-panel="autonomy-timeline"]').click();await timelineLoad(run.timeline_id);});
    if(run.episode_draft_id)button('草案を開く',async()=>{
      if(episodeDraft?.status==='pending'&&($("episode-summary").value!==episodeDraft.summary||$("episode-day").value!==episodeDraft.day)){notice('編集中の草案があります。保存または見送り後に開いてください。');return;}
      await refreshEpisodeDrafts();if(room?.id!==selected)return;
      if(!Array.from($("episode-drafts").options).some(o=>o.value===run.episode_draft_id))$("episode-drafts").append(new Option('自動作成の草案',run.episode_draft_id));
      $("episode-drafts").value=run.episode_draft_id;await loadEpisodeDraft(run.episode_draft_id);
    });
    if(['queued','running'].includes(run.state)&&!run.cancel_requested)button('取り消す',async()=>{await api('/api/autonomy-cancel',{room:selected,id:run.id,revision:run.revision});if(room?.id===selected)await refreshMemoryPipeline();});
    if(run.state==='unknown'&&!run.reviewed)button('履歴を確認済みにする',async()=>{await api('/api/autonomy-acknowledge',{room:selected,id:run.id,revision:run.revision});if(room?.id===selected)await refreshMemoryPipeline();});
    const classification=run.classification;
    if(classification){
      const meta=document.createElement('p'),labels={waiting:'受付待ち',queued:'待機中',running:'分類中',succeeded:'追記案の作成完了',failed:'失敗',cancelled:'取り消し',unknown:'結果未確認'};
      meta.textContent='分類: '+(labels[classification.state]||classification.state)+(classification.error?' / '+classification.error:'');card.append(meta);
      if(classification.routing_result)button('分類結果を確認',async()=>{
        if(room?.id!==selected)return;
        const data=classification.routing_result;
        $("pipeline-source").textContent='追記案: '+data.drafts.map(d=>d.kind+' #'+d.id+' '+d.status).join(' / ')+'\n未分類・未抽出:\n'+(data.unclassified.join('\n\n')||'残った本文はありません。分類の正しさは人が確認してください。');
      });
      if(classification.timeline_id)button('分類の過程を見る',async()=>{$("tab-autonomy").click();document.querySelector('[data-panel="autonomy-timeline"]').click();await timelineLoad(classification.timeline_id);});
      if(classification.id&&['queued','running'].includes(classification.state)&&!classification.cancel_requested)button('分類を取り消す',async()=>{await api('/api/autonomy-cancel',{room:selected,id:classification.id,revision:classification.revision});if(room?.id===selected)await refreshMemoryPipeline();});
      if(classification.id&&classification.state==='unknown'&&!classification.reviewed)button('分類の履歴を確認済みにする',async()=>{await api('/api/autonomy-acknowledge',{room:selected,id:classification.id,revision:classification.revision});if(room?.id===selected)await refreshMemoryPipeline();});
    }
    $("pipeline-runs").append(card);
  }
  memoryPipelineControls();
}
$("pipeline-refresh").addEventListener('click',()=>refreshMemoryPipeline().catch(report));
document.querySelector('[data-panel="episode-panel"]').addEventListener('panelactivate',()=>refreshMemoryPipeline().catch(report));
$("pipeline-toggle").addEventListener('click',async()=>{
  if(!room||busy||pipelineRoom!==room.id||!pipelineData)return;
  const selected=room.id,policy=pipelineData.settings;busy=true;controls();
  try{await api('/api/memory-pipeline',{room:selected,enabled:!policy.enabled,revision:policy.revision});if(room?.id===selected)await refreshMemoryPipeline();}
  catch(error){if(room?.id===selected)$("pipeline-status").textContent=error.message+' 履歴を更新して確認してください。';report(error);}
  finally{busy=false;controls();}
});
$("pipeline-routing-toggle").addEventListener('click',async()=>{
  if(!room||busy||pipelineRoom!==room.id||!pipelineData)return;
  const selected=room.id,policy=pipelineData.settings;busy=true;controls();
  try{await api('/api/memory-pipeline-routing',{room:selected,enabled:!policy.routing_enabled,revision:policy.routing_revision});if(room?.id===selected)await refreshMemoryPipeline();}
  catch(error){if(room?.id===selected)$("pipeline-status").textContent=error.message+' 履歴を更新して確認してください。';report(error);}
  finally{busy=false;controls();}
});

let workingAutoPolicy=null;
let retentionState=null;
function retentionControls(){
  for(const id of ["retention-enabled","retention-days","retention-save","retention-refresh"])$(id).disabled=busy||editorBusy||slotsBusy||!retentionState;
}
function renderRetention(data){
  retentionState=data;$("retention-enabled").checked=data.policy.enabled;$("retention-days").value=data.policy.days;
  $("retention-report").textContent=(data.warning?data.warning+"\n":"")+data.entries.map(e=>e.slot+": "+(e.eligible?"休眠対象":"保護/対象外")+" · "+e.reason).join("\n")+"\n"+(data.history.length?"直近の確認: "+data.history[0].evaluated_at+" · 休眠 "+data.history[0].changed.map(e=>e.slot).join(", "):"自動確認の履歴はありません");retentionControls();
}
function slotsDraftDirty(){return !!($("slots-new-name").value||$("slots-new-text").value);}
function slotsControls(){
  retentionControls();
  const blocked=busy||editorBusy||slotsBusy;
  $("slots-auto").disabled=blocked||!workingAutoPolicy;
  $("slots-semantic").disabled=blocked||!workingAutoPolicy;
  $("slots-semantic").textContent="言い換えによる選択: "+(workingAutoPolicy?.semantic?"オン":"オフ");
  $("slots-semantic").setAttribute("aria-pressed",String(!!workingAutoPolicy?.semantic));
  $("slots-auto").textContent='話題の自動選択: '+(workingAutoPolicy?.enabled?'オン':'オフ');
  $("slots-auto").setAttribute('aria-pressed',String(!!workingAutoPolicy?.enabled));
  $("slots-refresh").disabled=blocked||!room;
  $("slots-list").disabled=blocked||!slotsState;
  $("slots-select").disabled=blocked||!slotsState||!slotsPreview?.exists||slotsPreview.slot===slotsState.active||slotsPreview.state==='dormant';
  $("slots-sleep").disabled=blocked||!slotsPreview?.exists||slotsPreview.state==='dormant'||slotsPreview.is_selected;
  $("slots-wake").disabled=blocked||!slotsPreview?.exists||slotsPreview.state!=='dormant';
  $("slots-state-history").disabled=blocked||!slotsPreview;
  $("slots-create").disabled=blocked||!slotsState||!$("slots-new-name").value;
  $("slots-new-name").disabled=blocked;$("slots-new-text").readOnly=blocked;$("slots-clear").disabled=blocked;
}
function resetSlots(id){
  workingAutoPolicy=null;retentionState=null;$("retention-report").textContent="";$("retention-enabled").checked=false;$("retention-days").value=30;
  slotsRoom=id;slotsListRequest++;slotsRequest++;slotsState=null;slotsPreview=null;slotsBusy=false;
  $("slots-list").replaceChildren(new Option("一覧を読み込んでください",""));
  $("slots-preview").textContent="";$("slots-status").textContent="";$("slots-active").textContent="一覧を読み込むと選択中のスロットを確認できます。";
  $("slots-state-label").textContent='';$("slots-state-events").textContent='';workingStateOperation=null;
  $("slots-new-name").value="";$("slots-new-text").value="";
}
async function previewSlot(){
  workingStateOperation=null;$("slots-state-events").textContent='';$("slots-state-label").textContent='';
  const selected=room?.id,slot=$("slots-list").value,request=++slotsRequest;slotsPreview=null;slotsControls();
  if(!selected||!slot)return;
  try{const [doc,usage]=await Promise.all([api("/api/working-slot?room="+encodeURIComponent(selected)+"&slot="+encodeURIComponent(slot)),api("/api/working-usage?"+new URLSearchParams({room:selected,slot}))]);if(room.id!==selected||request!==slotsRequest)return;slotsPreview=doc;$("slots-preview").textContent=doc.path+"\n"+(doc.exists?doc.text||"（本文は空です）":"（未配置です）");$("slots-state-label").textContent=(doc.state==='dormant'?'休眠中（復帰してから切り替えてください）':'使用可能')+(doc.is_selected?' · 選択中':'')+(doc.state_source_changed?' · 状態変更後に本文が変更されています':'')+(usage.last_use?" · 最終送信試行: "+new Date(usage.last_use.last_used_at).toLocaleString("ja-JP")+(usage.source_changed?"（以後に本文変更あり）":""):" · 送信試行記録なし");}
  catch(error){if(room.id===selected&&request===slotsRequest){$("slots-preview").textContent=error.message;report(error);}}
  finally{slotsControls();}
}
async function applySlotsState(data,preferred){
  slotsState=data;slotsPreview=null;
  $("slots-active").textContent="選択中: "+data.active+" · "+data.active_path;
  $("slots-list").replaceChildren();
  for(const slot of data.slots)$("slots-list").append(new Option(slot.name+(slot.state==='dormant'?'（休眠中）':'')+(slot.exists?"":"（未配置）"),slot.name));
  $("slots-list").value=preferred&&data.slots.some(s=>s.name===preferred)?preferred:data.active;
  $("slots-status").textContent=data.warnings.join("\n");
  await previewSlot();slotsControls();
}
async function refreshSlots(){
  if(!room||slotsBusy)return;const selected=room.id,request=++slotsListRequest;
  try{const [data,policy,retention]=await Promise.all([api("/api/working-slots?room="+encodeURIComponent(selected)),api('/api/working-selection?'+new URLSearchParams({room:selected})),api("/api/working-retention?"+new URLSearchParams({room:selected}))]);if(room.id===selected&&request===slotsListRequest){workingAutoPolicy=policy;renderRetention(retention);await applySlotsState(data,$("slots-list").value);}}
  catch(error){if(room?.id===selected&&request===slotsListRequest){slotsState=null;slotsPreview=null;$("slots-status").textContent=error.message;report(error);slotsControls();}}
}
async function workingDisplay(){
  const selected=room.id,character=await api("/api/character?room="+encodeURIComponent(selected));if(room.id!==selected)return;
  room.memory_sources.working=character.memory_sources.working;
  await refreshContext();
}
async function mutateSlot(create){
  if(!slotsState||slotsBusy||editorBusy||busy)return;
  if(editorDirty()){$("slots-status").textContent="未保存の本文があります。保存するか破棄してからスロットを操作してください。";return;}
  const selected=room.id,payload={room:selected,config_revision:slotsState.config_revision};
  if(create){payload.slot=$("slots-new-name").value;payload.text=$("slots-new-text").value;}
  else{if(!slotsPreview)return;payload.slot=slotsPreview.slot;payload.slot_revision=slotsPreview.revision;}
  slotsBusy=true;slotsListRequest++;controls();let reloadWorking=false;
  try{
    const result=await api(create?"/api/working-slot-create":"/api/working-slot-select",payload);
    if(room.id!==selected)return;
    if(create){$("slots-new-name").value="";$("slots-new-text").value="";}
    await applySlotsState(result.state,payload.slot);
    $("slots-status").textContent=create?"スロットを作成しました。選択設定は変更していません。":"選択先を切り替えました。次回の会話からこのスロットだけを使います。";
    reloadWorking=editorDocument?.key==="working";
    try{await workingDisplay();}catch(error){report(new Error("操作は完了しましたが、表示更新に失敗しました: "+error.message));}
  }catch(error){slotsPreview=null;$("slots-status").textContent=error.message+" 一覧を更新して確認してください。";report(error);}
  finally{slotsBusy=false;controls();if(reloadWorking)await loadEditor(true);}
}
$("slots-panel").addEventListener("toggle",()=>{if(room&&!$("slots-panel").hidden&&$("slots-panel").open&&!slotsState)refreshSlots().catch(report);});
$("slots-refresh").addEventListener("click",()=>refreshSlots().catch(report));
async function saveWorkingSelection(semantic=false){
  if(!room||slotsBusy||busy||!workingAutoPolicy)return;
  const selected=room.id;slotsBusy=true;slotsControls();
  try{const result=await api('/api/working-selection',{room:selected,enabled:semantic?workingAutoPolicy.enabled:!workingAutoPolicy.enabled,semantic:semantic?!workingAutoPolicy.semantic:workingAutoPolicy.semantic,revision:workingAutoPolicy.revision});if(room?.id===selected){workingAutoPolicy=result;$("slots-status").textContent='選択設定を保存しました。次の会話・自律処理から適用します。';}}
  catch(error){if(room?.id===selected)$("slots-status").textContent=error.message+' 一覧を更新して確認してください。';report(error);}
  finally{slotsBusy=false;slotsControls();}
}
$("slots-auto").addEventListener("click",()=>saveWorkingSelection());
$("slots-semantic").addEventListener("click",()=>saveWorkingSelection(true));
$("retention-refresh").addEventListener("click",()=>{$("retention-details").open=true;refreshSlots().catch(report);});
$("retention-save").addEventListener("click",async()=>{
  if(!room||!retentionState||busy||editorBusy||slotsBusy)return;
  const selected=room.id,payload={room:selected,enabled:$("retention-enabled").checked,days:Number($("retention-days").value),revision:retentionState.policy.revision};
  slotsBusy=true;slotsControls();
  try{await api("/api/working-retention",payload);const data=await api("/api/working-retention?"+new URLSearchParams({room:selected}));if(room?.id===selected){renderRetention(data);$("slots-status").textContent="自動休眠の設定を保存しました。設定した日数の猶予後から対象になります。";}}
  catch(error){if(room?.id===selected)$("slots-status").textContent=error.message+" 一覧を更新して確認してください。";report(error);}
  finally{slotsBusy=false;slotsControls();}
});
$("slots-list").addEventListener("change",()=>previewSlot().catch(report));
$("slots-select").addEventListener("click",()=>mutateSlot(false).catch(report));
async function changeWorkingState(state){
  if(!slotsPreview||busy||editorBusy||slotsBusy)return;
  const selected=room.id,doc=slotsPreview;
  const data={room:selected,slot:doc.slot,state,revision:doc.state_revision},signature=JSON.stringify(data);
  if(workingStateOperation?.signature!==signature)workingStateOperation={signature,id:crypto.randomUUID()};
  slotsBusy=true;controls();
  try{await api('/api/working-state',{...data,request_id:workingStateOperation.id});const latest=await api('/api/working-slots?'+new URLSearchParams({room:selected}));if(room?.id===selected)await applySlotsState(latest,doc.slot);}
  catch(e){report(e);}finally{slotsBusy=false;controls();}
}
$("slots-sleep").addEventListener('click',()=>changeWorkingState('dormant'));
$("slots-wake").addEventListener('click',()=>changeWorkingState('active'));
$("slots-state-history").addEventListener('click',async()=>{
  if(!slotsPreview)return;const doc=slotsPreview,selected=room.id;
  try{const history=await api('/api/working-state-history?'+new URLSearchParams({room:selected,slot:doc.slot}));
    if(room?.id!==selected||slotsPreview!==doc)return;
    $("slots-state-events").textContent=history.entries.map(e=>e.created+' UTC / '+e.previous+' → '+e.current.state).join('\n')||'状態の変更履歴はありません。';
  }catch(e){report(e);}
});
$("slots-create").addEventListener("click",()=>mutateSlot(true).catch(report));
$("slots-new-name").addEventListener("input",slotsControls);
$("slots-clear").addEventListener("click",()=>{$("slots-new-name").value="";$("slots-new-text").value="";slotsControls();});

let resourceDoc=null, resourceDraft=null, resourceDrafts=[], resourceRequest=0;
let mergeDoc=null,mergeRoom=null,mergeRequest=0,mergeOperation=null,mergeReviewOperation=null;
let mergeOldest=0,mergeMore=false;
function mergePhaseName(phase){return ({applied:'統合済み',undone:'原本へ復旧済み',prepared:'統合未完了',undo_prepared:'取り消し未完了',conflict:'要確認・復旧待ち'})[phase]||phase;}
function resetEntityMerges(selected){mergeRoom=selected;mergeDoc=null;mergeOperation=null;mergeOldest=0;mergeMore=false;mergeRequest++;$("entity-merges-select").replaceChildren(new Option('選択…',''));$("entity-merges-info").textContent='';for(const id of ['entity-merge-before','entity-merge-source','entity-merge-planned','entity-merge-current','entity-merge-current-source'])$(id).value='';}
function entityMergeControls(){
  const blocked=busy||editorBusy||slotsBusy||!room;
  $("entity-merges-refresh").disabled=blocked;$("entity-merges-select").disabled=blocked;
  $("entity-merges-older").disabled=blocked||!mergeMore;$("entity-merges-older").hidden=!mergeMore;
  const loaded=mergeDoc?.room===room?.id&&mergeDoc?.id===$("entity-merges-select").value;
  $("entity-merge-undo").disabled=blocked||!loaded||!mergeDoc.can_undo;
  $("entity-merge-recover").disabled=blocked||!loaded||!mergeDoc.can_recover;
}
async function refreshEntityMerges(preferred=null){
  if(!room)return;const selected=room.id,request=++mergeRequest,old=$("entity-merges-select").value;
  const rows=await api('/api/entity-merges?'+resourceQuery({room:selected}));
  if(room?.id!==selected||request!==mergeRequest)return;
  $("entity-merges-select").replaceChildren(new Option('選択…',''));
  for(const row of rows)$("entity-merges-select").append(new Option(row.source+' → '+row.target+' · '+mergePhaseName(row.phase)+' · '+row.created+' UTC',row.id));
  const selectedKey=preferred||old;
  if(selectedKey){if(!rows.some(row=>row.id===selectedKey))$("entity-merges-select").append(new Option('選択中の履歴',selectedKey));$("entity-merges-select").value=selectedKey;}
  mergeMore=rows.length===50;mergeOldest=rows.at(-1)?.sequence||0;
  await loadEntityMerge();
  entityMergeControls();
}
async function loadEntityMerge(){
  if(!room)return;const selected=room.id,key=$("entity-merges-select").value,request=++mergeRequest;
  mergeDoc=null;mergeOperation=null;entityMergeControls();if(!key){$("entity-merges-info").textContent='';for(const id of ['entity-merge-before','entity-merge-source','entity-merge-planned','entity-merge-current','entity-merge-current-source'])$(id).value='';return;}
  const doc=await api('/api/entity-merge?'+resourceQuery({room:selected,id:key}));
  if(room?.id!==selected||request!==mergeRequest||$("entity-merges-select").value!==key)return;
  mergeDoc={...doc,room:selected};
  $("entity-merges-select").selectedOptions[0].textContent=doc.source+' → '+doc.target+' · '+mergePhaseName(doc.phase)+' · '+doc.created+' UTC';
  $("entity-merge-before").value=doc.before_target;$("entity-merge-source").value=doc.before_source;$("entity-merge-planned").value=doc.planned_target;
  $("entity-merge-current").value=doc.current_target.text;$("entity-merge-current-source").value=doc.current_source.text;
  $("entity-merges-info").textContent=doc.source+' → '+doc.target+' · '+mergePhaseName(doc.phase)+' · '+doc.archive+(doc.archive_exists?'':'（ファイル未退避または削除済み。原本はDBにも保持）')+' '+doc.reason;
  entityMergeControls();
}
async function undoEntityMerge(recover){
  if(!mergeDoc||busy)return;
  if(resourceDirty()||updateReviewDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const doc=mergeDoc,signature=JSON.stringify([doc.room,doc.id,doc.revision,recover]);
  if(mergeOperation?.signature!==signature)mergeOperation={signature,room:doc.room,id:doc.id,revision:doc.revision,request_id:crypto.randomUUID()};
  busy=true;controls();let confirmed=null;
  try{
    const result=await api(recover?'/api/entity-recover':'/api/entity-undo',mergeOperation);
    confirmed=result;
    if(room?.id!==doc.room)return;
    await refreshEntityMerges(doc.id);await refreshContext();
    if(result.status==='ok'&&mergeReviewOperation?.room===doc.room&&mergeReviewOperation.draft_id===doc.draft_id)mergeReviewOperation=null;
    if(pendingReview?.room===doc.room){const fresh=await api('/api/update-draft?'+resourceQuery({room:doc.room,id:pendingReview.id}));updateReviewMetadata(fresh);}
    notice(result.status==='ok'?(result.replayed?'保存済みの操作結果を確認しました。現在の状態は履歴に表示しています。':'両方の原本へ戻しました。退避した原本と統合履歴は保持しています。'):'復旧が完了していません。履歴の本文と状態を確認してください。');
  }catch(error){if(confirmed?.status==='ok')notice('操作は保存済みです。表示更新に失敗したため、統合履歴を読み直してください。');else report(error);}finally{busy=false;controls();}
}
$("entity-merges-refresh").addEventListener('click',()=>refreshEntityMerges().catch(report));
$("entity-merges-older").addEventListener('click',async()=>{
  if(!room||!mergeMore)return;const selected=room.id,request=++mergeRequest;
  try{
    const rows=await api('/api/entity-merges?'+resourceQuery({room:selected,before:mergeOldest}));
    if(room?.id!==selected||request!==mergeRequest)return;
    const known=new Set(Array.from($("entity-merges-select").options,option=>option.value));
    for(const row of rows)if(!known.has(row.id))$("entity-merges-select").append(new Option(row.source+' → '+row.target+' · '+mergePhaseName(row.phase)+' · '+row.created+' UTC',row.id));
    mergeMore=rows.length===50;mergeOldest=rows.at(-1)?.sequence||0;entityMergeControls();
  }catch(error){report(error);}
});
$("entity-merges-select").addEventListener('change',()=>loadEntityMerge().catch(report));
$("entity-merge-undo").addEventListener('click',()=>undoEntityMerge(false));
$("entity-merge-recover").addEventListener('click',()=>undoEntityMerge(true));
document.querySelector('[data-panel="entity-merges-panel"]').addEventListener('panelactivate',()=>refreshEntityMerges().catch(report));
let entityStateOperation=null,entityHistoryRequest=0;
function entityStateName(value){return value==='dormant'?'休眠':value==='merged'?'統合済み':'有効';}
function resetEntityState(){entityStateOperation=null;entityHistoryRequest++;$("entity-state-history").replaceChildren();}
function entityStateControls(){
  const doc=resourceDoc,loaded=doc?.kind==='entity'&&doc.room===room?.id&&doc.exists;
  const blocked=busy||editorBusy||slotsBusy||!loaded;
  $("entity-sleep").disabled=blocked||doc?.state==='dormant';
  $("entity-wake").disabled=blocked||doc?.state!=='dormant';
  $("entity-history").disabled=blocked;
  $("entity-sleep").disabled||=!!doc?.merge_pending||doc?.state==='merged';$("entity-wake").disabled||=!!doc?.merge_pending||doc?.state==='merged';
  $("entity-state-label").textContent=doc?.merge_pending?'未完了の統合があります。統合履歴から復旧してください。':loaded?'状態: '+entityStateName(doc.state)+(doc.state==='dormant'?'（記憶検索の対象外）':'')+(doc.state_source_changed?' · 状態変更時から本文が更新されています。':''):'';
}
async function changeEntityState(next){
  if(!resourceDoc||busy)return;
  if(resourceDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const doc=resourceDoc,signature=JSON.stringify([doc.room,doc.name,next,doc.state_revision]);
  if(entityStateOperation?.signature!==signature)entityStateOperation={signature,room:doc.room,name:doc.name,state:next,revision:doc.state_revision,request_id:crypto.randomUUID()};
  busy=true;controls();
  try{
    const result=await api('/api/entity-state',entityStateOperation);
    if(resourceDoc!==doc||room?.id!==doc.room)return;
    await resourceRead(true);await refreshContext();
    if(result.replayed||resourceDoc.state!==next){notice('保存済みの操作結果を確認しました。現在の状態は'+(resourceDoc.state==='dormant'?'休眠中です。':'有効です。'));}
    else notice(next==='dormant'?'休眠にしました。本文は残し、このファイルを記憶検索から外しました。':'復帰しました。既存の意味検索用索引があればそのまま使えます。未索引の本文は索引更新が必要です。');
  }catch(error){report(error);}finally{busy=false;controls();}
}
$("entity-sleep").addEventListener('click',()=>changeEntityState('dormant'));
$("entity-wake").addEventListener('click',()=>changeEntityState('active'));
$("entity-history").addEventListener('click',async()=>{
  if(!resourceDoc)return;const doc=resourceDoc,request=++entityHistoryRequest;
  try{
    const result=await api('/api/entity-state-history?'+resourceQuery({room:doc.room,name:doc.name}));
    if(request!==entityHistoryRequest||resourceDoc!==doc||room?.id!==doc.room)return;
    $("entity-state-history").replaceChildren();
    for(const entry of result.entries){const line=document.createElement('p');line.textContent=entry.created+' UTC · '+entityStateName(entry.previous)+' → '+entityStateName(entry.current.state)+(entry.changed?'':'（変更なし）');$("entity-state-history").append(line);}
    if(!result.entries.length)$("entity-state-history").textContent='状態変更の履歴はありません。';
  }catch(error){report(error);}
});
let entityPreview=null,entityRequestId=null;
function resetEntityPreview(){entityPreview=null;entityRequestId=null;$("entity-preview-body").replaceChildren();$("entity-status").textContent="";}
function entityControls(){
  entityStateControls();
  const blocked=busy||editorBusy||slotsBusy||!room;
  const loaded=resourceDoc?.kind==='entity'&&resourceDoc.room===room?.id&&resourceDoc.exists&&resourceDoc.editable;
  $("entity-find").disabled=blocked||!loaded;
  $("entity-method").disabled=blocked||!loaded;
  $("entity-source").disabled=blocked||!loaded;
  $("entity-preview").disabled=blocked||!loaded||!$("entity-source").value;
  $("entity-propose").disabled=blocked||!entityPreview||!loaded||entityPreview.room!==room?.id||entityPreview.target.name!==resourceDoc?.name||entityPreview.target.revision!==resourceDoc?.revision;
}
$("entity-source").addEventListener('change',()=>{resetEntityPreview();entityControls();});
$("entity-find").addEventListener('click',async()=>{
  if(!resourceDoc||busy)return;
  if(resourceDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const doc=resourceDoc;resetEntityPreview();busy=true;controls();
  try{
    const result=await api('/api/entity-candidates?'+resourceQuery({room:doc.room,name:doc.name,method:$("entity-method").value}));
    if(resourceDoc!==doc||room?.id!==doc.room)return;
    const semantic=result.basis==='indexed_centroid';
    const options=new Map(result.rows.map(row=>[row.name,new Option(row.name+' · '+Math.round(row.score*100)+'%（'+(semantic?'本文類似度':'名前')+'）',row.name)]));
    for(const option of $("resource-files").options)if(option.value&&option.value!==doc.name&&!options.has(option.value))options.set(option.value,new Option(option.textContent+(semantic?'（その他・手動選択）':''),option.value));
    $("entity-source").replaceChildren(new Option('選択…',''),...options.values());
    $("entity-candidates-info").textContent=`${result.scanned}件の${semantic?'本文':'名前'}を比較、休眠${result.skipped_dormant||0}件を除外、候補${result.total}件（上位20件まで）。${result.warning}`;
  }catch(error){report(error);}finally{busy=false;controls();}
});
$("entity-preview").addEventListener('click',async()=>{
  if(!resourceDoc||busy)return;
  if(resourceDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const doc=resourceDoc,source=$("entity-source").value;resetEntityPreview();busy=true;controls();
  try{
    const result=await api('/api/entity-preview?'+resourceQuery({room:doc.room,target:doc.name,source}));
    if(resourceDoc!==doc||room?.id!==doc.room)return;
    entityPreview={...result,room:doc.room,target:{...result.target,name:doc.name},source:{...result.source,name:source}};
    entityRequestId=crypto.randomUUID();
    for(const [key,title] of [['target','保存先'],['source','参照元（原本を保持）']]){
      const label=document.createElement('label'),body=document.createElement('textarea');
      label.textContent=title+': '+result[key].path+(result[key].state==='dormant'?'（休眠中）':'');body.rows=8;body.readOnly=true;body.value=result[key].text;label.append(body);$("entity-preview-body").append(label);
    }
    $("entity-status").textContent='内容が別人・別概念でないか確認してください。作成する案は両方の全文を並べたもので、AI更新案で編集・承認できます。';
  }catch(error){report(error);}finally{busy=false;controls();}
});
$("entity-propose").addEventListener('click',async()=>{
  if(!entityPreview||busy)return;
  if(resourceDirty()||updateReviewDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const preview=entityPreview;busy=true;controls();
  try{
    const text=preview.target.text+'\n\n---\n\n## 参照元: '+preview.source.name+'\n\n'+preview.source.text;
    const result=await api('/api/entity-propose',{room:preview.room,target:preview.target.name,source:preview.source.name,revision:preview.target.revision,source_revision:preview.source.revision,text,request_id:entityRequestId});
    if(room?.id!==preview.room)return;
    if(result.draft_status!=='pending'){notice('この整理案はすでに確認済みです。新しい案は作成していません。');return;}
    await updateReviewList();showWorkspace('memory');revealPanel('updates-panel');
    $("updates-list").value=String(result.draft_id);$("updates-list").dispatchEvent(new Event('change'));
    notice('確認待ちの整理案を作りました。参照元は保存後もそのまま残ります。');
  }catch(error){report(error);}finally{busy=false;controls();}
});
let entryState=null,entryDoc=null,entryRequest=0,entryLoading=false;
const entryKinds=new Set(['diary','episode','dream','creative','analysis']);
const dreamFields=['trigger_topic','insight','log_entry'];
function entryDirty(){return !!entryDoc&&(editorCanonical($("entry-text").value)!==editorCanonical(entryDoc.text)||(entryDoc.kind==='dream'&&dreamFields.some(key=>editorCanonical($("entry-"+key).value)!==editorCanonical(entryDoc.fields[key]))));}
function resourceDirty(){return (!!resourceDoc&&editorCanonical($("resource-text").value)!==editorCanonical(resourceDoc.text))||entryDirty();}
function resetResource(){resetEntityState();resetEntityPreview();$("entity-source").replaceChildren(new Option('選択…',''));$("entity-candidates-info").textContent='';resourceRequest++;entryRequest++;entryState=null;entryDoc=null;entryLoading=false;$("entry-text").value="";$("entry-path").textContent="";$("entry-list").replaceChildren(new Option("エントリを選択…",""));resourceDoc=null;resourceDraft=null;$("resource-text").value="";$("resource-text").readOnly=true;$("resource-path").textContent="";$("resource-drafts").replaceChildren(new Option("草案を選択…",""));}
function resourceControls(){
  $("core-reflect").hidden=$("resource-kind").value!=="core";
  $("core-reflect").disabled=busy||resourceDirty();
  entryControls();
  entityControls();
  entityMergeControls();
  const blocked=busy||editorBusy||slotsBusy;
  for(const id of ["resource-kind","resource-name","resource-files","resource-list","resource-load","resource-drafts","resource-drafts-refresh","resource-reset","resource-backups-refresh","resource-backups","resource-backup-load","resource-empty"])$(id).disabled=blocked||!room;
  for(const id of ["resource-save","resource-update","resource-compress"])$(id).disabled=blocked||!resourceDoc||resourceDoc.room!==room?.id||!resourceDoc.editable;
  $("resource-stop").disabled=!busy;$("resource-text").readOnly=blocked||!resourceDoc||!resourceDoc.editable;
}
function resourceParams(){return {room:room.id,kind:$("resource-kind").value,name:$("resource-name").value};}
function resourceQuery(p){return new URLSearchParams(p).toString();}
async function resourceRead(force=false){
  if(resourceDirty()&&!force)throw new Error("未保存の本文があります。保存または破棄してください。");
  const p=resourceParams(),request=++resourceRequest,doc=await api("/api/resource?"+resourceQuery(p));
  if(request!==resourceRequest||room.id!==p.room||$("resource-kind").value!==p.kind||$("resource-name").value!==p.name)return;
  resetEntityState();resetEntityPreview();resourceDoc={...doc,...p};resourceDraft=null;positionResource();$("resource-text").value=doc.text;$("resource-path").textContent=doc.path+(doc.state==='merged'?'（統合済みの退避元）':doc.exists?'':'（保存時に新規作成）');$("resource-status").textContent=doc.edit_error||"本文を確認してから保存してください。";resourceControls();
}
async function resourceDraftList(){
  const selected=room.id,rows=await api("/api/resource-drafts?room="+encodeURIComponent(selected));if(room.id!==selected)return;
  resourceDrafts=rows.filter(r=>!entryKinds.has(r.kind));$("resource-drafts").replaceChildren(new Option("草案を選択…",""));for(const r of resourceDrafts)$("resource-drafts").append(new Option(r.kind+" / "+r.name+" · "+r.created,r.id));
}
async function resourceList() {
  const p=resourceParams(),items=await api("/api/resources?"+resourceQuery(p));
  if(room.id!==p.room||$("resource-kind").value!==p.kind)return;
  $("resource-files").replaceChildren(new Option("選択…",""));
  for(const item of items)$("resource-files").append(new Option(["entity","knowledge"].includes(p.kind)?item.path.split(/[\\/]/).pop():item.path,item.name));
  await resourceDraftList();
}
$("resource-list").addEventListener("click",()=>resourceList().catch(report));
$("resource-files").addEventListener("change",()=>{$("resource-name").value=$("resource-files").value;});
$("resource-load").addEventListener("click",()=>resourceRead().catch(report));
$("resource-reset").addEventListener("click",()=>{resetResource();resourceControls();});
$("resource-drafts-refresh").addEventListener("click",()=>resourceDraftList().catch(report));
$("resource-drafts").addEventListener("change",async()=>{try{
  if(resourceDirty())throw new Error("未保存の本文があります。先に保存または破棄してください。");
  const draft=resourceDrafts.find(r=>r.id===Number($("resource-drafts").value));if(!draft)return;
  if(draft.kind==='entity'&&JSON.parse(draft.dependencies||'[]').some(dep=>dep.kind==='entity')){
    if(updateReviewDirty())throw new Error('更新案に未保存の修正があります。先に保存または読み込みを解除してください。');
    await updateReviewList();showWorkspace('memory');revealPanel('updates-panel');$("updates-list").value=String(draft.id);$("updates-list").dispatchEvent(new Event('change'));return;
  }
  $("resource-kind").value=draft.kind;$("resource-name").value=draft.name;showWorkspace(positionResource());revealPanel("resources-panel");await resourceRead();
  if(resourceDoc?.kind!==draft.kind||resourceDoc?.name!==draft.name)return;
  resourceDraft=draft;$("resource-text").value=draft.text;$("resource-status").textContent="AI草案です。確認・修正して保存してください。";
}catch(e){report(e);}});
async function resourceSave(){
  if(!resourceDoc||busy)return;const d=resourceDoc;busy=true;controls();
  try{const text=$("resource-text").value;await api(resourceDraft?"/api/resource-approve":"/api/resource-save",resourceDraft?{room:d.room,id:resourceDraft.id,text}:{...d,text});
    resourceDoc={...d,text};resourceDraft=null;notice("本文をバックアップ付きで保存しました。意味検索には索引更新が必要です。");
    $("resource-kind").value=d.kind;$("resource-name").value=d.name;await resourceRead(true);await resourceDraftList();await refreshContext();
  }catch(e){report(e);}finally{busy=false;controls();await selectRoom(d.room);}
}
$("resource-save").addEventListener("click",()=>resourceSave());
async function resourceGenerate(mode){
  if(!resourceDoc||busy)return;if(resourceDirty()){notice("未保存の本文があります。先に保存または破棄してください。");return;}
  const d=resourceDoc;busy=true;generating=true;controls();$("resource-status").textContent="草案を生成中…原本は変更しません。";
  try{const draft=await api("/api/resource-generate",{...d,mode});resourceDraft=draft;$("resource-text").value=draft.text;$("resource-status").textContent="草案を保存しました。内容を確認・修正してから保存してください。";await resourceDraftList();}
  catch(e){report(e);}finally{busy=false;generating=false;controls();}
}
let sleepCompressionLoading=false,sleepCompressionSequence=0;
function sleepControls(){
  const locked=busy||editorBusy||slotsBusy||lifecycleBusy;
  const cross=$("sleep-kind").value==="crossfile";$("sleep-crossfile-sources-label").hidden=!cross;$("sleep-crossfile-target-label").hidden=!cross;$("sleep-compression-file").closest("label").hidden=cross;$("sleep-crossfile-sources").disabled=locked;$("sleep-crossfile-target").disabled=locked;
  $("sleep-compression-options").hidden=!['compression','crossfile'].includes($("sleep-kind").value);$("sleep-compression-mode").disabled=locked;$("sleep-compression-file").disabled=locked;$("sleep-compression-week").disabled=locked;
  $("sleep-kind").disabled=locked;$("sleep-preview").disabled=locked||!room||sleepCompressionLoading;
  $("sleep-generate").disabled=locked||!localReady||!sleepSource||sleepSource.room!==room?.id||sleepSource.kind!==$("sleep-kind").value||sleepCompressionLoading;
  $("sleep-stop").disabled=!generating;$("sleep-history").disabled=locked||!room;
  $("sleep-index").disabled=locked||!sleepIndex||sleepIndex.room!==room?.id||!sleepIndex.approved||sleepIndex.progress.state==='building';
  $("sleep-index-stop").disabled=locked||!sleepIndex||sleepIndex.room!==room?.id||sleepIndex.progress.state!=='building'||sleepIndex.progress.room!==room?.id;
}
async function sleepHistory(){
  if(!room)return;const selected=room.id;
  const rows=await api('/api/sleep-reviews?'+new URLSearchParams({room:selected}));
  if(room?.id!==selected)return;
  const kinds={identity:'永続記憶',notepad:'短期メモ',dream:'夢日記',episode:'エピソード',episode_compression:'エピソード圧縮'};
  $("sleep-history-text").textContent=rows.map(r=>`${r.created} ${kinds[r.kind]}${r.week?' / '+(r.mode==='monthly'?'月次':'週次')+' / '+r.name+' / '+r.week:''}: ${r.state}${r.draft_id?` / 案 #${r.draft_id} ${r.draft_status}`:''}${r.error?' / '+r.error:''}`).join('\n')||'実行履歴はありません（最新50件まで）。';
  const coverage=await api('/api/sleep-index?'+new URLSearchParams({room:selected}));
  if(room?.id!==selected)return;sleepIndex={...coverage,room:selected};
  const labels={covered:'反映済み',needs_update:'更新が必要',unavailable:'検索モデル未準備',unreadable:'検索本文を確認できません',incomplete:'一部確認（警告あり）'};
  $("sleep-index-status").textContent=`索引処理: ${coverage.progress.state}${coverage.progress.error?' / '+coverage.progress.error:''}\n`+
    (coverage.documents.map(d=>`${d.path}: ${labels[d.state]} (${d.cached}/${d.chunks})`).join('\n')||'確認保存した整理案はまだありません。')+
    (coverage.warnings.length?'\n注意: '+coverage.warnings.join(' / '):'');controls();
}
$("sleep-index").addEventListener('click',async()=>{
  if(!room||busy||editorBusy||slotsBusy)return;
  const selected=room.id;busy=true;controls();
  try{semanticState(await api('/api/sleep-index',{room:selected}));await sleepHistory();notice('索引更新を受け付けました。実行履歴・索引状態を更新して結果を確認してください。');}
  catch(e){report(e);}finally{busy=false;controls();}
});
$("sleep-index-stop").addEventListener('click',async()=>{
  try{semanticState(await api('/api/embedding-stop',{}));await sleepHistory();}catch(e){report(e);}
});
$("sleep-kind").addEventListener('change',()=>{sleepSource=null;sleepRequest=null;$("sleep-source").value='';$("sleep-status").textContent='資料を確認してください。';controls();});
async function sleepCompressionFiles(){
  if(!room||!["compression","crossfile"].includes($("sleep-kind").value))return;
  const selected=room.id,previous=$("sleep-compression-file").value,sequence=++sleepCompressionSequence;
  sleepCompressionLoading=true;controls();$("sleep-compression-file").replaceChildren(new Option('読み込み中…',''));
  try{
    const files=await api('/api/episode-compression-files?room='+encodeURIComponent(selected));
    if(room?.id!==selected||!["compression","crossfile"].includes($("sleep-kind").value)||sequence!==sleepCompressionSequence)return;
    $("sleep-compression-file").replaceChildren(...files.map(f=>new Option(f.name,f.name)));
    const chosen=new Set(Array.from($("sleep-crossfile-sources").selectedOptions,o=>o.value));$("sleep-crossfile-sources").replaceChildren(...files.map(f=>new Option(f.name,f.name,false,chosen.has(f.name))));
    if(files.some(f=>f.name===previous))$("sleep-compression-file").value=previous;
    if(!$("sleep-compression-week").value){const d=new Date();d.setDate(d.getDate()-((d.getDay()+6)%7)-7);$("sleep-compression-week").value=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');}
    if(sleepSource?.kind==='compression'&&(sleepSource.name!==$("sleep-compression-file").value||sleepSource.week!==$("sleep-compression-week").value||sleepSource.compression_mode!==$("sleep-compression-mode").value)){sleepSource=null;sleepRequest=null;$("sleep-source").value='';$("sleep-status").textContent='資料を確認してください。';}
    if(sleepSource?.kind==='crossfile'&&(JSON.stringify(sleepSource.names)!==JSON.stringify(Array.from($('sleep-crossfile-sources').selectedOptions,o=>o.value))||sleepSource.name!==$('sleep-crossfile-target').value||sleepSource.week!==$('sleep-compression-week').value||sleepSource.compression_mode!==$('sleep-compression-mode').value)){sleepSource=null;sleepRequest=null;$('sleep-source').value='';$('sleep-status').textContent='資料を確認してください。';}
  }catch(error){if(sequence===sleepCompressionSequence&&room?.id===selected){sleepSource=null;sleepRequest=null;$("sleep-source").value='';$("sleep-status").textContent='一覧を読み込めませんでした。もう一度資料を確認してください。';}throw error;}finally{if(sequence===sleepCompressionSequence){sleepCompressionLoading=false;controls();}}
}
$("sleep-kind").addEventListener('change',()=>sleepCompressionFiles().catch(report));
for(const id of ['sleep-compression-file','sleep-compression-week','sleep-compression-mode','sleep-crossfile-sources','sleep-crossfile-target'])$(id).addEventListener('change',()=>{sleepSource=null;sleepRequest=null;$("sleep-source").value='';$("sleep-status").textContent='資料を確認してください。';controls();});
$("sleep-preview").addEventListener('click',async()=>{
  if(!room||busy||editorBusy||slotsBusy)return;
  const selected=room.id,kind=$("sleep-kind").value;sleepSource=null;sleepRequest=null;busy=true;controls();
  try{const source=await api('/api/sleep-preview?'+new URLSearchParams({room:selected,kind,name:kind==='crossfile'?$("sleep-crossfile-target").value:$("sleep-compression-file").value,names:JSON.stringify(Array.from($("sleep-crossfile-sources").selectedOptions,o=>o.value)),week:$("sleep-compression-week").value,compression_mode:$("sleep-compression-mode").value}));
    if(room?.id!==selected)return;sleepSource=source;sleepRequest=crypto.randomUUID();
    $("sleep-source").value=source.source;$("sleep-status").textContent=source.warning;
  }catch(e){$("sleep-source").value='';report(e);}finally{busy=false;controls();}
});
$("sleep-generate").addEventListener('click',async()=>{
  if(!sleepSource||busy||editorBusy||slotsBusy||sleepSource.room!==room?.id)return;
  if(resourceDirty()||updateReviewDirty()){notice('未保存の本文があります。先に保存または破棄してください。');return;}
  const source=sleepSource;busy=true;generating=true;controls();$("sleep-status").textContent='整理案を生成中…120秒まで。原本は変更しません。';
  try{const draft=await api('/api/sleep-generate',{...source,request_id:sleepRequest});
    $("sleep-status").textContent=draft.status==='pending'?`確認待ちの案 #${draft.id} を作成しました。「AI更新案」で確認してください。`:`同じ操作の案 #${draft.id} は ${draft.status} です。新しい生成は「資料を確認」から始めてください。`;
    await updateReviewList();await sleepHistory();
  }catch(e){$("sleep-status").textContent='完了未確認です。履歴を更新してください。同じ操作の再送では再生成しません。新しい試行は「資料を確認」から。';report(e);}
  finally{busy=false;generating=false;controls();}
});
$("sleep-stop").addEventListener('click',()=>api(['compression','crossfile'].includes(sleepSource?.kind)?'/api/episode-compression-cancel':'/api/cancel',['compression','crossfile'].includes(sleepSource?.kind)?{room:sleepSource.room,request_id:sleepRequest}:{}).catch(report));
$("sleep-history").addEventListener('click',()=>sleepHistory().catch(report));
document.querySelector('[data-panel="sleep-panel"]').addEventListener('panelactivate',()=>{
  if(sleepSource?.room!==room?.id){sleepSource=null;sleepRequest=null;$("sleep-source").value='';$("sleep-status").textContent='資料を確認してください。';}
  sleepHistory().catch(report);sleepCompressionFiles().catch(report);controls();
});
$("resource-update").addEventListener("click",()=>resourceGenerate("update"));
$("resource-compress").addEventListener("click",()=>resourceGenerate("compress"));
$("resource-stop").addEventListener("click",()=>api("/api/cancel",{}).catch(report));

let cacheRevision=null;
$("embedding-cache-check").addEventListener("click",async()=>{try{const r=await api("/api/embedding-cache");cacheRevision=r.revision;$("embedding-cache-status").textContent=`全${r.total}件・参照中${r.reusable}件・未使用${r.orphan}件。原本は変更しません。`;$("embedding-cache-prune").disabled=!r.orphan;}catch(e){cacheRevision=null;$("embedding-cache-prune").disabled=true;report(e);}});
$("embedding-cache-prune").addEventListener("click",async()=>{try{const r=await api("/api/embedding-prune",{revision:cacheRevision});$("embedding-cache-status").textContent=`未使用キャッシュ${r.removed}件を整理しました。`;cacheRevision=null;$("embedding-cache-prune").disabled=true;}catch(e){report(e);}});

function toolEventText(row){
  const status=row.status?" ["+row.status+"]":" [旧形式]";
  const elapsed=typeof row.duration_ms==="number"?" · "+row.duration_ms.toFixed(1)+"ms":"";
  let body=row.result;try{body=JSON.stringify(JSON.parse(body),null,2);}catch(_){}
  const raw=row.started||(row.created?row.created.replace(" ","T")+"Z":"");
  const date=new Date(raw),when=Number.isNaN(date.getTime())?raw:date.toLocaleString();
  return when+" · "+row.name+status+elapsed+"\n"+body;
}
$("tool-events-refresh").addEventListener("click",async()=>{try{const selected=room.id,rows=await api("/api/tool-events?room="+encodeURIComponent(selected));if(room.id===selected)$("tool-events").textContent=rows.map(toolEventText).join("\n\n")||"実行履歴はありません。";}catch(e){report(e);}});

let chatLogState=null,chatLogRequest=0,chatLogLoading=false;
function chatLogControls(){
  for(const id of ["chat-log-load","chat-log-older","chat-log-search-button","chat-log-restore","chat-log-revisions","chat-log-query"])$(id).disabled=chatLogLoading||busy||editorBusy||slotsBusy||!!conversationEdit||!room;
  $("chat-log-older").disabled ||= !chatLogState?.next_before;
  for(const button of $("chat-log-matches").querySelectorAll("button"))button.disabled=chatLogLoading||busy||!!conversationEdit;
}
async function chatLogLoad(before=0,focus=0){
  if(conversationEdit){notice("会話の編集を確定またはキャンセルしてください。");return;}
  const selected=room.id,request=++chatLogRequest,q=$("chat-log-query").value;
  chatLogLoading=true;controls();
  try{
    const r=await api("/api/chat-log?"+new URLSearchParams({room:selected,q,before,focus}));
    if(room.id!==selected||request!==chatLogRequest)return;
    chatLogState={...r,room:selected};$("chat-log-messages").replaceChildren();$("chat-log-matches").replaceChildren();
    for(const m of r.messages){
      const body=message(m.role,m.content,m.status,m.images||[],m.id,$("chat-log-messages"),true,m),article=body.closest("article");
      if(q&&m.content.toLocaleLowerCase().includes(q.toLocaleLowerCase()))article.classList.add("search-match");
      if(m.id===r.focus_id)article.classList.add("search-focus");
    }
    if(!r.messages.length){const empty=document.createElement("p");empty.className="muted small";empty.textContent=q?"一致する発言はありません。":"会話ログはまだありません。";$("chat-log-messages").append(empty);}
    for(const m of r.matches||[]){
      const button=document.createElement("button");button.type="button";button.className="chat-log-match";button.textContent=m.created+" · "+(m.role==="user"?(m.speaker||"YOU"):m.role==="assistant"?room.name:"SYSTEM")+" · "+m.content;
      button.addEventListener("click",()=>chatLogLoad(0,m.id).catch(report));$("chat-log-matches").append(button);
    }
    $("chat-log-revisions").replaceChildren(new Option("復元対象を選択…",""));
    for(const v of r.revisions)if(!v.restored)$("chat-log-revisions").append(new Option("#"+v.id+" "+v.created+" "+v.action,v.id));
    $("chat-log-status").textContent=(q?"検索結果 "+r.matches.length+"件（最大100件）。":"")+r.messages.length+"発言を時系列表示。"+(r.focus_id?"対象の前後を表示しています。":"")+" 日時はUTC。";
    controls();
    if(r.focus_id)$("chat-log-messages").querySelector(`[data-message-id="${r.focus_id}"]`)?.scrollIntoView({block:"center"});
  }finally{if(request===chatLogRequest){chatLogLoading=false;controls();}}
}
$("chat-log-search").addEventListener("submit",event=>{event.preventDefault();chatLogLoad().catch(report);});
$("chat-log-load").addEventListener("click",()=>{$("chat-log-query").value="";chatLogLoad().catch(report);});
$("chat-log-older").addEventListener("click",()=>{if(chatLogState?.next_before)chatLogLoad(chatLogState.next_before).catch(report);});
$("chat-log-restore").addEventListener("click",async()=>{
  if(busy||conversationEdit||!chatLogState||chatLogState.room!==room.id)return;
  const id=Number($("chat-log-revisions").value);if(!id){notice("復元する変更履歴を選んでください。");return;}
  busy=true;controls();
  try{await api("/api/chat-restore",{room:room.id,id,revision:chatLogState.revision});await refreshConversation();await chatLogLoad();notice("変更前の会話を復元しました。");}
  catch(error){report(error);}finally{busy=false;controls();}
});

$("resource-backups-refresh").addEventListener("click",async()=>{if(!resourceDoc)return;const d=resourceDoc;try{const rows=await api("/api/resource-backups?"+resourceQuery({room:d.room,kind:d.kind,name:d.name}));if(resourceDoc!==d)return;$("resource-backups").replaceChildren(new Option("バックアップを選択…",""));for(const b of rows)$("resource-backups").append(new Option(b.created+(b.exists?"":"（未配置）"),b.id));}catch(e){report(e);}});
$("resource-backup-load").addEventListener("click",async()=>{if(!resourceDoc)return;if(resourceDirty()){notice("未保存の本文があります。先に保存または破棄してください。");return;}const d=resourceDoc,id=$("resource-backups").value;if(!id)return;try{const b=await api("/api/resource-backup?"+resourceQuery({room:d.room,kind:d.kind,name:d.name,id}));if(resourceDoc!==d)return;resourceDraft=null;$("resource-text").value=b.text;$("resource-status").textContent="バックアップ本文を読み込みました。内容を確認して保存してください。";}catch(e){report(e);}});
$("resource-empty").addEventListener("click",()=>{if(resourceDoc?.kind!=="knowledge"||busy){notice("知識資料を読み込んでください。");return;}resourceDraft=null;$("resource-text").value="";$("resource-status").textContent="本文を空にする候補です。保存すると元の全文はバックアップへ残ります。";});

let worldState=null,worldBaseline="",worldSelected="";
function worldForm(){return JSON.stringify([$("world-area").value,$("world-place").value,$("world-description").value]);}
function worldDirty(){return !!worldState&&worldForm()!==worldBaseline;}
function worldControls(){for(const id of ["current-location","world-load","world-places","world-move","world-area","world-place","world-description","world-save","world-delete-place","world-delete-area","world-discard","world-images","world-background"])$(id).disabled=busy||editorBusy||slotsBusy||!room;}
async function worldRefresh(form=true,previewAfterSave=null){
  const selected=room.id;if(worldState?.room!==selected)worldImagePreview();
  const r=await api("/api/world?room="+encodeURIComponent(selected));if(room.id!==selected)return;
  const previewChoice=$("world-images").value;
  const dirty=worldDirty(),previous=worldState;
  const reset=form||!previous||previous.room!==selected||(!dirty&&previous.world.revision!==r.world.revision);
  worldState={...r,room:selected};
  if(!reset&&dirty&&previous.world.revision!==r.world.revision){
    worldState.world=previous.world;worldState.places=previous.places;worldState.areas=previous.areas;worldState.place_backgrounds=previous.place_backgrounds;
    $("world-status").textContent="世界設定が更新されています。未保存の入力と元の版を保持しています。最新内容を確認してください。";
  }
  if(reset){$("world-places").replaceChildren(new Option("場所を選択…",""));for(let i=0;i<r.places.length;i++)$("world-places").append(new Option(r.places[i].area+" / "+r.places[i].name,i));$("world-area").value="";$("world-place").value="";$("world-description").value="";worldBaseline=worldForm();worldSelected="";$("world-images").replaceChildren(new Option("背景無し",""));for(const name of r.images)$("world-images").append(new Option(name.split("/").pop(),name));$("world-images").value="";}
  if(!reset&&JSON.stringify(previous?.images)!==JSON.stringify(r.images)){$("world-images").replaceChildren(new Option("背景無し",""));for(const name of r.images)$("world-images").append(new Option(name.split("/").pop(),name));}
  $("world-images").value=previewAfterSave!==null&&(previewAfterSave===""||r.images.includes(previewAfterSave))?previewAfterSave:!reset&&(previewChoice===""||r.images.includes(previewChoice))?previewChoice:"";
  renderCurrentLocation(r,selected);
}
function worldImagePreview(){
  const container=$("world-image-preview"),status=$("world-image-preview-status"),path=$("world-images").value,selected=room?.id;
  container.replaceChildren();container.hidden=false;status.textContent="";
  const placeholder=document.createElement("span");placeholder.className="world-preview-placeholder";placeholder.textContent="✦";placeholder.setAttribute("role","img");placeholder.setAttribute("aria-label","背景画像なし");container.append(placeholder);
  if(!path||worldState?.room!==selected||!worldState.images.includes(path))return;
  const img=document.createElement('img');img.alt='選択した背景：'+path.split('/').pop();img.hidden=true;container.append(img);container.hidden=false;status.textContent='画像を読み込み中…';
  const current=()=>img.isConnected&&room?.id===selected&&$("world-images").value===path;
  img.addEventListener('load',()=>{if(!current())return;img.hidden=false;placeholder.hidden=true;status.textContent='プレビューです。「背景画像を設定」で適用します。';});
  img.addEventListener('error',()=>{if(!current())return;img.hidden=true;placeholder.hidden=false;status.textContent='背景画像を読み込めませんでした。別の画像を選んでください。';});
  img.src='/api/world-image?'+new URLSearchParams({room:selected,path});
}
$("world-images").addEventListener('change',worldImagePreview);
function renderCurrentLocation(r,selected){
  const dropdown=$("current-location"),counts=new Map(),groups=new Map();
  for(const p of r.places)counts.set(p.name,(counts.get(p.name)||0)+1);
  dropdown.replaceChildren(new Option(counts.get(r.current_location)===1?"場所を選択…":r.current_location||"場所を選択…",""));
  for(const [index,p] of r.places.entries()){
    if(!groups.has(p.area)){const group=document.createElement("optgroup");group.label=p.area;groups.set(p.area,group);dropdown.append(group);}
    const option=new Option(p.name,String(index));option.disabled=counts.get(p.name)>1;
    groups.get(p.area).append(option);
    if(p.name===r.current_location&&!option.disabled)dropdown.value=String(index);
  }
  const note=$("location-note");note.hidden=r.source.path!=="cache/temporary_location.json";note.textContent="会話には一時的現在地が優先されます。";
  worldImagePreview();
  const img=$("world-background-image"),placeholder=$("location-image-placeholder");
  const source=r.background?"/api/world-image?"+new URLSearchParams({room:selected,path:r.background}):"";
  if(!source){img.hidden=true;placeholder.hidden=false;img.removeAttribute("src");}
  else if(img.getAttribute("src")!==source){img.hidden=true;placeholder.hidden=false;img.src=source;}
  else{const loaded=img.complete&&img.naturalWidth>0;img.hidden=!loaded;placeholder.hidden=loaded;}
}
$("world-background-image").addEventListener("load",()=>{const img=$("world-background-image");if(!img.getAttribute("src")||!img.naturalWidth)return;img.hidden=false;$("location-image-placeholder").hidden=true;});
$("world-background-image").addEventListener("error",()=>{const img=$("world-background-image");if(img.naturalWidth>0)return;img.hidden=true;$("location-image-placeholder").hidden=false;});
$("current-location").addEventListener("change",async()=>{
  if(!worldState||busy||editorBusy||slotsBusy||worldState.room!==room?.id)return;
  const place=worldState.places[Number($("current-location").value)];
  if($("current-location").value===""||!place){renderCurrentLocation(worldState,room.id);return;}
  busy=true;controls();const selected=room.id;
  try{
    const result=await api("/api/world-move",{room:selected,place:place.name,world_revision:worldState.world.revision,location_revision:worldState.location.revision});
    if(room.id!==selected)return;
    worldState={...result,room:selected};renderCurrentLocation(result,selected);await refreshContext();
    notice("現在地を変更しました。");
  }catch(e){renderCurrentLocation(worldState,selected);report(e);}finally{busy=false;controls();}
});
$("world-load").addEventListener("click",()=>{if(worldDirty()){notice("世界設定に未保存の編集があります。保存または破棄してください。");return;}worldRefresh().catch(report);});
$("world-discard").addEventListener("click",()=>worldRefresh().catch(report));
$("world-places").addEventListener("change",()=>{if(!worldState)return;if(worldDirty()){$("world-places").value=worldSelected;notice("未保存の場所の説明があります。保存または破棄してください。");return;}worldSelected=$("world-places").value;$("world-images").value=worldState.place_backgrounds?.[Number(worldSelected)]&&worldSelected!==""?worldState.place_backgrounds[Number(worldSelected)]:"";worldImagePreview();if(worldSelected==="")return;const p=worldState.places[Number($("world-places").value)];if(!p)return;$("world-area").value=p.area;$("world-place").value=p.name;$("world-description").value=p.description;worldBaseline=worldForm();});
async function worldAction(action){
  if(!worldState||worldState.room!==room.id||busy)return;busy=true;controls();const selected=room.id;
  try{let result;const chosenBackground=$("world-images").value;if(action==="move")result=await api("/api/world-move",{room:selected,place:$("world-place").value,world_revision:worldState.world.revision,location_revision:worldState.location.revision});
  else if(action==="background")result=await api("/api/world-background",{room:selected,path:$("world-images").value,revision:worldState.scene.revision});
  else result=await api("/api/world-update",{room:selected,revision:worldState.world.revision,area:$("world-area").value,place:$("world-place").value,description:$("world-description").value,action});
  worldState={...result,room:selected};worldBaseline=worldForm();await worldRefresh(true,action==="background"?chosenBackground:null);await refreshContext();$("world-status").textContent="保存しました。次回の会話に反映します。";
  }catch(e){report(e);}finally{busy=false;controls();}
}
for(const action of ["save","delete-place","delete-area","move","background"])$("world-"+action).addEventListener("click",()=>worldAction(action).catch(report));

let itemDraftDoc=null,itemDraftBusy=false,itemDraftBaseline="",itemDraftGenerateOp=null,itemDraftApproveOp=null,itemDraftRows=[];
const itemDraftFieldIds={name:'item-draft-name',category:'item-draft-category',description:'item-draft-description',taste:'item-draft-taste',image_prompt:'item-draft-image-prompt'};
function itemDraftFields(){return Object.fromEntries(Object.entries(itemDraftFieldIds).map(([key,id])=>[key,$(id).value]));}
function itemDraftForm(){return JSON.stringify([$("item-draft-request").value,$("item-draft-scope").value,$("item-draft-amount").value,itemDraftFields()]);}
function itemDraftDirty(){return itemDraftBaseline?itemDraftForm()!==itemDraftBaseline:!!($("item-draft-request").value||Object.values(itemDraftFields()).some(Boolean));}
function itemDraftControls(){
 const blocked=busy||itemDraftBusy||!room;for(const el of $("item-drafts-panel").querySelectorAll('button,input,textarea,select'))el.disabled=blocked;
 $("item-draft-cancel").disabled=!itemDraftBusy;$("item-draft-approve").disabled=blocked||itemDraftDoc?.room!==room?.id||itemDraftDoc?.state!=='ready';$("item-draft-image").disabled=blocked||!$("item-draft-image-prompt").value.trim();
}
function resetItemDraft(){itemDraftDoc=null;itemDraftBaseline="";itemDraftGenerateOp=null;itemDraftApproveOp=null;itemDraftRows=[];$("item-draft-request").value='';$("item-draft-amount").value='1';$("item-draft-scope").value='user';for(const id of Object.values(itemDraftFieldIds))$(id).value='';$("item-draft-original").textContent='';$("item-draft-status").textContent='';$("item-draft-image-replace").checked=false;$("item-draft-list").replaceChildren(new Option('選択…',''));}
function paintItemDraft(doc){itemDraftDoc=doc;itemDraftGenerateOp=null;itemDraftApproveOp=null;$("item-draft-request").value=doc.request;$("item-draft-scope").value=doc.scope;$("item-draft-amount").value=doc.amount;const f=doc.approved_fields||doc.fields||{};for(const [key,id] of Object.entries(itemDraftFieldIds))$(id).value=f[key]||'';$("item-draft-original").textContent=JSON.stringify(doc.fields||{},null,2);$("item-draft-status").textContent=doc.state==='ready'?'草案です。内容を確認・編集してから登録してください。':doc.state==='approved'?'所持品へ登録済みです。':doc.state+'：'+doc.error;itemDraftBaseline=itemDraftForm();itemDraftControls();}
async function loadItemDrafts(){if(!room)return;const selected=room.id,rows=await api('/api/item-drafts?room='+encodeURIComponent(selected));if(room?.id!==selected)return;itemDraftRows=rows;$("item-draft-list").replaceChildren(new Option('選択…',''));for(const row of rows)$("item-draft-list").append(new Option(row.created+' · '+row.title+' · '+row.state,row.id));}
$("item-drafts-panel").addEventListener('toggle',()=>{if($("item-drafts-panel").open)loadItemDrafts().catch(report);});
$("item-draft-refresh").addEventListener('click',()=>loadItemDrafts().catch(report));
$("item-draft-clear").addEventListener('click',()=>{resetItemDraft();itemDraftControls();loadItemDrafts().catch(report);});
$("item-draft-list").addEventListener('change',async()=>{if(itemDraftDirty()){$("item-draft-list").value=itemDraftDoc?.id||'';notice('編集中のアイテム案を登録または破棄してください。');return;}const key=$("item-draft-list").value,selected=room?.id;if(!key||!selected)return;try{const doc=await api('/api/item-draft?'+new URLSearchParams({room:selected,id:key}));if(room?.id===selected)paintItemDraft(doc);}catch(error){report(error);}});
$("item-draft-generate").addEventListener('click',async()=>{
 if(!room||busy||itemDraftBusy)return;if(itemDraftDoc&&itemDraftDirty()){notice('編集中のアイテム案を登録または破棄してください。');return;}if(lifeDirty()){notice('生活データの編集を保存または破棄してください。');return;}
 const selected=room.id;itemDraftBusy=true;busy=true;controls();
 try{const life=await api('/api/lifestyle?room='+encodeURIComponent(selected));const payload={room:selected,scope:$("item-draft-scope").value,request:$("item-draft-request").value,amount:Number($("item-draft-amount").value),revision:life.revision};const sig=JSON.stringify(payload);if(itemDraftGenerateOp?.signature!==sig)itemDraftGenerateOp={signature:sig,id:crypto.randomUUID()};
 const doc=await api('/api/item-draft-generate',{...payload,request_id:itemDraftGenerateOp.id});if(room?.id===selected){paintItemDraft(doc);await loadItemDrafts();$("item-draft-list").value=doc.id;}}
 catch(error){$("item-draft-status").textContent=error.message+' 入力は保持しています。';report(error);}
 finally{itemDraftBusy=false;busy=false;controls();}
});
$("item-draft-cancel").addEventListener('click',()=>api('/api/cancel',{}).catch(report));
$("item-draft-approve").addEventListener('click',async()=>{
 const doc=itemDraftDoc;if(!room||busy||itemDraftBusy||doc?.room!==room.id||doc?.state!=='ready')return;
 if($("item-draft-request").value!==doc.request||$("item-draft-scope").value!==doc.scope||Number($("item-draft-amount").value)!==doc.amount){notice('生成時の所持先・数量・依頼内容は変更できません。新しい案として作成してください。');return;}
 const payload={room:room.id,id:doc.id,revision:doc.revision,fields:itemDraftFields()},sig=JSON.stringify(payload);if(itemDraftApproveOp?.signature!==sig)itemDraftApproveOp={signature:sig,id:crypto.randomUUID()};itemDraftBusy=true;busy=true;controls();
 try{await api('/api/item-draft-approve',{...payload,request_id:itemDraftApproveOp.id});paintItemDraft(await api('/api/item-draft?'+new URLSearchParams({room:doc.room,id:doc.id})));if(!lifeDirty())await lifeLoad(true);await refreshContext();await loadItemDrafts();$("item-draft-status").textContent='所持品に登録しました。画像はまだ生成していません。';}
 catch(error){$("item-draft-status").textContent=error.message+' 入力は保持しています。';report(error);}
 finally{itemDraftBusy=false;busy=false;controls();}
});
$("item-draft-image").addEventListener('click',()=>{if(busy||imageBusy||itemDraftBusy)return;if($("image-prompt").value.trim()&&!$("item-draft-image-replace").checked){notice('入力済みの画像生成内容を置き換える確認にチェックしてください。');return;}$("image-prompt").value=$("item-draft-image-prompt").value;imageOperation=null;showWorkspace('world');showSubView('world','images-panel');imageControls();refreshImageJobs().catch(report);notice('画像生成欄へ反映しました。生成を予約するまで画像は作成しません。');});
for(const id of Object.values(itemDraftFieldIds))$(id).addEventListener('input',itemDraftControls);
let lifeState=null,lifeBaseline="",lifeScope="user",lifeSelected="",sharedClosetDoc=null,sharedClosetRequest=null;
const lifeFields=["life-name","life-category","life-amount","life-description","life-taste","life-appearance","life-note"];
function lifeForm(){return JSON.stringify(lifeFields.map(id=>$(id).value).concat($("life-enabled").checked));}
function lifeDirty(){return !!lifeState&&lifeForm()!==lifeBaseline;}
function lifeControls(){for(const element of $("lifestyle-panel").querySelectorAll("button,input,textarea,select"))element.disabled=busy||editorBusy||slotsBusy||!room;itemDraftControls();}
function paintLifeEditScope(){$("life-edit-scope").textContent=sharedClosetDoc?.enabled&&$("life-scope").value==='user'?'編集対象：全ルーム共通のユーザー衣装・外見。変更は共通を使う他のルームにも反映します。':'編集対象：このルームの衣装・外見。';}
function lifeRender(){
  if(!lifeState)return;const data=lifeState.data,scope=$("life-scope").value,location=$("life-location").value;
  lifeScope=scope;lifeSelected="";
  const items=scope==="placed"?data.placed.locations[location]||[]:data[scope].items;
  $("life-items").replaceChildren(new Option("新規作成",""));for(const i of items)$("life-items").append(new Option(i.name+" ×"+i.amount,i.id));
  lifeFields.forEach(id=>$(id).value="");$("life-category").value="その他";$("life-amount").value=1;$("life-enabled").checked=false;
  $("life-clothes").replaceChildren(new Option("衣装を選択…",""));$("life-outfit").textContent="";
  if(scope!=="placed"){
    const profile=data[scope+"_profile"],catalog=data[scope+"_closet"].items,worn=profile.current?.worn||[];
    $("life-appearance").value=profile.base?.description||"";$("life-note").value=profile.current?.note||"";$("life-enabled").checked=profile.enabled===true;
    for(const i of catalog)$("life-clothes").append(new Option((worn.includes(i.id)?"着用中 · ":"")+i.name,i.id));
    $("life-outfit").textContent="着用: "+catalog.filter(i=>worn.includes(i.id)).map(i=>i.name).join("、");
  }
  $("life-revisions").replaceChildren(new Option("復元対象を選択…",""));for(const r of lifeState.revisions)$("life-revisions").append(new Option(r.created+" · "+r.action,r.id));
  $("life-status").textContent=lifeState.overlay?"あかりのDBで管理中。NexusArk原本は保持しています。":"NexusArk原本の読取状態です。保存操作までDBへ取り込みません。";
  lifeBaseline=lifeForm();paintLifeEditScope();
}
async function lifeLoad(force=false){
  if(!room)return;
  if(lifeDirty()&&!force){notice("生活データに未保存の入力があります。保存または破棄してください。");return;}
  const selected=room.id;
  const [lifestyle,closet]=await Promise.all([
    api("/api/lifestyle?room="+encodeURIComponent(selected)),
    api("/api/shared-closet?room="+encodeURIComponent(selected))
  ]);
  if(room?.id!==selected||(!force&&lifeDirty()))return;
  lifeState=lifestyle;sharedClosetDoc=closet;sharedClosetRequest=null;lifeRender();sharedClosetRender();
}
function sharedClosetRender(){
  paintLifeEditScope();
  if(!sharedClosetDoc||sharedClosetDoc.room!==room?.id)return;
  $("shared-closet-status").textContent=(sharedClosetDoc.enabled?"このルームは共通設定を使用中。":"このルームは専用設定を使用中。")+(sharedClosetDoc.exists?"共通衣装: "+sharedClosetDoc.data.user_closet.items.length+"件。":"共通設定は未作成です。");
  $("shared-closet-history").replaceChildren(new Option("復元対象を選択…",""));for(const h of sharedClosetDoc.history)$("shared-closet-history").append(new Option(h.created+" · "+h.action,h.id));
  $("shared-closet-confirm").checked=false;
}
async function sharedClosetAction(action){
  if(busy||!room)return;
  if(lifeDirty()){notice("未保存の生活データを保存または破棄してください。");return;}
  if(!lifeState||!sharedClosetDoc){await lifeLoad();return;}
  if(["copy-from-room","restore"].includes(action)&&!$("shared-closet-confirm").checked){notice("共通設定を置き換える確認にチェックしてください。");return;}
  const payload={room:room.id,action,revision:sharedClosetDoc.revision,mode_revision:sharedClosetDoc.mode_revision,life_revision:lifeState.revision,version_id:action==="restore"?Number($("shared-closet-history").value):null};
  const signature=JSON.stringify(payload);if(sharedClosetRequest?.signature!==signature)sharedClosetRequest={signature,id:crypto.randomUUID()};
  busy=true;controls();try{await api("/api/shared-closet",{...payload,request_id:sharedClosetRequest.id});await lifeLoad(true);await refreshContext();}finally{busy=false;controls();}
}
for(const [id,action] of [["enable","enable"],["disable","disable"],["copy","copy-from-room"],["restore","restore"]])$("shared-closet-"+id).addEventListener("click",()=>sharedClosetAction(action).catch(report));
$("life-load").addEventListener("click",()=>lifeLoad().catch(report));
$("life-discard").addEventListener("click",()=>lifeLoad(true).catch(report));
$("life-list").addEventListener("click",()=>{if(lifeDirty()){notice("未保存の入力があります。保存または破棄してください。");return;}lifeRender();});
$("life-items").addEventListener("change",()=>{if(!lifeState)return;if(lifeDirty()){$("life-items").value=lifeSelected;notice("未保存の入力があります。保存または破棄してください。");return;}lifeSelected=$("life-items").value;const scope=$("life-scope").value,items=scope==="placed"?lifeState.data.placed.locations[$("life-location").value]||[]:lifeState.data[scope].items;const item=items.find(i=>i.id===$("life-items").value);if(!item)return;for(const key of ["name","category","amount","description","taste"])$("life-"+key).value=item[key]??"";lifeBaseline=lifeForm();});
async function lifeAction(action){
  if(!lifeState||lifeState.room!==room.id||busy)return;
  const values={scope:$("life-scope").value,location:$("life-location").value,id:$("life-items").value,owner:$("life-owner").value,closet_id:$("life-clothes").value,amount:Number($("life-amount").value),name:$("life-name").value,category:$("life-category").value,description:action==="appearance"?$("life-appearance").value:$("life-description").value,taste:$("life-taste").value,note:$("life-note").value,enabled:$("life-enabled").checked,revision_id:Number($("life-revisions").value)};
  busy=true;controls();try{lifeState=await api("/api/lifestyle-change",{room:room.id,revision:lifeState.revision,action,values});lifeRender();sharedClosetDoc=await api("/api/shared-closet?room="+encodeURIComponent(room.id));sharedClosetRender();await refreshContext();}catch(e){report(e);}finally{busy=false;controls();}
}
for(const action of ["create","edit","consume","transfer","place","pickup","duplicate","remove","closet-create","wear","take-off","closet-remove","restore","import-originals"])$("life-"+action).addEventListener("click",()=>lifeAction(action).catch(report));
$("life-appearance-save").addEventListener("click",()=>lifeAction("appearance").catch(report));

$("life-scope").addEventListener("change",()=>{if(lifeDirty()){$("life-scope").value=lifeScope;notice("未保存の入力があります。保存または破棄してください。");return;}lifeRender();});

// Named shortcuts reuse the existing editor instances, including their drafts.
function revealPanel(id) {
  const workspace=$(id).closest('.workspace-panel')?.id.replace('workspace-','');
  if(workspace&&$("workspace-"+workspace).querySelector('.workspace-shortcuts')) {
    if($("workspace-"+workspace).hidden)showWorkspace(workspace);
    showSubView(workspace,id);return;
  }
  const panel=$(id);
  for(let element=panel;element&&element!==document.body;element=element.parentElement)if(element.tagName==="DETAILS")element.open=true;
  panel.scrollIntoView({block:"start"});
}
function positionResource() {
  const kind=$("resource-kind").value;
  $("entity-organizer").hidden=kind!=="entity";
  $("entity-state-panel").hidden=kind!=="entity";
  const workspace=["world","location","temporary"].includes(kind)?"world":"memory";
  $(workspace+"-resources").append($("resources-panel"));
  $("resource-heading").textContent=(document.querySelector('[data-resource="'+kind+'"]')?.textContent||"本文")+"を編集";
  const fixed=["core","identity","notepad"].includes(kind);
  $("resource-file-picker").hidden=fixed;$("resource-name-field").hidden=fixed;$("resource-load").hidden=fixed;
  $("resource-mode-info").textContent=fixed?"この項目の決まったファイルを直接編集します。":"";
  return workspace;
}
function resourcePanel(){return entryKinds.has($("resource-kind").value)?'entry-panel':'resources-panel';}
async function openResource(kind) {
  if(!room||busy||editorBusy||slotsBusy){notice("読み込みや生成の完了を待ってください。");return;}
  if($("resource-kind").value!==kind){
    if(resourceDirty()){notice("本文に未保存の変更があります。保存または破棄してから別の項目を開いてください。");showWorkspace(positionResource());revealPanel(resourcePanel());return;}
    resetResource();$("resource-kind").value=kind;$("resource-name").value="";
    $("resource-files").replaceChildren(new Option("選択…",""));
  }
  showWorkspace(positionResource());revealPanel(resourcePanel());
  resourceControls();
  if(entryKinds.has(kind)){
    $("entry-heading").textContent=document.querySelector('[data-resource="'+kind+'"]').textContent+'のエントリ';
    if(!entryState||entryState.kind!==kind||entryState.room!==room.id)await refreshEntries();
    return;
  }
  if(["core","identity","notepad"].includes(kind)){
    if(!resourceDoc||resourceDoc.kind!==kind)await resourceRead();
    await resourceDraftList();
  }else await resourceList();
}
document.querySelectorAll("[data-panel]").forEach(button=>button.addEventListener("click",()=>revealPanel(button.dataset.panel)));
document.querySelectorAll("[data-editor]").forEach(button=>button.addEventListener("click",async()=>{
  if(busy||editorBusy||slotsBusy){notice("読み込みや生成の完了を待ってください。");return;}
  const key=button.dataset.editor;
  if($("editor-key").value!==key&&editorDirty()){notice("指示・話題に未保存の変更があります。保存または破棄してから切り替えてください。");revealPanel("document-editor");return;}
  const loaded=$("editor-key").value===key&&editorDocument;
  $("editor-key").value=key;revealPanel("document-editor");if(!loaded)await loadEditor();
}));
document.querySelectorAll("[data-resource]").forEach(button=>button.addEventListener("click",()=>openResource(button.dataset.resource).catch(report)));
$("open-search-settings").addEventListener("click",()=>{
  showWorkspace("settings");
  revealPanel("embedding-details");
  $("embedding-model").focus({preventScroll:true});
});

function entryControls(){
  const blocked=busy||editorBusy||slotsBusy||entryLoading||!room;
  for(const id of ['entry-year','entry-month','entry-list','entry-refresh','entry-reset'])$(id).disabled=blocked;
  $("entry-save").disabled=blocked||!entryDoc||!entryDoc.editable||!entryDirty();
  $("entry-text").readOnly=blocked||!entryDoc||!entryDoc.editable;
  for(const key of dreamFields)$("entry-"+key).readOnly=blocked||!entryDoc||!entryDoc.editable;
}
function entryMatches(item){
  const year=$("entry-year").value,month=$("entry-month").value;
  if(!year&&!month)return true;
  if(!item.date)return false;
  if(year){const first=year+'-'+(month||'01')+'-01',last=year+'-'+(month||'12')+'-31';return item.date<=last&&item.end_date>=first;}
  const a=Number(item.date.slice(5,7)),b=Number(item.end_date.slice(5,7)),m=Number(month);
  const years=Number(item.end_date.slice(0,4))-Number(item.date.slice(0,4));
  return years>1||(years===1?(m>=a||m<=b):(m>=a&&m<=b));
}
function renderEntryChoices(preferred){
  entryDoc=null;$("entry-text").value='';$("entry-path").textContent='';
  $("entry-dream-fields").hidden=entryState.kind!=='dream';$("entry-body-label").textContent=entryState.kind==='dream'?'今後の指針':'本文';
  for(const key of dreamFields)$("entry-"+key).value='';
  $("entry-list").replaceChildren(new Option('エントリを選択…',''));
  for(const [index,item] of entryState.entries.entries())if(entryMatches(item)){
    const day=item.date?(item.date===item.end_date?item.date:item.date+'～'+item.end_date):'日付なし';
    $("entry-list").append(new Option((entryState.kind==='dream'?item.timestamp:day)+' · '+item.preview.replace(/\s+/g,' ').slice(0,70)+' · #'+(Number(item.id)+1),String(index)));
    if(preferred&&item.name===preferred.name&&item.id===preferred.id)$("entry-list").value=String(index);
  }
  if(!$("entry-list").value&&$("entry-list").options.length>1)$("entry-list").selectedIndex=1;
  entryState.year=$("entry-year").value;entryState.month=$("entry-month").value;
  $("entry-status").textContent=($("entry-list").options.length-1)+'件。'+entryState.warnings.join(' / ');
  entryControls();
}
async function refreshEntries(preferred){
  if(entryDirty()){notice('エントリに未保存の変更があります。保存または破棄してください。');return;}
  const selected=room.id,kind=$("resource-kind").value,request=++entryRequest;
  entryLoading=true;entryControls();
  try{
    const result=await api('/api/resource-entries?'+resourceQuery({room:selected,kind}));
    if(request!==entryRequest||room.id!==selected||$("resource-kind").value!==kind)return;
    const previous=entryState?.kind===kind?entryState:null;
    entryState={...result,room:selected,kind};
    $("entry-warnings").textContent=result.warnings.join(' / ');$("entry-warnings").hidden=!result.warnings.length;
    const years=result.entries.flatMap(item=>[item.date,item.end_date]).filter(Boolean).map(day=>Number(day.slice(0,4)));
    $("entry-year").replaceChildren(new Option('すべての年',''));
    if(years.length)for(let year=Math.max(...years);year>=Math.min(...years);year--){const value=String(year).padStart(4,'0');$("entry-year").append(new Option(value,value));}
    $("entry-year").value=previous?.year||'';$("entry-month").value=previous?.month||'';
    renderEntryChoices(preferred);
  }finally{if(request===entryRequest){entryLoading=false;entryControls();}}
  if(request===entryRequest)await readEntry();
}
async function readEntry(){
  if(entryDirty())return;
  const selection=$("entry-list").value,item=entryState?.entries[Number(selection)];
  if(selection===''||!item){entryDoc=null;entryControls();return;}
  const p={room:entryState.room,kind:entryState.kind,name:item.name,id:item.id},request=++entryRequest;
  entryLoading=true;entryControls();
  try{
    const doc=await api('/api/resource-entry?'+resourceQuery(p));
    if(request!==entryRequest||room.id!==p.room||$("resource-kind").value!==p.kind)return;
    if(doc.revision!==item.revision)throw new Error('元ファイルが変わりました。一覧を更新してください。');
    entryDoc={...doc,selection};$("entry-text").value=doc.text;
    for(const key of dreamFields)$("entry-"+key).value=doc.fields?.[key]||'';
    $("entry-path").textContent=doc.path+' · '+(doc.date||'日付なし')+' · #'+(Number(doc.id)+1);
    $("entry-status").textContent='選択したエントリだけを編集します。'+(doc.editable?'':'改行が混在しているため参照のみです。');
  }finally{if(request===entryRequest){entryLoading=false;entryControls();}}
}
$("entry-refresh").addEventListener('click',()=>refreshEntries(entryDoc).catch(report));
$("entry-list").addEventListener('change',()=>{
  if(entryDirty()){$("entry-list").value=entryDoc.selection;notice('未保存のエントリがあります。保存または破棄してください。');return;}
  entryDoc=null;$("entry-text").value='';readEntry().catch(report);
});
for(const id of ['entry-year','entry-month'])$(id).addEventListener('change',()=>{
  if(entryDirty()){$("entry-year").value=entryState.year;$("entry-month").value=entryState.month;notice('未保存のエントリがあります。保存または破棄してください。');return;}
  renderEntryChoices();readEntry().catch(report);
});
for(let month=1;month<=12;month++)$("entry-month").append(new Option(month+'月',String(month).padStart(2,'0')));
$("entry-text").addEventListener('input',entryControls);
for(const key of dreamFields)$("entry-"+key).addEventListener('input',entryControls);
$("entry-reset").addEventListener('click',()=>{if(entryDoc){$("entry-text").value=entryDoc.text;for(const key of dreamFields)$("entry-"+key).value=entryDoc.fields?.[key]||'';}entryControls();});
$("entry-save").addEventListener('click',async()=>{
  if(!entryDoc||busy||entryLoading)return;
  const doc=entryDoc;busy=true;controls();notice();
  try{
    const fields=doc.kind==='dream'?Object.fromEntries(dreamFields.map(key=>[key,$("entry-"+key).value])):undefined;
    const saved=await api('/api/resource-entry-save',{...doc,text:$("entry-text").value,fields});
    entryDoc={...saved.entry,selection:doc.selection};$("entry-text").value=saved.entry.text;
    await refreshEntries(doc);await selectRoom(doc.room);
    notice('選択したエントリを保存しました。元ファイルはバックアップ済みです。意味検索には索引更新が必要です。');
  }catch(error){report(error);}finally{busy=false;controls();}
});

// Each category shows one subview; the same form nodes retain their draft state.
const subSelections={external:'external-x-panel','room-settings':'persona-contract-panel',shared:'shared-procedures-panel',rooms:"room-management-panel",autonomy:"autonomy-records",memory:"memory-search",world:"world-panel",settings:"settings-model-panel"};
function subTarget(button){return button.dataset.panel||(button.dataset.editor?"document-editor":entryKinds.has(button.dataset.resource)?"entry-panel":"resources-panel");}
function ensureSubView(workspace){
  const root=$("workspace-"+workspace);
  if(!root?.querySelector('.workspace-shortcuts'))return;
  const selected=subSelections[workspace];
  const element=$(selected==="closet-panel"?"lifestyle-panel":selected);
  const target=element&&root.contains(element)?selected:(subTarget(root.querySelector('.workspace-shortcuts button')));
  showSubView(workspace,target,false);
}
let autonomySettingsPending=false,autonomySettingsLoading=false;
async function loadAutonomySettings(){
  autonomySettingsPending=false;
  if(!room||$("autonomy-settings").hidden||$("workspace-autonomy").hidden)return;
  if(autonomySettingsLoading){autonomySettingsPending=true;return;}
  if(roomSwitchBlocked()){autonomySettingsPending=true;return;}
  const selected=room.id;
  autonomySettingsLoading=true;
  try{
    if(typeof motivationLoad==='function')await motivationLoad();
    if(room?.id===selected)await loadAutonomyDetails();
  }catch(error){report(error);}
  finally{
    autonomySettingsLoading=false;
    if(room?.id!==selected||autonomySettingsPending)syncRoomSelector();
  }
}
document.querySelector('[data-panel="autonomy-settings"]').addEventListener('panelactivate',loadAutonomySettings);
async function loadAutonomyDetails(){
  if(!state?.debug_mode||!room||busy)return;
  const selected=room.id;
  if(!affectConfig||affectRoom!==selected)await affectLoad();
  if(room?.id!==selected)return;
  if(!repetitionConfig||repetitionRoom!==selected)await repetitionLoad();
  if(room?.id!==selected)return;
  if(!window.AkariResonance?.ready())await window.AkariResonance?.load();
  if(room?.id!==selected)return;
  if(!window.AkariSessions?.ready())await window.AkariSessions?.load();
}
let panelActivation=0;
function showSubView(workspace,id,resetScroll=true){
  const root=$("workspace-"+workspace),target=$(id==="closet-panel"?"lifestyle-panel":id);
  if(!root||!target||!root.contains(target))return;
  if(workspace==='external'&&window.AkariExternal?.selectTab(id)===false)return;
  subSelections[workspace]=id;
  for(const panel of root.querySelectorAll('[data-subview]')){
    panel.hidden=panel!==target;
    if(panel===target&&panel.tagName==="DETAILS")panel.open=true;
  }
  if(target.id==="lifestyle-panel"){
    target.classList.toggle('closet-view',id==="closet-panel");
    $("closet-panel").hidden=id!=="closet-panel";
    $("closet-panel").open=id==="closet-panel";
  }
  let selectedButton=null;
  for(const button of root.querySelectorAll('.workspace-shortcuts button')){
    const selected=button.dataset.resource?["resources-panel","entry-panel"].includes(target.id)&&button.dataset.resource===$("resource-kind").value:
      button.dataset.editor?target.id==="document-editor"&&button.dataset.editor===$("editor-key").value:button.dataset.panel===id;
    button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
    if(selected)selectedButton=button;
  }
  if(selectedButton){
    target.setAttribute('aria-labelledby',selectedButton.id);
    const nav=selectedButton.parentElement,area=nav.getBoundingClientRect(),button=selectedButton.getBoundingClientRect();
    if(button.left<area.left+12)nav.scrollLeft+=button.left-area.left-12;
    else if(button.right>area.right-12)nav.scrollLeft+=button.right-area.right+12;
  }
  if(resetScroll)root.scrollTop=0;
  if(selectedButton&&!root.hidden){
    const activation=++panelActivation;
    queueMicrotask(()=>{
      if(activation===panelActivation&&!root.hidden&&subSelections[workspace]===id&&selectedButton.getAttribute('aria-selected')==='true'){
        selectedButton.dispatchEvent(new Event('panelactivate'));
      }
    });
  }
}
for(const workspace of Object.keys(subSelections)){
  const root=$("workspace-"+workspace),nav=root.querySelector('.workspace-shortcuts');
  nav.setAttribute('role','tablist');
  [...nav.querySelectorAll('button')].forEach((button,index)=>{
    button.id='subtab-'+workspace+'-'+index;button.setAttribute('role','tab');
    const id=subTarget(button);button.setAttribute('aria-controls',id==="closet-panel"?"lifestyle-panel":id);
  });
  nav.addEventListener('keydown',event=>{
    const buttons=[...nav.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
    if(index<0||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    buttons[next].focus();buttons[next].click();
  });
  for(const panel of root.querySelectorAll('[data-subview]'))panel.setAttribute('role','tabpanel');
}
// The inventory and closet share their loaded data and clothing registration fields.
for(const id of ['life-location','life-items','life-amount','life-taste','life-owner'])$(id).closest('label').classList.add('inventory-only');
$("life-list").classList.add('inventory-only');
$("life-create").parentElement.classList.add('inventory-only');
for(const workspace of Object.keys(subSelections))ensureSubView(workspace);
document.addEventListener("keydown",event=>{
  if(event.key!=="Escape"||event.defaultPrevented||event.isComposing||event.keyCode===229||event.repeat||event.ctrlKey||event.altKey||event.metaKey)return;
  // Let Escape close a modal / cancel IME conversion before cancelling generation.
  if(document.querySelector('dialog[open]'))return;
  const stop=['cancel','group-cancel','cw-cancel','resource-stop','sleep-stop','item-draft-cancel','appearance-model-cancel']
    .map(id=>$(id)).find(button=>button&&!button.disabled&&!button.hidden&&button.getClientRects().length&&getComputedStyle(button).visibility!=='hidden');
  if(stop){event.preventDefault();stop.click();return;}
  if(compactSettings.matches&&!document.body.classList.contains("settings-hidden")){
    document.body.classList.add("settings-hidden");syncSettingsToggle();$("toggle-settings").focus();
  }
});


function updateReviewDirty(){return !!pendingReview&&$("updates-after").value!==pendingReviewOriginal;}
function updateReviewMetadata(review){
  pendingReview=review;$("updates-before").value=review.current.text;$("updates-sources").replaceChildren();
  for(const source of review.source_documents||[]){const label=document.createElement('label'),body=document.createElement('textarea');label.textContent='参照元（本文だけの保存では保持）: '+source.document.path;body.readOnly=true;body.rows=8;body.value=source.document.text;label.append(body);$("updates-sources").append(label);}
  $("updates-path").textContent=review.current.path;
  const entity=review.kind==='entity'&&review.source_documents?.length===1&&review.source_documents[0].kind==='entity';
  $("updates-status").textContent=review.current.merge_pending?'未完了の統合があります。統合履歴から復旧してください。更新案は保持しています。':review.conflict?'対象または参照元が変更済みです。この案は保存できません。内容を確認して新しい案を作ってください。':entity?'確認待ちです。本文だけの保存では参照元を残します。「統合して元側を退避」は両原本を保全して元側を退避します。':'確認待ちです。内容を修正してから保存できます。';
}
function resetUpdateReview(){pendingReview=null;pendingReviewOriginal="";$("updates-before").value="";$("updates-after").value="";$("updates-sources").replaceChildren();$("updates-path").textContent="";$("updates-status").textContent="";$("updates-list").value="";}
function updateReviewControls(){
  const blocked=busy||editorBusy||slotsBusy||pendingReviewBusy||!room;
  $("updates-list").disabled=blocked;$("updates-refresh").disabled=blocked;$("updates-reset").disabled=blocked;
  const loaded=!!pendingReview&&pendingReview.room===room?.id&&String(pendingReview.id)===$("updates-list").value;
  $("updates-after").readOnly=blocked||!loaded;
  $("updates-approve").disabled=blocked||!loaded||pendingReview.conflict;
  $("updates-discard").disabled=blocked||!loaded||pendingReview.current.merge_pending;
  $("updates-merge").disabled=blocked||!loaded||pendingReview.conflict||pendingReview.kind!=='entity'||pendingReview.source_documents?.length!==1||pendingReview.source_documents[0].kind!=='entity';
  $("updates-merge").hidden=!loaded||pendingReview.kind!=='entity'||pendingReview.source_documents?.length!==1||pendingReview.source_documents[0].kind!=='entity';
}
async function updateReviewList(){
  if(!room)return;const selected=room.id,request=++pendingReviewListRequest;
  const rows=await api("/api/update-drafts?room="+encodeURIComponent(selected));if(room.id!==selected||request!==pendingReviewListRequest)return;
  const old=$("updates-list").value;
  $("updates-list").replaceChildren(new Option("更新案を選択…",""));
  for(const row of rows)$("updates-list").append(new Option(row.kind+(row.name?" / "+row.name:"")+" · "+row.created,row.id));
  if(rows.some(row=>String(row.id)===old))$("updates-list").value=old;
  updateReviewControls();
}
$("updates-refresh").addEventListener("click",()=>updateReviewList().catch(report));
$("updates-reset").addEventListener("click",()=>{resetUpdateReview();updateReviewControls();});
$("updates-after").addEventListener("input",updateReviewControls);
$("updates-list").addEventListener("change",async()=>{
  if(updateReviewDirty()){$("updates-list").value=String(pendingReview.id);notice("更新案に未保存の修正があります。保存するか、読み込みを解除してください。");return;}
  const selected=room.id,id=$("updates-list").value;resetUpdateReview();$("updates-list").value=id;
  if(!id){updateReviewControls();return;}
  pendingReviewBusy=true;updateReviewControls();
  try{const review=await api("/api/update-draft?room="+encodeURIComponent(selected)+"&id="+id);if(room.id!==selected)return;
    updateReviewMetadata(review);$("updates-after").value=review.text;pendingReviewOriginal=$("updates-after").value;
  }catch(error){report(error);}finally{pendingReviewBusy=false;updateReviewControls();}
});
async function finishUpdateReview(discard){
  if(!pendingReview||busy)return;const review=pendingReview;busy=true;controls();
  try{await api(discard?"/api/update-discard":"/api/update-approve",{room:review.room,id:review.id,text:$("updates-after").value});
    resetUpdateReview();await updateReviewList();notice(discard?"更新案を見送りました。原本は変更していません。":"原本をバックアップ付きで保存しました。検索対象の資料は必要に応じて意味検索の索引を更新してください。");
  }catch(error){report(error);}finally{busy=false;controls();if(room?.id===review.room)await selectRoom(review.room);}
}
$("updates-approve").addEventListener("click",()=>finishUpdateReview(false).catch(report));
$("updates-merge").addEventListener('click',async()=>{
  if(!pendingReview||busy)return;
  if(resourceDirty()){notice('本文に未保存の変更があります。先に保存または破棄してください。');return;}
  const review=pendingReview,source=review.source_documents[0],text=$("updates-after").value;
  const signature=JSON.stringify([review.room,review.id,text,review.current.state_revision,source.document.state_revision]);
  if(mergeReviewOperation?.signature!==signature)mergeReviewOperation={signature,room:review.room,draft_id:review.id,text,target_state_revision:review.current.state_revision,source_state_revision:source.document.state_revision,request_id:crypto.randomUUID()};
  busy=true;controls();let confirmed=null;
  try{
    const result=await api('/api/entity-merge',mergeReviewOperation);
    confirmed=result;
    if(room?.id!==review.room)return;
    if(result.status==='ok'){
      resetUpdateReview();await updateReviewList();resetResource();showWorkspace('memory');revealPanel('entity-merges-panel');
      await refreshEntityMerges(result.id);await refreshContext();
      notice(result.replayed?'保存済みの操作結果を確認しました。現在の状態は履歴に表示しています。':'統合して元側を退避しました。両原本は保持しています。');
    }else{
      const fresh=await api('/api/update-draft?'+resourceQuery({room:review.room,id:review.id}));updateReviewMetadata(fresh);
      if(result.status!=='unknown')mergeReviewOperation=null;
      notice(result.status==='unknown'?'未完了の統合です。更新案は保持しています。統合履歴から復旧してください。':'統合は完了せず、両原本へ戻しました。更新案は画面に保持しています。');
    }
  }catch(error){if(confirmed?.status==='ok')notice('統合は保存済みです。表示更新に失敗したため、統合履歴を読み直してください。');else report(error);}finally{busy=false;controls();}
});
$("updates-discard").addEventListener("click",()=>finishUpdateReview(true).catch(report));

let scheduleState=null,scheduleBusy=false,scheduleRequest=0,scheduleOperation=null,scheduleSignature="";
function scheduleKind(){
  const kind=$("schedule-kind").value;
  $("schedule-duration-field").hidden=kind!=="timer";$("schedule-at-field").hidden=kind!=="alarm";$("schedule-pomodoro-fields").hidden=kind!=="pomodoro";
}
function scheduleControls(){
  for(const button of $("time-panel").querySelectorAll('button'))button.disabled=scheduleBusy||lifecycleBusy;
}
function scheduleCard(title,body){
  const card=document.createElement('div');card.className='schedule-card';
  const name=document.createElement('strong');name.textContent=title;const text=document.createElement('p');text.textContent=body;
  card.append(name,text);return card;
}
async function refreshSchedules(){
  if(!room)return;const selected=room.id,request=++scheduleRequest;
  const data=await api('/api/schedules?room='+encodeURIComponent(selected));
  if(room?.id!==selected||request!==scheduleRequest)return;
  const signature=JSON.stringify([selected,data]);
  if(signature===scheduleSignature){scheduleControls();return;}
  scheduleSignature=signature;scheduleState=data;
  $("open-letters").textContent=data.letters_unread?'手紙箱 ('+data.letters_unread+')':'手紙箱';
  $("open-notifications").textContent=data.unread?'通知 ('+data.unread+')':'通知';
  $("schedule-list").replaceChildren();$("notification-list").replaceChildren();
  const states={queued:'待機中',notified:'通知を保存済み',cancelled:'取消済み'};
  for(const item of data.schedules){
    const card=scheduleCard(item.title,(states[item.state]||item.state)+' · '+new Date(item.due_at).toLocaleString()+(item.kind==='pomodoro'?' · '+(item.phase==='work'?'作業':'休憩')+' '+item.cycle+'/'+item.cycles:''));
    if(item.state==='queued'){
      const button=document.createElement('button');button.type='button';button.textContent='取消';
      button.addEventListener('click',()=>scheduleAction('/api/schedule-cancel',{room:selected,id:item.id,revision:item.revision}));card.append(button);
    }$("schedule-list").append(card);
  }
  for(const item of data.notifications){
    const phase=item.phase?(item.phase==='work'?'作業':'休憩')+' '+item.cycle+' 終了 · ':'';
    const card=scheduleCard((item.read?'既読 · ':'未読 · ')+item.title,phase+item.message+'\n'+new Date(item.created_at).toLocaleString()+(item.late_seconds?' · 期限から'+item.late_seconds+'秒遅れて通知':''));
    if(item.watch_check_id){const open=document.createElement('button');open.type='button';open.textContent='URL監視の取得結果を見る';open.addEventListener('click',async()=>{try{showWorkspace('room-settings');revealPanel('watchlist-panel');await watchLoad();await watchShowCheck(item.watch_check_id);}catch(error){report(error);}});card.append(open);}
    if(!item.read){const button=document.createElement('button');button.type='button';button.textContent='既読にする';button.addEventListener('click',()=>scheduleAction('/api/notification-read',{room:selected,id:item.id}));card.append(button);}
    $("notification-list").append(card);
  }
  if(!data.schedules.length)$("schedule-list").textContent='予約はありません。';
  if(!data.notifications.length)$("notification-list").textContent='通知はありません。';
  scheduleControls();
}
async function scheduleAction(path,data){
  if(scheduleBusy||lifecycleBusy)return;scheduleBusy=true;scheduleControls();
  try{await api(path,data);if(room?.id===data.room)await refreshSchedules();}
  catch(error){report(error);}finally{scheduleBusy=false;scheduleControls();}
}
$("open-notifications").addEventListener('click',()=>{showWorkspace('world');revealPanel('time-panel');refreshSchedules().catch(report);});
$("schedule-refresh").addEventListener('click',()=>refreshSchedules().catch(report));
$("schedule-kind").addEventListener('change',scheduleKind);
$("schedule-form").addEventListener('input',()=>scheduleOperation=null);
$("schedule-form").addEventListener('submit',async event=>{
  event.preventDefault();if(!room||scheduleBusy||lifecycleBusy)return;
  const kind=$("schedule-kind").value,selected=room.id;
  let at='';if(kind==='alarm'){
    const date=new Date($("schedule-at").value);if(!Number.isFinite(date.getTime())){notice('通知日時を入力してください。');return;}at=date.toISOString();
  }
  scheduleOperation||=crypto.randomUUID();
  const data={room:selected,request_id:scheduleOperation,kind,title:$("schedule-title").value,message:$("schedule-message").value,
    seconds:Number($("schedule-seconds").value),at,work_seconds:Number($("schedule-work").value),break_seconds:Number($("schedule-break").value),cycles:Number($("schedule-cycles").value)};
  scheduleBusy=true;scheduleControls();
  try{const result=await api('/api/schedule-create',data);scheduleOperation=null;
    if(room?.id===selected){$("schedule-status").textContent='予約を登録しました。通知日時: '+new Date(result.schedule.due_at).toLocaleString();await refreshSchedules();}
  }catch(error){report(error);}finally{scheduleBusy=false;scheduleControls();}
});
scheduleKind();

let imageSettingsDoc=null,imagePreviousProvider='disabled',imageProfileEdits={},imageSettingsBusy=false,imageRequest=0,imageBusy=false,imageOperation=null,imageJobsSignature='',legacyImageRows=[],legacyImageRoom=null;
const imageLocalFields={sampler:'image-sampler',steps:'image-steps',cfg_scale:'image-cfg',positive_prefix:'image-prefix',positive_append:'image-append',negative_prompt:'image-negative'};
function cacheImageProfile(){
  const provider=imagePreviousProvider;if(provider==='disabled'||!imageSettingsDoc)return;
  const value=provider==='local'?{url:$("image-base-url").value}:{base_url:$("image-base-url").value,model:$("image-model-name").value};
  value.timeout_seconds=Number($('image-timeout').value);
  if(provider==='local')for(const [key,id] of Object.entries(imageLocalFields))value[key]=['steps','cfg_scale'].includes(key)?Number($(id).value):$(id).value;
  else if($("image-api-key").value||$("image-clear-key").checked)value.api_key=$("image-clear-key").checked?'':$("image-api-key").value;
  imageProfileEdits[provider]={...imageProfileEdits[provider],...value};
}
function renderImageProfile(){
  const provider=$("image-provider").value;imagePreviousProvider=provider;
  $("image-provider-fields").hidden=provider==='disabled';if(provider==='disabled'){$("image-api-key").value='';return;}
  const profile={...imageSettingsDoc.profiles[provider],...imageProfileEdits[provider]};
  $('image-timeout').value=profile.timeout_seconds;
  $("image-base-url").value=profile.url||profile.base_url||'';$("image-model-name").value=profile.model||'';
  $("image-api-key").value=profile.api_key||'';$("image-clear-key").checked=false;
  $("image-key-status").textContent=profile.key_set?'キーは設定済みです。':'キーは未設定です。';
  $("image-cloud-fields").hidden=provider==='local';$("image-local-fields").hidden=provider!=='local';
  if(provider==='local')for(const [key,id] of Object.entries(imageLocalFields))$(id).value=profile[key];
}
async function loadImageSettings(){
  imageSettingsDoc=await api('/api/image-settings');imageProfileEdits={};$("image-provider").value=imageSettingsDoc.provider;renderImageProfile();
  $("image-settings-status").textContent=imageSettingsDoc.warning||'設定を読み込みました。';imageProviderStatus();
}
function imageProviderStatus(){
  $("image-provider-status").textContent='画像生成: '+(imageSettingsDoc?.provider||'disabled')+'。生成先のサーバー/モデルが利用可能か確認してください。';imageControls();
}
$("image-provider").addEventListener('change',()=>{cacheImageProfile();renderImageProfile();});
$("image-reload-settings").addEventListener('click',()=>loadImageSettings().catch(report));
$("image-settings-form").addEventListener('submit',async event=>{
  event.preventDefault();if(!imageSettingsDoc||imageSettingsBusy)return;cacheImageProfile();imageSettingsBusy=true;$("image-save-settings").disabled=true;
  try{imageSettingsDoc=await api('/api/image-settings',{provider:$("image-provider").value,profiles:imageProfileEdits,revision:imageSettingsDoc.revision});
    imageProfileEdits={};renderImageProfile();imageProviderStatus();$("image-settings-status").textContent='画像生成設定を保存しました。';
  }catch(error){report(error);}finally{imageSettingsBusy=false;$("image-save-settings").disabled=false;}
});
$("web-browser-query").addEventListener('input',()=>{$("web-browser-open").href='https://duckduckgo.com/?q='+encodeURIComponent($("web-browser-query").value);});
$("web-search-test").addEventListener('click',async()=>{
  const button=$("web-search-test"),status=$("web-search-test-status"),results=$("web-search-test-results");
  if(button.disabled)return;button.disabled=true;results.replaceChildren();status.textContent='DuckDuckGoで検索テストを実行中…';
  try{
    const data=await api('/api/web-search-test',{query:$("web-browser-query").value.trim()||'Python programming language'});
    const timing=`（${data.http_status==null?'HTTP応答なし':'HTTP '+data.http_status}・${(data.duration_ms/1000).toFixed(2)}秒）`;
    status.textContent=data.ok?(data.results.length?`検索成功：${data.results.length}件 ${timing}`:`検索完了：該当結果は0件 ${timing}`):`${data.reason==='challenge'?'アクセス確認が必要です。検索は完了していません。':data.message} ${timing}`;
    for(const row of data.results){
      const item=document.createElement('p'),link=document.createElement('a'),snippet=document.createElement('span');
      const url=new URL(row.url);if(!['http:','https:'].includes(url.protocol))continue;
      link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.textContent=row.title||row.url;
      snippet.textContent=' — '+row.snippet;item.append(link,snippet);results.append(item);
    }
  }catch(error){status.textContent='検索テストを完了できませんでした：'+error.message;}
  finally{button.disabled=false;}
});
$("open-image-settings").addEventListener('click',()=>{showWorkspace('settings');revealPanel('external-settings');});
let appearancePresetDoc=null,appearancePresetOperation=null,appearancePresetSelected="";
function appearancePresetNameDirty(){return !!appearancePresetDoc&&$("appearance-preset-name").value!==(appearancePresetDoc.entries.find(p=>p.id===appearancePresetSelected)?.name||"");}
let appearanceModelBusy=false,appearanceModelOperation=null,appearanceEditDoc=null;
let appearanceDraft=null,appearanceRequest=0,appearanceTemplateDoc=null,appearanceTemplateBusy=false,appearanceTemplateRequest=0,appearanceTemplateOperation=null;
function appearanceTemplateDirty(){return !!appearanceTemplateDoc&&(appearanceTemplateDoc.settings.body!==$("appearance-template-body").value||appearanceTemplateDoc.settings.enabled!==$("appearance-template-enabled").checked);}
async function loadAppearanceTemplate(discard=false){
  if(!room)return;
  if((appearanceTemplateDirty()||appearancePresetNameDirty())&&!discard){notice('書式の編集を保持しています。保存か破棄の後に読み直してください。');return;}
  const selected=room.id,scope=$("appearance-image-scope").value,request=++appearanceTemplateRequest;appearanceTemplateBusy=true;appearanceControls();
  try{const doc=await api('/api/appearance-template?'+new URLSearchParams({room:selected,scope}));if(room?.id!==selected||request!==appearanceTemplateRequest)return;
    appearanceTemplateDoc=doc;appearanceTemplateOperation=null;$("appearance-template-body").value=doc.settings.body;$("appearance-template-enabled").checked=doc.settings.enabled;
    $("appearance-template-history").replaceChildren(new Option('履歴を選択…',''));for(const row of doc.history)$("appearance-template-history").append(new Option(row.created+' · '+(row.settings.enabled?'保存した書式':'内蔵形式'),String(row.id)));
    $("appearance-template-status").textContent=doc.settings.enabled?'保存した書式を使用します。':'内蔵形式を使用します。';
    await loadAppearancePresets(selected,scope,request);
  }catch(error){if(room?.id===selected){appearanceTemplateDoc=null;$("appearance-template-status").textContent=error.message;}report(error);}
  finally{if(request===appearanceTemplateRequest)appearanceTemplateBusy=false;appearanceControls();}
}

async function loadAppearancePresets(selected,scope,request){
 const doc=await api('/api/appearance-presets?'+new URLSearchParams({room:selected,scope}));if(room?.id!==selected||request!==appearanceTemplateRequest)return;
 appearancePresetDoc=doc;appearancePresetOperation=null;appearancePresetSelected='';$("appearance-preset-name").value='';$("appearance-preset-list").replaceChildren(new Option('新規登録',''));
 for(const p of doc.entries)$("appearance-preset-list").append(new Option((p.archived?'保管中 · ':'')+p.name,p.id));$("appearance-preset-status").textContent=doc.entries.length+'件（保管分を含む）。';
}
$("appearance-preset-list").addEventListener('change',()=>{if(appearancePresetNameDirty()){$("appearance-preset-list").value=appearancePresetSelected;notice("編集中の書式名を登録するか、編集を破棄してください。");return;}appearancePresetSelected=$("appearance-preset-list").value;const p=appearancePresetDoc?.entries.find(p=>p.id===$("appearance-preset-list").value);$("appearance-preset-name").value=p?.name||'';appearancePresetOperation=null;});
$("appearance-preset-load").addEventListener('click',()=>{
 if(appearanceTemplateDirty()||appearancePresetNameDirty()){notice('書式の編集を保存または破棄してから読み込んでください。');return;}
 const p=appearancePresetDoc?.entries.find(p=>p.id===$("appearance-preset-list").value);if(!p)return;if(p.archived){notice('保管中の書式を復帰してから読み込んでください。');return;}
 $("appearance-template-body").value=p.body;$("appearance-template-enabled").checked=true;appearanceTemplateOperation=null;appearanceControls();$("appearance-preset-status").textContent='編集欄へ読み込みました。「書式を保存」まで使用設定は変わりません。';
});
async function saveAppearancePreset(action){
 if(!room||!appearanceTemplateDoc||appearanceTemplateBusy||busy||imageBusy||lifecycleBusy)return;
 if(action!=='save'&&appearancePresetNameDirty()){notice('編集中の書式名を登録または破棄してください。');return;}
 const old=appearancePresetDoc?.entries.find(p=>p.id===$("appearance-preset-list").value);if(action!=='save'&&!old)return;
 const payload={room:room.id,scope:appearanceTemplateDoc.settings.scope,id:old?.id||'',name:action==='save'?$("appearance-preset-name").value:old.name,body:action==='save'?$("appearance-template-body").value:old.body,archived:action==='archive'||action==='save'&&!!old?.archived,revision:old?.revision||''};
 const signature=JSON.stringify(payload);if(appearancePresetOperation?.signature!==signature)appearancePresetOperation={signature,id:crypto.randomUUID()};payload.request_id=appearancePresetOperation.id;
 appearanceTemplateBusy=true;appearanceControls();
 try{const result=await api('/api/appearance-presets',payload);await loadAppearancePresets(payload.room,payload.scope,appearanceTemplateRequest);if(room?.id===payload.room){appearancePresetSelected=result.preset.id;$("appearance-preset-list").value=result.preset.id;$("appearance-preset-name").value=result.preset.name;$("appearance-preset-status").textContent='書式集を保存しました。使用中の書式は変更していません。';}}
 catch(error){$("appearance-preset-status").textContent=error.message+' 入力は保持しています。';report(error);}
 finally{appearanceTemplateBusy=false;appearanceControls();}
}
for(const a of ['save','archive','restore'])$("appearance-preset-"+a).addEventListener('click',()=>saveAppearancePreset(a).catch(report));
function appearanceDraftDirty(){return !!window.AkariImageLinkJobs?.dirty()||!!window.AkariLifestyleImages?.dirty()||appearanceTemplateDirty()||appearancePresetNameDirty()||!!($("appearance-image-extra").value||(appearanceDraft?$("appearance-image-draft").value!==appearanceDraft.prompt:$("appearance-image-draft").value));}
function resetAppearanceDraft(){appearanceEditDoc=null;appearanceModelOperation=null;appearanceDraft=null;appearanceRequest++;$("appearance-image-draft").value="";$("appearance-image-extra").value="";$("appearance-image-status").textContent="";$("appearance-image-replace").checked=false;}
function appearanceControls(){
  const blocked=imageBusy||lifecycleBusy||appearanceTemplateBusy||!room,doc=appearanceDraft?.room===room?.id?appearanceDraft:null;
  for(const id of ['appearance-image-scope','appearance-image-extra','appearance-image-draft','appearance-image-preview','appearance-image-clear'])$(id).disabled=blocked;
  const template=appearanceTemplateDoc?.settings,matching=template?.room===room?.id&&template?.scope===$("appearance-image-scope").value;
  for(const id of ['appearance-template-enabled','appearance-template-body','appearance-template-history','appearance-template-save','appearance-preset-list','appearance-preset-name','appearance-preset-load','appearance-preset-save','appearance-preset-archive','appearance-preset-restore'])$(id).disabled=blocked||!matching;
  $("appearance-template-save").disabled ||= busy||appearancePresetNameDirty();
  $("appearance-model-mode").disabled=blocked||busy;
  $("appearance-model-generate").disabled=blocked||busy||!doc||!matching||appearanceTemplateDirty()||doc.scope!==$("appearance-image-scope").value||doc.extra!==$("appearance-image-extra").value.trim()||!$("appearance-image-draft").value.trim();
  $("appearance-model-cancel").disabled=!appearanceModelBusy;
  $("appearance-model-undo").disabled=blocked||busy||!appearanceEditDoc||appearanceEditDoc.scope!==$("appearance-image-scope").value||appearanceEditDoc.revision!==doc?.revision||appearanceEditDoc.template_revision!==doc?.template_revision||appearanceEditDoc.extra!==$("appearance-image-extra").value.trim();

  $("appearance-template-reload").disabled=blocked;
  $("appearance-image-preview").disabled ||= !matching||appearanceTemplateDirty();
  $("appearance-image-preview").textContent=$("appearance-image-draft").value&&(!doc||$("appearance-image-draft").value!==doc.prompt)?'下書きを破棄して作り直す':'下書きを作成';
  const hasPrompt=!!$("image-prompt").value.trim();$("appearance-image-replace-label").hidden=!hasPrompt;$("appearance-image-replace").disabled=blocked;
  $("appearance-image-apply").disabled=blocked||!doc||!$("appearance-image-draft").value.trim()||doc.scope!==$("appearance-image-scope").value||doc.extra!==$("appearance-image-extra").value.trim()||appearanceTemplateDirty()||!matching||doc.template_revision!==template.revision||hasPrompt&&!$("appearance-image-replace").checked;
}
$("appearance-image-preview").addEventListener('click',async()=>{
  if(!room||imageBusy||lifecycleBusy||appearanceTemplateBusy||appearanceTemplateDirty())return;
  if(lifeDirty()){notice('外見・衣装などに未保存の入力があります。保存か破棄の後に下書きを作成してください。');return;}
  const selected=room.id,request=++appearanceRequest;imageBusy=true;imageControls();
  try{const result=await api('/api/appearance-image-preview',{room:selected,scope:$("appearance-image-scope").value,extra:$("appearance-image-extra").value,template_revision:appearanceTemplateDoc?.settings.revision});if(room?.id===selected&&request===appearanceRequest){appearanceDraft=result;appearanceEditDoc=null;appearanceModelOperation=null;$("appearance-image-draft").value=result.prompt;$("appearance-image-replace").checked=false;$("appearance-image-status").textContent=result.warning;}}
  catch(error){if(room?.id===selected)$("appearance-image-status").textContent=error.message;report(error);}
  finally{imageBusy=false;imageControls();}
});
$("appearance-image-apply").addEventListener('click',async()=>{
  if(!appearanceDraft||appearanceDraft.room!==room?.id||imageBusy||lifecycleBusy||appearanceTemplateBusy||appearanceTemplateDirty())return;
  if(lifeDirty()){notice('生活データの未保存の入力を保存または破棄してください。');return;}
  if($("image-prompt").value.trim()&&!$("appearance-image-replace").checked)return;
  const doc=appearanceDraft,text=$("appearance-image-draft").value.trim();if(!text)return;
  if(doc.scope!==$("appearance-image-scope").value||doc.extra!==$("appearance-image-extra").value.trim()){notice("対象か追加指定が変わっています。下書きを作り直してください。");return;}
  imageBusy=true;imageControls();
  try{await api('/api/appearance-image-preview',{room:doc.room,scope:doc.scope,extra:doc.extra,revision:doc.revision,template_revision:doc.template_revision});if(room?.id===doc.room){$("image-prompt").value=text;$("image-prompt").dispatchEvent(new Event('input',{bubbles:true}));resetAppearanceDraft();$("appearance-image-status").textContent='生成欄に反映しました。内容・縦横比・参照画像を確認し、画像生成を予約してください。';$("image-prompt").focus();}}
  catch(error){$("appearance-image-status").textContent=error.message;report(error);}
  finally{imageBusy=false;imageControls();}
});
$("life-image-draft").addEventListener('click',async()=>{
  if(imageBusy||lifecycleBusy||!room)return;
  if(lifeDirty()){notice('未保存の外見・衣装を保存または破棄してください。');return;}
  const scope=$("life-scope").value;if(!['user','persona'].includes(scope)){notice('対象をユーザーかキャラクターにしてください。');return;}
  showWorkspace('world');showSubView('world','images-panel');$("appearance-image-panel").open=true;refreshImageJobs().then(refreshImageReferences).catch(report);
  if(appearanceDraftDirty()){notice('編集中の画像の下書きを保持しています。破棄か反映の後に作成してください。');return;}
  const selected=room.id;$("appearance-image-scope").value=scope;await loadAppearanceTemplate();if(room?.id===selected)$("appearance-image-preview").click();
});
$("appearance-image-panel").addEventListener('toggle',()=>{if($("appearance-image-panel").open&&room&&!appearanceTemplateDoc)loadAppearanceTemplate().catch(report);});
$("appearance-image-scope").addEventListener('change',()=>{if(appearanceTemplateDirty()||appearancePresetNameDirty()){$("appearance-image-scope").value=appearanceTemplateDoc.settings.scope;notice('書式の編集を保存または破棄してください。');appearanceControls();return;}loadAppearanceTemplate().catch(report);});
$("appearance-template-reload").addEventListener('click',()=>loadAppearanceTemplate(true).catch(report));
for(const id of ['appearance-template-enabled','appearance-template-body','appearance-preset-name'])$(id).addEventListener('input',()=>{appearanceTemplateOperation=null;appearanceControls();});
$("appearance-template-history").addEventListener('change',()=>{if(appearanceTemplateDirty()){$("appearance-template-history").value="";notice("書式の編集を保存または破棄してから履歴を選んでください。");return;}const row=appearanceTemplateDoc?.history.find(row=>String(row.id)===$("appearance-template-history").value);if(!row)return;$("appearance-template-body").value=row.settings.body;$("appearance-template-enabled").checked=row.settings.enabled;appearanceTemplateOperation=null;$("appearance-template-status").textContent='過去の書式を編集欄へ読み込みました。保存するまで未反映です。';appearanceControls();});
$("appearance-template-save").addEventListener('click',async()=>{
  if(!room||!appearanceTemplateDoc||imageBusy||busy||lifecycleBusy||appearanceTemplateBusy)return;
  const config=appearanceTemplateDoc.settings,payload={room:room.id,scope:config.scope,enabled:$("appearance-template-enabled").checked,body:$("appearance-template-body").value,revision:config.revision};
  const signature=JSON.stringify(payload);if(appearanceTemplateOperation?.signature!==signature)appearanceTemplateOperation={signature,id:crypto.randomUUID()};payload.request_id=appearanceTemplateOperation.id;
  appearanceTemplateBusy=true;appearanceControls();
  try{await api('/api/appearance-template',payload);if(room?.id===payload.room){await loadAppearanceTemplate(true);$("appearance-template-status").textContent='書式を保存しました。作成済みの下書きは作り直してください。';}}
  catch(error){if(room?.id===payload.room)$("appearance-template-status").textContent=error.message+' 入力は保持しています。';report(error);}
  finally{appearanceTemplateBusy=false;appearanceControls();}
});
$("appearance-model-generate").addEventListener('click',async()=>{
 const doc=appearanceDraft;if(!doc||doc.room!==room?.id||busy||imageBusy||appearanceModelBusy||appearanceTemplateDirty())return;
 if(lifeDirty()){notice('外見・衣装の編集を保存または破棄してください。');return;}
 const payload={room:room.id,scope:doc.scope,extra:doc.extra,revision:doc.revision,template_revision:doc.template_revision,text:$("appearance-image-draft").value,mode:$("appearance-model-mode").value},signature=JSON.stringify(payload);
 if(appearanceModelOperation?.signature!==signature)appearanceModelOperation={signature,id:crypto.randomUUID()};appearanceModelBusy=true;imageBusy=true;busy=true;controls();imageControls();
 try{const result=await api('/api/image-prompt-edit',{...payload,request_id:appearanceModelOperation.id});if(room?.id===payload.room){appearanceEditDoc=result;$("appearance-image-draft").value=result.output;$("appearance-image-status").textContent='文章の草案を作りました。画像は未生成です。外見・衣装・禁止条件を確認してから反映してください。';}}
 catch(error){if(room?.id===payload.room)$("appearance-image-status").textContent=error.message+' 元の文章は保持しています。';report(error);}
 finally{appearanceModelBusy=false;imageBusy=false;busy=false;controls();imageControls();}
});
$("appearance-model-cancel").addEventListener('click',()=>api('/api/cancel',{}).catch(report));
$("appearance-model-undo").addEventListener('click',()=>{if(!appearanceEditDoc||busy||imageBusy)return;$("appearance-image-draft").value=appearanceEditDoc.input;appearanceEditDoc=null;appearanceModelOperation=null;appearanceControls();$("appearance-image-status").textContent='モデルに渡す前の文章へ戻しました。';});
$("appearance-image-clear").addEventListener('click',()=>{resetAppearanceDraft();appearanceControls();});
for(const id of ['appearance-image-scope','appearance-image-extra','appearance-image-draft','appearance-image-replace','image-prompt'])$(id).addEventListener('input',appearanceControls);
function imageControls(){for(const button of $("images-panel").querySelectorAll('button'))button.disabled=imageBusy||lifecycleBusy;
  const provider=imageSettingsDoc?.provider,profile=imageSettingsDoc?.profiles?.[provider];$("image-generate").disabled=imageBusy||lifecycleBusy||!provider||provider==='disabled'||provider!=='local'&&!(profile?.model&&profile?.key_set);appearanceControls();window.AkariLifestyleImages?.controls();window.AkariImageLinkJobs?.controls();}
async function imageMutation(path,data){
  if(imageBusy||lifecycleBusy)return;imageBusy=true;imageControls();
  try{await api(path,data);if(room?.id===data.room)await refreshImageJobs();}catch(error){report(error);}finally{imageBusy=false;imageControls();}
}
async function refreshImageJobs(){
  if(!room)return;const selected=room.id,request=++imageRequest;
  const data=await api('/api/image-jobs?room='+encodeURIComponent(selected));
  if(room?.id!==selected||request!==imageRequest)return;
  if(legacyImageRoom!==selected){legacyImageRoom=selected;legacyImageRows=[];$("image-references").replaceChildren();}
  const signature=JSON.stringify([selected,data,legacyImageRows]);if(signature===imageJobsSignature){imageControls();return;}imageJobsSignature=signature;
  $("image-job-list").replaceChildren();$("image-gallery").replaceChildren();
  const labels={queued:'受付済み・会話後に開始',running:'生成中',succeeded:'保存完了',failed:'実行せず終了',cancelled:'取消済み',cancelling:'取消要求中',unknown:'結果不明'};
  for(const item of data.jobs){
    const card=scheduleCard(item.error_code==='timeout'?'タイムアウト（結果未確認）':labels[item.state]||item.state,item.prompt.slice(0,200)+(item.error?'\n'+item.error:'')+(item.cancel_requested&&item.state==='succeeded'?'\n取消要求後に完成した画像を保全しています。':'')+(item.artifact_path?'\n保存先: '+item.artifact_path:'')+(item.link_label?'\n関連先: '+item.link_label:'')+(item.link_result?'\n'+item.link_result.message:''));
    if(['queued','running'].includes(item.state)){
      const button=document.createElement('button');button.type='button';button.textContent='取消';button.addEventListener('click',()=>imageMutation('/api/image-cancel',{room:selected,id:item.id,revision:item.revision}));card.append(button);
    }$("image-job-list").append(card);
  }
  const known=new Set(data.images.map(i=>i.path));
  for(const item of [...data.images,...legacyImageRows.filter(i=>!known.has(i.path))]){
    const card=document.createElement('figure'),image=document.createElement('img');
    const url='/api/generated-image?'+new URLSearchParams({room:selected,id:item.id});image.src=url;image.loading='lazy';image.alt=item.prompt?.slice(0,120)||item.name||'生成画像';
    const caption=document.createElement('figcaption');caption.textContent=item.provider+' · '+(item.width?item.width+'×'+item.height:item.name||'既存画像');
    const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.append(image);card.append(link,caption);const bind=document.createElement('button');bind.type='button';bind.textContent='この画像を関連付ける';bind.addEventListener('click',()=>window.AkariLifestyleImages?.pick(item.id).catch(report));card.append(bind);$("image-gallery").append(card);
  }
  $("image-job-status").textContent=data.warning||'画像 '+data.images.length+'件・表示対象の既存画像 '+legacyImageRows.length+'件。';imageControls();
}
async function refreshImageReferences(){
  const selected=room.id,rows=await api('/api/image-references?room='+encodeURIComponent(selected));if(room?.id!==selected)return;
  const retained=Array.from($("image-references").selectedOptions).filter(o=>!o.value.startsWith('ref_')&&o.dataset.room===selected).map(o=>({id:o.value,label:o.textContent}));
  const chosen=new Set(Array.from($("image-references").selectedOptions).map(o=>o.value));$("image-references").replaceChildren();
  for(const row of rows)$("image-references").append(new Option(row.name,row.id,false,chosen.has(row.id)));
  for(const row of retained)if(!Array.from($("image-references").options).some(o=>o.value===row.id)){const option=new Option(row.label,row.id,false,true);option.dataset.room=selected;$("image-references").append(option);}
}
$("image-refresh").addEventListener('click',async()=>{try{await refreshImageJobs();await refreshImageReferences();}catch(error){report(error);}});
$("image-legacy-load").addEventListener('click',async()=>{if(!room)return;const selected=room.id;try{const rows=await api('/api/legacy-images?room='+encodeURIComponent(selected));if(room?.id!==selected)return;legacyImageRows=rows;legacyImageRoom=selected;await refreshImageJobs();}catch(error){report(error);}});
$("image-generate-form").addEventListener('input',()=>imageOperation=null);
$("image-generate-form").addEventListener('submit',async event=>{
  event.preventDefault();if(!room||imageBusy||lifecycleBusy)return;
  const reference_ids=Array.from($("image-references").selectedOptions).map(o=>o.value);
  if(reference_ids.length>4){notice('参照画像は4枚までです。');return;}
  let link_plan;try{link_plan=window.AkariImageLinkJobs?.request()||null;}catch(error){report(error);return;}
  imageOperation||=crypto.randomUUID();const selected=room.id;imageBusy=true;imageControls();
  try{const result=await api('/api/image-generate',{room:selected,request_id:imageOperation,prompt:$("image-prompt").value,aspect:$("image-aspect").value,reference_ids,link_plan});
    window.AkariImageLinkJobs?.accepted();imageOperation=null;if(room?.id===selected){$("image-job-status").textContent='画像生成を受け付けました。ID: '+result.job.id;await refreshImageJobs();}
  }catch(error){report(error);}finally{imageBusy=false;imageControls();}
});
document.querySelector('[data-panel="images-panel"]').addEventListener('panelactivate',()=>{refreshImageJobs().then(refreshImageReferences).catch(report);});

let activeAttachmentDoc=null,activeAttachmentOperation=null;
let attachmentDoc=null,attachmentPage=null,attachmentRequest=0,attachmentBusy=false,attachmentOperation=null,attachmentUsageRequest=0,attachmentUsageBusy=false;
$('attachments-usage-check').addEventListener('click',async()=>{
 if(!room||attachmentUsageBusy)return;const selected=room.id,request=++attachmentUsageRequest;attachmentUsageBusy=true;$('attachments-usage-check').disabled=true;$('attachments-usage').textContent='確認中…';
 try{const data=await api('/api/attachment-usage?room='+encodeURIComponent(selected));if(room?.id!==selected||request!==attachmentUsageRequest)return;const size=n=>(n/1048576).toFixed(2)+' MiB';$('attachments-usage').textContent=`確認時刻（UTC）: ${data.checked_at}\n登録: ${data.registered}枚 / 登録時 ${size(data.registered_bytes)}\n実ファイル: ${data.present}枚 / ${size(data.file_bytes)}\n会話で使用: ${data.categories.active.count}枚 / ${size(data.categories.active.bytes)}\n会話から除外: ${data.categories.hidden.count}枚 / ${size(data.categories.hidden.bytes)}\n発言が退避中: ${data.categories.retired.count}枚 / ${size(data.categories.retired.bytes)}\n欠落: ${data.missing}枚 / 読取不可: ${data.blocked}枚 / サイズ不一致: ${data.size_changed}枚\n継続添付の登録: ${data.persistent}枚 / 他の発言からの参照: ${data.linked}枚\n未登録ファイル: ${data.unregistered}件 / ${size(data.unregistered_bytes)} / 分類しない項目: ${data.unclassified}件`;}
 catch(error){if(room?.id===selected&&request===attachmentUsageRequest){$('attachments-usage').textContent=error.message;report(error);}}
 finally{if(request===attachmentUsageRequest){attachmentUsageBusy=false;$('attachments-usage-check').disabled=false;}}
});
function attachmentControls(){
  window.AkariAttachmentDisposal?.controls();
  const doc=attachmentDoc?.room===room?.id?attachmentDoc:null,blocked=attachmentBusy||busy||!!conversationEdit||!room;
  for(const id of ['attachments-list','attachments-filter','attachments-refresh','attachments-first','attachments-next'])$(id).disabled=blocked;
  $("attachments-next").disabled ||= !attachmentPage?.next_before;
  $("attachments-hide").disabled=blocked||!doc||!!doc.hidden;
  $("attachments-restore").disabled=blocked||!doc||!doc.hidden||!doc.available;
  $("attachments-reuse").disabled=blocked||processingImages||!doc?.available;
  $("attachments-origin").disabled=blocked||!doc||!!doc.message_deleted;
  $("active-attachment-add").disabled=blocked||!doc||!doc.available||!!doc.hidden||!!doc.message_deleted;
  $("active-attachment-remove").disabled=blocked||!$("active-attachment-list").value;
  $("active-attachment-clear").disabled=blocked||!activeAttachmentDoc?.entries.length;
  $("active-attachment-list").disabled=blocked;
}
function attachmentPaint(doc=null){
  attachmentDoc=doc;attachmentOperation=null;$("attachments-preview").replaceChildren();
  $("attachments-info").textContent=doc?`${doc.name} · ${doc.mime} · ${(doc.size/1024).toFixed(1)} KiB · ${doc.width}×${doc.height} · ${doc.created} UTC · 発言 #${doc.message_id} · ${doc.hidden?'会話から除外':'添付中'}${doc.message_deleted?'（発言は退避中）':''}${doc.issue?' · '+doc.issue:''}`:'';
  $("attachments-excerpt").textContent=doc?.excerpt||'';
  if(doc?.available)$("attachments-preview").append(imageElement(doc));attachmentControls();
}
async function attachmentSelect(id){
  const selected=room?.id,request=++attachmentRequest;attachmentPaint();if(!selected||!id)return;
  attachmentBusy=true;attachmentControls();
  try{const doc=await api('/api/attachment?'+new URLSearchParams({room:selected,id}));if(room?.id===selected&&request===attachmentRequest)attachmentPaint(doc);}
  finally{if(request===attachmentRequest){attachmentBusy=false;attachmentControls();}}
}
async function attachmentLoad(before=0,selectedId=''){
  if(!room)return;const selected=room.id,request=++attachmentRequest,filter=$("attachments-filter").value;
  attachmentBusy=true;attachmentPaint();attachmentControls();
  try{const data=await api('/api/attachments?'+new URLSearchParams({room:selected,before,filter}));if(room?.id!==selected||request!==attachmentRequest)return;
    await refreshActiveAttachments();if(room?.id!==selected||request!==attachmentRequest)return;attachmentPage=data;$("attachments-count").textContent=`登録済み ${data.count}枚・登録時サイズ ${(data.bytes/1024/1024).toFixed(2)} MiB（会話から除外 ${data.hidden}枚）。20件ずつ表示。`;
    $("attachments-list").replaceChildren(new Option('画像を選択…',''),...data.entries.map(doc=>new Option(`${doc.name} · #${doc.message_id}${doc.hidden?' / 除外':''}${doc.message_deleted?' / 発言退避中':''}`,doc.id)));
    if(selectedId&&data.entries.some(doc=>doc.id===selectedId)){$("attachments-list").value=selectedId;await attachmentSelect(selectedId);}
  }finally{if(request===attachmentRequest){attachmentBusy=false;attachmentControls();}}
}
async function refreshActiveAttachments(){
 if(!room)return;const selected=room.id,data=await api('/api/active-attachments?room='+encodeURIComponent(selected));if(room?.id!==selected)return;
 activeAttachmentDoc=data;$("active-attachment-list").replaceChildren(new Option('選択…',''));for(const e of data.entries)$("active-attachment-list").append(new Option(e.name+(e.valid?'':'（要確認）'),e.id));
 $("active-attachments-caption").textContent=data.entries.length?'継続添付（画像対応モデル時）: '+data.entries.map(e=>e.name+(e.valid?'':'（要確認）')).join('、'):'';$("active-attachments-caption").hidden=!data.entries.length;
 $("active-attachment-status").textContent=data.entries.length+'枚を継続添付中。解除しても過去の発言との関連は残ります。';attachmentControls();
}
async function activeAttachmentAction(action){
 if(!room||attachmentBusy||busy||!activeAttachmentDoc||activeAttachmentDoc.room!==room.id)return;
 const doc=attachmentDoc,key=action==='add'?doc?.id:$("active-attachment-list").value;
 if(action!=='clear'&&!key)return;const payload={room:room.id,action,id:key||'',image_revision:action==='add'?doc?.revision:'',revision:activeAttachmentDoc.revision},sig=JSON.stringify(payload);
 if(activeAttachmentOperation?.signature!==sig)activeAttachmentOperation={signature:sig,id:crypto.randomUUID()};attachmentBusy=true;attachmentControls();
 try{await api('/api/active-attachments',{...payload,request_id:activeAttachmentOperation.id});await refreshActiveAttachments();activeAttachmentOperation=null;}catch(error){report(error);}finally{attachmentBusy=false;attachmentControls();}
}
for(const a of ['add','remove','clear'])$("active-attachment-"+a).addEventListener('click',()=>activeAttachmentAction(a).catch(report));
$("active-attachment-list").addEventListener('change',attachmentControls);
$("attachments-list").addEventListener('change',()=>attachmentSelect($("attachments-list").value).catch(report));
for(const id of ['attachments-refresh','attachments-first'])$(id).addEventListener('click',()=>attachmentLoad().catch(report));
$("attachments-filter").addEventListener('change',()=>attachmentLoad().catch(report));
$("attachments-next").addEventListener('click',()=>attachmentLoad(attachmentPage?.next_before||0).catch(report));
document.querySelector('[data-panel="attachments-panel"]').addEventListener('panelactivate',()=>attachmentLoad().catch(report));
for(const action of ['hide','restore'])$("attachments-"+action).addEventListener('click',async()=>{
  const doc=attachmentDoc;if(!doc||doc.room!==room?.id||attachmentBusy||busy||conversationEdit)return;
  const signature=JSON.stringify([doc.room,doc.id,doc.revision,action]);if(attachmentOperation?.signature!==signature)attachmentOperation={signature,id:crypto.randomUUID()};
  attachmentBusy=true;attachmentControls();
  try{await api('/api/attachment-change',{room:doc.room,id:doc.id,revision:doc.revision,action,request_id:attachmentOperation.id});
    if(room?.id!==doc.room)return;await refreshConversation();if(chatLogState?.room===doc.room)await chatLogLoad();await attachmentLoad(0,doc.id);
    $("attachments-status").textContent=action==='hide'?'会話の表示とモデルへの送信から外しました。画像の原本は保持しています。':'画像を元の発言へ戻しました。退避中の発言は会話ログの変更履歴から復元できます。';
  }catch(error){report(error);}finally{attachmentBusy=false;attachmentControls();}
});
$("attachments-reuse").addEventListener('click',async()=>{
  const doc=attachmentDoc;if(!doc||doc.room!==room?.id||attachmentBusy||busy||processingImages||conversationEdit)return;
  attachmentBusy=true;attachmentControls();
  try{const response=await fetch(doc.url);if(!response.ok)throw new Error((await response.json()).error);const blob=await response.blob();
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('画像を読み込めませんでした。'));reader.readAsDataURL(blob);});
    if(room?.id!==doc.room||attachmentDoc?.revision!==doc.revision)return;
    if(busy||processingImages||conversationEdit)throw new Error('会話・画像の処理が終わってから再添付してください。');
    if(pendingImages.length>=4||blob.size+pendingImages.reduce((sum,image)=>sum+image.size,0)>12*1024*1024)throw new Error('画像は4枚・合計12MiBまでです。');
    pendingImages.push({name:doc.name,data,size:blob.size});renderImages();showWorkspace('chat');notice('入力欄へ再添付しました。送信はまだ行っていません。');
  }catch(error){report(error);}finally{attachmentBusy=false;attachmentControls();}
});
$("attachments-origin").addEventListener('click',async()=>{
  const doc=attachmentDoc;if(!doc||doc.room!==room?.id||doc.message_deleted||busy||attachmentBusy||conversationEdit)return;
  try{$("chat-log-query").value='';showWorkspace('logs');await chatLogLoad(0,doc.message_id);}catch(error){report(error);}
});

let avatarData=null,avatarBaseline='',avatarBusy=false,avatarOperation=null,avatarRequest=0,portraitRequest=0,avatarFailedVideo='',avatarUploadSelection=null;
function avatarForm(){return {mode:$("avatar-mode").value,expression:$("avatar-mode").value==='manual'?$("avatar-expression").value:'idle',idle_seconds:Number($("avatar-idle-seconds").value),animated:$("avatar-animated").checked,internal_emotion:$("avatar-internal-emotion").checked};}
function avatarDirty(){return !!avatarCrop||!!avatarData&&JSON.stringify(avatarForm())!==avatarBaseline;}
const avatarSlots=['profile','idle','thinking','neutral','joy','anger','anxious','sadness'];
function avatarControls(){
  const blocked=avatarBusy||busy||!!conversationEdit||!room||avatarData?.room!==room.id;
  for(const id of ['avatar-save','avatar-mode','avatar-reload','avatar-idle-seconds','avatar-internal-emotion','avatar-crop-mode','avatar-animated'])$(id).disabled=blocked;
  for(const button of $('avatar-grid').querySelectorAll('button'))button.disabled=blocked||(button.dataset.action==='delete'&&button.dataset.available!=='true');
}
function avatarPreview(){
  for(const old of $('avatar-grid').querySelectorAll('video'))old.pause();
  $('avatar-grid').replaceChildren();
  for(const name of avatarSlots){
    const entry=avatarData?.entries.find(item=>item.name===name),available=!!entry?.available&&!entry.hidden;
    const card=document.createElement('section');card.className='avatar-card';card.dataset.expression=name;
    const title=document.createElement('strong');title.textContent=name;card.append(title);
    const preview=document.createElement('div');preview.className='avatar-card-image';
    if(available){if(entry.mime?.startsWith('video/')){const video=document.createElement('video');video.src=entry.url;video.muted=true;video.controls=true;video.playsInline=true;video.preload='metadata';preview.append(video);}else{const image=imageElement(entry);image.alt=name;preview.append(image);}}
    else{const dummy=document.createElement('span');dummy.textContent='✦';dummy.className='avatar-card-placeholder';dummy.setAttribute('aria-label',name+'：画像なし');preview.append(dummy);}
    card.append(preview);const buttons=document.createElement('div');buttons.className='avatar-card-actions';
    for(const action of ['upload','delete']){const button=document.createElement('button');button.type='button';button.dataset.action=action;button.dataset.available=String(available);button.textContent=action==='upload'?'登録':'削除';button.setAttribute('aria-label',name+'を'+button.textContent);button.addEventListener('click',()=>{
      if(action==='upload'){if(avatarDirty()){notice('表示設定を保存・破棄してから画像を登録してください。');return;}$('avatar-name').value=name;avatarUploadSelection={room:room.id,name};$('avatar-file').click();}
      else avatarChange('delete',null,name).catch(report);
    });buttons.append(button);}card.append(buttons);$('avatar-grid').append(card);
  }
  $('avatar-info').textContent=avatarData?.warning||'';avatarControls();
}
function avatarFill(data){
  avatarData=data;avatarOperation=null;
  $('avatar-mode').value=data.settings.mode;$('avatar-expression').value=data.settings.mode==='manual'?data.settings.expression:'idle';$('avatar-idle-seconds').value=data.settings.idle_seconds||0;$('avatar-internal-emotion').checked=data.settings.internal_emotion===true;$('avatar-animated').checked=data.settings.animated===true;
  avatarBaseline=JSON.stringify(avatarForm());avatarPreview();
}
async function refreshPortrait(){
  if(!room)return;const selected=room.id,request=++portraitRequest,data=await api('/api/avatar-state?room='+encodeURIComponent(selected));
  if(room?.id!==selected||request!==portraitRequest)return;
  if(data.url===avatarFailedVideo){data.url=data.fallback_url;data.mime='image/png';}
  const video=$('avatar-video'),moving=data.mime?.startsWith('video/');
  $('avatar').hidden=moving||!data.url;$('avatar-placeholder').hidden=!!data.url;video.hidden=!moving||!data.url;
  if(moving&&data.url){if(video.getAttribute('src')!==data.url){video.muted=true;video.src=data.url;video.onerror=()=>{if(room?.id!==selected||video.getAttribute('src')!==data.url)return;avatarFailedVideo=data.url;video.hidden=true;video.pause();$('avatar').hidden=!data.fallback_url;$('avatar-placeholder').hidden=!!data.fallback_url;if(data.fallback_url)$('avatar').src=data.fallback_url;$('avatar-status').textContent='動画を再生できないためプロフィールへ代替しました。';};avatarPlayback();}}
  else{if(video.getAttribute('src')){video.pause();video.removeAttribute('src');video.load();}if(data.url!==$('avatar').getAttribute('src')){if(data.url)$('avatar').src=data.url;else $('avatar').removeAttribute('src');}}
  $('avatar').alt=room.name+' / '+data.expression;$('avatar').title=data.reason;video.title=data.reason;

}
async function avatarLoad(force=false){
  if(!room)return;if(!force&&avatarData?.room===room.id&&avatarDirty()){await refreshPortrait();return;}
  const selected=room.id,request=++avatarRequest,snapshot=JSON.stringify(avatarForm());avatarBusy=true;avatarControls();
  try{const data=await api('/api/avatars?room='+encodeURIComponent(selected));if(room?.id!==selected||request!==avatarRequest)return;
    if(avatarData?.room===selected&&JSON.stringify(avatarForm())!==snapshot){notice('読込中の表情設定の入力を保持しました。');return;}avatarFill(data);await refreshPortrait();
  }finally{if(request===avatarRequest){avatarBusy=false;avatarControls();}}
}
async function avatarChange(action,image=null,target=null){
  if(!avatarData||avatarData.room!==room?.id||avatarBusy||busy||conversationEdit)return;
  if(action!=='settings'&&avatarDirty()){notice('表示設定を保存・破棄してから画像を管理してください。');return;}
  const selected=room.id,values={room:selected,revision:avatarData.revision,action,expression:action==='settings'?avatarForm().expression:action==='upload'?$("avatar-name").value:target,mode:$("avatar-mode").value,idle_seconds:Number($("avatar-idle-seconds").value),animated:$("avatar-animated").checked,internal_emotion:$("avatar-internal-emotion").checked,image},signature=JSON.stringify(values);
  if(avatarOperation?.signature!==signature)avatarOperation={signature,id:crypto.randomUUID()};avatarBusy=true;avatarControls();
  try{const result=await api('/api/avatar-change',{...values,request_id:avatarOperation.id});if(room?.id!==selected)return;avatarFill(result.document);await refreshPortrait();$("avatar-status").textContent=action==='delete'?'削除しました。キャラフォルダの原本と変更履歴は保持しています。':'保存しました。キャラフォルダの原本は変更していません。';}
  catch(error){report(error);}finally{avatarBusy=false;avatarControls();}
}
$("avatar-form").addEventListener('submit',event=>{event.preventDefault();avatarChange('settings').catch(report);});
$("avatar-reload").addEventListener('click',()=>avatarLoad(true).catch(report));
let avatarCrop=null;
function drawAvatarCrop(){
 if(!avatarCrop)return;const {image,ratio}=avatarCrop,zoom=Number($("avatar-crop-zoom").value);let w=image.naturalWidth,h=w/ratio;if(h>image.naturalHeight){h=image.naturalHeight;w=h*ratio;}w/=zoom;h/=zoom;
 const x=(image.naturalWidth-w)*Number($("avatar-crop-x").value)/100,y=(image.naturalHeight-h)*Number($("avatar-crop-y").value)/100,scale=Math.min(1,1024/Math.max(w,h)),canvas=$("avatar-crop-canvas");canvas.width=Math.max(1,Math.round(w*scale));canvas.height=Math.max(1,Math.round(h*scale));canvas.getContext('2d').drawImage(image,x,y,w,h,0,0,canvas.width,canvas.height);
}
async function cropAvatar(data){
 if($("avatar-crop-mode").value==='none')return data;
 const image=new Image();image.src=data;await image.decode();if(image.naturalWidth>8192||image.naturalHeight>8192||image.naturalWidth*image.naturalHeight>16777216)throw new Error('画像の寸法が大きすぎます。');
 return await new Promise(resolve=>{avatarCrop={image,ratio:$("avatar-crop-mode").value==='portrait'?4/5:1,resolve};$("avatar-crop-zoom").value='1';$("avatar-crop-x").value='50';$("avatar-crop-y").value='50';drawAvatarCrop();$("avatar-crop-dialog").showModal();});
}
function finishAvatarCrop(accept){if(!avatarCrop)return;const {resolve}=avatarCrop;let data=null;if(accept)data=$("avatar-crop-canvas").toDataURL('image/png');avatarCrop=null;$("avatar-crop-dialog").close();resolve(data);}
for(const id of ['avatar-crop-zoom','avatar-crop-x','avatar-crop-y'])$(id).addEventListener('input',drawAvatarCrop);
$("avatar-crop-accept").addEventListener('click',()=>finishAvatarCrop(true));$("avatar-crop-cancel").addEventListener('click',()=>finishAvatarCrop(false));$("avatar-crop-dialog").addEventListener('cancel',event=>{event.preventDefault();finishAvatarCrop(false);});
$("avatar-file").addEventListener('change',async()=>{
  const file=$("avatar-file").files[0],selection=avatarUploadSelection;avatarUploadSelection=null;if(!file)return;
  const selected=selection?.room,target=selection?.name;
  try{if(!selection||room?.id!==selected)throw new Error('ルームが変わったため画像を登録しませんでした。');if(file.size>5*1024*1024||!['image/png','image/jpeg','image/gif','video/mp4','video/webm'].includes(file.type))throw new Error('PNG/JPEG/GIF/MP4/WebM・5MiB以下のファイルを選んでください。');const moving=['image/gif','video/mp4','video/webm'].includes(file.type);if(moving&&$('avatar-crop-mode').value!=='none')throw new Error('GIF・動画は切り抜きせず元のファイルを使用してください。');
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('画像を読み込めませんでした。'));reader.readAsDataURL(file);});
    const prepared=moving?data:await cropAvatar(data);if(!prepared)return;if(room?.id!==selected||$("avatar-name").value!==target){notice('切り抜き中にルームや表情名が変わったため登録しませんでした。');return;}await avatarChange('upload',{name:file.name,data:prepared});
  }catch(error){report(error);}finally{$("avatar-file").value='';}
});

let diagnosticsData=null,diagnosticsLoading=false,diagnosticsRequest=0;
const diagnosticsMarkers=new Map();
function diagnosticsKey(){const source=$("diagnostics-source").value;return source+(['tools','metrics'].includes(source)?':'+room?.id:'');}
function diagnosticsControls(){
  const source=$("diagnostics-source").value;
  for(const id of ['diagnostics-refresh','diagnostics-older','diagnostics-clear','diagnostics-reset'])$(id).disabled=diagnosticsLoading||!room;
  $("diagnostics-level").disabled=!['runtime','console'].includes(source);$("diagnostics-query").disabled=source==='engine';
  $("diagnostics-older").disabled ||= !diagnosticsData?.next_before;
  $("diagnostics-clear").disabled ||= !diagnosticsData;
}
async function refreshDiagnostics(before=0){
  if(!room)return;const selected=room.id,source=$("diagnostics-source").value,key=diagnosticsKey(),marker=diagnosticsMarkers.get(key)||{},request=++diagnosticsRequest;
  diagnosticsLoading=true;diagnosticsControls();
  try{const data=await api('/api/diagnostics?'+new URLSearchParams({source,room:selected,before,after:marker.after||0,epoch:marker.epoch||'',q:$("diagnostics-query").value,level:$("diagnostics-level").value}));
    if(room?.id!==selected||key!==diagnosticsKey()||request!==diagnosticsRequest)return;
    diagnosticsData=data;if(data.reset)diagnosticsMarkers.delete(key);
    $("diagnostics-output").textContent=source==='engine'?data.text:(data.entries||[]).map(row=>JSON.stringify(row,null,2)).join('\n\n');
    $("diagnostics-status").textContent=`対象: ${data.scope} · ${source==='engine'?'末尾のログ':(data.entries||[]).length+'件（新しい順・日時はUTC）'}`+(data.reset?' · 再起動・ログ置換等で表示位置をリセットしました。':'')+(data.evicted?' · 古い内部ログは保持上限から外れています。':'')+(data.truncated?' · 表示上限より前を省略しています。':'')+(!$("diagnostics-output").textContent?' · 表示する記録はありません。':'');
  }catch(error){if(request===diagnosticsRequest)report(error);}finally{if(request===diagnosticsRequest){diagnosticsLoading=false;diagnosticsControls();}}
}
$("diagnostics-source").addEventListener('change',()=>{diagnosticsData=null;refreshDiagnostics().catch(report);});
$("diagnostics-level").addEventListener('change',()=>refreshDiagnostics().catch(report));
$("diagnostics-filter-form").addEventListener('submit',event=>{event.preventDefault();refreshDiagnostics().catch(report);});
$("diagnostics-older").addEventListener('click',()=>{$("diagnostics-auto").checked=false;refreshDiagnostics(diagnosticsData?.next_before||0).catch(report);});
$("diagnostics-clear").addEventListener('click',()=>{if(!diagnosticsData)return;diagnosticsMarkers.set(diagnosticsKey(),{after:diagnosticsData.watermark,epoch:diagnosticsData.epoch});refreshDiagnostics().catch(report);});
$("diagnostics-reset").addEventListener('click',()=>{diagnosticsMarkers.delete(diagnosticsKey());refreshDiagnostics().catch(report);});
document.querySelector('[data-panel="diagnostics-panel"]').addEventListener('panelactivate',()=>refreshDiagnostics().catch(report));
setInterval(()=>{if($("diagnostics-auto").checked&&!diagnosticsLoading&&document.visibilityState==='visible'&&!$("workspace-settings").hidden&&!$("diagnostics-panel").hidden)refreshDiagnostics().catch(report);},2000);

let permissionData=null,permissionBaseline='',permissionBusy=false,permissionOperation=null;
function permissionForm(){return Object.fromEntries(Array.from(document.querySelectorAll('[data-tool-policy]'),select=>[select.dataset.toolPolicy,select.value]));}
function permissionDirty(){return !!permissionData&&JSON.stringify(permissionForm())!==permissionBaseline;}
function permissionControls(){
  const blocked=permissionBusy||busy||!!conversationEdit||!room||permissionData?.room!==room.id;
  for(const field of $("tool-permissions-form").querySelectorAll('select,button'))field.disabled=blocked;
  $("tool-permissions-reload").disabled=permissionBusy||busy||!room;
  for(const button of $("tool-approval-list").querySelectorAll('button'))button.disabled=blocked||permissionDirty();
  $("tool-approvals-refresh").disabled=permissionBusy||busy||!room;
  syncRoomSelector();
}
function permissionFill(data){
  permissionData=data;permissionOperation=null;$("tool-permission-categories").replaceChildren();
  const modes={off:'停止',ask:'毎回確認（個別申請）',read:'参照のみ',propose:'参照＋確認待ち草案',write:'すべて（既存の許可範囲内）'},operations={read:'参照',propose:'草案',write:'更新'};
  for(const [key,label] of Object.entries(data.categories)){
    const wrapper=document.createElement('label'),title=document.createElement('span'),select=document.createElement('select'),count=document.createElement('small');title.textContent=label;select.dataset.toolPolicy=key;select.id='tool-policy-'+key;
    for(const [value,text] of Object.entries(modes))select.append(new Option(text,value));select.value=data.settings.levels[key];
    const tools=data.tools.filter(tool=>tool.category===key);count.className='muted small';count.textContent=Object.entries(operations).map(([operation,text])=>text+' '+tools.filter(tool=>tool.operation===operation).length).join(' / ');wrapper.append(title,select,count);$("tool-permission-categories").append(wrapper);
  }
  permissionBaseline=JSON.stringify(permissionForm());$("tool-permission-catalog").replaceChildren();
  const effects={draft:'確認待ち草案',autonomy_document:'活動記録',working:'作業メモ',location:'現在地',temporary:'一時的な場所',lifestyle:'所持品・衣装',schedule:'予約',image_job:'画像生成の受付',local_inbox:'手紙・お知らせ',purpose_proposal:'目的の変更案',question_proposal:'問いの進展案',activity_timeline:'行動の過程',research_subscription:'研究購読',research_queued:'研究実行の受付',url_watch_setting:'URL監視設定',url_check_queued:'URL取得の受付'};
  for(const [category,label] of Object.entries({...data.categories,capabilities:'カテゴリの要求'})){
    const section=document.createElement('details'),summary=document.createElement('summary'),list=document.createElement('ul');summary.textContent=label;section.append(summary,list);
    for(const tool of data.tools.filter(tool=>tool.category===category)){const item=document.createElement('li');item.textContent=tool.name+' · '+operations[tool.operation]+' · '+(!tool.permitted?(data.settings.levels[category]==='ask'?'個別申請が必要':'設定で停止'):!tool.available?'接続等の準備待ち':'利用範囲内')+(tool.effects.length?' · '+tool.effects.map(effect=>effects[effect]||effect).join('、'):'');list.append(item);}$("tool-permission-catalog").append(section);
  }permissionControls();
}
async function loadPermissions(force=false){
  if(!room||permissionBusy)return;if(!force&&permissionData?.room===room.id&&permissionDirty()){notice('編集中のツール権限を保存・破棄してください。');return;}
  const selected=room.id;permissionBusy=true;permissionControls();
  try{const [data,requests]=await Promise.all([api('/api/tool-permissions?room='+encodeURIComponent(selected)),api('/api/tool-approvals?room='+encodeURIComponent(selected))]);if(room?.id!==selected)return;permissionFill(data);permissionRequestsFill(requests.requests,selected);$("tool-permissions-status").textContent=data.settings.configured?'このルームの保存済み設定です。':'初期設定です。従来の利用範囲を維持しています。';}
  catch(error){report(error);}finally{permissionBusy=false;permissionControls();}
}
$("tool-permissions-form").addEventListener('submit',async event=>{
  event.preventDefault();if(!permissionData||permissionData.room!==room?.id||permissionBusy||busy)return;
  const selected=room.id,levels=permissionForm(),revision=permissionData.settings.revision,signature=JSON.stringify([selected,levels,revision]);if(permissionOperation?.signature!==signature)permissionOperation={signature,id:crypto.randomUUID()};
  permissionBusy=true;permissionControls();let saved=false;
  try{await api('/api/tool-permissions-save',{room:selected,levels,revision,request_id:permissionOperation.id});saved=true;}
  catch(error){report(error);}finally{permissionBusy=false;permissionControls();}
  if(saved&&room?.id===selected){await loadPermissions(true);$("tool-permissions-status").textContent='権限を保存しました。次の会話・ツール呼出しから適用します。';}
});
$("tool-permissions-reload").addEventListener('click',()=>loadPermissions(true).catch(report));
$("tool-permissions-defaults").addEventListener('click',()=>{for(const select of document.querySelectorAll('[data-tool-policy]'))select.value='write';$("tool-permissions-status").textContent='既定値を入力しました。まだ保存していません。';});
document.querySelector('[data-panel="tool-permissions-panel"]').addEventListener('panelactivate',()=>loadPermissions().catch(report));

function permissionRequestsFill(items,selected){
  const expanded=new Set(Array.from($("tool-approval-list").querySelectorAll('details[open]'),item=>item.dataset.approvalId));
  $("tool-approval-list").replaceChildren();const states={pending:'承認待ち',approved:'承認済み・未実行',rejected:'却下・取消',running:'実行中',used:'使用済み',unknown:'結果未確認'};
  $("tool-approvals-status").textContent=items.length?'期限内の未完了を優先して最大50件。承認は実行済みを意味しません。':'申請はありません。';
  for(const item of items){
    const section=document.createElement('details'),summary=document.createElement('summary'),reason=document.createElement('p'),args=document.createElement('pre'),meta=document.createElement('p'),actions=document.createElement('div');
    section.dataset.approvalId=item.id;section.open=expanded.has(item.id);summary.textContent=item.tool+' · '+states[item.state]+(!item.current?' · 期限/設定変更等で無効':'');reason.textContent='理由: '+item.intent;args.textContent=JSON.stringify(item.arguments,null,2);args.style.whiteSpace='pre-wrap';args.style.overflowWrap='anywhere';meta.className='muted small';meta.textContent='期限: '+new Date(item.expires*1000).toLocaleString()+(item.result_status?' · 結果: '+item.result_status:'');actions.className='button-row';
    for(const action of ['approve','reject']){
      if(action==='approve'&&(item.state!=='pending'||!item.current)||action==='reject'&&!['pending','approved'].includes(item.state))continue;
      const button=document.createElement('button'),request_id=crypto.randomUUID();button.type='button';button.dataset.approvalAction=action;button.textContent=action==='approve'?'この内容を一回だけ承認':'却下・承認取消';
      button.addEventListener('click',async()=>{
        if(room?.id!==selected||permissionBusy||busy||permissionDirty())return;permissionBusy=true;permissionControls();
        try{await api('/api/tool-approval-decide',{room:selected,id:item.id,revision:item.revision,action,request_id});const data=await api('/api/tool-approvals?room='+encodeURIComponent(selected));if(room?.id===selected){permissionRequestsFill(data.requests,selected);notice(action==='approve'?'承認しました。まだ未実行です。会話で続きを伝えてください。':'申請を却下・取消しました。');}}
        catch(error){report(error);}finally{permissionBusy=false;permissionControls();}
      });actions.append(button);
    }
    section.append(summary,reason,args,meta,actions);$("tool-approval-list").append(section);
  }permissionControls();
}
$("tool-permissions-form").addEventListener('change',permissionControls);
$("tool-approvals-refresh").addEventListener('click',async()=>{
  if(!room||permissionBusy||busy)return;const selected=room.id;permissionBusy=true;permissionControls();
  try{const data=await api('/api/tool-approvals?room='+encodeURIComponent(selected));if(room?.id===selected)permissionRequestsFill(data.requests,selected);}
  catch(error){report(error);}finally{permissionBusy=false;permissionControls();}
});

function avatarPlayback(){const video=$('avatar-video');video.autoplay=!document.hidden&&!matchMedia('(max-width:700px)').matches&&!video.hidden;if(video.autoplay)video.play().catch(()=>{});else video.pause();}
document.addEventListener('visibilitychange',avatarPlayback);matchMedia('(max-width:700px)').addEventListener('change',avatarPlayback);

$("core-reflect").addEventListener('click',async()=>{
 if(!room||busy||resourceDirty())return;busy=true;controls();
 try{const preview=await api('/api/core-reflection?room='+encodeURIComponent(room.id));await api('/api/core-reflection',{room:room.id,revision:preview.revision,request_id:crypto.randomUUID()});await updateReviewList();notice('全文反映の確認案を作りました。AI更新案で確認保存してください。');showWorkspace('memory');revealPanel('updates-panel');}catch(e){report(e);}finally{busy=false;controls();}
});

document.querySelector('[data-open-common-closet]').addEventListener('click',()=>{showWorkspace('shared');revealPanel('shared-closet-panel');});
document.querySelector('[data-panel="shared-closet-panel"]').addEventListener('panelactivate',()=>lifeLoad().catch(report));
document.querySelector('[data-open-embedding]').addEventListener('click',()=>{showWorkspace('settings');revealPanel('embedding-details');});

// Public application update controls. Downloading never modifies the running code.
let updateDocument=null,updatePendingApply=false,updateLoading=false;
function updateControls(){
 const running=updateDocument&&['checking','downloading','staging','applying'].includes(updateDocument.state);
 $('app-update-check').disabled=updateLoading||lifecycleBusy||running;
 $('app-update-apply').disabled=updateLoading||lifecycleBusy||!updateDocument?.restart_supported||!['available','prepared'].includes(updateDocument?.state);
}
function renderUpdate(data){
 updateDocument=data;
 const names={idle:'未確認',checking:'確認中',current:'同じバージョンです',available:'異なるバージョンがあります',downloading:'取得中',staging:'取得ファイルを確認中',prepared:'取得完了・適用可能',applying:'適用・再起動中',failed:'更新を完了できませんでした'};
 $('app-update-status').textContent='現在: '+(data.current||'未設定')+' / 公開: '+(data.remote||'未確認')+' / '+(names[data.state]||data.state)+(data.bytes?' / 取得 '+Math.ceil(data.bytes/1048576)+' MB':'')+(data.error?' / '+data.error:'')+(data.last?' / 前回: '+({complete:'更新完了',failed:'未適用',unknown:'復元結果未確認'}[data.last.state]||data.last.state)+(data.last.error?' '+data.last.error:''):'');
 updateControls();
}
async function loadUpdate(){
 if(updateLoading||lifecycleBusy)return;
 updateLoading=true;updateControls();
 try{
  renderUpdate(await api('/api/update'));
  if(updatePendingApply&&updateDocument.state==='failed')updatePendingApply=false;
  if(updatePendingApply&&updateDocument.state==='prepared'){
   updatePendingApply=false;
   if(applicationDraftDirty()){notice('編集内容を保存または破棄してから、更新して再起動してください。');return;}
   await finishApplication(true,updateDocument.revision);
  }
 }catch(error){updatePendingApply=false;report(error);}finally{updateLoading=false;updateControls();}
}
$('app-update-check').onclick=async()=>{
 updateLoading=true;updateControls();
 try{await api('/api/update/check',{});}catch(error){report(error);}finally{updateLoading=false;}
 await loadUpdate();
};
$('app-update-apply').onclick=async()=>{
 if(applicationDraftDirty()){notice('未保存の編集を保存または破棄してください。');return;}
 if(!updateDocument)return;
 if(updateDocument.state==='prepared'){await finishApplication(true,updateDocument.revision);return;}
 updateLoading=true;updateControls();
 try{await api('/api/update/prepare',{revision:updateDocument.revision});updatePendingApply=true;}catch(error){report(error);}finally{updateLoading=false;}
 await loadUpdate();
};
document.querySelector('[data-panel="app-update-panel"]').addEventListener('panelactivate',loadUpdate);
setInterval(()=>{if(!lifecycleBusy&&(updatePendingApply||!$('workspace-settings').hidden&&!$('app-update-panel').hidden))loadUpdate();},1500);

window.addEventListener('load',async()=>{
 try{
  const data=await api('/api/update'),last=data.last;
  if(!last||sessionStorage.getItem('akari-update-result')===last.id)return;
  sessionStorage.setItem('akari-update-result',last.id);
  notice(last.state==='complete'?'アップデートが完了しました: '+last.version:'アップデートは完了していません。共通設定のアップデートとコンソールを確認してください。'+(last.error?' '+last.error:''),15000);
 }catch{} // A result notification must never block normal startup.
});
