(()=>{
const defaults={local:'',openai:'https://api.openai.com/v1',gemini:'https://generativelanguage.googleapis.com/v1beta',anthropic:'https://api.anthropic.com/v1',compatible:''};
const fields=['provider','endpoint','model','key','clear_key','sampling'];
const geminiInitialModels=['gemini-3.1-flash-lite','gemini-3.5-flash-lite','gemma-4-26b-a4b-it','gemma-4-31b-it'];
const notify=()=>{if(state?.engine)engineState(state.engine);};
function editor(prefix,common){
 const el=k=>document.getElementById(prefix+k);let doc=null,base='',loadedKey='',loading=false,saving=false,fetching=false,sequence=0,request=null,reported=null,failed=false,target=common?'':null,capSequence=0,capTimer=null;
 function values(){const v=Object.fromEntries(fields.map(k=>[k,el(k).type==='checkbox'?el(k).checked:el(k).value]));if(v.clear_key)v.key='';v.inherit=common?false:el('inherit').checked;return v;}
 async function editorKey(data,id){return data.values.key_saved?(await api('/api/conversation-api-secret',{room:id,revision:data.revision})).key:'';}
 function savedProfile(){return doc?.profiles?.[el('provider').value];}
 function sameConnection(){const saved=savedProfile();return !!saved&&el('endpoint').value.replace(/\/+$/,'')===(saved.endpoint||'').replace(/\/+$/,'');}
 function keyStatus(){
  const saved=sameConnection()&&savedProfile().key_saved;
  el('key-status').textContent=el('clear_key').checked?'保存すると登録済みキーを削除します。':el('key').value&&el('key').value!==loadedKey?'新しいキーを入力中（保存前）。':saved?'APIキー登録済み。変更するときだけ新しいキーを入力してください。':'APIキー未登録。';
 }
 function paintCaps(caps){const label=v=>v===true?'対応':v===false?'非対応':'未判定';el('capabilities').textContent='自動設定：画像入力 '+label(caps?.vision)+' ／ ツール呼び出し '+label(caps?.tools)+(caps?.source==='previous-setting'?'（情報がないため従来設定を維持）':caps?.source==='api-metadata'?'（取得したモデル情報）':caps?.source==='known-model'?'（既知のモデル情報）':'（能力情報を取得できていません）');}
 async function preview(){
  const turn=++capSequence,loadTurn=sequence;if(!doc||loading)return;
  const v=values();if(v.provider==='local'){paintCaps(null);return;}
  try{const caps=await api('/api/conversation-api-capabilities',{room:target,revision:doc.revision,values:{provider:v.provider,endpoint:v.endpoint,model:v.model,inherit:v.inherit}});if(turn===capSequence&&loadTurn===sequence)paintCaps(caps);}catch{if(turn===capSequence&&loadTurn===sequence)paintCaps(null);}
 }
 function schedulePreview(){clearTimeout(capTimer);capSequence++;capTimer=setTimeout(preview,250);}
 function dirty(){return !!doc&&JSON.stringify(values())!==base;}
 function initialModels(){
  const selected=el('model').value;
  if(el('provider').value!=='gemini'){el('models').replaceChildren(new Option(selected||'モデル一覧を取得してください',selected));return;}
  el('models').replaceChildren(new Option('モデルを選択してください',''),...geminiInitialModels.map(model=>new Option(model,model)));
  if(selected&&!geminiInitialModels.includes(selected))el('models').add(new Option(selected+'（現在の指定）',selected));
  el('models').value=selected;
 }
 function status(){
  if(loading||failed||!doc)return {state:loading?'loading':'error',vision:false,remote:true,error:failed?'会話接続設定を読み直してください。':''};
  const v=doc.effective.values;if(v.provider==='local')return null;
  return reported||{state:v.model&&v.key_saved?'ready':'error',vision:v.vision,remote:true,provider:v.provider,error:v.key_saved?'':'会話APIのキーを保存してください。'};
 }
 function controls(){
  const blocked=loading||saving||fetching||!doc,inherit=!common&&el('inherit').checked;
  for(const k of fields)el(k).disabled=blocked||inherit||(common&&k!=='provider'&&el('provider').value==='local');
  if(!common)el('inherit').disabled=blocked;
  el('fields').hidden=inherit;el('remote').hidden=values().provider==='local';el('local').hidden=values().provider!=='local';
  el('save').disabled=blocked;el('reload').disabled=loading||saving;el('fetch').disabled=blocked||inherit||values().provider==='local';el('models').disabled=blocked||inherit;
  el('endpoint').readOnly=values().provider!=='compatible';
 }
 function paint(data,key=''){
  capSequence++;clearTimeout(capTimer);doc=data;reported=null;
  for(const k of fields){const e=el(k);if(e.type==='checkbox')e.checked=!!data.values[k];else e.value=data.values[k]||'';}
  if(!common)el('inherit').checked=!!data.values.inherit;
  if(common&&data.revision==='0'){
   el('provider').value='gemini';
   el('endpoint').value=defaults.gemini;
  }
  loadedKey=key;el('key').value=key;el('clear_key').checked=false;
  initialModels();
  base=JSON.stringify(values());const v=data.effective.values;keyStatus();paintCaps(data.values.capabilities);
  el('effective').textContent=(data.effective.source==='common'?'適用元：共通設定':'適用元：このルーム')+' ／ '+(v.provider==='local'?'ローカルllama.cpp':v.provider+' ／ '+v.model);
  el('status').textContent=(data.values.key_saved?'キー保存済み。':'キー未保存。')+(data.duplicates?.length?'同じ認証情報の登録：'+data.duplicates.map(x=>x||'共通設定').join('、'):'');controls();notify();
 }
 async function load(id,reloadLocal=false){
  target=id;const turn=++sequence;loading=true;failed=false;fetching=false;capSequence++;clearTimeout(capTimer);doc=null;reported=null;base='';loadedKey='';request=null;
  for(const k of fields){const e=el(k);if(e.type==='checkbox')e.checked=false;else e.value='';}
  if(!common)el('inherit').checked=true;
  el('effective').textContent='';el('key-status').textContent='';paintCaps(null);el('status').textContent='会話接続を読込中…';controls();notify();
  try{
   const data=await api('/api/conversation-api?room='+encodeURIComponent(id));
   if(turn!==sequence||!common&&room?.id!==id)return;
   const key=await editorKey(data,id);
   if(reloadLocal){
    const fresh=await api('/api/state');
    if(turn!==sequence)return;
    await loadLocalModelFields(fresh.settings,fresh.models);
   }
   if(turn!==sequence||!common&&room?.id!==id)return;
   paint(data,key);
  }
  catch(e){if(turn===sequence){failed=true;report(e);}}
  finally{if(turn===sequence){loading=false;controls();notify();}}
 }
 el('provider').onchange=async()=>{
  const provider=el('provider').value;
  if(!doc)return;
  const saved=savedProfile(),turn=sequence;
  loadedKey='';
  el('endpoint').value=saved?.endpoint||defaults[provider];
  el('model').value=saved?.model||'';
  el('key').value='';
  el('clear_key').checked=false;
  el('sampling').checked=!!saved?.sampling;
  initialModels();
  el('status').textContent=saved?'保存済みの接続設定を表示しています。':'接続方式を変更しました。キーとモデルを指定して保存してください。';
  if(saved?.key_saved){
   fetching=true;controls();
   try{
    const result=await api('/api/conversation-api-secret',{room:target,revision:doc.revision,provider});
    if(turn!==sequence||el('provider').value!==provider)return;
    loadedKey=result.key;
    if(sameConnection())el('key').value=loadedKey;
   }catch(error){if(turn===sequence)report(error);}
   finally{if(turn===sequence)fetching=false;}
  }
  if(turn===sequence){keyStatus();paintCaps(saved?.capabilities);controls();schedulePreview();}
 };
 el('key').onfocus=()=>{if(loadedKey&&el('key').value===loadedKey)el('key').select();};
 el('key').oninput=keyStatus;el('clear_key').onchange=keyStatus;
 el('endpoint').oninput=()=>{
  if(sameConnection()){
   if(!el('key').value&&!el('clear_key').checked)el('key').value=loadedKey;
  }else if(loadedKey&&el('key').value===loadedKey)el('key').value='';
  keyStatus();schedulePreview();
 };
 el('model').oninput=schedulePreview;
 if(!common)el('inherit').onchange=controls;
 el('reload').onclick=()=>load(common?'':room.id,common);
 el('models').onchange=()=>{if(el('models').value){el('model').value=el('models').value;preview();}};
 el('fetch').onclick=async()=>{
  if(!doc||loading||saving||fetching)return;const turn=sequence,id=target,body={room:id,values:values(),revision:doc.revision};fetching=true;controls();el('status').textContent='モデル一覧を取得中…';
  try{const data=await api('/api/conversation-api-models',body);if(turn!==sequence)return;const selected=el('model').value;
   el('models').replaceChildren(new Option('モデルを選択してください',''),...data.models.map(m=>new Option(m,m)));
   if(selected){if(!data.models.includes(selected))el('models').add(new Option(selected+'（現在の指定・取得一覧外）',selected));el('models').value=selected;}
   preview();el('status').textContent=data.models.length+'件のモデルを取得しました。選択後に設定を保存してください。一覧への掲載は会話・画像・ツールへの対応確認ではありません。';
  }catch(e){if(turn===sequence){el('status').textContent=e.message;report(e);}}finally{if(turn===sequence){fetching=false;controls();}}
 };
 el('form').onsubmit=async e=>{
  e.preventDefault();if(!doc||saving||loading||fetching)return;
  const turn=sequence,id=target,body={room:id,values:values(),revision:doc.revision},signature=JSON.stringify(body);
  if(request?.signature!==signature)request={signature,id:crypto.randomUUID()};saving=true;controls();
  let localSaved=false,generationSaved=false;
  try{
   if(common){
    normalizeGenerationTokens();
    if(!document.getElementById('generation-settings-form').reportValidity())return;
   }
   if(common&&body.values.provider==='local'){
    if(!document.getElementById('settings-form').reportValidity())return;
    await saveSettings();
    localSaved=true;
   }else if(common){
    await saveGenerationSettings();
    generationSaved=true;
   }
   if(!localSaved||doc.revision==='0'||doc.values.provider!=='local'){
    await api('/api/conversation-api',{...body,request_id:request.id});
   }
   if(turn!==sequence)return;
   const data=await api('/api/conversation-api?room='+encodeURIComponent(id));if(turn!==sequence)return;
   const key=await editorKey(data,id);if(turn!==sequence)return;paint(data,key);request=null;notice(localSaved?'設定を保存しました。モデルの起動設定は再起動後に反映されます。':common?'設定を保存しました。':'このルームの会話接続を保存しました。');
   if(common&&room&&!roomEditor.dirty())await roomEditor.load(room.id);
  }catch(e){if(turn===sequence)report(localSaved||generationSaved?new Error((localSaved?'ローカルモデルの設定':'生成・メモリ設定')+'を保存しました。接続方式の保存確認に失敗しました。'+e.message):e);}finally{saving=false;controls();}
 };
 return {dirty,status,load,reportStatus:(s,id)=>{
  if(doc?.room!==id||dirty()||loading)return;
  if(s?.remote&&s.revision!==doc.effective.revision||!s?.remote&&doc.values.inherit&&doc.effective.values.provider!=='local'){load(id);return;}
  reported=s?.remote?s:null;
 }};
}
const roomEditor=editor('conversation-api-',false),commonEditor=editor('conversation-api-common-',true);
window.AkariConversation={dirty:()=>roomEditor.dirty()||commonEditor.dirty(),status:roomEditor.status,roomChanged:roomEditor.load,loadCommon:()=>commonEditor.load(''),reportStatus:roomEditor.reportStatus};
})();
