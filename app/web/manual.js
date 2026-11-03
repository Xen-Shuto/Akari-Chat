(()=>{'use strict';
 const content=document.getElementById('manual-content'),status=document.getElementById('manual-status'),back=document.getElementById('manual-back'),retry=document.getElementById('manual-retry');
 let ticket=0,controller=null,ownDepth=0;
 const appearanceRoom=new URLSearchParams(location.search).get('room')||'';
 let appearanceTicket=0;
 function manualURL(doc='MANUAL.md',hash=''){
  const params=new URLSearchParams({doc});if(appearanceRoom)params.set('room',appearanceRoom);
  return location.origin+'/manual?'+params+hash;
 }
 async function refreshAppearance(){
  const serial=++appearanceTicket;
  try{
   let response=await fetch('/api/appearance?'+new URLSearchParams({room:appearanceRoom}),{cache:'no-store'});
   if(!response.ok&&appearanceRoom)response=await fetch('/api/appearance',{cache:'no-store'});
   if(!response.ok)return;const data=await response.json();if(serial!==appearanceTicket)return;
   for(const [key,value] of Object.entries(AkariAppearanceTokens(data.effective)))document.documentElement.style.setProperty(key,value);
  }catch{/* Keep the current readable theme if the app is temporarily unavailable. */}
 }
 const valid=name=>/^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.md$/.test(name);
 function reference(raw,doc){
  // Markdown links to source files/README outside the manual are plain text.
  if(/^[a-z][a-z0-9+.-]*:/i.test(raw))return raw;
  if(raw.startsWith('//')||raw.startsWith('/')||raw.includes('\\'))return null;
  const parts=doc.split('/').slice(0,-1);
  for(const part of raw.split(/[?#]/,1)[0].split('/')){
   if(part==='..'){if(!parts.length)return null;parts.pop();}else if(part&&part!=='.')parts.push(part);
  }
  let target;try{target=new URL(raw,'https://manual.invalid/'+doc);}catch{return null;}
  let name;try{name=decodeURIComponent(target.pathname.slice(1));}catch{return null;}
  if(!valid(name)||target.search)return null;
  return manualURL(name,target.hash);
 }
 function headings(){
  const used=new Map();
  for(const h of content.querySelectorAll('h1,h2,h3,h4,h5,h6')){
   const base=h.textContent.trim().toLowerCase().replace(/[^\p{Letter}\p{Number}\p{Mark}_\-\s]/gu,'').replace(/\s+/g,'-');
   const count=used.get(base)||0;used.set(base,count+1);h.id=base+(count?'-'+count:'');
  }
 }
 function scroll(){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}
  const heading=id&&document.getElementById(id);if(heading&&content.contains(heading))heading.scrollIntoView();else window.scrollTo(0,0);
 }
 async function load(){
  const serial=++ticket;controller?.abort();controller=new AbortController();
  const doc=new URLSearchParams(location.search).get('doc')||'MANUAL.md';
  content.replaceChildren();content.setAttribute('aria-busy','true');status.hidden=false;status.className='';status.textContent='読み込み中…';retry.hidden=true;back.disabled=ownDepth===0;
  try{
   if(!valid(doc))throw Error('マニュアルの指定が不正です。');
   const response=await fetch('/manual/document?'+new URLSearchParams({doc}),{signal:controller.signal,cache:'no-store'}),data=await response.json();
   if(!response.ok)throw Error(data.error||'マニュアルを読み込めません。');
   if(serial!==ticket)return;
   // A fresh body prevents the renderer cache from keeping a previous document's links.
   const body=document.createElement('div');AkariMarkdown.render(body,data.text,{resolveLink:raw=>reference(raw,data.path)});content.append(body);
   for(const a of content.querySelectorAll('a')){const u=new URL(a.href);if(u.origin===location.origin&&u.pathname==='/manual')a.removeAttribute('target');}
   headings();document.title=(content.querySelector('h1')?.textContent||'マニュアル')+' | あかり';status.hidden=true;scroll();
  }catch(error){if(serial!==ticket||error.name==='AbortError')return;status.className='error';status.textContent=error.message;retry.hidden=false;}
  finally{if(serial===ticket)content.setAttribute('aria-busy','false');}
 }
 document.addEventListener('click',event=>{
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  const a=event.target.closest('a');if(!a)return;const u=new URL(a.href);
  if(u.origin!==location.origin||u.pathname!=='/manual')return;
  event.preventDefault();history.pushState({manualDepth:++ownDepth},'',u);load();
 });
 back.onclick=()=>history.back();retry.onclick=load;window.addEventListener('popstate',event=>{ownDepth=event.state?.manualDepth||0;load();});
 for(const link of document.querySelectorAll('.manual-toolbar a'))link.href=manualURL();
 window.addEventListener('focus',refreshAppearance);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshAppearance();});
 history.replaceState({manualDepth:0},'',location.href);refreshAppearance();load();
})();
