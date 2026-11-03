/* Draft-only authoring. Model output is text, never executable UI or a room action. */
(()=>{'use strict';
const el=id=>document.getElementById('cw-'+id),keys=['name','description','role','personality','speech','first_person','user_address','relationship','background','appearance','interests','values','examples','activity'];
const labels={name:'名前',description:'紹介文',role:'役割・コンセプト',personality:'性格',speech:'話し方',first_person:'一人称',user_address:'ユーザーの呼び方',relationship:'関係',background:'来歴',appearance:'外見',interests:'関心',values:'大切にするもの',examples:'会話例',activity:'不在中の活動'};
let running=false,ignoring=false,operation=null,creation=null,candidates=null,trial=[],baseline='';
const profile=()=>Object.fromEntries(keys.map(k=>[k,el(k).value]));
const snapshot=()=>JSON.stringify([profile(),el('prompt').value,el('folder').value,trial]);
const dirty=()=>!ignoring&&(running||snapshot()!==baseline);
function status(text){el('status').textContent=text;}
function controls(){
 for(const node of document.querySelectorAll('#character-wizard-panel input,#character-wizard-panel textarea,#character-wizard-panel button'))node.disabled=running;
 el('cancel').disabled=!running||!operation;el('create').disabled=running||!el('confirm').checked||!el('prompt').value.trim()||!el('name').value.trim()||!el('folder').value.trim();
}
function paintTrial(){el('trial-log').textContent=trial.map(r=>(r.role==='user'?'あなた':'キャラクター')+'：'+r.content).join('\n\n');}
function invalidate(){creation=null;candidates=null;el('candidates').hidden=true;el('confirm').checked=false;trial=[];paintTrial();controls();}
async function preview(){const before=JSON.stringify(profile()),oldPrompt=el('prompt').value,result=await api('/api/character-wizard-preview',{profile:profile()});if(before!==JSON.stringify(profile())||oldPrompt!==el('prompt').value)throw new Error('編集中の入力を保持しました。もう一度指示文を作成してください。');creation=null;el('prompt').value=result.prompt;trial=[];paintTrial();el('confirm').checked=false;controls();return result;}
async function generate(mode){
 if(running||!room)return;
 try{
  if(mode==='trial'&&!el('prompt').value.trim())await preview();
  const message=el('trial-message').value;
  if(mode==='trial'&&!message.trim()){status('試し会話の発言を入力してください。');return;}
  const selected=room.id,payload={source_room:selected,mode,profile:profile(),prompt:el('prompt').value,history:trial.slice(-12),message};
  operation={id:crypto.randomUUID()};running=true;controls();status(mode==='suggest'?'空欄の候補を考えています…':'作成中のキャラクターが応答中…');
  const result=await api('/api/character-wizard-generate',{...payload,request_id:operation.id});
  if(mode==='suggest'){
   candidates=result.additions;el('candidate-list').replaceChildren();
   for(const [key,value] of Object.entries(candidates)){
    const label=document.createElement('label'),check=document.createElement('input'),body=document.createElement('span');
    check.type='checkbox';check.checked=true;check.dataset.field=key;body.textContent=labels[key]+'：'+value;body.style.whiteSpace='pre-wrap';label.className='check';label.append(check,body);el('candidate-list').append(label);
   }
   el('candidates').hidden=false;status('候補を確認し、採用する項目を選んでください。入力済みの項目は上書きしません。');
  }else{trial.push({role:'user',content:message.trim()},{role:'assistant',content:result.text});trial=trial.slice(-12);el('trial-message').value='';paintTrial();status('試し会話の結果です。話し方や設定を調整できます。');}
 }catch(error){status(error.message);report(error);}finally{running=false;operation=null;controls();}
}
el('suggest').addEventListener('click',()=>generate('suggest'));el('trial').addEventListener('click',()=>generate('trial'));
el('cancel').addEventListener('click',async()=>{if(!operation)return;try{await api('/api/cancel',{id:operation.id});status('生成の中止を要求しました。入力は保持します。');}catch(error){report(error);}});
el('preview').addEventListener('click',()=>preview().then(()=>status('指示文を作成しました。内容を確認してください。')).catch(report));
el('apply').addEventListener('click',async()=>{
 if(running||!candidates)return;
 for(const node of el('candidate-list').querySelectorAll('input:checked'))if(!el(node.dataset.field).value.trim())el(node.dataset.field).value=candidates[node.dataset.field];
 el('candidates').hidden=true;candidates=null;invalidate();
 try{await preview();status('選んだ候補を採用して指示文を作成しました。内容を確認できます。');}catch(error){report(error);}
});
for(const key of keys)el(key).addEventListener('input',invalidate);
el('prompt').addEventListener('input',invalidate);el('folder').addEventListener('input',()=>{creation=null;el('confirm').checked=false;controls();});el('confirm').addEventListener('change',controls);
el('trial-clear').addEventListener('click',()=>{trial=[];paintTrial();controls();});
el('reset').addEventListener('click',()=>{if(running)return;for(const key of keys)el(key).value='';for(const key of ['prompt','folder','trial-message'])el(key).value='';trial=[];candidates=null;creation=null;el('candidates').hidden=true;el('confirm').checked=false;paintTrial();baseline=snapshot();status('下書きを破棄しました。');controls();});
el('create').addEventListener('click',async()=>{
 if(running||!el('confirm').checked)return;
 ignoring=true;let other;
 try{other=busy||generating||editorDirty()||resourceDirty()||worldDirty()||lifeDirty()||autonomyDirty()||slotsDraftDirty()||appearanceDraftDirty()||itemDraftDirty();}finally{ignoring=false;}
 if(other){status('ほかの画面の変更を保存または破棄し、実行中の処理が終わってから作成してください。');return;}
 running=true;controls();
 try{
  const draft=await api('/api/character-wizard-preview',{profile:profile(),prompt:el('prompt').value});
  const key=JSON.stringify([el('folder').value,draft.revision]);if(!creation||creation.key!==key)creation={key,id:crypto.randomUUID()};
  const result=await api('/api/character-wizard-create',{folder:el('folder').value,profile:draft.profile,prompt:draft.prompt,revision:draft.revision,request_id:creation.id});
  baseline=snapshot();ignoring=true;try{await loadState(result.room.id);}finally{ignoring=false;}status('「'+result.room.name+'」を作成しました。試し会話は会話ログへ引き継いでいません。');notice('キャラクターを作成しました。');
 }catch(error){status(error.message);report(error);}finally{running=false;controls();}
});
baseline=snapshot();controls();window.AkariCharacterWizard={dirty};
})();
