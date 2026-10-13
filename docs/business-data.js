/* Monthly revenue records. No personal records or credentials in this file. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.BusinessData=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const currencies=['USD','KRW'];
  const key=r=>r.currency+':'+r.month;
  function month(v){if(typeof v!=='string'||!/^\d{4}-(0[1-9]|1[0-2])$/.test(v)||v<'1990-01'||v>'2099-12')throw new Error('Choose a valid month.');return v;}
  function record(v){
    if(!v||!currencies.includes(v.currency))throw new Error('Choose USD or KRW.');
    month(v.month);
    if(typeof v.amount!=='number'||!Number.isFinite(v.amount)||v.amount<0||v.amount>1e15)throw new Error('Enter a revenue amount of zero or more.');
    const amount=Math.round(v.amount*(v.currency==='KRW'?1:100))/(v.currency==='KRW'?1:100);
    if(typeof v.updatedAt!=='string'||!Number.isFinite(Date.parse(v.updatedAt)))throw new Error('The saved date is invalid.');
    return {month:v.month,currency:v.currency,amount,updatedAt:new Date(v.updatedAt).toISOString()};
  }
  function book(v={schemaVersion:1,records:[]}){
    if(v.schemaVersion!==1||!Array.isArray(v.records)||v.records.length>1600)throw new Error('The business record could not be read.');
    const records=v.records.map(record).sort((a,b)=>key(a).localeCompare(key(b)));
    if(new Set(records.map(key)).size!==records.length)throw new Error('The business record contains a duplicate month.');
    return {schemaVersion:1,records};
  }
  function localMonth(now=new Date()){return now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');}
  function save(value,input,{now=new Date(),currentMonth=localMonth(now)}={}){
    const b=book(value);month(input.month);
    if(input.month>currentMonth)throw new Error('Choose this month or an earlier month.');
    const r=record({...input,updatedAt:now.toISOString()});
    return book({...b,records:[...b.records.filter(x=>key(x)!==key(r)),r]});
  }
  function entry(b,k){return b.records.find(r=>key(r)===k)||null;}
  function same(a,b){return (!a&&!b)||!!a&&!!b&&key(a)===key(b)&&a.amount===b.amount;}
  function shift(m,n){const [y,k]=m.split('-').map(Number),d=new Date(Date.UTC(y,k-1+n,1));return d.toISOString().slice(0,7);}
  function merge(local,base,remote,pending){
    local=book(local);remote=book(remote);base=base?book(base):book();
    const conflicts=[],records=remote.records.slice();
    for(const k of [...new Set(pending)]){
      const mine=entry(local,k),before=entry(base,k),theirs=entry(remote,k);
      if(!mine)throw new Error('A waiting revenue entry is missing.');
      if(!same(theirs,before)&&!same(mine,theirs)){conflicts.push({key:k,local:mine,remote:theirs});continue;}
      const i=records.findIndex(r=>key(r)===k);if(i>=0)records.splice(i,1);records.push(same(mine,theirs)?theirs:mine);
    }
    return {book:book({schemaVersion:1,records}),conflicts};
  }
  function stats(value,currency,selected){
    month(selected);const rows=book(value).records.filter(r=>r.currency===currency),year=selected.slice(0,4);
    const current=rows.find(r=>r.month===selected)||null,previous=rows.find(r=>r.month===shift(selected,-1))||null;
    const ytd=rows.filter(r=>r.month.startsWith(year)&&r.month<=selected);
    const sum=ytd.reduce((s,r)=>s+r.amount,0),change=current&&previous?current.amount-previous.amount:null;
    const percent=change===null||previous.amount===0?null:change/previous.amount*100;
    const series=Array.from({length:12},(_,i)=>{const m=shift(selected,i-11);return {month:m,amount:rows.find(r=>r.month===m)?.amount??null};});
    const quarters=Array.from({length:4},(_,i)=>{const q=ytd.filter(r=>Math.floor((Number(r.month.slice(5))-1)/3)===i);return {label:'Q'+(i+1),amount:q.reduce((s,r)=>s+r.amount,0),count:q.length};});
    return {current,previous,ytd:sum,count:ytd.length,average:ytd.length?sum/ytd.length:null,change,percent,series,quarters,rows:rows.filter(r=>r.month<=selected).sort((a,b)=>b.month.localeCompare(a.month)),best:ytd.length?ytd.reduce((a,b)=>a.amount>=b.amount?a:b):null,runRate:current?current.amount*12:null};
  }
  return {book,record,key,entry,same,save,shift,stats,merge,localMonth};
});
