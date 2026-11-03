(()=>{
 const el=id=>document.getElementById(id);let operation=null,loading=false;
 async function refresh(){
  if(!room)return;const selected=room.id,data=await api('/api/action-reservations?room='+encodeURIComponent(selected));if(room.id!==selected)return;
  el('action-list').replaceChildren();
  for(const item of data.entries){const row=document.createElement('div');row.className='schedule-card';const text=document.createElement('p');text.textContent=item.kind+' · '+item.intent+' · '+new Date(item.due*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'})+' JST · '+item.state+(item.run_state?' / 実行:'+item.run_state:'');row.append(text);if(item.state!=='cancelled'){const button=document.createElement('button');button.textContent='予約を取消';button.onclick=async()=>{try{await api('/api/action-reservation-cancel',{room:selected,id:item.id,revision:item.revision});await refresh();}catch(e){report(e);}};row.append(button);}el('action-list').append(row);}
  if(!data.entries.length)el('action-list').textContent='行動予約はありません。';
 }
 el('action-form').addEventListener('submit',async event=>{event.preventDefault();if(loading||!room)return;const at=el('action-at').value;const days=el('action-days').value.trim();
  const payload={room:room.id,kind:el('action-kind').value,intent:el('action-intent').value,details:el('action-details').value,at:at+'+09:00',weekdays:days?days.split(',').map(v=>Number(v.trim())):[]};const key=JSON.stringify(payload);if(operation?.key!==key)operation={key,id:crypto.randomUUID()};loading=true;el('action-create').disabled=true;
  try{await api('/api/action-reservation-create',{...payload,request_id:operation.id});operation=null;el('action-status').textContent='予約を保存しました。実行はまだです。';await refresh();}catch(e){report(e);}finally{loading=false;el('action-create').disabled=false;}
 });
 el('action-refresh').onclick=()=>refresh().catch(report);document.querySelector('[data-panel="time-panel"]').addEventListener('panelactivate',()=>refresh().catch(report));
window.AkariActionReservations={refresh};
})();
