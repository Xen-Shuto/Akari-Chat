"use strict";
(() => {
  const E=id=>document.getElementById(id);
  let selected=null,config=null,baseline="",saving=false,ticket=0,lastLoad=0;
  const form=()=>({enabled:E("sleep-enabled").checked,start:E("sleep-start").value,end:E("sleep-end").value});
  const dirty=()=>!!config&&JSON.stringify(form())!==baseline;
  function controls(){
    const locked=busy||editorBusy||slotsBusy||lifecycleBusy||saving;
    for(const id of ["enabled","start","end"])E("sleep-"+id).disabled=locked||!config||selected!==room?.id;
    E("sleep-save").disabled=locked||!config||selected!==room?.id||!dirty();
    E("sleep-reload").disabled=locked||!room;
  }
  function fill(data){
    config=data.settings;E("sleep-enabled").checked=config.enabled;E("sleep-start").value=config.start;E("sleep-end").value=config.end;
    baseline=JSON.stringify(form());
    E("sleep-policy-status").textContent=!config.enabled?"睡眠時間は無効です。":data.sleeping?"睡眠時間中です。定期自律と短いお知らせを控え、記憶整理を優先します。":"起床時間中です。睡眠時間は日本時間で判定します。";
    controls();
  }
  async function load(discard=false){
    if(!room||saving)return;
    const id=room.id;
    if(selected===id&&config&&!discard&&(dirty()||Date.now()-lastLoad<30000))return;
    if(dirty()&&!discard&&selected===id)return;
    const current=++ticket;
    try{
      const data=await api("/api/sleep-policy?room="+encodeURIComponent(id));
      if(current!==ticket||room?.id!==id||(!discard&&selected===id&&dirty()))return;
      lastLoad=Date.now();selected=id;fill(data);
    }catch(error){report(error);}
  }
  for(const id of ["enabled","start","end"])E("sleep-"+id).addEventListener("input",controls);
  E("sleep-reload").addEventListener("click",()=>load(true));
  E("sleep-save").addEventListener("click",async()=>{
    if(!config||saving||selected!==room?.id)return;
    const id=selected;const values=form();saving=true;controls();
    try{
      const data=await api("/api/sleep-policy",{room:id,settings:values,revision:config.revision});
      if(room?.id===id&&selected===id){fill(data);E("sleep-policy-status").textContent="睡眠時間を保存しました。未保存の夜間整理・お知らせ設定は再読込して確認してください。";}
    }catch(error){report(error);}finally{saving=false;controls();}
  });
  window.AkariSleep={load,dirty,controls};
})();
