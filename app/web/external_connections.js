(()=>{
const names={google:'Googleカレンダー',x:'X / Twitter',discord:'Discord'},forms=[];
let snapshot=null,version=0,working=false,loading=0,operationDirty=false;
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
function input(parent,label,type='text'){const l=node('label',label),e=node(type==='textarea'?'textarea':'input');if(type!=='textarea')e.type=type;l.append(e);parent.append(l);return e;}
function button(parent,text,run){const e=node('button',text);e.type='button';parent.append(e);e.addEventListener('click',()=>run().catch(report));return e;}
function values(f){const out={};for(const [k,e] of Object.entries(f.fields)){if(e.type==='checkbox')out[k]=e.checked;else if(e.type==='number')out[k]=Number(e.value);else if(k==='channels'||k==='allowed_user_ids')out[k]=e.value.split(/[\s,]+/).filter(Boolean);else out[k]=e.value;}if(out.clear_secrets)for(const k of ['token','refresh_token','client_secret'])if(k in out)out[k]='';return out;}
function settingsDirty(){return forms.some(f=>f.base&&JSON.stringify(values(f))!==f.base);}
function configDirty(){return settingsDirty()||forms.some(f=>f.cookie?.value.trim());}
function xMode(f){if(f.provider!=='x')return;f.apiGroup.hidden=f.fields.mode.value!=='api';f.cookieGroup.hidden=f.fields.mode.value==='api';}
function dirty(){return working||operationDirty||configDirty();}
function state(){const hosts=[xPosts,...forms.map(f=>f.host),$('external-operation'),$('external-discord-controls')].filter(Boolean);for(const host of hosts)for(const e of host.querySelectorAll('input,select,textarea,button'))e.disabled=working||!!loading||!snapshot;}
function discordRequired(f){if(f.provider!=='discord')return;for(const key of ['default_channel','user_id'])f.fields[key].required=f.fields.enabled.checked;}
function paint(data,only=null,editorToken=''){snapshot=data;for(const f of forms){if(only&&f!==only)continue;const config=data[f.scope]?.[f.provider];if(!config)continue;f.operation=null;for(const [k,e] of Object.entries(f.fields)){if(e.type==='checkbox')e.checked=config.values[k]??(k==='approval_for_replies');else e.value=Array.isArray(config.values[k])?config.values[k].join(' '):config.values[k]??(k==='mode'?'api':k==='receive_mode'?'rest':k==='reply_mode'?'mentions':k==='reply_max_per_day'?20:'');}if(f.provider==='x'){f.cookieRead++;f.cookie.value='';f.cookieFile.value='';xMode(f);}if(f.provider==='discord')f.fields.token.value=config.values.token_saved?editorToken:'';discordRequired(f);f.revision=config.revision;f.base=JSON.stringify(values(f));f.status.textContent=['token','refresh_token','client_secret','browser_state'].filter(k=>config.values[k+'_saved']&&(f.provider!=='x'||(f.fields.mode.value==='api'?k!=='browser_state':k==='browser_state'))).map(k=>({token:'トークン保存済み',refresh_token:'更新トークン保存済み',client_secret:'クライアント秘密鍵保存済み',browser_state:'Cookie保存済み'})[k]).join(' / ')+(config.values.verified_account?' ／ 確認済みアカウントID：'+config.values.verified_account:'');if(f.scope==='room'&&f.provider!=='google'){const duplicates=data.duplicates?.[f.provider]||[];if(f.provider==='discord'&&config.values.enabled&&(!config.values.default_channel||!config.values.user_id))f.status.textContent+=' ／ デフォルトのチャンネルIDとユーザIDを設定して保存してください。';f.status.textContent+=(duplicates.length?' ／ 他ルームと登録が重複：'+duplicates.map(m=>m.room+(m.archived?' [保管]':'')+'（'+m.reason+'）').join('、'):' ／ 他ルームとの重複は検出されていません（未確認のアカウントIDは判定対象外）');}}
 const received=$('external-discord-received');received.textContent=JSON.stringify(data.discord||{},null,2);const actions=$('discord-reply-controls');actions.replaceChildren();for(const item of data.discord?.replies||[]){if(item.state==='unknown'&&!item.reviewed||['queued','generating'].includes(item.state)){const action=item.state==='unknown'?'acknowledge':'cancel';button(actions,item.state==='unknown'?'送信先を確認済みにする：'+item.id:'自動返信を中止：'+item.id,async()=>{await api('/api/external-discord-reply',{room:room.id,id:item.id,action});await reload(true);});}}
 const host=$('external-outbox');host.replaceChildren();
 for(const item of data.outbox||[]){
  if(item.provider==='x')continue;
  const box=outboxCard(item);box.dataset.provider=item.provider;box.hidden=item.provider!==provider.value;host.append(box);
 }
 xPostRefresh++;renderXPosts(data.outbox||[]);state();
}
function outboxCard(item){
 const box=node('article',null,'external-card');
 if(item.provider==='x'){
  box.append(node('h4','投稿内容'),node('pre',item.payload.text||''));
  for(const [key,label] of [['reply_to','返信先'],['quote_post','引用元']]){
   if(!item.payload[key])continue;
   const link=node('a',label+'：'+item.payload[key]);link.href='https://x.com/i/status/'+encodeURIComponent(item.payload[key]);link.target='_blank';link.rel='noopener noreferrer';box.append(link);
  }
  if(item.result?.message)box.append(node('p',item.result.message));
  const response=item.result?.response;
  const url=response?.url||(response?.data?.id?'https://x.com/i/status/'+response.data.id:'');
  if(typeof url==='string'&&/^https:\/\/x\.com\/[A-Za-z0-9_]+\/status\/\d+$/.test(url)){
   const link=node('a','Xで投稿を見る');link.href=url;link.target='_blank';link.rel='noopener noreferrer';box.append(link);
  }
 }else{
  box.append(node('p',names[item.provider]+' / '+item.action+' / '+item.status),node('pre',JSON.stringify({content:item.payload,result:item.result},null,2)));
 }
 for(const picture of item.payload.images||[]){
  const img=node('img');img.src='/api/generated-image?'+new URLSearchParams({room:item.room,id:picture.id});img.alt='添付画像';img.width=120;img.loading='lazy';box.append(img);
 }
 if(item.status==='pending'){
  const check=input(box,'送信先と内容を確認しました','checkbox');
  for(const approve of [true,false])button(box,approve?(item.provider==='x'?'承認して投稿':'確認した内容で外部へ送信'):'却下',async()=>{
   if(working||room?.id!==item.room)return;
   if(configDirty()){notice('先に外部連携の設定を保存してください。');return;}
   if(approve&&!check.checked){notice('送信先と内容の確認にチェックしてください。');return;}
   working=true;state();
   try{
    const result=await api('/api/external-review',{room:item.room,id:item.id,revision:item.revision,approve});
    notice(result.status==='sent'?'外部送信を完了しました。':result.status==='rejected'?'下書きを却下しました。':'送信状態：'+result.status+'。履歴を確認してください。');
    if(room?.id===item.room)await reload(true);
   }finally{working=false;state();}
  });
 }
 return box;
}
const xPosts=node('section');xPosts.id='x-posts';
const xSettings=$('external-x-forms'),xTabs=node('div',null,'editor-row');
xTabs.setAttribute('role','tablist');xTabs.setAttribute('aria-label','X / Twitterの画面');
$('external-x-panel').insertBefore(xTabs,xSettings);
$('external-x-panel').append(xPosts);
const xPanels=[[xPosts,'投稿・承認待ち'],[xSettings,'設定']];
const xTabButtons=xPanels.map(([panel,title])=>{
 const tab=node('button',title);tab.type='button';tab.id=panel.id+'-tab';
 tab.setAttribute('role','tab');tab.setAttribute('aria-controls',panel.id);
 panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',tab.id);
 tab.onclick=()=>showXTab(tab);
 xTabs.append(tab);return tab;
});
function showXTab(active){
 for(const [index,tab] of xTabButtons.entries()){
  const selected=active===tab;
  tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
  xPanels[index][0].hidden=!selected;
 }
 if(active===xTabButtons[0])refreshXPosts().catch(report);
}
xTabs.addEventListener('keydown',event=>{
 const index=xTabButtons.indexOf(event.target);
 if(index<0||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
 event.preventDefault();
 const next=event.key==='Home'?0:event.key==='End'?1:1-index;
 showXTab(xTabButtons[next]);xTabButtons[next].focus();
});
const xSections=new Map();
let xPostRefresh=0;
async function refreshXPosts(){
 if(!room||working||loading)return;
 const selected=room.id,ticket=++xPostRefresh;
 const data=await api('/api/external-connections?room='+encodeURIComponent(selected));
 if(room?.id!==selected||ticket!==xPostRefresh||working||loading)return;
 renderXPosts(data.outbox||[]);state();
}
for(const [key,title,label] of [['pending','承認待ち','承認待ちを更新'],['history','投稿履歴','履歴を更新']]){
 const section=node('section'),list=node('div'),detail=node('div');
 section.append(node('h3',title));button(section,label,refreshXPosts);section.append(list,detail);
 xPosts.append(section);xSections.set(key,{list,detail,selected:null});
}
showXTab(xTabButtons[0]);
function renderXPosts(outbox){
 const labels={pending:'承認待ち',sent:'投稿済み',rejected:'却下',failed:'失敗',unknown:'結果未確認',sending:'送信中'};
 for(const [key,section] of xSections){
  const items=outbox.filter(item=>item.provider==='x'&&(item.status==='pending')===(key==='pending'));
  section.list.replaceChildren();section.detail.replaceChildren();
  if(!items.length){section.list.append(node('p',key==='pending'?'承認待ちの下書きはありません。':'投稿履歴はありません。','muted small'));continue;}
  const table=node('table'),header=table.createTHead().insertRow(),body=table.createTBody();
  for(const name of ['ID','時刻','画像','投稿内容','状態'])header.append(node('th',name));
  for(const item of items){
   const row=body.insertRow(),open=node('button',item.id);open.type='button';
   open.setAttribute('aria-label','投稿内容を確認 '+item.id);
   open.setAttribute('aria-pressed',String(section.selected===item.id));
   open.onclick=()=>{section.selected=item.id;renderXPosts(outbox);state();section.list.querySelector('[aria-pressed="true"]')?.focus();};
   row.insertCell().append(open);
   const excerpt=Array.from(item.payload.text||'').slice(0,140).join('');
   for(const value of [new Date(item.created*1000).toLocaleString('ja-JP'),String(item.payload.images?.length||0),excerpt,labels[item.status]||item.status])row.insertCell().textContent=value;
  }
  const frame=node('div',null,'x-post-table');frame.append(table);section.list.append(frame);
  const item=items.find(item=>item.id===section.selected);
  if(item)section.detail.append(outboxCard(item));
 }
}
async function readBotToken(data,id){const config=data.room?.discord;return config?.values.token_saved?(await api('/api/external-secret',{room:id,provider:'discord',revision:config.revision})).token:'';}
async function reload(force=false){if(!room)return;if(dirty()&&!force){notice('未保存の外部連携設定があります。保存または破棄してください。');return;}const id=room.id,serial=++version;loading=serial;state();try{const data=await api('/api/external-connections?room='+encodeURIComponent(id));if(room?.id!==id||serial!==version)return;const token=await readBotToken(data,id);if(room?.id!==id||serial!==version)return;paint(data,null,token);}finally{if(loading===serial){loading=0;state();}}}
for(const scope of ['common','room'])for(const provider of (scope==='common'?['google']:Object.keys(names))){const host=node('fieldset'),legend=node('legend',names[provider]);host.append(legend);host.dataset.provider=provider;$(scope==='common'?'external-common-forms':'external-'+provider+'-forms').append(host);const f={scope,provider,host,fields:{},base:null,cookieRead:0};forms.push(f);f.fields.enabled=input(host,scope==='common'?'この接続を利用可能にする':'このルームで利用を許可する','checkbox');
 if(scope==='common'||provider!=='google'){
 if(provider==='x'){f.fields.mode=node('select');f.fields.mode.setAttribute('aria-label','Xの接続方式');f.fields.mode.append(new Option('公式API','api'),new Option('Cookie','browser'));host.append(f.fields.mode);f.apiGroup=node('section');f.apiGroup.id='x-api-settings';f.cookieGroup=node('section');f.cookieGroup.id='x-cookie-settings';host.append(f.apiGroup,f.cookieGroup);f.fields.username=input(f.cookieGroup,'Cookieのユーザー名（確認済み）');f.fields.username.readOnly=true;f.fields.mode.addEventListener('change',()=>xMode(f));}const authHost=provider==='x'?f.apiGroup:host;
 if(provider!=='discord'){f.fields.client_id=input(authHost,'OAuthクライアントID');f.fields.client_secret=input(authHost,'OAuthクライアント秘密鍵（必要な場合）','password');}
 f.fields.token=input(authHost,provider==='discord'?'Botトークン':'アクセストークン（手動設定の場合）','password');f.fields.token.autocomplete='new-password';
 if(provider!=='discord')f.fields.refresh_token=input(authHost,'更新トークン（手動設定の場合）','password');
 if(provider==='x')f.fields.account=input(authHost,'対象アカウントの数値ユーザーID');f.fields.clear_secrets=input(host,'保存した認証情報を消去する','checkbox');
 }
 if(scope==='room'&&provider==='google'){f.fields.calendar=input(host,'使用するカレンダーID');const select=node('select');select.setAttribute('aria-label','取得したカレンダー');host.append(select);button(host,'カレンダー一覧を取得',async()=>{const r=await api('/api/external-read',{room:room.id,provider,action:'calendars'});select.replaceChildren(new Option('選択してください',''));for(const item of r.data.items||[])select.append(new Option(item.summary||item.id,item.id));});select.onchange=()=>{if(select.value)f.fields.calendar.value=select.value;};}
 else if(scope==='room'&&provider==='discord'){f.fields.default_channel=input(host,'デフォルトのチャンネルID（有効時必須）');f.fields.channels=input(host,'許可するチャンネルID（メイン以外・空白またはカンマ区切り）');f.fields.user_id=input(host,'ユーザID（あかり利用者・有効時必須）');f.fields.allowed_user_ids=input(host,'許可するユーザID（利用者以外・空白またはカンマ区切り）');for(const key of ['default_channel','user_id']){f.fields[key].inputMode='numeric';f.fields[key].pattern='[0-9]{1,25}';}f.fields.enabled.addEventListener('change',()=>discordRequired(f));host.append(node('p','メインチャンネルと追加チャンネルで読み書きできます。Botへの対話は利用者本人と許可ユーザーだけを受け付けます。追加ユーザーが空なら利用者本人のみ。','muted small'));f.fields.receive_mode=node('select');f.fields.receive_mode.setAttribute('aria-label','Discord受信方式');f.fields.receive_mode.append(new Option('定期取得（60秒）','rest'),new Option('Gateway常時接続','gateway'));host.append(f.fields.receive_mode);f.fields.auto_reply=input(host,'自動返信を許可する（生成後、確認なしでDiscordへ送信）','checkbox');f.fields.reply_mode=node('select');f.fields.reply_mode.setAttribute('aria-label','Discord自動返信対象');f.fields.reply_mode.append(new Option('Botへのメンション・返信だけ','mentions'),new Option('許可ユーザーの発言すべて（許可チャンネル内）','always'));host.append(f.fields.reply_mode);f.fields.reply_max_per_day=input(host,'自動返信の1日上限（日本時間・0は無制限）','number');f.fields.reply_max_per_day.min='0';f.fields.reply_max_per_day.max='100';host.append(node('p','自動返信は初期OFF。キャラ指示と同じDiscordチャンネルの会話を使い、あかりの通常会話履歴や記憶は渡しません。音声・動画入力は行いません。','muted small'));}
 if(provider==='x'){
  f.fields.posting_summary=input(host,'投稿方針の要約','textarea');f.fields.posting_summary.maxLength=1000;
  f.fields.posting_guidelines=input(host,'投稿ガイドライン','textarea');f.fields.posting_guidelines.maxLength=6000;
  f.fields.auto_post=input(host,'自動投稿（承認なしでXに投稿）','checkbox');
  f.fields.approval_for_replies=input(host,'引用・リプライは承認必須','checkbox');
  f.fields.notify_discord_approval=input(host,'Discordに承認依頼を通知','checkbox');
  host.append(node('p','承認ボタンを使うには、このルームのDiscordで常時接続の受信を開始してください。ボタンを操作できるのは設定したユーザIDの本人だけです。','muted small'));
  f.fields.is_premium=input(host,'X Premium（長文投稿を許可）','checkbox');
 }
 button(host,'設定を保存',async()=>{if(working||loading||!snapshot)return;if(provider==='x'&&f.cookie.value.trim()){notice('Cookieは「Cookieを取り込む」で保存するか、入力を破棄してください。');return;}if(provider==='discord'){for(const key of ['default_channel','user_id'])f.fields[key].value=f.fields[key].value.trim();discordRequired(f);if(!f.fields.default_channel.reportValidity()||!f.fields.user_id.reportValidity())return;}const id=room?.id,body={room:scope==='common'?'':id,provider,revision:f.revision,values:values(f)};working=true;state();try{const result=await api('/api/external-config',body);if(room?.id!==id)return;snapshot[scope][provider]=result;if(scope==='room'&&provider!=='google'){const checked=await api('/api/external-connections?room='+encodeURIComponent(id));if(room?.id!==id)return;snapshot.duplicates=checked.duplicates;}const token=provider==='discord'?await readBotToken(snapshot,id):'';if(room?.id!==id)return;paint(snapshot,f,token);f.operation=null;notice('外部連携の設定を保存しました。');}finally{working=false;state();}});
 button(host,'未保存の変更を破棄して再読込',async()=>{if(working)return;f.base=null;if(f.cookie){f.cookieRead++;f.cookie.value='';f.cookieFile.value='';}await reload(true);});
 if(provider==='x'||scope==='common'&&provider==='google'){const oauthHost=provider==='x'?f.apiGroup:host;const link=node('a','認証ページを開く');link.target='_blank';link.rel='noopener noreferrer';link.hidden=true;button(oauthHost,'OAuth認証URLを作る',async()=>{if(dirty()){notice('先に設定を保存してください。');return;}const r=await api('/api/external-oauth',{provider,room:provider==='x'?room.id:''});link.href=r.url;link.hidden=false;notice('認証ページを開き、接続先で許可してください。戻り先：'+r.redirect_uri);});oauthHost.append(link);}
 if(scope==='room'&&provider==='x'){
  f.cookieGroup.append(node('p','自分のXアカウントから書き出したCookie JSONを取り込みます。Cookie配列、またはcookies配列を含むJSONに対応。取り込み時に接続先を確認し、成功時だけ暗号化して保存します。','muted small'));
  f.cookie=input(f.cookieGroup,'Cookie JSON','textarea');f.cookie.maxLength=262144;f.cookie.autocomplete='off';f.cookie.spellcheck=false;
  f.cookieFile=input(f.cookieGroup,'Cookie JSONファイルを選択','file');f.cookieFile.accept='.json,application/json';f.cookieFile.addEventListener('change',async()=>{const file=f.cookieFile.files[0],id=room?.id,roomVersion=version,serial=++f.cookieRead;if(!file)return;try{if(file.size>262144)throw Error('Cookie JSONは256KiB以内で指定してください。');const content=await file.text();if(id===room?.id&&roomVersion===version&&serial===f.cookieRead)f.cookie.value=content;}catch(e){report(e);}finally{if(serial===f.cookieRead)f.cookieFile.value='';}});
  button(f.cookieGroup,'Cookieを取り込む',async()=>{if(working||loading||!room)return;if(settingsDirty()||operationDirty){notice('先に設定を保存し、操作中の入力を完了または破棄してください。');return;}if(f.fields.mode.value!=='browser'){notice('接続方式をCookieにして保存してください。');return;}if(!f.cookie.value.trim()){notice('Cookie JSONを入力してください。');return;}const id=room.id,body={room:id,revision:f.revision,cookies:f.cookie.value};working=true;state();try{const result=await api('/api/external-x-cookies',body);if(room?.id!==id)return;f.cookie.value='';await reload(true);notice(result.message);}finally{working=false;state();}});
  button(f.cookieGroup,'Cookieの入力を破棄',async()=>{f.cookieRead++;f.cookie.value='';f.cookieFile.value='';});xMode(f);
 }
 if(provider==='x'){
  for(const [group,label] of [[f.apiGroup,'API接続テスト'],[f.cookieGroup,'セッション状態確認']]){
   const status=node('p',null,'muted small');status.setAttribute('role','status');
   button(group,label,async()=>{
    if(!room||working||loading)return;
    if(configDirty()){status.textContent='先に設定を保存してください。';return;}
    const id=room.id;
    working=true;state();status.textContent='確認中…';
    try{
     const result=await api('/api/external-read',{room:id,provider:'x',action:'identity'});
     if(room?.id!==id)return;
     const account=result.data?.username||result.data?.data?.username||result.data?.data?.id;
     if(!account)throw Error('認証中のアカウントを確認できませんでした。');
     await reload(true);
     if(room?.id===id)status.textContent=(group===f.cookieGroup?'ログイン済み':'API接続成功')+' ／ '+account;
    }catch(error){if(room?.id===id)status.textContent=label+'に失敗しました。'+error.message;}
    finally{working=false;state();}
   });
   group.append(status);
  }
 }
 if(scope==='room'&&provider==='discord')button(host,'接続確認・他ルームとの重複を確認',async()=>{if(dirty()){notice('先に設定を保存してください。');return;}const id=room.id;await api('/api/external-read',{room:id,provider,action:'identity'});if(room?.id===id)await reload(true);});
 f.status=node('p','', 'muted small');host.append(f.status);
}
const receiver=node('section');receiver.id='external-discord-controls';receiver.append(node('h3','Discord受信・自動返信'));receiver.append(node('p','受信方式と自動返信の許可は上のルーム設定で指定します。Botには閲覧・履歴参照・送信権限と必要なMessage Content Intentを与えてください。接続と認証はこのルーム専用です。','muted small'));for(const action of ['start','stop'])button(receiver,action==='start'?'このルームの受信を開始':'このルームの受信を停止',async()=>{if(configDirty()){notice('先に外部連携の設定を保存してください。');return;}await api('/api/external-discord',{room:room.id,action});await reload(true);});const received=node('pre');received.id='external-discord-received';received.style.whiteSpace='pre-wrap';received.style.maxHeight='20rem';received.style.overflow='auto';receiver.append(received);const replyControls=node('div');replyControls.id='discord-reply-controls';receiver.append(replyControls);$('external-discord-panel').append(receiver);
const op=$('external-operation'),provider=node('select'),action=node('select');provider.setAttribute('aria-label','操作する接続先');action.setAttribute('aria-label','外部連携の操作');for(const [k,v] of Object.entries(names))provider.append(new Option(v,k));provider.hidden=true;op.append(provider,action);
const fields={},labels={start:'開始日時（例：2026-10-28T09:00:00+09:00）',end:'終了日時（タイムゾーン付き）',summary:'予定のタイトル',description:'予定の説明',event_id:'更新・削除する予定ID',etag:'取得結果のetag（競合検出用）',channel:'チャンネルID（空欄はデフォルト）',text:'投稿・メッセージ本文',url:'Xの投稿URL（Cookie方式）',after:'次ページID（メンバー取得結果のafter・省略可）'};
for(const [k,label] of Object.entries(labels)){fields[k]=input(op,label,k==='text'||k==='description'?'textarea':'text');fields[k].addEventListener('input',()=>operationDirty=true);}
const directory=node('section');directory.id='discord-directory';directory.append(node('h3','Discordの送信先'));
const guildLabel=node('label','サーバー'),guildSelect=node('select');guildSelect.id='discord-guild';guildLabel.append(guildSelect);directory.append(guildLabel);
const channelLabel=node('label','チャンネル'),channelSelect=node('select');channelSelect.id='discord-channel';channelLabel.append(channelSelect);directory.append(channelLabel);
const recipientLabel=node('label','宛先'),recipientSelect=node('select');recipientSelect.id='discord-recipient';recipientLabel.append(recipientSelect);op.append(recipientLabel);fields.recipient=recipientSelect;
const directoryStatus=node('p','許可済みチャンネルだけ送信先に選べます。メンバーは閲覧権限の一覧で、オンライン一覧ではありません。','muted small');directory.append(directoryStatus);op.append(directory);
let guildAfter='',memberAfter='',directorySequence=0;
function resetRecipients(){recipientSelect.replaceChildren(new Option('指定しない（チャンネルへの発言）',''));memberAfter='';memberNext.hidden=true;}
function resetDirectory(){directorySequence++;guildAfter='';guildNext.hidden=true;fields.channel.value='';guildSelect.replaceChildren(new Option('サーバーを選択…',''));channelSelect.replaceChildren(new Option('チャンネルを選択…',''));resetRecipients();}
async function directoryRead(kind,args,consume){
 if(working||loading||!room)return;
 if(configDirty()){notice('先にDiscord設定を保存してください。');return;}
 const id=room.id,sequence=directorySequence;loading++;state();
 try{const r=await api('/api/external-read',{room:id,provider:'discord',action:kind,args});if(room?.id===id&&sequence===directorySequence)consume(r.data);}
 finally{loading--;state();}
}
async function getGuilds(next=false){return directoryRead('guilds',{after:next?guildAfter:''},data=>{if(!next){resetDirectory();}for(const item of data.guilds)if(!Array.from(guildSelect.options).some(o=>o.value===item.id))guildSelect.append(new Option(item.name+' ('+item.id+')',item.id));guildAfter=data.after||'';guildNext.hidden=!guildAfter;});}
button(directory,'サーバー一覧を取得',()=>getGuilds(false));const guildNext=button(directory,'次のサーバーを取得',()=>guildAfter?getGuilds(true):Promise.resolve());
async function getChannels(){if(!guildSelect.value)return;return directoryRead('channels',{guild:guildSelect.value},data=>{channelSelect.replaceChildren(new Option('チャンネルを選択…',''));resetRecipients();for(const item of data.channels){const option=new Option('#'+item.name+(item.allowed?'':'（未許可）'),item.id);option.disabled=!item.allowed;channelSelect.append(option);}});}
button(directory,'チャンネル一覧を取得',getChannels);guildSelect.onchange=()=>{fields.channel.value='';operationDirty=true;channelSelect.replaceChildren(new Option('チャンネルを選択…',''));resetRecipients();getChannels().catch(report);};
async function getMembers(next=false){const channel=fields.channel.value.trim()||snapshot?.room?.discord?.values?.default_channel;if(!channel)return;return directoryRead('members',{channel,after:next?memberAfter:''},data=>{if(!next)resetRecipients();for(const item of data.members)if(!Array.from(recipientSelect.options).some(o=>o.value===item.id))recipientSelect.append(new Option(item.name+(item.bot?' [Bot]':'')+' ('+item.id+')',item.id));memberAfter=data.after||'';memberNext.hidden=!memberAfter;directoryStatus.textContent=data.meaning+(data.more?' 次ページがあります。':'');});}
channelSelect.onchange=()=>{fields.channel.value=channelSelect.value;operationDirty=true;resetRecipients();if(channelSelect.value)getMembers().catch(report);};
fields.channel.addEventListener('input',resetRecipients);recipientSelect.onchange=()=>operationDirty=true;
button(directory,'メンバー一覧を取得',()=>getMembers(false));const memberNext=button(directory,'次のメンバーを取得',()=>memberAfter?getMembers(true):Promise.resolve());resetDirectory();
const imageLabel=node('label','送信する画像（最大4枚・合計8MiB）'),imageSelect=node('select');imageSelect.multiple=true;imageSelect.setAttribute('aria-label','外部へ送信する画像');imageLabel.append(imageSelect);op.append(imageLabel);fields.image_ids=imageSelect;
async function loadImages(){const id=room?.id;if(!id)return;const chosen=Array.from(imageSelect.selectedOptions,o=>o.value),data=await api('/api/image-jobs?room='+encodeURIComponent(id));if(room?.id!==id)return;imageSelect.replaceChildren(...data.images.map(i=>new Option(i.name||i.prompt?.slice(0,60)||i.id,i.id)));for(const option of imageSelect.options)option.selected=chosen.includes(option.value);}
button(imageLabel,'このルームの画像候補を更新',loadImages);const file=node('input');file.type='file';file.accept='image/png,image/jpeg,image/webp';file.multiple=true;file.setAttribute('aria-label','外部送信用の画像を取り込む');imageLabel.append(file);file.onchange=async()=>{try{if(!room)return;const id=room.id;for(const f of Array.from(file.files||[]).slice(0,4)){if(f.size>5*1024*1024)throw Error('画像は1枚5MiB以内です。');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(f);});const item=await api('/api/external-image-import',{room:id,name:f.name,data,request_id:crypto.randomUUID()});if(room?.id!==id)return;await loadImages();const option=Array.from(imageSelect.options).find(o=>o.value===item.id);if(option)option.selected=true;}operationDirty=true;}catch(e){report(e);}finally{file.value='';}};
imageSelect.onchange=()=>operationDirty=true;
const reads={google:['calendars','events','freebusy'],x:['identity','timeline','mentions','notifications','thread'],discord:['identity','channel','messages','members']},writes={google:['create','update','delete'],x:['post'],discord:['send']},labelsAction={calendars:'カレンダー一覧',events:'予定を読む',freebusy:'空き時間を読む',identity:'認証中アカウント',timeline:'投稿一覧',mentions:'メンション',notifications:'通知（ブラウザ）',thread:'投稿と返信（ブラウザ）',channel:'チャンネル情報',messages:'メッセージを読む',members:'メンバー一覧（閲覧権限）',create:'予定を追加',update:'予定を更新',delete:'予定を削除',post:'投稿する',send:'メッセージを送る'};
function change(){const p=provider.value,a=action.value;directory.hidden=p!=='discord';const needed=p==='google'?(a==='calendars'?[]:a==='delete'?['event_id','etag']:a==='update'?['summary','description','start','end','event_id','etag']:a==='create'?['summary','description','start','end']:['start','end']):p==='x'?(a==='post'?['text','image_ids']:a==='thread'?['url']:[]):a==='send'?['channel','recipient','text','image_ids']:a==='members'?['channel','after']:['messages','channel'].includes(a)?['channel']:[];for(const [k,e] of Object.entries(fields))e.parentElement.hidden=!needed.includes(k);}
provider.onchange=()=>{action.replaceChildren();for(const a of [...reads[provider.value],...writes[provider.value]])action.append(new Option(labelsAction[a],a));change();};action.onchange=change;provider.value='x';provider.onchange();
const output=node('pre');button(op,'参照する／送信前の下書きを作る',async()=>{if(working||loading||!room)return;if(configDirty()){notice('先に外部連携の設定を保存してください。');return;}const id=room.id,p=provider.value,a=action.value,args=Object.fromEntries(Object.entries(fields).filter(([k,e])=>!e.parentElement.hidden).map(([k,e])=>[k,k==='image_ids'?Array.from(e.selectedOptions,o=>o.value):e.value]));working=true;state();try{const read=reads[p].includes(a),r=await api(read?'/api/external-read':'/api/external-propose',{room:id,provider:p,action:a,args,request_id:crypto.randomUUID()});if(room?.id!==id)return;output.textContent=JSON.stringify(r,null,2);operationDirty=false;await reload(true);notice(read?'接続先から取得しました。':'下書きを作成しました。外部にはまだ送信していません。');}finally{working=false;state();}});op.append(output);
function clearOperation(){resetDirectory();for(const e of Object.values(fields)){if(e.tagName==='SELECT')for(const option of e.options)option.selected=false;else e.value='';}output.textContent='';operationDirty=false;}
button(op,'操作入力をクリア',async()=>{if(!working)clearOperation();});
function selectTab(id){const p={'external-x-panel':'x','external-discord-panel':'discord','external-google-panel':'google'}[id];if(!p){$('external-service-actions').hidden=true;return true;}
 if(p!==provider.value&&(operationDirty||working)){notice('入力中の操作があります。実行するか「操作入力をクリア」で破棄してから切り替えてください。');return false;}
 if(p==='x'){$('external-service-actions').hidden=true;if(!xPosts.hidden)refreshXPosts().catch(report);return true;}
 if(p!==provider.value){clearOperation();provider.value=p;provider.onchange();}
 $(id).append($('external-service-actions'));$('external-service-actions').hidden=false;
 for(const card of $('external-outbox').children)card.hidden=card.dataset.provider!==p;
 return true;
}
window.AkariExternal={selectTab,dirty,roomChanged:async id=>{version++;xPostRefresh++;for(const status of xSettings.querySelectorAll('#x-api-settings [role="status"],#x-cookie-settings [role="status"]'))status.textContent='';for(const section of xSections.values())section.selected=null;renderXPosts([]);resetDirectory();snapshot=null;operationDirty=false;for(const f of forms){f.base=null;f.revision=null;if(f.cookie){f.cookieRead++;f.cookie.value='';f.cookieFile.value='';}f.status.textContent='このルームの設定を読み込んでいます。';for(const e of Object.values(f.fields)){if(e.type==='checkbox')e.checked=false;else e.value='';}for(const link of f.host.querySelectorAll('a'))link.hidden=true;}for(const e of Object.values(fields))e.value='';imageSelect.replaceChildren();output.textContent='';await reload(true);}};
})();
