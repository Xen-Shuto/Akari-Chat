"use strict";
(()=>{
 let selectedRoom=null,doc=null,loading=false,writing=false,sequence=0,operation=null;
 const el=id=>document.getElementById('image-link-'+id);
 const form=()=>[el('target').value,el('image').value];
 function dirty(){return !!el('image').value&&el('image').value!==(doc?.target.key===el('target').value?doc?.binding?.image_id||'':'');}
 function matches(){return !!doc&&!!room&&doc.room===room.id&&doc.target.key===el('target').value&&(doc.candidate?.id||'')===el('image').value;}
 function controls(){const locked=busy||imageBusy||lifecycleBusy||loading||writing;
  for(const element of $('image-link-panel').querySelectorAll('button,select'))element.disabled=locked;
  el('save').disabled=locked||!matches()||!doc.candidate||doc.target.missing;
  el('clear').disabled=locked||!matches()||!doc.binding||dirty();
  el('reference').disabled=locked||!matches()||!doc.binding||!doc.available||doc.target_changed||doc.image_changed||dirty();
  el('history-load').disabled=locked||!el('history-image').value;
 }
 function option(select,id,label){if(id&&!Array.from(select.options).some(o=>o.value===id))select.append(new Option(label||id,id));}
 function roomChanged(){if(selectedRoom===room?.id)return;selectedRoom=room?.id;sequence++;doc=null;operation=null;loading=false;writing=false;el('target').replaceChildren(new Option('選択…',''));el('image').replaceChildren(new Option('選択…',''));el('preview').replaceChildren();el('status').textContent='対象と画像を選択してください。';el('history').replaceChildren();el('history-image').replaceChildren(new Option('選択…',''));controls();}
 async function list(){if(!room||loading||writing)return;if(dirty()){notice('画像の候補を保存または破棄してください。');return;}roomChanged();const selected=room.id,request=++sequence,prior=form();loading=true;controls();
  try{const data=await api('/api/lifestyle-image-options?room='+encodeURIComponent(selected));if(room?.id!==selected||request!==sequence||JSON.stringify(form())!==JSON.stringify(prior))return;
   el('target').replaceChildren(new Option('選択…',''),...data.targets.map(t=>new Option(t.label,t.key)));if(data.targets.some(t=>t.key===prior[0]))el('target').value=prior[0];
   el('image').replaceChildren(new Option('選択…',''),...data.images.map(i=>new Option(i.label,i.id)));option(el('image'),prior[1],'選択中の画像');el('image').value=prior[1];
  }finally{if(request===sequence){loading=false;controls();}}
  if(el('target').value)await read();
 }
 async function read(){if(!room||!el('target').value)return;const selected=room.id,values=form(),request=++sequence;loading=true;controls();
  try{const data=await api('/api/lifestyle-image-link?'+new URLSearchParams({room:selected,target_key:values[0],image_id:values[1]}));if(room?.id!==selected||request!==sequence||JSON.stringify(form())!==JSON.stringify(values))return;doc=data;operation=null;
   if(!values[1]&&data.binding&&data.available){option(el('image'),data.binding.image_id,data.binding.image_label);el('image').value=data.binding.image_id;loading=false;return await read();}
   paint();const history=await api('/api/lifestyle-image-history?'+new URLSearchParams({room:selected,target_key:values[0]}));if(room?.id!==selected||request!==sequence||JSON.stringify(form())!==JSON.stringify(values))return;
   el('history').textContent=history.map(h=>h.created+' / '+(h.after?'関連付け':'解除')).join('\n')||'履歴はありません。';el('history-image').replaceChildren(new Option('選択…',''));for(const h of history)for(const value of [h.before,h.after])if(value)option(el('history-image'),value.image_id,value.image_label||value.image_id);
  }catch(error){if(room?.id===selected&&request===sequence){el('status').textContent=error.message+' 候補は保持しています。';report(error);}}
  finally{if(request===sequence){loading=false;controls();}}
 }
 function paint(){const area=el('preview');area.replaceChildren();
  if(doc.available){const image=document.createElement('img');image.src=doc.url;image.alt='関連付け中の画像';area.append(image);}
  if(doc.candidate&&doc.candidate.id!==doc.binding?.image_id){const image=document.createElement('img');image.src='/api/generated-image?'+new URLSearchParams({room:doc.room,id:doc.candidate.id});image.alt='関連付け候補の画像';area.append(image);}
  el('status').textContent=doc.target.missing?'関連先が現在見つかりません。関連付けの解除は可能です。':doc.target_changed?'対象の内容が変更されています。以前の画像との関連です。確認して再関連付けしてください。':doc.binding&&!doc.available?'関連画像の実体を確認できません。解除または別の候補を選んでください。':doc.binding?'画像が関連付けられています。':'未関連付けです。対象と候補を確認して保存してください。';
 }
 async function change(action){if(!matches()||writing||busy||imageBusy||lifecycleBusy)return;const selected=room.id,payload={room:selected,target_key:doc.target.key,image_id:action==='clear'?'':el('image').value,revision:action==='clear'?doc.clear_revision:doc.revision,action},signature=JSON.stringify(payload);if(operation?.signature!==signature)operation={signature,id:crypto.randomUUID()};writing=true;busy=true;controls();
  try{await api('/api/lifestyle-image-link',{...payload,request_id:operation.id});if(room?.id!==selected)return;operation=null;doc=null;if(action==='clear')el('image').value='';await read();if(!doc){el('status').textContent=action==='clear'?'関連付けの解除は完了しました。対象一覧を更新してください。':'関連付けの保存は完了しました。表示を更新してください。';return;}el('status').textContent=action==='clear'?'関連付けを解除しました。画像本体と履歴は保持しています。':'画像を関連付けました。画像本体と原本は変更していません。';}
  catch(error){report(error);}finally{writing=false;busy=false;window.controls?.();controls();}
 }
 async function pick(id){if(!room||writing||loading)return;if(dirty()&&el('image').value!==id){notice('選択中の画像候補を保存または破棄してください。');return;}await list();option(el('image'),id,'ギャラリーで選んだ画像');el('image').value=id;$('image-link-panel').open=true;operation=null;if(el('target').value)await read();else el('status').textContent='関連先を選んでください。';controls();}
 el('target').addEventListener('change',()=>{const pending=!!el('image').value&&el('image').value!==(doc?.binding?.image_id||'');if(pending&&doc&&doc.target.key!==el('target').value){el('target').value=doc.target.key;notice('画像候補を保存または破棄してから関連先を変更してください。');return;}if(doc&&!pending)el('image').value='';operation=null;doc=null;read().catch(report);controls();});
 el('image').addEventListener('change',()=>{operation=null;read().catch(report);controls();});el('refresh').addEventListener('click',()=>list().catch(report));
 el('save').addEventListener('click',()=>change('link').catch(report));el('clear').addEventListener('click',()=>change('clear').catch(report));
 el('discard').addEventListener('click',()=>{el('image').value='';operation=null;read().catch(report);controls();});
 el('history-load').addEventListener('click',()=>{if(dirty()){notice('現在の候補を先に保存または破棄してください。');return;}const id=el('history-image').value;if(!id)return;option(el('image'),id,'履歴の画像');el('image').value=id;read().catch(report);controls();});
 el('history-image').addEventListener('change',controls);
 el('reference').addEventListener('click',async()=>{if(!matches()||!doc.available||doc.target_changed||doc.image_changed)return;const snapshot=doc,values=form();try{const fresh=await api('/api/lifestyle-image-reference?'+new URLSearchParams({room:snapshot.room,target_key:values[0],image_id:values[1],revision:snapshot.revision}));if(room?.id!==snapshot.room||JSON.stringify(form())!==JSON.stringify(values))return;if(fresh.id!==snapshot.binding.image_id)throw new Error('関連画像が変わりました。更新して確認してください。');const select=$('image-references'),ids=new Set(Array.from(select.selectedOptions,o=>o.value));ids.add(snapshot.binding.image_id);if(ids.size>4)throw new Error('参照画像は4枚までです。先に選択を減らしてください。');option(select,snapshot.binding.image_id,snapshot.binding.image_label);for(const opt of select.options)if(ids.has(opt.value)){opt.selected=true;if(opt.value===snapshot.binding.image_id)opt.dataset.room=room.id;}select.dispatchEvent(new Event('input',{bubbles:true}));notice('参照欄へ追加しました。生成先の画像入力対応を確認してから予約してください。');}catch(error){report(error);}});
 $('image-link-panel').addEventListener('toggle',()=>{if($('image-link-panel').open&&!loading&&!writing&&!dirty())list().catch(report);});
 document.querySelector('[data-panel="images-panel"]').addEventListener('panelactivate',()=>{if($('image-link-panel').open&&!loading&&!writing&&!dirty())list().catch(report);});
 window.AkariLifestyleImages={controls,dirty,roomChanged,pick};
})();
