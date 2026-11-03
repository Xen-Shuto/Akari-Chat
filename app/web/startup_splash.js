"use strict";
(()=>{
 const token=new URLSearchParams(location.search).get('token');
 if(!token){location.replace('/');return;}
 history.replaceState(null,'','/');
 let release,mainLoaded=false,active=true;
 const ready=new Promise(resolve=>release=resolve);
 window.AkariStartup={ready,get active(){return active;},mainReady(){mainLoaded=true;},mainFailed(){document.getElementById('status').textContent='画面の準備に失敗しました。再読み込みするか、コンソールを確認してください。';}};
 const overlay=document.getElementById('splash'),locked=new Map();
 const lock=node=>{if(node.nodeType===1&&node!==overlay){if(!locked.has(node))locked.set(node,node.inert);node.inert=true;}};
 for(const node of document.body.children)lock(node);
 const observer=new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)if(node.parentNode===document.body)lock(node);});
 observer.observe(document.body,{childList:true});overlay.focus({preventScroll:true});
 const events=['keydown','keyup','keypress','click','dblclick','pointerdown','pointerup','mousedown','mouseup','touchstart','touchmove','wheel','submit','dragover','drop'];
 const block=event=>{if(active){event.preventDefault();event.stopImmediatePropagation();}};
 for(const name of events)window.addEventListener(name,block,{capture:true,passive:false});
 let shown=performance.now(),loaded=false,leaving=false,mediaStarted=false;
 const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 async function finish(){
  if(leaving)return;leaving=true;
  await wait(Math.max(0,1000-(performance.now()-shown)));
  overlay.classList.add('leaving');await wait(500);
  observer.disconnect();active=false;
  for(const [node,inert] of locked)if(node.isConnected)node.inert=inert;
  for(const name of events)window.removeEventListener(name,block,true);
  overlay.remove();
 }
 function showMedia(kind){
  mediaStarted=true;const el=document.createElement(kind==='video'?'video':'img');
  const ready=()=>{if(loaded)return;loaded=true;shown=performance.now();};
  if(kind==='video'){el.muted=true;el.autoplay=true;el.loop=true;el.playsInline=true;el.addEventListener('loadeddata',ready,{once:true});}
  else{el.alt='起動スプラッシュ';el.addEventListener('load',ready,{once:true});}
  el.addEventListener('error',()=>{el.remove();ready();},{once:true});
  el.src='/startup-splash/media?token='+encodeURIComponent(token);document.getElementById('media').append(el);
  setTimeout(ready,3000);
 }
 (async()=>{
  while(!leaving){
   try{
    const response=await fetch('/startup-splash/status?token='+encodeURIComponent(token),{cache:'no-store'});
    if(!response.ok)throw Error('起動状態を確認できません');
    const state=await response.json();
    if(!mediaStarted){if(state.media)showMedia(state.media);else{mediaStarted=true;loaded=true;}}
    if(state.failed){document.getElementById('status').textContent='起動に失敗しました。コンソールを確認してください。';return;}
    if(state.initialized)release();
    if(state.done&&loaded&&mainLoaded){await finish();return;}
   }catch(error){document.getElementById('status').textContent='起動状態を確認しています…';}
   await wait(150);
  }
 })();
})();
