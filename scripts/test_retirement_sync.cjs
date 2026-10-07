const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const J=require('./retirement-journal.js'),Sync=require('../docs/retirement-sync.js');
const {createServer}=require('./retirement_sync_server.cjs');
const {operate,state}=require('./retirement_service.cjs');
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
