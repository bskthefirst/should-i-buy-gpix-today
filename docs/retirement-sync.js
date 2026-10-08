/* One shared record on the owner's Mac mini. No keys or personal data in source. */
(function(root,factory){
  if(typeof module==='object'&&module.exports)module.exports=factory(require('./retirement-journal.js'));
  else root.RetirementSync=factory(root.RetirementJournal);
})(typeof globalThis!=='undefined'?globalThis:this,function(J){
  'use strict';
  const KEY='retire-sync-v1';
  function fingerprint(p) {const v=J.profile(p);delete v.exportedAt;return JSON.stringify(v);}
  function values(p) {const v=J.transport(p);delete v.exportedAt;return JSON.stringify(v);}
  function connection(v) {
    const u=new URL(v.endpoint);
    if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash)throw new Error('The Mac mini address must use HTTPS.');
    if(!/^[A-Za-z0-9_-]{43}$/.test(v.token))throw new Error('The connection key is invalid.');
    return {endpoint:u.href.replace(/\/$/,''),token:v.token};
  }
  function create({storage,fetcher=fetch,read,apply,prepare,status,editing=()=>false,allowedEndpoint=null,now=()=>new Date()}) {
    let saved=null,busy=false,conflict=null,generation=0,reachable=false,phase='unpaired';
    const validate=v=>{const c=connection(v);if(allowedEndpoint&&c.endpoint!==allowedEndpoint)throw new Error('This connection points to an unrecognized server.');return c;};
    try {const v=JSON.parse(storage.getItem(KEY)||'null');if(v)saved={...v,...validate(v)};}catch(e){status('Connection settings could not be read. Reconnect this browser.');}
    function stamp(){return new Date(now()).toISOString();}
    function state(){return {phase,connected:phase==='synced',paired:!!saved&&!saved.pending,reachable,pending:!!saved?.pending,dirty:!!saved?.dirty,conflict:!!conflict,lastSyncedAt:saved?.lastSyncedAt||null,lastContactAt:saved?.lastContactAt||null,serverUpdatedAt:saved?.serverUpdatedAt||null};}
    if(saved){conflict=saved.conflict||null;phase=conflict?'conflict':'checking';}
    function persist(){if(saved)storage.setItem(KEY,JSON.stringify(saved));else storage.removeItem(KEY);}
    function notify(text,next=phase){phase=next;status(text,state());}
    function choose(data,text){conflict=data;saved.conflict=data;persist();notify(text,'conflict');}
    async function request(method,body) {
      const r=await fetcher(saved.endpoint+'/v1/plan',{method,headers:{Authorization:'Bearer '+saved.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(30000)});
      const data=await r.json();
      if(r.status!==409&&!r.ok)throw new Error(r.status===401?'This browser needs a new connection key.':'Mac mini unavailable. Your edits stay here and will retry automatically.');
      reachable=true;saved.lastContactAt=stamp();saved.serverUpdatedAt=data.updatedAt||null;persist();
      if(r.status===409)return {conflict:data};
      return data;
    }
    function accept(data,options={}){const p=J.profile(data.profile);apply(p,options);saved.revision=data.revision;saved.dirty=false;saved.synced=fingerprint(read());saved.lastSyncedAt=saved.lastContactAt||stamp();delete saved.conflict;persist();notify('Synced with Mac mini. Telegram uses this shared record.','synced');}
    function mark(){if(!saved)return;generation++;saved.dirty=true;persist();if(conflict)notify('Your edits remain on this device. Choose which copy to keep.','conflict');else notify(reachable?'Editing. Your update will save when you leave the fields.':'Saved on this device only. Your edits will sync when the Mac mini connection returns.',reachable?'editing':'offline');}
    async function cycle() {
      if(!saved||busy||editing())return;
      if(conflict){notify('Your browser and Mac mini have different numbers. Choose which copy to keep.','conflict');return;}
      if(saved.pending){await connect({...saved,keepLocal:!!saved.dirty});return;}
      busy=true;
      try {
        if(saved.dirty) {
          prepare();const profile=read(),version=generation;
          notify('Saving to Mac mini…','saving');
          const data=await request('PUT',{expectedRevision:saved.revision,profile});
          if(data.conflict)choose(data.conflict,'Another device changed these numbers. Choose which copy to keep.');
          else if(version===generation&&!editing())accept(data);
          else {saved.revision=data.revision;persist();notify('Your newest edits are waiting to save.','editing');}
        } else {
          if(!reachable)notify('Checking the Mac mini connection…','checking');
          const data=await request('GET');
          // Never replace a draft which appeared while the request was in flight.
          if(!saved.dirty&&!editing())accept(data);
        }
      } catch(e){reachable=false;notify(e.message?.includes('connection key')?e.message:'Saved on this device only. Cannot reach Mac mini. Sync will retry automatically.','offline');}
      finally {busy=false;}
    }
    async function connect(config) {
      if(busy)throw new Error('Wait for the current update to finish.');
      const next=validate(config),previous=saved,previousConflict=conflict;saved={...next,pending:true,dirty:!!config.keepLocal};conflict=null;reachable=false;busy=true;notify('Connecting to Mac mini…','checking');
      try {
        const data=await request('GET'),local=read(),remote=J.profile(data.profile);
        saved.revision=data.revision;saved.pending=false;
        // A blank or migrated browser adopts the shared record. Real unconnected edits require a choice.
        if(!config.keepLocal && config.isEmpty&&!saved.dirty&&!editing())accept(data);
        else if(values(local)!==values({...remote,forecast:remote.forecast||local.forecast})) choose(data,'Your browser and Mac mini have different numbers. Choose which copy to keep.');
        else {apply({...remote,journal:J.merge(remote.journal,local.journal)});saved.dirty=fingerprint(read())!==fingerprint(remote);saved.synced=fingerprint(remote);if(!saved.dirty)saved.lastSyncedAt=saved.lastContactAt||stamp();persist();notify(saved.dirty?'Connected. Finishing the history sync…':'Synced with Mac mini. Telegram uses this shared record.',saved.dirty?'saving':'synced');}
      } catch(e){
        reachable=false;
        if(previous&&!previous.pending){saved=previous;conflict=previousConflict;notify(e.message,conflict?'conflict':'offline');throw e;}
        const dirty=!!saved?.dirty||!!config.keepLocal||!!previous?.dirty;
        saved={...validate(config),pending:true,isEmpty:!!config.isEmpty&&!dirty,dirty};persist();
        notify('Saved on this device only. Cannot reach Mac mini. This connection will retry automatically.','offline');
      }finally{busy=false;}
      if(!saved?.pending)await cycle();
    }
    async function resolve(which) {
      if(!conflict)return;
      if(busy)return;
      const data=conflict;
      if(which==='remote') {
        // A choice restored after reload must use the current server copy, not an old snapshot.
        busy=true;const version=generation;
        try {
          const latest=await request('GET');
          if(version!==generation){notify('Your numbers changed while checking. Choose which copy to keep.','conflict');return;}
          conflict=null;accept(latest,{replaceJournal:true});
        } catch(e){reachable=false;notify('Cannot reach Mac mini. Your copy remains unchanged. Choose again when the connection returns.','conflict');}
        finally{busy=false;}
      } else {conflict=null;delete saved.conflict;saved.revision=data.revision;saved.dirty=true;persist();await cycle();}
    }
    function disconnect(){if(busy)throw new Error('Wait for the current update to finish.');saved=null;conflict=null;reachable=false;persist();notify('Disconnected. Your browser copy remains saved.','unpaired');}
    notify(saved?(conflict?'Your browser and Mac mini have different numbers. Choose which copy to keep.':'Checking the Mac mini connection…'):'Connect this browser once to share your record.');
    return {connect,cycle,mark,resolve,disconnect,state,paired:()=>!!saved&&!saved.pending,reachable:()=>reachable,phase:()=>phase,lastSyncedAt:()=>saved?.lastSyncedAt||null,connected:()=>!!saved&&!saved.pending,dirty:()=>!!saved?.dirty,conflict:()=>conflict};
  }
  return {create,connection,fingerprint,values};
});
