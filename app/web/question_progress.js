"use strict";
let questionData=null,questionCurrent=null,questionRoom=null,questionPage=0,questionBusy=false,questionBaseline='',questionOperation=null;
function questionForm(){return {status:A('question-progress-kind').value,summary:A('question-summary').value,evidence:A('question-evidence').value};}
function questionDirty(){return (typeof questionPipelineDirty==='function'&&questionPipelineDirty())||!!questionData&&questionRoom===room?.id&&questionData.state==='pending'&&JSON.stringify(questionForm())!==questionBaseline;}
function questionControls(){
  const blocked=questionBusy||busy||!room||questionRoom!==room.id,pending=questionData?.state==='pending';
  for(const field of A('question-review-form').querySelectorAll('input,select,textarea,button'))field.disabled=blocked||!pending;
  A('question-approve').disabled=blocked||!pending||!questionCurrent||questionCurrent.revision!==questionData.before.revision||questionData?.reference_checks?.some(item=>!item.matched);
  for(const id of ['question-refresh','question-discard','question-proposals','question-prev','question-next'])A(id).disabled=questionBusy||busy||!room;
  A('question-prev').disabled=questionBusy||busy||questionPage===0;A('question-next').disabled=questionBusy||busy||!A('question-next').dataset.more;
  if(typeof questionPipelineControls==='function')questionPipelineControls();

  syncRoomSelector();
}
function questionDescribe(doc){return doc?doc.title+'\n状態: '+doc.state+'\n'+doc.body+'\n次の一手: '+doc.next_action+'\n進展: '+(doc.details?.progress_summary||'')+'\n解決内容: '+(doc.details?.resolution||''):'取得できる問いがありません。';}
function questionPaint(){
  A('question-before').textContent=questionData?questionDescribe(questionData.before):'提案を選択してください。';A('question-current').textContent=questionDescribe(questionCurrent);
  A('question-suggestion').textContent=questionData?JSON.stringify(questionData.suggested||{},null,2):'';
  const checks=questionData?.reference_checks||[];
  const sourceNames={conversation:'会話',autonomy_run:'自律実行'},roleNames={user:'あなた',assistant:'キャラクター'};
  A('question-references').textContent=checks.length?checks.map(item=>[(sourceNames[item.kind]||item.kind)+' #'+item.id+(item.role?' / '+(roleNames[item.role]||item.role):'')+(item.created_at?' / '+item.created_at:''),item.matched?'本文・引用一致（内容の真偽は未判定）':'照合できません: '+item.reason,'引用: '+item.quote,item.matched?'前後の本文: '+item.context:''].join('\n')).join('\n\n'):'参照元の指定なし。申告された根拠だけの提案です。';
  A('question-progress-kind').value=questionData?.status||'partial';A('question-summary').value=questionData?.summary||'';A('question-evidence').value=questionData?.evidence||'';
  questionBaseline=JSON.stringify(questionForm());questionOperation=null;
  const labels={pending:'確認待ち・問いには未反映',approved:'承認して保存済み',rejected:'却下済み'};
  A('question-status').textContent=questionData?labels[questionData.state]+' / 根拠の記載は申告内容です。'+(questionData.state==='pending'&&questionCurrent?.revision!==questionData.before.revision?' 現在の問いは変更済み、または取得できません。再提案が必要です。':''):'';questionControls();
}
async function questionList(){
  if(!room||questionBusy)return;const selected=room.id;if(questionRoom!==selected){questionRoom=selected;questionData=null;questionCurrent=null;questionPage=0;questionPaint();}
  questionBusy=true;questionControls();
  try{
    const data=await api('/api/question-proposals?'+new URLSearchParams({room:selected,offset:questionPage}));if(room?.id!==selected)return;
    const select=A('question-proposals');select.replaceChildren(new Option('提案を選択',''));
    for(const item of data.entries)select.append(new Option(item.title+' / '+item.status+' / '+item.state,item.id));
    if(questionData&&!data.entries.some(item=>item.id===questionData.id))select.append(new Option(questionData.title+' / '+questionData.state,questionData.id));select.value=questionData?.id||'';
    A('question-next').dataset.more=data.more?'yes':'';
  }catch(error){report(error);}finally{questionBusy=false;questionControls();if(typeof loadQuestionPipeline==='function')await loadQuestionPipeline();}
}
async function questionLoad(key){
  if(!room||questionBusy)return;const selected=room.id;questionBusy=true;questionControls();
  try{
    const data=await api('/api/question-proposal?'+new URLSearchParams({room:selected,id:key}));let current=null;
    try{current=await api('/api/autonomy-document?'+new URLSearchParams({room:selected,id:data.question_id}));}catch(error){report(error);}
    if(room?.id===selected){questionData=data;questionCurrent=current;questionRoom=selected;questionPaint();}
  }catch(error){A('question-proposals').value=questionData?.id||'';report(error);}finally{questionBusy=false;questionControls();}
}
A('question-proposals').addEventListener('change',async()=>{
  if(questionDirty()){A('question-proposals').value=questionData.id;notice('編集中の進展案を承認・破棄してください。');return;}
  const key=A('question-proposals').value;if(key)await questionLoad(key);else{questionData=null;questionCurrent=null;questionPaint();}
});
async function questionReview(action){
  if(!questionData||questionData.state!=='pending'||questionRoom!==room?.id||questionBusy||busy)return;
  const selected=room.id,key=questionData.id,revision=questionData.revision,edited=action==='approve'?questionForm():null,signature=JSON.stringify([selected,key,revision,action,edited]);
  if(questionOperation?.signature!==signature)questionOperation={signature,id:crypto.randomUUID()};questionBusy=true;questionControls();let saved=false;
  try{await api('/api/question-review',{room:selected,id:key,revision,action,edited,request_id:questionOperation.id});saved=true;}
  catch(error){report(error);}finally{questionBusy=false;questionControls();}
  if(saved&&room?.id===selected){await questionLoad(key);await questionList();notice(action==='approve'?'確認した進展を保存しました。':'進展案を却下しました。');}
}
A('question-review-form').addEventListener('submit',event=>{event.preventDefault();questionReview('approve');});A('question-reject').addEventListener('click',()=>questionReview('reject'));
A('question-discard').addEventListener('click',()=>{if(questionData)questionLoad(questionData.id);else questionList();});A('question-refresh').addEventListener('click',questionList);
for(const [id,delta] of [['question-prev',-20],['question-next',20]])A(id).addEventListener('click',()=>{questionPage=Math.max(0,questionPage+delta);questionList();});
document.querySelector('[data-panel="question-progress-panel"]').addEventListener('panelactivate',questionList);
questionControls();
