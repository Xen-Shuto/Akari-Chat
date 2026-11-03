"use strict";
(()=>{
 let plan=null,pending=false,version=0,selectedRoom=null;
 const target=()=>document.getElementById('image-job-target'),status=()=>document.getElementById('image-job-link-status'),check=()=>document.getElementById('image-job-link-confirm');
 function controls(){const locked=busy||imageBusy||lifecycleBusy||pending;target().disabled=locked;check().disabled=locked||!target().value;document.getElementById('image-job-target-refresh').disabled=locked;}
 function reset(){version++;plan=null;pending=false;target().value='';check().checked=false;status().textContent='選択しなければ画像だけを保存します。';controls();}
 function roomChanged(){if(selectedRoom===room?.id)return;selectedRoom=room?.id;reset();target().replaceChildren(new Option('関連付けない',''));}
 async function read(){const id=target().value;if(!room||!id){reset();return;}const selected=room.id,request=++version;plan=null;pending=true;check().checked=false;controls();
  try{const data=await api('/api/lifestyle-image-link?'+new URLSearchParams({room:selected,target_key:id}));if(request!==version||room?.id!==selected||target().value!==id)return;if(data.target.missing)throw new Error('現在の対象がありません。');plan={room:selected,target_key:id,revision:data.revision};status().textContent='関連先：'+data.target.label+(data.binding?' ／ 現在の画像「'+data.binding.image_label+'」を差し替えます。':' ／ 現在は未関連付けです。')+' 対象や関連画像が変わった場合は自動反映を見送ります。';}
  catch(error){if(request===version){status().textContent=error.message;report(error);}}
  finally{if(request===version){pending=false;controls();}}
 }
 async function load(){if(!room||pending)return;imageOperation=null;const selected=room.id,prior=target().value,request=++version;pending=true;controls();
  try{const data=await api('/api/lifestyle-image-options?room='+encodeURIComponent(selected));if(request!==version||room?.id!==selected)return;target().replaceChildren(new Option('関連付けない',''),...data.targets.filter(t=>!t.missing).map(t=>new Option(t.label,t.key)));if(data.targets.some(t=>t.key===prior&&!t.missing))target().value=prior;plan=null;check().checked=false;}
  finally{if(request===version){pending=false;controls();}}
  if(target().value)await read();else status().textContent='選択しなければ画像だけを保存します。';
 }
 function request(){if(!target().value)return null;if(pending||!plan||plan.room!==room?.id||plan.target_key!==target().value||!check().checked)throw new Error('生成後の関連先と差し替え内容を確認してください。');return {target_key:plan.target_key,revision:plan.revision};}
 target().addEventListener('change',()=>{imageOperation=null;read().catch(report);});check().addEventListener('change',()=>imageOperation=null);
 document.getElementById('image-job-target-refresh').addEventListener('click',()=>load().catch(report));
 document.querySelector('[data-panel="images-panel"]').addEventListener('panelactivate',()=>{if(!target().value)load().catch(report);});
 window.AkariImageLinkJobs={controls,roomChanged,request,accepted:reset,dirty:()=>!!target().value};
})();
