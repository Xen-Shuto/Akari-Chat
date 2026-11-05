"use strict";
const N=id=>document.getElementById(id);
let nightRoom=null,nightConfig=null,nightBaseline='',nightRuns=[];
let manualMemoryRuns=[],manualMemoryRequest=null,manualMemoryPolling=false;
const nightKinds=['diary','episode','dream','identity','notepad'];
function nightInput(raw){try{const input=JSON.parse(raw);if(input.kind==='diary')return '未整理の会話 '+input.message_count+'発言'+(input.more?'（残りは次回）':'');return input.name+' / '+input.week;}catch{return '対象資料を読めません';}}
function nightForm(){return {enabled:N('night-enabled').checked,auto_save:N('night-auto-save').checked,start:N('night-start').value,end:N('night-end').value,kinds:nightKinds.filter(k=>N('night-'+k).checked),compression_targets:['weekly','monthly'].filter(mode=>N('night-cross-'+mode).checked||N('night-compress-'+mode).value).map(mode=>N('night-cross-'+mode).checked?{mode,name:N('night-prefix-'+mode).value,sources:Array.from(N('night-sources-'+mode).selectedOptions,o=>o.value)}:{mode,name:N('night-compress-'+mode).value})};}
function sleepScheduleDirty(){return !!nightConfig&&JSON.stringify(nightForm())!==nightBaseline;}
function resetSleepSchedule(id){
  if(nightRoom===id)return;nightRoom=id;nightConfig=null;nightBaseline='';
  N('night-auto-save').checked=false;N('night-enabled').checked=false;N('night-start').value='00:00';N('night-end').value='07:00';
  for(const k of nightKinds)N('night-'+k).checked=false;
  for(const mode of ['weekly','monthly']){N('night-compress-'+mode).replaceChildren(new Option('圧縮しない',''));N('night-cross-'+mode).checked=false;N('night-sources-'+mode).replaceChildren();N('night-prefix-'+mode).value=mode;}
  N('night-status').textContent='夜間設定を読み込んでください。';N('night-history').textContent='';
  nightRuns=[];manualMemoryRuns=[];manualMemoryRequest=null;N('manual-memory-status').textContent='';N('manual-memory-history').textContent='';N('manual-memory-confirm').checked=false;N('night-unknown').replaceChildren(new Option('対象なし',''));
  N('night-trace').replaceChildren(new Option('選択…',''));
}
function sleepScheduleControls(){
  const locked=busy||editorBusy||slotsBusy||lifecycleBusy;
  for(const key of ['enabled','auto-save','start','end',...nightKinds])N('night-'+key).disabled=locked||!nightConfig;
  for(const mode of ['weekly','monthly']){const cross=N('night-cross-'+mode).checked;N('night-compress-'+mode).disabled=locked||!nightConfig||cross;N('night-cross-'+mode).disabled=locked||!nightConfig;for(const kind of ['sources','prefix'])N('night-'+kind+'-'+mode).disabled=locked||!nightConfig||!cross;}
  N('night-save').disabled=locked||!nightConfig||!sleepScheduleDirty();N('night-reload').disabled=locked||!room;
  N('night-unknown').disabled=locked;N('night-ack').disabled=locked||!N('night-unknown').value;
  const manualActive=manualMemoryRuns.some(r=>['preparing','queued','running','indexing','unknown'].includes(r.state)||['failed','cancelled'].includes(r.state)&&(r.payload.operation||r.payload.step));
  N('manual-memory-incremental').disabled=locked||!nightConfig||manualActive;
  N('manual-memory-rebuild').disabled=locked||!nightConfig||manualActive||!N('manual-memory-confirm').checked;
  N('manual-memory-refresh').disabled=locked||manualMemoryPolling||!room;
  N('manual-memory-cancel').disabled=locked||!manualMemoryRuns.some(r=>['queued','running','indexing'].includes(r.state));
  N('manual-memory-ack').disabled=locked||manualMemoryRuns[0]?.state!=='unknown'||manualMemoryRuns[0]?.payload.prepared;
  N('manual-memory-resume').disabled=locked||!manualMemoryRuns[0]?.payload.prepared||!['failed','unknown','cancelled'].includes(manualMemoryRuns[0]?.state);
  N('night-trace').disabled=locked;N('night-trace-open').disabled=locked||!N('night-trace').value;
}
async function loadSleepSchedule(force=false){
  if(!room||busy||editorBusy||slotsBusy)return;
  if(sleepScheduleDirty()&&!force){N('night-status').textContent='未保存の設定があります。保存するか、破棄して再読込してください。';return;}
  const selected=room.id;busy=true;controls();
  try{
    const [data,files]=await Promise.all([api('/api/sleep-schedule?'+new URLSearchParams({room:selected})),api('/api/episode-compression-files?room='+encodeURIComponent(selected)).catch(()=>null)]);
    if(room?.id!==selected)return;
    nightRoom=selected;nightConfig=data.settings;
    nightRuns=data.runs;renderManualMemory(data.manual||[]);
    N('night-auto-save').checked=nightConfig.auto_save===true;N('night-enabled').checked=nightConfig.enabled;N('night-start').value=nightConfig.start;N('night-end').value=nightConfig.end;
    for(const k of nightKinds)N('night-'+k).checked=nightConfig.kinds.includes(k);
    for(const mode of ['weekly','monthly']){const target=(nightConfig.compression_targets||[]).find(t=>t.mode===mode),names=target?.sources||[],source=N('night-sources-'+mode);N('night-cross-'+mode).checked=!!target?.sources;N('night-prefix-'+mode).value=target?.sources?target.name:mode;source.replaceChildren(...Array.from(new Set([...(files||[]).map(f=>f.name),...names]),n=>new Option(n,n,false,names.includes(n))));const value=target&&!target.sources?target.name:'',select=N('night-compress-'+mode);select.replaceChildren(new Option('圧縮しない',''),...(files||[]).map(f=>new Option(f.name,f.name)));if(value&&!Array.from(select.options).some(o=>o.value===value))select.append(new Option(value+'（保存済み指定）',value));select.value=value;}
    nightBaseline=JSON.stringify(nightForm());N('night-status').textContent='設定を読み込みました。日時は日本時間です。'+(!files?' 圧縮ファイル一覧は未取得です。保存済みの指定は保持しています。':'');
    const states={queued:'待機中',running:'実行中',complete:'案作成完了',failed:'失敗',unknown:'結果未確認',expired:'時間帯終了',cancelled:'取消',skipped:'見送り'};
    const names={diary:'日記',episode:'エピソード',dream:'夢日記',identity:'永続記憶',notepad:'短期メモ',weekly_compression:'週次圧縮',monthly_compression:'月次圧縮'};
    N('night-history').textContent=data.runs.map(r=>`${r.night} ${names[r.kind]}${r.input_snapshot?' / '+nightInput(r.input_snapshot):''}: ${states[r.state]||r.state}${r.reviewed?'（確認済み）':''}${r.draft_id?` / 案 #${r.draft_id} ${r.draft_status}`:''}${r.error?' / '+r.error:''}${r.auto_save_state?' / 自動保存: '+r.auto_save_state:''}${r.auto_save_error?' / '+r.auto_save_error:''}`).join('\n')||'夜間の実行履歴はありません（最大50件、未確認・待機中を優先）。';
    N('night-unknown').replaceChildren(new Option('選択…',''),...data.runs.filter(r=>r.state==='unknown'&&!r.reviewed||r.auto_save_state==='unknown').map(r=>new Option(r.night+' '+names[r.kind],r.id)));
    N('night-trace').replaceChildren(new Option('選択…',''),...data.runs.filter(r=>r.timeline_id||!['queued','running'].includes(r.state)).map(r=>new Option(r.night+' '+names[r.kind]+' / '+(states[r.state]||r.state),r.id)));
  }catch(e){report(e);}finally{busy=false;controls();}
}
for(const mode of ['weekly','monthly'])for(const key of ['compress','cross','sources','prefix']){N('night-'+key+'-'+mode).addEventListener('change',()=>controls());N('night-'+key+'-'+mode).addEventListener('input',()=>controls());}
for(const key of ['enabled','auto-save','start','end',...nightKinds])N('night-'+key).addEventListener('change',()=>controls());
N('night-reload').addEventListener('click',()=>loadSleepSchedule(true));
N('night-save').addEventListener('click',async()=>{
  if(!nightConfig||busy||nightRoom!==room?.id)return;
  busy=true;controls();
  try{
    nightConfig=await api('/api/sleep-schedule',{room:nightRoom,settings:nightForm(),revision:nightConfig.revision});
    nightBaseline=JSON.stringify(nightForm());N('night-status').textContent='夜間設定を保存しました。確認待ち案は「記憶・知識 → AI更新案」で確認できます。';
  }catch(e){report(e);}finally{busy=false;controls();}
});

N('night-unknown').addEventListener('change',()=>controls());
N('night-trace').addEventListener('change',()=>controls());
N('night-trace-open').addEventListener('click',async()=>{
  const item=nightRuns.find(r=>r.id===N('night-trace').value);if(!item||busy)return;
  if(sleepScheduleDirty()||timelineDirty()){notice('未保存の設定またはタイムラインがあります。先に保存または破棄してください。');return;}
  busy=true;controls();
  try{
    const result=item.timeline_id?{timeline_id:item.timeline_id}:await api('/api/sleep-timeline',{room:nightRoom,id:item.id,revision:item.revision});
    showWorkspace('autonomy');revealPanel('autonomy-timeline');await timelineLoad(result.timeline_id);
  }catch(e){report(e);}finally{busy=false;controls();}
});
N('night-ack').addEventListener('click',async()=>{
  const item=nightRuns.find(r=>r.id===N('night-unknown').value);
  if(!item||busy)return;
  if(sleepScheduleDirty()){notice('夜間設定に未保存の変更があります。先に保存または破棄してください。');return;}
  busy=true;controls();
  try{await api(item.auto_save_state==='unknown'?'/api/night-autosave-ack':'/api/sleep-schedule-ack',{room:nightRoom,id:item.id,revision:item.auto_save_state==='unknown'?item.auto_save_revision:item.revision});}
  catch(e){report(e);}finally{busy=false;controls();}
  await loadSleepSchedule();
});


function renderManualMemory(runs){
  manualMemoryRuns=runs;
  const labels={preparing:'準備中',queued:'待機中',running:'整理中',indexing:'索引作成中',complete:'完了',cancelled:'中止',failed:'失敗',unknown:'結果未確認',reviewed:'準備失敗を確認済み'};
  const text=r=>r.payload.date+' '+(r.mode==='rebuild'?'全再整理':'差分整理')+' / '+(labels[r.state]||r.state)+' / 保存・確認済み '+r.payload.saved+'件 / 会話 '+r.payload.cursor+'～'+r.payload.maximum+(r.payload.error?' / '+r.payload.error:'')+(r.payload.archive?' / 退避先: app/data/'+r.payload.archive:'');
  N('manual-memory-status').textContent=runs.length?text(runs[0]):'手動整理の実行履歴はありません。';
  N('manual-memory-history').textContent=runs.map(text).join('\n');
}
async function refreshManualMemory(){
  if(!room||manualMemoryPolling||busy)return;
  const selected=room.id;manualMemoryPolling=true;
  try{
    const data=await api('/api/sleep-schedule?room='+encodeURIComponent(selected));
    if(room?.id===selected)renderManualMemory(data.manual||[]);
  }catch(error){if(room?.id===selected)report(error);}
  finally{manualMemoryPolling=false;controls();}
}
async function startManualMemory(mode){
  if(!nightConfig||busy||editorBusy||slotsBusy||lifecycleBusy)return;
  if(sleepScheduleDirty()||autonomyDirty()){notice('未保存の設定を保存または破棄してください。');return;}
  if(mode==='rebuild'&&!N('manual-memory-confirm').checked)return;
  const selected=room.id;
  const signature=JSON.stringify([selected,mode,nightConfig.revision]);
  if(manualMemoryRequest?.signature!==signature)manualMemoryRequest={signature,id:crypto.randomUUID()};
  busy=true;controls();
  try{
    await api('/api/manual-memory',{room:selected,mode,revision:nightConfig.revision,request_id:manualMemoryRequest.id});
    manualMemoryRequest=null;
    N('manual-memory-confirm').checked=false;
    notice('RAG設定をONで保存し、手動整理を受け付けました。');
  }catch(error){report(error);}
  finally{busy=false;controls();}
  await refreshManualMemory();
}
N('manual-memory-incremental').onclick=()=>startManualMemory('incremental');
N('manual-memory-rebuild').onclick=()=>startManualMemory('rebuild');
N('manual-memory-refresh').onclick=refreshManualMemory;
N('manual-memory-confirm').onchange=()=>controls();
for(const action of ['cancel','resume','ack']){
  N('manual-memory-'+action).onclick=async()=>{
    const run=manualMemoryRuns[0];
    if(!run||busy)return;
    busy=true;controls();
    try{await api('/api/manual-memory-'+action,{room:room.id,id:run.id,revision:run.revision});}
    catch(error){report(error);}
    finally{busy=false;controls();}
    await refreshManualMemory();
  };
}
setInterval(()=>{
 if(document.visibilityState==='visible'&&!N('workspace-room-settings').hidden&&!N('sleep-schedule-panel').hidden)refreshManualMemory();
},2500);
