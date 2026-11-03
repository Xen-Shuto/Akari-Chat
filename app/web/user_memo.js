"use strict";
let memoRoom=null,memoDoc=null,memoBaseline='',memoBusy=false,memoTicket=0;
function userMemoDirty(){return !!memoRoom&&A('user-memo-text').value!==memoBaseline;}
async function userMemoLoad(discard=false){
  if(!room)return;const selected=room.id,before=A('user-memo-text').value,ticket=++memoTicket;
  const doc=await api('/api/user-memo?room='+encodeURIComponent(selected));if(room?.id!==selected||ticket!==memoTicket)return;
  if(memoRoom===selected&&!discard&&userMemoDirty()){A('user-memo-status').textContent='未保存の入力を保持しています。最新の内容を読むには破棄して再読込してください。';return;}
  if(A('user-memo-text').value!==before)return;
  memoRoom=selected;memoDoc=doc;A('user-memo-text').value=doc.text;memoBaseline=A('user-memo-text').value;A('user-memo-save').disabled=!doc.editable;
  A('user-memo-status').textContent=doc.exists?'保存済み。通常の自律行動へ渡します。自動消去しません。':'書き置きはまだありません。';
}
A('user-memo-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!room||!memoDoc||memoRoom!==room.id||memoBusy)return;memoBusy=true;
  const selected=room.id,text=A('user-memo-text').value;A('user-memo-save').disabled=true;
  try{const result=await api('/api/user-memo-save',{room:selected,text,revision:memoDoc.revision});if(room?.id===selected){memoDoc=result.document;memoBaseline=result.document.text.replace(/\r\n?/g,'\n');if(A('user-memo-text').value===text)A('user-memo-text').value=memoBaseline;A('user-memo-status').textContent=result.changed?'書き置きを保存しました。変更前の原本はバックアップ済みです。':'同じ内容なので変更していません。';}}catch(error){report(error);}finally{memoBusy=false;A('user-memo-save').disabled=!memoDoc?.editable;}
});
A('user-memo-refresh').addEventListener('click',()=>userMemoLoad().catch(report));
A('user-memo-discard').addEventListener('click',()=>{if(!memoBusy)userMemoLoad(true).catch(report);});
document.querySelector('[data-panel="user-memo-panel"]').addEventListener('panelactivate',()=>userMemoLoad().catch(report));
