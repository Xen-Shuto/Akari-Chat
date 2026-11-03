"use strict";
let questionPipelineData=null,questionPipelineRoom=null;
function questionPipelineDirty(){return !!questionPipelineData&&questionPipelineRoom===room?.id&&A('question-auto-enabled').checked!==questionPipelineData.settings.enabled;}
function questionPipelineControls(){
  const blocked=questionBusy||busy||!room||questionPipelineRoom!==room.id||!questionPipelineData;
  A('question-auto-enabled').disabled=blocked;A('question-auto-save').disabled=blocked;
  A('question-auto-reload').disabled=questionBusy||busy||!room;
  for(const button of A('question-auto-runs').querySelectorAll('button'))button.disabled=blocked||questionPipelineDirty();
}
function questionPipelinePaint(data,selected){
  A('question-auto-status').textContent=(data.settings.enabled?'自動判定ON':'自動判定OFF')+' / 次の受付待ち回答: '+data.waiting_replies+'件'+(data.blocked_reason?' / '+data.blocked_reason:'')+(questionPipelineDirty()?' / 設定は編集中・未保存です。':'');
  const host=A('question-auto-runs');host.replaceChildren();const states={queued:'待機中',running:'実行中',succeeded:'判定完了',partial:'一部未完了',failed:'失敗',cancelled:'中止',unknown:'結果未確認'};
  for(const run of data.runs){
    const section=document.createElement('details'),title=document.createElement('summary'),meta=document.createElement('p'),actions=document.createElement('div');
    title.textContent=(states[run.state]||run.state)+' / '+new Date(run.created_at).toLocaleString();
    meta.className='muted small';meta.textContent='対象の問い: '+run.question_ids.length+'件 / 完了発言: '+run.source_ids.length+'件'+(run.state==='succeeded'?' / 進展案: '+(run.question_result?.length||0)+'件（解決の認定ではありません）':'')+(run.error?' / '+run.error:'')+(run.warnings.length?' / '+run.warnings.join(' '):'');
    actions.className='button-row';
    function button(label,task){const control=document.createElement('button');control.type='button';control.textContent=label;control.addEventListener('click',()=>questionPipelineAction(selected,task));actions.append(control);}
    button('判定に使った資料を見る',async()=>{const source=await api('/api/question-pipeline-source?'+new URLSearchParams({room:selected,id:run.id}));if(room?.id===selected)A('question-auto-source').textContent=JSON.stringify(source,null,2);});
    if(['queued','running','unknown'].includes(run.state)&&!(run.state==='unknown'&&run.reviewed))button(run.state==='unknown'?'結果を確認済みにする':'この判定を中止',async()=>{await api(run.state==='unknown'?'/api/autonomy-acknowledge':'/api/autonomy-cancel',{room:selected,id:run.id,revision:run.revision});});
    section.append(title,meta,actions);host.append(section);
  }
}
async function loadQuestionPipeline(discard=false){
  if(!room||questionBusy)return;const selected=room.id;
  if(questionPipelineRoom!==selected){questionPipelineData=null;questionPipelineRoom=selected;A('question-auto-enabled').checked=false;A('question-auto-source').textContent='';A('question-auto-runs').replaceChildren();}
  const preserve=questionPipelineDirty()&&!discard;questionBusy=true;questionControls();
  try{const data=await api('/api/question-pipeline?room='+encodeURIComponent(selected));if(room?.id!==selected)return;
    // Preserve both the draft and its base revision until the explicit reload/save.
    if(!preserve){questionPipelineData=data;A('question-auto-enabled').checked=data.settings.enabled;}
    questionPipelinePaint(data,selected);
  }catch(error){report(error);}finally{questionBusy=false;questionControls();}
}
async function questionPipelineAction(selected,task){
  if(room?.id!==selected||questionBusy||busy||questionPipelineDirty())return;questionBusy=true;questionControls();
  try{await task();}catch(error){report(error);}finally{questionBusy=false;questionControls();}
  if(room?.id===selected)await loadQuestionPipeline();
}
A('question-auto-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!room||!questionPipelineData||questionBusy||busy||questionPipelineRoom!==room.id)return;
  const selected=room.id,enabled=A('question-auto-enabled').checked,revision=questionPipelineData.settings.revision;questionBusy=true;questionControls();let saved=false;
  try{await api('/api/question-pipeline',{room:selected,enabled,revision});saved=true;}
  catch(error){report(error);}finally{questionBusy=false;questionControls();}
  if(saved&&room?.id===selected){await loadQuestionPipeline(true);notice('問いの自動判定設定を保存しました。過去の会話の一括判定は行いません。');}
});
A('question-auto-reload').addEventListener('click',()=>loadQuestionPipeline(true));
A('question-auto-enabled').addEventListener('change',()=>{questionPipelineControls();if(questionPipelineData)A('question-auto-status').textContent='自動判定設定は未保存です。保存または再読込してください。';});
questionPipelineControls();
