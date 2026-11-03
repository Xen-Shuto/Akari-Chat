/* Local-only Markdown display. Source text remains unchanged in storage/editors. */
(()=>{'use strict';
 const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const parser=new marked.Marked({gfm:true,breaks:true,async:false,renderer:{
  html:token=>escape(token.text),
  image:token=>escape('[画像: '+(token.text||'画像')+']')
 }});
 const policy={RETURN_DOM_FRAGMENT:true,ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false,
  ALLOWED_TAGS:['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','del','blockquote','pre','code','ul','ol','li','a','table','thead','tbody','tr','th','td','input'],
  ALLOWED_ATTR:['href','title','start','align','type','checked','disabled']};
 const states=new WeakMap();
 function apply(body,text,state,resolveLink=null){
  if(state.text===text&&state.resolveLink===resolveLink)return;
  try{
   const fragment=DOMPurify.sanitize(parser.parse(text),policy);
   for(const link of fragment.querySelectorAll('a')){
    let url;try{const raw=link.getAttribute('href')||'';url=new URL(resolveLink?resolveLink(raw):raw);}catch{}
    if(!url||!['http:','https:'].includes(url.protocol)||url.username||url.password){link.replaceWith(...link.childNodes);continue;}
    link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';
   }
   for(const input of fragment.querySelectorAll('input')){input.type='checkbox';input.disabled=true;input.tabIndex=-1;}
   for(const child of [...fragment.childNodes])if(child.nodeType===3&&!child.textContent.trim())child.remove();
   const thinking=body.querySelector('.thinking-indicator');
   body.replaceChildren(fragment);
   if(thinking&&body.classList.contains('is-thinking'))body.append(thinking);
  }catch{body.textContent=text;}
  body.classList.add('markdown-body');state.text=text;state.resolveLink=resolveLink;state.time=performance.now();
 }
 function render(body,text,{streaming=false,resolveLink=null}={}){
  text=String(text??'');let state=states.get(body);
  if(!state){state={text:null,time:0,timer:null,pending:''};states.set(body,state);}
  state.pending=text;state.pendingResolveLink=resolveLink;
  if(!streaming||performance.now()-state.time>=80){clearTimeout(state.timer);state.timer=null;apply(body,text,state,resolveLink);}
  else if(state.timer===null)state.timer=setTimeout(()=>{state.timer=null;apply(body,state.pending,state,state.pendingResolveLink);},80-(performance.now()-state.time));
 }
 window.AkariMarkdown={render};
})();
