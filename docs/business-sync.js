/* Reuses this browser's private Mac mini connection. Business data stays private. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./business-data.js'));else root.BusinessSync=factory(root.BusinessData);})(typeof globalThis!=='undefined'?globalThis:this,function(B){
  'use strict';
  const KEY='business-records-v1',ENDPOINT='https://bills-mac-mini.tailf2c209.ts.net:8443';
  function create({storage,fetcher=fetch,status=()=>{},now=()=>new Date()}){
    let value={book:B.book(),base:null,pending:[],conflicts:[],lastSyncedAt:null},phase='local',busy=false,queued=false,generation=0,error='',lastRaw=null;
    try{
      lastRaw=storage.getItem(KEY);const raw=JSON.parse(lastRaw||'null');
      if(raw){value={...raw,book:B.book(raw.book),base:raw.base?B.book(raw.base):null};
        if(!Array.isArray(value.pending)||value.pending.some(k=>!B.entry(value.book,k)))throw new Error('Invalid waiting entries.');
        value.conflicts=raw.conflicts||[];
      }
    }catch(e){error='The saved business record could not be read. Keep this browser data and restore a backup before saving.';}
    function config(){try{const c=JSON.parse(storage.getItem('retire-sync-v1')||'null');return c&&c.endpoint===ENDPOINT&&/^[A-Za-z0-9_-]{43}$/.test(c.token)?c:null;}catch(e){return null;}}
    function state(){return {...value,phase,paired:!!config(),error};}
    function notify(next,message=''){phase=next;status(state(),message);}
    function persist(next=value){
      if(storage.getItem(KEY)!==lastRaw)throw new Error('Another tab updated business records. Reload this page before saving.');
      const raw=JSON.stringify(next);storage.setItem(KEY,raw);lastRaw=raw;value=next;
    }
    async function request(c,method='GET',body){
      const r=await fetcher(c.endpoint+'/v1/business',{method,headers:{Authorization:'Bearer '+c.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(15000)});
      const data=await r.json();
      if(!r.ok&&r.status!==409)throw new Error(r.status===401?'Reconnect this browser with your private Telegram link.':'The Mac mini could not save this update.');
      return {...data,book:B.book(data.book),conflict:r.status===409};
    }
    async function cycle(){
      if(error){notify('error',error);return;}
      const c=config();if(!c){notify('local');return;}
      if(busy){queued=true;return;}
      try{const raw=storage.getItem(KEY);if(raw!==lastRaw){const latest=JSON.parse(raw||'null');if(!latest)throw new Error('The business record changed in another tab. Reload this page.');const next={...latest,book:B.book(latest.book),base:latest.base?B.book(latest.base):null};if(!Array.isArray(next.pending)||next.pending.some(k=>!B.entry(next.book,k)))throw new Error('The newer business record could not be read.');value=next;lastRaw=raw;generation++;}}catch(e){notify('offline',e.message);return;}
      if(value.conflicts.length){notify('conflict');return;}
      busy=true;const version=generation;
      try{
        if(phase==='local'||phase==='offline')notify('checking');
        const remote=await request(c);
        if(version!==generation){queued=true;return;}
        if(value.pending.length){
          const merged=B.merge(value.book,value.base,remote.book,value.pending);
          if(merged.conflicts.length){persist({...value,conflicts:merged.conflicts});notify('conflict');return;}
          notify('saving');
          const result=await request(c,'PUT',{expectedRevision:remote.revision,book:merged.book});
          if(result.conflict){queued=true;return;}
          if(version!==generation){persist({...value,base:result.book});queued=true;return;}
          persist({book:result.book,base:result.book,pending:[],conflicts:[],lastSyncedAt:now().toISOString()});
        }else persist({book:remote.book,base:remote.book,pending:[],conflicts:[],lastSyncedAt:now().toISOString()});
        notify('synced');
      }catch(e){notify('offline',e.message);}
      finally{busy=false;if(queued){queued=false;Promise.resolve().then(cycle);}}
    }
    async function save(input){
      if(error)throw new Error(error);
      if(value.conflicts.length)throw new Error('Resolve the different revenue entries before saving another month.');
      const book=B.save(value.book,input,{now:now()}),k=B.key(input);
      persist({...value,book,pending:[...new Set([...value.pending,k])]});generation++;
      notify(config()?'saving':'local');await cycle();return state();
    }
    async function resolve(k,which){
      if(busy)return;
      const conflict=value.conflicts.find(c=>c.key===k);if(!conflict)return;
      const replace=(b,r)=>B.book({schemaVersion:1,records:[...b.records.filter(x=>B.key(x)!==k),...(r?[r]:[])]});
      let book=value.book,pending=value.pending;
      if(which==='remote'){book=replace(book,conflict.remote);pending=pending.filter(x=>x!==k);}
      else if(which!=='local')throw new Error('Choose an entry to keep.');
      const base=replace(value.base||B.book(),conflict.remote);
      persist({...value,book,base,pending,conflicts:value.conflicts.filter(c=>c.key!==k)});generation++;
      if(value.conflicts.length)notify('conflict');else await cycle();
    }
    notify(error?'error':config()?'checking':'local',error);
    return {save,cycle,resolve,state};
  }
  return {create};
});
