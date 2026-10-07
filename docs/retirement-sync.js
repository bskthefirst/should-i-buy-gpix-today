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
  function create({storage,fetcher=fetch,read,apply,prepare,status,editing=()=>false,allowedEndpoint=null}) {
    let saved=null,busy=false,conflict=null,generation=0;
    const validate=v=>{const c=connection(v);if(allowedEndpoint&&c.endpoint!==allowedEndpoint)throw new Error('This connection points to an unrecognized server.');return c;};
    try {const v=JSON.parse(storage.getItem(KEY)||'null');if(v)saved={...v,...validate(v)};}catch(e){status('Connection settings could not be read. Reconnect this browser.');}
    function persist(){if(saved)storage.setItem(KEY,JSON.stringify(saved));else storage.removeItem(KEY);}
    function notify(text){status(text,{connected:!!saved&&!saved.pending,pending:!!saved?.pending,conflict:!!conflict});}
    async function request(method,body) {
      const r=await fetcher(saved.endpoint+'/v1/plan',{method,headers:{Authorization:'Bearer '+saved.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store',signal:AbortSignal.timeout(30000)});
      const data=await r.json();
      if(r.status===409)return {conflict:data};
      if(!r.ok)throw new Error(r.status===401?'This browser needs a new connection key.':'Mac mini unavailable. Your edits stay here and will retry automatically.');
      return data;
    }
    function accept(data){const p=J.profile(data.profile);apply(p);saved.revision=data.revision;saved.dirty=false;saved.synced=fingerprint(read());persist();notify('Synced with Mac mini. Telegram uses this shared record.');}
    function mark(){if(!saved)return;generation++;saved.dirty=true;persist();notify('Editing. Your update will save when you leave the fields.');}
    async function cycle() {
      if(!saved||busy||conflict||editing())return;
      if(saved.pending){await connect({...saved,keepLocal:!!saved.dirty});return;}
      busy=true;
      try {
        if(saved.dirty) {
          prepare();const profile=read(),version=generation;
          notify('Saving to Mac mini…');
          const data=await request('PUT',{expectedRevision:saved.revision,profile});
          if(data.conflict){conflict=data.conflict;notify('Another device changed these numbers. Choose which copy to keep.');}
          else if(version===generation&&!editing())accept(data);
          else {saved.revision=data.revision;persist();notify('Your newest edits are waiting to save.');}
        } else {
          const data=await request('GET');
          // Never replace a draft which appeared while the request was in flight.
          if(!saved.dirty&&!editing())accept(data);
        }
      } catch(e){notify(e.message||'Mac mini unavailable. Your edits will retry automatically.');}
      finally {busy=false;}
    }
    async function connect(config) {
      if(busy)throw new Error('Wait for the current update to finish.');
      const previous=saved;saved={...validate(config),pending:true,dirty:!!config.keepLocal};busy=true;notify('Connecting to Mac mini…');
      try {
        const data=await request('GET'),local=read(),remote=J.profile(data.profile);
        saved.revision=data.revision;saved.pending=false;
        // A blank or migrated browser adopts the shared record. Real unconnected edits require a choice.
        if(!config.keepLocal && config.isEmpty&&!saved.dirty&&!editing())accept(data);
        else if(values(local)!==values({...remote,forecast:remote.forecast||local.forecast})) {conflict=data;persist();notify('Your browser and Mac mini have different numbers. Choose which copy to keep.');}
        else {apply({...remote,journal:J.merge(remote.journal,local.journal)});saved.dirty=fingerprint(read())!==fingerprint(remote);saved.synced=fingerprint(remote);persist();notify('Connected. Finishing the history sync…');}
      } catch(e){
        if(previous&&!previous.pending){saved=previous;throw e;}
        const dirty=!!saved?.dirty||!!config.keepLocal||!!previous?.dirty;
        saved={...validate(config),pending:true,isEmpty:!!config.isEmpty&&!dirty,dirty};persist();
        notify('Mac mini unavailable. This connection will retry automatically.');
      }finally{busy=false;}
      if(!saved?.pending)await cycle();
    }
    async function resolve(which) {
      if(!conflict)return;
      const data=conflict;conflict=null;
      if(which==='remote')accept(data);
      else {saved.revision=data.revision;saved.dirty=true;persist();await cycle();}
    }
    function disconnect(){if(busy)throw new Error('Wait for the current update to finish.');saved=null;conflict=null;persist();notify('Disconnected. Your browser copy remains saved.');}
    return {connect,cycle,mark,resolve,disconnect,connected:()=>!!saved&&!saved.pending,dirty:()=>!!saved?.dirty,conflict:()=>conflict};
  }
  return {create,connection,fingerprint,values};
});
