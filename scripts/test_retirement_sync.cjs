const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const J=require('./retirement-journal.js'),Sync=require('../docs/retirement-sync.js');
const {createServer}=require('./retirement_sync_server.cjs');
const {operate,state,locked}=require('./retirement_service.cjs');
function profile(){return J.profile({schemaVersion:2,sharesGpix:50.25,sharesGpiq:3,costGpix:52,costGpiq:56,taxPct:15,target:250,costs:[{name:'Claude',amount:20,times:1,per:'month'}],selectedExpense:{name:'Claude',amount:20},forecast:{monthly:1000,gpixPct:80,inflation:2.5,lunchbox:0,homeCost:6,scenario:'base',whole:true}});}
async function fixture(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'retirement-sync-')),file=path.join(dir,'plan.json'),token=crypto.randomBytes(32).toString('base64url');
  fs.writeFileSync(file,JSON.stringify(profile()));
  const at=new Date().toISOString();
  for(const [ticker,name]of[['GPIX','data.json'],['GPIQ','data-gpiq.json']])fs.writeFileSync(path.join(dir,name),JSON.stringify({ticker,generated_at:at,fund:{price:55},distributions:{last_amount:.4,ttm_sum:4.8,last_ex:at.slice(0,10)}}));
  await operate(file,'baseline','',dir);
  const server=createServer({planPath:file,token},{dataDir:dir});await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const url='http://127.0.0.1:'+server.address().port+'/v1/plan';
  const request=(method='GET',body,headers={})=>fetch(url,{method,headers:{Authorization:'Bearer '+token,Origin:'https://bskthefirst.github.io',...(body?{'Content-Type':'application/json'}:{}),...headers},body:body?JSON.stringify(body):undefined});
  return {dir,file,token,request,server,url,cleanup:async()=>{await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true});}};
}
test('HTTPS connection files reject insecure addresses and invalid keys',()=>{
  const token=crypto.randomBytes(32).toString('base64url');
  assert.throws(()=>Sync.connection({endpoint:'http://example.com',token}),/HTTPS/);
  assert.throws(()=>Sync.connection({endpoint:'https://example.com',token:'short'}),/invalid/);
  assert.throws(()=>Sync.connection({endpoint:'https://user:pass@example.com',token}),/HTTPS/);
  assert.equal(Sync.connection({endpoint:'https://example.com/',token}).endpoint,'https://example.com');
});
test('the API requires authentication, limits origins, and never serves private files',async()=>{
  const f=await fixture();try{
    assert.equal((await f.request('GET',null,{Authorization:''})).status,401);
    assert.equal((await f.request('GET',null,{Authorization:'Bearer '+'é'.repeat(43)})).status,401);
    assert.equal((await f.request('GET',null,{Origin:'https://other.example'})).status,403);
    assert.equal((await fetch(f.url.replace('/v1/plan','/plan.json'),{headers:{Authorization:'Bearer '+f.token}})).status,404);
    const r=await f.request('OPTIONS',null,{Authorization:''});assert.equal(r.status,204);assert.equal(r.headers.get('access-control-allow-origin'),'https://bskthefirst.github.io');
    assert.equal((await f.request('GET')).headers.get('cache-control'),'no-store');
  }finally{await f.cleanup();}
});
test('two devices share purchases, average prices and history without stale overwrites',async()=>{
  const f=await fixture();try{
    const first=await(await f.request()).json(),p=first.profile;p.sharesGpix+=1;p.costGpix=52.10;p.forecast.monthly=1500;
    const r=await f.request('PUT',{expectedRevision:first.revision,profile:p});assert.equal(r.status,200);
    const next=await r.json();assert.equal(next.profile.sharesGpix,51.25);assert.equal(next.profile.costGpix,52.10);assert.equal(next.profile.forecast.monthly,1500);assert.equal(next.profile.journal.snapshots.length,2);
    const stale=await f.request('PUT',{expectedRevision:first.revision,profile:first.profile});assert.equal(stale.status,409);assert.equal((await(await f.request()).json()).profile.sharesGpix,51.25);
    const again=await f.request('PUT',{expectedRevision:next.revision,profile:next.profile});assert.equal(again.status,200);assert.equal((await again.json()).profile.journal.snapshots.length,2);
    const invalid={...next.profile,sharesGpix:-1};const before=fs.readFileSync(f.file,'utf8');assert.equal((await f.request('PUT',{expectedRevision:state(f.file).revision,profile:invalid})).status,400);assert.equal(fs.readFileSync(f.file,'utf8'),before);
    assert.equal(fs.statSync(f.file).mode&0o777,0o600);
  }finally{await f.cleanup();}
});
function browser(fetcher,p=profile()){
  const map=new Map(),messages=[];let current=p,isEditing=false;
  const storage={getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};
  const options={storage,fetcher,read:()=>current,apply:p=>{current=p;},prepare:()=>{J.record(current.journal,current,{Gpix:.4,Gpiq:.4});},status:t=>messages.push(t),editing:()=>isEditing};
  const client=Sync.create(options);
  return {client,map,messages,options,get p(){return current;},edit:n=>{current.sharesGpix=n;client.mark();},editing:v=>{isEditing=v;}};
}
test('offline drafts survive reload, retry automatically, and refresh a second device',async()=>{
  const f=await fixture();let offline=false;try{
    const fetcher=async(_,options)=>{if(offline)throw new Error('Offline');return f.request(options.method,options.body?JSON.parse(options.body):undefined);};
    const a=browser(fetcher),b=browser(fetcher);const config={endpoint:'https://sync.example',token:f.token};
    await a.client.connect(config);await b.client.connect(config);
    a.editing(true);a.edit(51.25);await a.client.cycle();assert.equal(state(f.file).profile.sharesGpix,50.25);
    offline=true;a.editing(false);await a.client.cycle();assert.equal(a.client.dirty(),true);
    const reloaded=Sync.create(a.options);assert.equal(reloaded.dirty(),true);
    offline=false;await reloaded.cycle();assert.equal(state(f.file).profile.sharesGpix,51.25);
    await b.client.cycle();assert.equal(b.p.sharesGpix,51.25);assert.equal(reloaded.dirty(),false);
  }finally{await f.cleanup();}
});
test('concurrent edits require a choice and preserve the local draft until resolved',async()=>{
  const f=await fixture();try{
    const fetcher=(_,o)=>f.request(o.method,o.body?JSON.parse(o.body):undefined),a=browser(fetcher),b=browser(fetcher),config={endpoint:'https://sync.example',token:f.token};
    await a.client.connect(config);await b.client.connect(config);a.edit(51.25);await a.client.cycle();b.edit(52.25);await b.client.cycle();
    assert.ok(b.client.conflict());assert.equal(b.p.sharesGpix,52.25);assert.equal(state(f.file).profile.sharesGpix,51.25);
    await b.client.resolve('remote');assert.equal(b.p.sharesGpix,51.25);assert.equal(b.client.dirty(),false);
  }finally{await f.cleanup();}
});
test('a Telegram edit reaches connected browsers through the same record',async()=>{
  const f=await fixture();try{
    const b=browser((_,o)=>f.request(o.method,o.body?JSON.parse(o.body):undefined));await b.client.connect({endpoint:'https://sync.example',token:f.token});
    await operate(f.file,'holdings','GPIX 53.25 52.30 GPIQ 3 56',f.dir);await b.client.cycle();assert.equal(b.p.sharesGpix,53.25);assert.equal(b.p.costGpix,52.30);
  }finally{await f.cleanup();}
});
test('a first connection retries after the HTTPS server becomes reachable',async()=>{
  const f=await fixture();let offline=true;try{
    const b=browser(async(_,o)=>{if(offline)throw new Error('Offline');return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    await b.client.connect({endpoint:'https://sync.example',token:f.token});assert.equal(b.client.connected(),false);
    offline=false;await b.client.cycle();assert.equal(b.client.connected(),true);assert.equal(b.p.sharesGpix,50.25);
  }finally{await f.cleanup();}
});
test('a saved connection does not show as reachable after a network failure',async()=>{
  const f=await fixture();let offline=false;const notices=[];
  try {
    const b=browser(async(_,o)=>{if(offline)throw new Error('Offline');return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    const client=Sync.create({...b.options,status:(text,info)=>notices.push({text,info})});
    await client.connect({endpoint:'https://sync.example',token:f.token});assert.equal(notices.at(-1).info.connected,true);
    offline=true;await client.cycle();assert.equal(notices.at(-1).info.connected,false);assert.equal(notices.at(-1).info.paired,true);assert.match(notices.at(-1).text,/this device only/);
    offline=false;await client.cycle();assert.equal(notices.at(-1).info.connected,true);
  }finally{await f.cleanup();}
});
test('edits made during a failed first connection remain a draft after retry',async()=>{
  const f=await fixture();let rejectFirst,first=true;try{
    const b=browser((_,o)=>{if(first){first=false;return new Promise((resolve,reject)=>{rejectFirst=reject;});}return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    const pending=b.client.connect({endpoint:'https://sync.example',token:f.token,isEmpty:true});
    b.edit(51.75);rejectFirst(new Error('Offline'));await pending;assert.equal(b.client.dirty(),true);
    await b.client.cycle();assert.ok(b.client.conflict());assert.equal(b.p.sharesGpix,51.75);assert.equal(state(f.file).profile.sharesGpix,50.25);
  }finally{await f.cleanup();}
});
test('a connection link cannot redirect a paired website to a different server',async()=>{
  let requests=0;const b=browser(async()=>{requests++;});const c=Sync.create({...b.options,allowedEndpoint:'https://trusted.example'});
  await assert.rejects(c.connect({endpoint:'https://other.example',token:crypto.randomBytes(32).toString('base64url')}),/unrecognized/);assert.equal(requests,0);
});
test('updates recover an abandoned process lock and refuse an active process lock',async()=>{
  const f=await fixture();try{
    fs.writeFileSync(f.file+'.lock',JSON.stringify({pid:99999999}));assert.equal(await locked(f.file,()=>42),42);assert.equal(fs.existsSync(f.file+'.lock'),false);
    fs.writeFileSync(f.file+'.lock',JSON.stringify({pid:process.pid}));await assert.rejects(locked(f.file,()=>42),/running/);fs.unlinkSync(f.file+'.lock');
  }finally{await f.cleanup();}
});
test('status distinguishes local drafts from a saved shared record and keeps browser and server clocks separate',async()=>{
  const f=await fixture();let offline=false,clock='2030-01-02T03:04:05.000Z';const notices=[];
  try {
    const b=browser(async(_,o)=>{if(offline)throw new Error('Offline');return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    const client=Sync.create({...b.options,now:()=>clock,status:(text,info)=>notices.push({text,info})});
    assert.equal(client.phase(),'unpaired');assert.equal(client.state().lastSyncedAt,null);
    await client.connect({endpoint:'https://sync.example',token:f.token});
    assert.equal(client.phase(),'synced');assert.equal(client.lastSyncedAt(),clock);
    assert.equal(client.state().lastContactAt,clock);assert.equal(client.state().serverUpdatedAt,state(f.file).updatedAt);
    b.p.sharesGpix=51.25;client.mark();
    assert.equal(client.phase(),'editing');assert.equal(client.state().connected,false);assert.equal(client.paired(),true);
    assert.equal(client.lastSyncedAt(),clock);
    offline=true;clock='2030-01-02T03:05:05.000Z';await client.cycle();
    assert.equal(client.phase(),'offline');assert.equal(client.reachable(),false);assert.equal(client.dirty(),true);
    assert.equal(client.state().lastContactAt,'2030-01-02T03:04:05.000Z');assert.equal(client.lastSyncedAt(),'2030-01-02T03:04:05.000Z');
    assert.ok(notices.some(n=>n.info.phase==='saving'&&!n.info.connected));
    offline=false;clock='2030-01-02T03:06:05.000Z';await client.cycle();
    assert.equal(client.phase(),'synced');assert.equal(client.state().connected,true);assert.equal(client.lastSyncedAt(),clock);
    client.disconnect();assert.equal(client.phase(),'unpaired');assert.equal(client.reachable(),false);assert.equal(client.lastSyncedAt(),null);
  }finally{await f.cleanup();}
});
test('reload checks a saved connection before displaying it as shared and clean polling does not flash checking',async()=>{
  const f=await fixture();const notices=[];try {
    const b=browser((_,o)=>f.request(o.method,o.body?JSON.parse(o.body):undefined));await b.client.connect({endpoint:'https://sync.example',token:f.token});
    const last=b.client.lastSyncedAt(),client=Sync.create({...b.options,status:(text,info)=>notices.push(info)});
    assert.equal(client.phase(),'checking');assert.equal(client.state().connected,false);assert.equal(client.paired(),true);
    assert.equal(client.lastSyncedAt(),last);assert.equal(client.reachable(),false);
    await client.cycle();assert.equal(client.phase(),'synced');
    notices.length=0;await client.cycle();assert.ok(notices.length);assert.ok(notices.every(n=>n.phase==='synced'));
  }finally{await f.cleanup();}
});
test('a conflicting draft survives reload and accepting the shared copy checks its latest version',async()=>{
  const f=await fixture();try {
    const fetcher=(_,o)=>f.request(o.method,o.body?JSON.parse(o.body):undefined),a=browser(fetcher),b=browser(fetcher),config={endpoint:'https://sync.example',token:f.token};
    await a.client.connect(config);await b.client.connect(config);a.edit(51.25);await a.client.cycle();b.edit(52.25);await b.client.cycle();
    assert.equal(b.client.phase(),'conflict');assert.equal(b.client.state().connected,false);
    const restored=Sync.create(b.options);assert.equal(restored.phase(),'conflict');assert.ok(restored.conflict());
    await restored.cycle();assert.equal(b.p.sharesGpix,52.25);assert.equal(restored.dirty(),true);
    a.edit(53.25);await a.client.cycle();await restored.resolve('remote');
    assert.equal(b.p.sharesGpix,53.25);assert.equal(restored.phase(),'synced');assert.equal(restored.conflict(),null);
  }finally{await f.cleanup();}
});
test('an initial differing copy and failed conflict resolution do not silently overwrite local holdings',async()=>{
  const f=await fixture();let offline=false;try {
    const b=browser(async(_,o)=>{if(offline)throw new Error('Offline');return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    b.p.sharesGpix=60;await b.client.connect({endpoint:'https://sync.example',token:f.token});
    assert.equal(b.client.phase(),'conflict');assert.equal(b.client.lastSyncedAt(),null);
    const restored=Sync.create(b.options);await restored.cycle();assert.equal(b.p.sharesGpix,60);
    offline=true;await restored.resolve('remote');assert.equal(b.p.sharesGpix,60);assert.ok(restored.conflict());assert.equal(restored.state().connected,false);
    offline=false;await restored.resolve('local');assert.equal(state(f.file).profile.sharesGpix,60);assert.equal(restored.phase(),'synced');
  }finally{await f.cleanup();}
});
test('a newer draft during an upload never receives a shared-saved timestamp',async()=>{
  const f=await fixture();let held=null,holdPut=false,clock='2030-01-02T03:04:05.000Z';try {
    const b=browser((_,o)=>{if(holdPut&&o.method==='PUT')return new Promise(resolve=>{held=()=>resolve(f.request(o.method,JSON.parse(o.body)));});return f.request(o.method,o.body?JSON.parse(o.body):undefined);});
    const client=Sync.create({...b.options,now:()=>clock});await client.connect({endpoint:'https://sync.example',token:f.token});
    const last=client.lastSyncedAt();b.p.sharesGpix=51.25;client.mark();holdPut=true;clock='2030-01-02T03:05:05.000Z';
    const saving=client.cycle();assert.equal(client.phase(),'saving');assert.equal(client.state().connected,false);
    b.p.sharesGpix=52.25;client.mark();held();await saving;
    assert.equal(client.phase(),'editing');assert.equal(client.dirty(),true);assert.equal(client.lastSyncedAt(),last);
    assert.equal(client.state().lastContactAt,clock);assert.equal(b.p.sharesGpix,52.25);assert.equal(state(f.file).profile.sharesGpix,51.25);
    holdPut=false;await client.cycle();assert.equal(state(f.file).profile.sharesGpix,52.25);assert.equal(client.phase(),'synced');
  }finally{await f.cleanup();}
});

test('concurrent recorded purchases can be reviewed against latest totals without losing or duplicating shares',async()=>{
  const f=await fixture();try{
    const fetcher=(_,o)=>f.request(o.method,o.body?JSON.parse(o.body):undefined);
    const a=browser(fetcher),b=browser(fetcher),config={endpoint:'https://sync.example',token:f.token};
    await a.client.connect(config);await b.client.connect(config);
    const today=new Date().toISOString().slice(0,10);
    const inputA={id:'purchase-device-a',ticker:'GPIX',shares:2,price:60,date:today};
    const inputB={id:'purchase-device-b',ticker:'GPIX',shares:1.25,price:56,date:today};
    a.options.apply(J.preparePurchase(a.p,inputA).profile);a.client.mark();
    b.options.apply(J.preparePurchase(b.p,inputB).profile);b.client.mark();
    await a.client.cycle();await b.client.cycle();assert.equal(b.client.phase(),'conflict');
    // The frontend keeps inputB separately, then discards its unapplied local report on remote choice.
    let replace=false;const originalApply=b.options.apply;
    b.options.apply=(p,options)=>{replace=!!options?.replaceJournal;originalApply(p);};
    const reloaded=Sync.create(b.options);await reloaded.resolve('remote');
    assert.equal(replace,true);assert.equal(b.p.sharesGpix,52.25);assert.equal(b.p.journal.purchases.length,1);
    b.options.apply(J.preparePurchase(b.p,inputB).profile);reloaded.mark();await reloaded.cycle();
    assert.equal(reloaded.phase(),'synced');const shared=state(f.file).profile;
    assert.equal(shared.sharesGpix,53.5);assert.equal(shared.journal.purchases.length,2);
    assert.equal(shared.costGpix,(50.25*52+2*60+1.25*56)/53.5);
    await a.client.cycle();assert.equal(a.p.sharesGpix,53.5);
    assert.equal(J.preparePurchase(shared,inputB).applied,false);
  }finally{await f.cleanup();}
});
