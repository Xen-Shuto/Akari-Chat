(()=>{
const keys=['youtube','spotify','bandcamp','soundcloud'],form=$('music-settings-form'),save=$('music-settings-save'),reload=$('music-settings-reload'),status=$('music-settings-status');
let snapshot=null,working=false,serial=0,operation=null;
const values=()=>Object.fromEntries(keys.map(k=>[k,k==='youtube'||$('music-'+k).checked]));
function dirty(){return !!snapshot&&JSON.stringify(values())!==JSON.stringify(snapshot.values);}
function controls(){for(const k of keys)$('music-'+k).disabled=k==='youtube'||working||!snapshot;save.disabled=working||!snapshot||!dirty();reload.disabled=working;}
function paint(data){snapshot=data;for(const k of keys)$('music-'+k).checked=k==='youtube'||data.values[k];operation=null;status.textContent='全ルーム共通の設定です。保存後の推薦から反映します。';controls();}
async function refresh(force=false){if(working||dirty()&&!force)return;const ticket=++serial;working=true;controls();try{const result=await api('/api/music-settings');if(ticket===serial)paint(result);}catch(e){report(e);}finally{if(ticket===serial)working=false;controls();}}
form.onchange=()=>{status.textContent=dirty()?'未保存の変更があります。':'保存済みの設定です。';controls();};
form.onsubmit=async event=>{event.preventDefault();if(working||!dirty())return;const body={values:values(),revision:snapshot.revision},signature=JSON.stringify(body);if(operation?.signature!==signature)operation={signature,id:crypto.randomUUID()};working=true;controls();try{paint(await api('/api/music-settings',{...body,request_id:operation.id}));notice('音楽検索の設定を保存しました。');}catch(e){report(e);}finally{working=false;controls();}};
reload.onclick=()=>refresh(true);document.querySelector('[data-panel="music-settings-panel"]').addEventListener('panelactivate',()=>refresh());
window.AkariMusic={dirty,refresh};controls();
})();
