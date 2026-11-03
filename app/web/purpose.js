"use strict";
let purposeProposal=null,purposeProposalRoom=null,purposeProposalPage=0,purposeReviewOperation=null,purposeSelectionSequence=0;
function purposeDescribe(doc){
  if(!doc)return 'まだ活動目的は設定されていません。';
  const lines=[doc.title,doc.body];for(const [key,label] of Object.entries(purposeLabels))if(doc.details?.[key]?.length)lines.push(label+'\n'+doc.details[key].map(value=>'・'+value).join('\n'));return lines.join('\n\n');
}
function purposeProposalPaint(){
  A('purpose-before').textContent=purposeProposal?purposeDescribe(purposeProposal.before):'提案を選択してください。';
  A('purpose-after').textContent=purposeProposal?purposeDescribe(purposeProposal.after):'';
  A('purpose-reason').textContent=purposeProposal?purposeProposal.reason+' / '+purposeProposal.state:'';
  for(const id of ['purpose-proposal-edit','purpose-proposal-reject'])A(id).disabled=!purposeProposal||purposeProposal.state!=='pending';
}
async function purposeProposalList(){
  if(!room)return;const selected=room.id;
  if(purposeProposalRoom!==selected){purposeProposalRoom=selected;purposeProposal=null;purposeProposalPage=0;purposeReviewOperation=null;purposeProposalPaint();}
  const page=purposeProposalPage,sequence=purposeSelectionSequence,data=await api('/api/purpose-proposals?'+new URLSearchParams({room:selected,offset:page}));if(room?.id!==selected||page!==purposeProposalPage)return;
  const select=A('purpose-proposals'),chosen=purposeSelectionSequence!==sequence?select.value:purposeProposal?.id||'',label=select.selectedOptions[0]?.textContent||chosen;select.replaceChildren(new Option('提案を選択',''));
  for(const item of data.entries)select.append(new Option(item.title+' / '+item.state,item.id));
  if(purposeProposal&&!data.entries.some(item=>item.id===purposeProposal.id))select.append(new Option(purposeProposal.title,purposeProposal.id));
  if(chosen&&!Array.from(select.options).some(option=>option.value===chosen))select.append(new Option(label,chosen));
  select.value=chosen;A('purpose-proposals-prev').disabled=purposeProposalPage===0;A('purpose-proposals-next').disabled=!data.more;
}
A('purpose-proposals').addEventListener('change',async()=>{
  purposeSelectionSequence++;
  const selected=room.id,key=A('purpose-proposals').value;if(!key){purposeProposal=null;purposeProposalPaint();return;}
  try{const data=await api('/api/purpose-proposal?'+new URLSearchParams({room:selected,id:key}));if(room?.id===selected&&A('purpose-proposals').value===key){purposeProposal=data;purposeReviewOperation=null;purposeProposalPaint();}}catch(error){report(error);}
});
A('purpose-proposal-edit').addEventListener('click',()=>{
  if(!purposeProposal||purposeProposal.state!=='pending')return;
  if(autoDocumentDirty()){notice('活動記録の未保存入力があります。保存または破棄してください。');return;}
  autoKind='purpose';A('autonomy-kind').value='purpose';autoPage=0;
  autoFillDocument({...purposeProposal.before,...purposeProposal.after,id:purposeProposal.before?.id||'',revision:purposeProposal.before?.revision||'',kind:'purpose'},purposeProposal);
  revealPanel('autonomy-records');autoList().catch(report);
});
A('purpose-proposal-reject').addEventListener('click',async()=>{
  if(!purposeProposal||purposeProposal.state!=='pending')return;const selected=room.id,key=purposeProposal.id;
  if(!purposeReviewOperation)purposeReviewOperation=crypto.randomUUID();A('purpose-proposal-reject').disabled=true;
  try{const result=await api('/api/purpose-review',{room:selected,id:key,revision:purposeProposal.revision,action:'reject',request_id:purposeReviewOperation});if(room?.id===selected&&purposeProposal?.id===key){purposeProposal=result.proposal;purposeProposalPaint();await purposeProposalList();}}
  catch(error){report(error);}finally{purposeProposalPaint();}
});
A('purpose-proposals-refresh').addEventListener('click',()=>purposeProposalList().catch(report));
for(const [id,delta] of [['purpose-proposals-prev',-20],['purpose-proposals-next',20]])A(id).addEventListener('click',()=>{purposeProposalPage=Math.max(0,purposeProposalPage+delta);purposeProposalList().catch(report);});
document.querySelector('[data-panel="purpose-proposals-panel"]').addEventListener('panelactivate',()=>purposeProposalList().catch(report));
