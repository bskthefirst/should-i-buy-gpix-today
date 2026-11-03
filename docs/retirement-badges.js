/* Shared badge rules. All income progress is an estimate after the entered tax rate. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./retirement-journal.js'));else root.RetirementBadges=factory(root.RetirementJournal);})(typeof globalThis!=='undefined'?globalThis:this,function(J){
 'use strict';
 function catalog(value,net){
  const p=J.profile(value),reported=J.sameHoldings(p.journal.snapshots.at(-1),p),valid=typeof net==='number'&&Number.isFinite(net)&&net>=0;
  const held=J.FUNDS.filter(([k])=>p['shares'+k]>0).length,months=p.journal.checkins.length;
  const baseline=p.journal.snapshots.find(r=>r.income>0)?.income||null;
  const grouped=new Map();for(const c of p.costs){const amount=c.amount*c.times*(c.per==='week'?52/12:1),name=c.name.trim();if(amount>0&&name)grouped.set(name.toLowerCase(),{name,amount:amount+(grouped.get(name.toLowerCase())?.amount||0)});}
  const bills=[...grouped.values()].sort((a,b)=>a.amount-b.amount).slice(0,3),goals=p.badgeGoals;
  const rows=[
   {id:'engines',title:'Four Engines',category:'holdings',icon:'rocket',pal:'coral',small:'FOUR ENGINES',value:held,target:4,detail:'Report a position in all four supported dividend holdings.',gap:Math.max(0,4-held)+' more dividend holding'+(4-held===1?'':'s'),qualified:reported&&held===4},
   {id:'dollar-day',title:'Dollar a Day',category:'income',icon:'sun',pal:'gold',small:'A DOLLAR A DAY',value:net,target:365/12,detail:'An estimated $365 a year after tax. Monthly average, not a daily payment.'},
   {id:'subscription',title:'Subscription Slayer',category:'income',icon:'scissors',pal:'violet',small:'SUBSCRIPTION',value:net,target:goals.subscription?.amount||null,detail:goals.subscription?goals.subscription.name+' · $'+goals.subscription.amount.toFixed(2)+'/month':'Choose a subscription and enter its actual monthly cost.',setup:'#reward-goals'},
   {id:'weekend',title:'Weekend Sponsor',category:'income',icon:'fork',pal:'rose',small:'WEEKEND SPONSOR',value:net,target:goals.weekend?.amount||null,detail:goals.weekend?goals.weekend.name+' · $'+goals.weekend.amount.toFixed(2)+'/month':'Enter the monthly weekend meal budget you want to cover.',setup:'#reward-goals'},
   {id:'bills',title:'Bills on Autopilot',category:'income',icon:'home',pal:'teal',small:'THREE BILLS',value:net,target:bills.length===3?bills.reduce((a,b)=>a+b.amount,0):null,detail:bills.length===3?'Cover '+bills.map(r=>r.name).join(', ')+'.':'Add three different expenses in What your dividends pay for.',setup:'#sec-costs'},
   {id:'groove',title:'Three-Month Groove',category:'habit',icon:'music',pal:'aqua',small:'THREE MONTHS',value:months,target:3,detail:'Confirm your holdings in three different calendar months. No purchase required.',gap:Math.max(0,3-months)+' more monthly confirmation'+(3-months===1?'':'s'),qualified:months>=3},
   {id:'yearbook',title:'Yearbook',category:'habit',icon:'book',pal:'indigo',small:'TWELVE MONTHS',value:months,target:12,detail:'Build twelve months of confirmed holdings history. No purchase required.',gap:Math.max(0,12-months)+' more monthly confirmations',qualified:months>=12},
   {id:'double',title:'Double Take',category:'income',icon:'sparkle',pal:'lime',small:'DOUBLE TAKE',value:net,target:baseline?baseline*2:null,detail:baseline?'Double the $'+baseline.toFixed(2)+'/month estimate in your first positive saved report.':'Record a positive income estimate to set your starting point.'}
  ];
  return rows.map((r,index)=>{const key='fun:'+r.id,earned=p.journal.achievements.find(a=>a.key===key),configured=!!r.target,progress=configured&&r.value!=null?Math.max(0,Math.min(1,r.value/r.target)):0;
   const qualifies=r.category==='income'?configured&&valid&&reported&&net>=r.target-1e-9:!!r.qualified;
   return {...r,index,key,configured,progress,qualifies,earned:earned||null,on:!!earned,threshold:r.category==='income'?r.target:0,incomeGap:r.category==='income'&&configured&&valid?Math.max(0,r.target-net):null};});
 }
 function award(p,net,at=new Date().toISOString()){
  const added=[];for(const r of catalog(p,net))if(r.qualifies&&!r.earned){
   if(p.journal.achievements.length>=2000)throw new Error('Badge history is full. Export a backup.');
   const a={key:r.key,title:r.title,threshold:r.threshold||0,at,baseline:false,category:r.category,detail:r.detail};p.journal.achievements.push(a);added.push(a);
  }return added;
 }
 function closest(p,net,count=3){return catalog(p,net).filter(r=>!r.on&&r.configured).sort((a,b)=>b.progress-a.progress||a.index-b.index).slice(0,count);}
 return {catalog,award,closest};
});
