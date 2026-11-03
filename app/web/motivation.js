"use strict";
let motivationData=null,motivationBaseline='',motivationBusy=false,motivationOperation=null;
function emotionFreshnessText(data){
  const report=data.state,effect=data.emotion_effect;
  let result='モデル自己申告の感情: '+report.emotion+' / 記録した強度 '+report.intensity+' / 直近会話のArousal（履歴値） '+report.arousal;
  if(!effect)return result;
  result+='\n時間補正後の強度: '+effect.intensity.toFixed(3)+' / 減衰係数 '+effect.factor.toFixed(3);
  if(effect.status==='unknown')return result+' / 申告時刻不明・判断への寄与なし';
  result+='\n自己申告の時刻: '+new Date(effect.reported_at*1000).toLocaleString()+' / 経過約 '+(effect.age_seconds/3600).toFixed(1)+'時間';
  return result+(effect.status==='expired'?' / 72時間以上経過・判断への寄与なし':' / 強度の半減時間 6時間');
}
function goalDeadlineText(target){
  if(target?.kind!=='goal')return '';
  if(target.deadline_state==='invalid')return ' / 期限は読取不能・加点なし';
  if(!target.due_at)return ' / 期限なし';
  const remaining=target.deadline_seconds,days=Math.ceil(Math.abs(remaining||0)/86400);
  const status=target.deadline_state==='overdue'?'期限超過':target.deadline_state==='due'?'期限到達':remaining>86400?'残り約'+days+'日':remaining>3600?'残り約'+Math.ceil(remaining/3600)+'時間':remaining>60?'残り約'+Math.ceil(remaining/60)+'分':'まもなく期限';
  return ' / 期限 '+new Date(target.due_at).toLocaleString()+' · '+status+' / 期限による加点 '+(target.deadline_bonus||0).toFixed(3);
}
function goalStagnationText(value){
  if(!value)return '';
  if(value.truncated)return '\n進捗記録の補正: 候補数上限の超過により集計を省略';
  if(!value.total)return '\n進捗記録の補正: 対象の未完了目標なし';
  return '\n進捗記録の補正: '+value.window_days+'日以上更新なし '+value.stale+'/'+value.total+'件（時刻不明 '+value.unknown+'件） / 減算 −'+value.penalty.toFixed(3);
}
function goalProgressText(target){
  if(target?.kind!=='goal')return '';
  if(!target.progress_updated_at)return ' / 進捗記録の更新時刻は不明';
  return ' / 進捗記録の更新 '+new Date(target.progress_updated_at).toLocaleString()+'（約'+(target.progress_age_seconds/86400).toFixed(1)+'日前）'+(target.progress_record_state==='stale'?' / 7日以上更新なし':'');
}
function motivationForm(){return {enabled:A('motivation-enabled').checked,threshold:Number(A('motivation-threshold').value),cooldown_seconds:Number(A('motivation-cooldown').value)*60,boredom_seconds:Number(A('motivation-boredom').value)*60};}
function motivationDirty(){return !!motivationData&&motivationData.room===room?.id&&JSON.stringify(motivationForm())!==motivationBaseline;}
function motivationControls(){
  for(const field of A('motivation-form').querySelectorAll('input,button'))field.disabled=motivationBusy||busy||motivationData?.room!==room?.id;
  if(typeof internalResetControls==='function')internalResetControls();
  A('motivation-refresh').disabled=motivationBusy||!room;A('motivation-reload').disabled=motivationBusy||!room;

  syncRoomSelector();
}
async function motivationLoad(discard=false){
  if(!room||motivationBusy)return;const selected=room.id;motivationBusy=true;motivationControls();
  try{
    const data=await api('/api/motivation?room='+encodeURIComponent(selected));if(room?.id!==selected)return;
    if(discard||motivationData?.room!==selected||!motivationDirty()){
      const c=data.settings;motivationData={room:selected,...data};A('motivation-enabled').checked=c.enabled;A('motivation-threshold').value=c.threshold;A('motivation-cooldown').value=c.cooldown_seconds/60;A('motivation-boredom').value=c.boredom_seconds/60;motivationBaseline=JSON.stringify(motivationForm());motivationOperation=null;
    }
    const labels={boredom:'退屈',curiosity:'好奇心',goal_achievement:'目標達成欲',relatedness:'関係性維持欲求'};
    A('motivation-state').textContent=data.reason+' · クールダウン残り '+data.cooldown_remaining+'秒\n'+Object.entries(data.levels).map(([key,value])=>labels[key]+': '+value.toFixed(3)+'（元 '+data.raw_levels[key].toFixed(3)+' / 充足 −'+data.satisfaction[key].toFixed(3)+' / 動機反復 −'+data.repetition_penalties[key].toFixed(3)+' / 報告反復 −'+Math.max(data.report_repetition?.penalties[key]||0,data.additional_repetition?.penalties[key]||0).toFixed(3)+(key==='goal_achievement'?' / 進捗記録 −'+(data.goal_stagnation?.penalty||0).toFixed(3):'')+'）').join('\n')+goalStagnationText(data.goal_stagnation)+'\n直近の完了動機: '+(data.state.recent_drives.map(key=>labels[key]||key).join(' → ')||'なし')+'\n'+emotionFreshnessText(data);
    if(data.additional_repetition)A('motivation-state').textContent+='\n追加の反復評価: '+data.additional_repetition.reason+' / 意味比較 '+data.additional_repetition.embedding_calls+'回 / キャッシュ '+data.additional_repetition.cache_hits+'件'+(data.additional_repetition.matches.length?' / '+data.additional_repetition.matches.map(m=>m.method+' '+m.newer_id+' / '+m.older_id+' / '+m.score.toFixed(3)).join('、'):'');
    if(data.report_repetition?.matches.length)A('motivation-state').textContent+='\n報告の類似（実作業の重複を示すものではありません）: '+data.report_repetition.matches.map(m=>m.newer_id+' / '+m.older_id+' / 類似度 '+m.score.toFixed(3)).join('、');
    A('motivation-events').textContent=data.events.map(item=>item.created+' / '+item.kind+' / '+item.emotion_source+' / '+item.emotion+' / '+item.arousal+(item.emotion_effect?' / 当時の補正後強度 '+item.emotion_effect.intensity.toFixed(3):'')+(item.satisfied_drive?' / 完了による一時補正: '+labels[item.satisfied_drive]:'')+(item.outcome?' / 結果: '+item.outcome:'')).join('\n')||'状態更新の履歴はありません。';
    if(data.activity_focus)A('motivation-state').textContent+='\n優先候補: '+(data.activity_focus.target?.title||'選択なし')+' / '+data.activity_focus.reason+(data.activity_focus.target?' / 目的との加点 '+data.activity_focus.target.alignment_bonus.toFixed(2)+goalDeadlineText(data.activity_focus.target)+goalProgressText(data.activity_focus.target):'');
    if(Number.isFinite(data.activity_focus?.evaluated_at))A('motivation-state').textContent+='\n候補の判定時刻: '+new Date(data.activity_focus.evaluated_at*1000).toLocaleString()+'（状態更新時点）';
    A('motivation-status').textContent=motivationDirty()?'未保存の設定を保持しています。状態だけ更新しました。':'保存済み設定です。定期実行自体の有効設定・間隔・日次上限も適用します。';
  }catch(error){report(error);}finally{motivationBusy=false;motivationControls();}
}
A('motivation-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!room||motivationBusy||busy||motivationData?.room!==room.id)return;
  const selected=room.id,settings=motivationForm(),revision=motivationData.settings.revision,signature=JSON.stringify([selected,settings,revision]);if(motivationOperation?.signature!==signature)motivationOperation={signature,id:crypto.randomUUID()};
  motivationBusy=true;motivationControls();let saved=false;
  try{const result=await api('/api/motivation-save',{room:selected,settings,revision,request_id:motivationOperation.id});if(room?.id===selected){motivationData.settings=result;motivationBaseline=JSON.stringify(motivationForm());motivationOperation=null;}saved=true;}catch(error){report(error);}finally{motivationBusy=false;motivationControls();}
  if(saved&&room?.id===selected)await motivationLoad(true);
});
A('motivation-reload').addEventListener('click',()=>motivationLoad(true));A('motivation-refresh').addEventListener('click',()=>motivationLoad());

let internalResetData=null,internalResetOperation=null;
function internalResetControls(){const locked=motivationBusy||busy;A('internal-reset-load').disabled=locked||!room;for(const id of ['internal-reset-questions','internal-reset-confirm'])A(id).disabled=locked||internalResetData?.room!==room?.id;A('internal-reset-submit').disabled=locked||internalResetData?.room!==room?.id||!A('internal-reset-confirm').checked;}
async function internalResetLoad(){if(!room||motivationBusy||busy)return;motivationBusy=true;motivationControls();internalResetControls();try{const selected=room.id;const data=await api('/api/internal-state-reset?room='+encodeURIComponent(selected));if(room.id!==selected)return;internalResetData=data;internalResetOperation=null;A('internal-reset-confirm').checked=false;A('internal-reset-status').textContent='対象：'+room.name+' / 未解決の問い '+data.question_count+'件。変更前の状態を保存してから実行します。';}catch(e){report(e);}finally{motivationBusy=false;motivationControls();internalResetControls();}}
A('internal-reset-load').onclick=internalResetLoad;
for(const id of ['internal-reset-questions','internal-reset-confirm'])A(id).onchange=internalResetControls;
A('internal-reset-submit').onclick=async()=>{if(!internalResetData||internalResetData.room!==room?.id||!A('internal-reset-confirm').checked||motivationBusy||busy)return;const selected=room.id,questions=A('internal-reset-questions').checked;const signature=JSON.stringify([selected,internalResetData.revision,questions]);if(internalResetOperation?.signature!==signature)internalResetOperation={signature,id:crypto.randomUUID()};motivationBusy=true;motivationControls();internalResetControls();let saved=false;try{const result=await api('/api/internal-state-reset',{room:selected,revision:internalResetData.revision,include_questions:questions,confirmed:true,request_id:internalResetOperation.id});A('internal-reset-status').textContent='リセットしました。保管した問い '+result.archived_questions+'件。';A('internal-reset-confirm').checked=false;internalResetData=null;saved=true;notice('内部状態をリセットしました。');}catch(e){report(e);}finally{motivationBusy=false;motivationControls();internalResetControls();}if(saved&&room?.id===selected)await motivationLoad();};
