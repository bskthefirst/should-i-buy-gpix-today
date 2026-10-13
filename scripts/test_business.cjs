const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const B=require('../docs/business-data.js'),Sync=require('../docs/business-sync.js'),Business=require('./business_service.cjs'),{createServer}=require('./retirement_sync_server.cjs');
const now=()=>new Date('2026-10-12T12:00:00Z'),entry=(month,amount,currency='USD')=>({month,amount,currency});
const save=(b,e)=>B.save(b,e,{now:now(),currentMonth:'2026-10'});
function storage(){const map=new Map();return {map,getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};}
async function fixture(){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'business-test-')),token=crypto.randomBytes(32).toString('base64url'),plan=path.join(dir,'plan.json');fs.writeFileSync(plan,'retirement stays untouched');
 const server=createServer({planPath:plan,token});await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const request=(method='GET',body,headers={})=>fetch('http://127.0.0.1:'+server.address().port+'/v1/business',{method,headers:{Authorization:'Bearer '+token,Origin:'https://bskthefirst.github.io',...(body?{'Content-Type':'application/json'}:{}),...headers},body:body?JSON.stringify(body):undefined});
 const fetcher=(_,o)=>request(o.method,o.body?JSON.parse(o.body):undefined);
 const create=(store=storage())=>{store.setItem('retire-sync-v1',JSON.stringify({endpoint:'https://bills-mac-mini.tailf2c209.ts.net:8443',token}));const notices=[];return {store,notices,client:Sync.create({storage:store,fetcher,now,status:(s,m)=>notices.push({s,m})})};};
 return {dir,plan,token,request,fetcher,create,file:path.join(dir,'business.json'),cleanup:async()=>{await new Promise(r=>server.close(r));fs.rmSync(dir,{recursive:true,force:true});}};
}
test('monthly saves update one entry and keep currency amounts separate',()=>{
 let b=save(B.book(),entry('2026-09',100));b=save(b,entry('2026-09',150));b=save(b,entry('2026-09',500000,'KRW'));
 assert.equal(b.records.length,2);assert.equal(B.stats(b,'USD','2026-09').ytd,150);assert.equal(B.stats(b,'KRW','2026-09').ytd,500000);
 assert.throws(()=>save(b,entry('2026-11',100)),/earlier/);assert.throws(()=>save(b,entry('2026-10',-1)),/zero/);assert.throws(()=>B.book({schemaVersion:1,records:[b.records[0],b.records[0]]}),/duplicate/);
});
test('missing months stay unknown and zero revenue is a saved observation',()=>{
 let b=save(B.book(),entry('2026-08',100));b=save(b,entry('2026-10',0));
 const s=B.stats(b,'USD','2026-10');assert.equal(s.change,null);assert.equal(s.average,50);assert.equal(s.count,2);assert.equal(s.ytd,100);assert.equal(s.series.at(-2).amount,null);assert.equal(s.series.at(-1).amount,0);assert.equal(s.runRate,0);
 b=save(b,entry('2026-09',0));const zero=B.stats(b,'USD','2026-10');assert.equal(zero.change,0);assert.equal(zero.percent,null);
});
test('growth, quarters, year boundaries and annual comparison use recorded revenue',()=>{
 let b=B.book();for(const e of [entry('2025-12',800),entry('2026-01',1000),entry('2026-02',1500),entry('2026-04',900)])b=save(b,e);
 const s=B.stats(b,'USD','2026-02');assert.equal(s.percent,50);assert.equal(s.ytd,2500);assert.equal(s.quarters[0].amount,2500);assert.equal(s.quarters[1].count,0);assert.equal(s.runRate,18000);assert.equal(s.best.month,'2026-02');assert.equal(B.shift('2026-01',-1),'2025-12');
});
test('business API authenticates, restricts origins and saves separately from holdings',async()=>{
 const f=await fixture();try{
  assert.equal((await f.request('GET',null,{Authorization:''})).status,401);assert.equal((await f.request('GET',null,{Origin:'https://other.example'})).status,403);
  const initial=await(await f.request()).json();assert.equal(initial.book.records.length,0);assert.equal(fs.existsSync(f.file),false);
  const r=await f.request('PUT',{expectedRevision:initial.revision,book:save(B.book(),entry('2026-10',3500))});assert.equal(r.status,200);
  assert.equal((await r.json()).book.records[0].amount,3500);assert.equal(fs.statSync(f.file).mode&0o777,0o600);assert.equal(fs.readFileSync(f.plan,'utf8'),'retirement stays untouched');
  assert.equal((await f.request('PUT',{expectedRevision:initial.revision,book:B.book()})).status,409);
  assert.equal((await f.request('PUT',{expectedRevision:Business.state(f.file).revision,book:{schemaVersion:1,records:[{...entry('2026-10',-1),updatedAt:now().toISOString()}]}})).status,400);
  assert.equal(Business.state(f.file).book.records[0].amount,3500);
 }finally{await f.cleanup();}
});
test('offline entries survive reload and sync to a second device after reconnection',async()=>{
 const f=await fixture();let offline=true;try{
  const store=storage();store.setItem('retire-sync-v1',JSON.stringify({endpoint:'https://bills-mac-mini.tailf2c209.ts.net:8443',token:f.token}));
  const fetcher=(...args)=>{if(offline)throw new Error('Offline');return f.fetcher(...args);};
  let a=Sync.create({storage:store,fetcher,now});await a.save(entry('2026-10',4000));assert.equal(a.state().phase,'offline');
  a=Sync.create({storage:store,fetcher,now});assert.equal(a.state().book.records[0].amount,4000);
  offline=false;await a.cycle();assert.equal(a.state().phase,'synced');assert.equal(a.state().pending.length,0);
  const b=f.create();await b.client.cycle();assert.equal(b.client.state().book.records[0].amount,4000);
 }finally{await f.cleanup();}
});
test('different months from two devices merge without replacing a recorded month',async()=>{
 const f=await fixture();try{
  const a=f.create(),b=f.create();await a.client.cycle();await b.client.cycle();
  await a.client.save(entry('2026-09',3000));await b.client.save(entry('2026-10',4000));
  assert.equal(b.client.state().phase,'synced');assert.equal(Business.state(f.file).book.records.length,2);await a.client.cycle();assert.equal(a.client.state().book.records.length,2);
 }finally{await f.cleanup();}
});
test('same-month conflicts preserve both entries and require a choice',async()=>{
 const f=await fixture();try{
  const a=f.create(),b=f.create();await a.client.cycle();await b.client.cycle();
  await a.client.save(entry('2026-10',3000));await b.client.save(entry('2026-10',4000));
  assert.equal(b.client.state().phase,'conflict');assert.equal(b.client.state().book.records[0].amount,4000);assert.equal(Business.state(f.file).book.records[0].amount,3000);
  const reloaded=Sync.create({storage:b.store,fetcher:f.fetcher,now});assert.equal(reloaded.state().conflicts[0].local.amount,4000);
  await reloaded.resolve('USD:2026-10','remote');assert.equal(reloaded.state().phase,'synced');assert.equal(reloaded.state().book.records[0].amount,3000);
 }finally{await f.cleanup();}
});
test('keeping a local conflicting entry checks the current shared record before saving',async()=>{
 const f=await fixture();try{
  const a=f.create(),b=f.create();await a.client.cycle();await b.client.cycle();await a.client.save(entry('2026-10',3000));await b.client.save(entry('2026-10',4000));
  await a.client.save(entry('2026-10',3500));await b.client.resolve('USD:2026-10','local');assert.equal(b.client.state().phase,'conflict');
  await b.client.resolve('USD:2026-10','local');assert.equal(b.client.state().phase,'synced');assert.equal(Business.state(f.file).book.records[0].amount,4000);
 }finally{await f.cleanup();}
});
test('a second tab or storage failure cannot overwrite locally saved records',async()=>{
 const store=storage(),a=Sync.create({storage:store,now});await a.save(entry('2026-09',100));
 const stale=Sync.create({storage:store,now});await a.save(entry('2026-10',200));
 await assert.rejects(stale.save(entry('2026-08',300)),/Another tab/);assert.equal(JSON.parse(store.getItem('business-records-v1')).book.records.length,2);
 const blocked={getItem:()=>null,setItem:()=>{throw new Error('Full');}},c=Sync.create({storage:blocked,now});await assert.rejects(c.save(entry('2026-10',500)),/Full/);assert.equal(c.state().book.records.length,0);
});
test('business tabs refresh shared browser storage without getting stuck offline',async()=>{
 const f=await fixture();try{
  const store=storage(),a=f.create(store);await a.client.cycle();const b=Sync.create({storage:store,fetcher:f.fetcher,now});
  await a.client.save(entry('2026-10',5000));await b.cycle();assert.equal(b.state().phase,'synced');assert.equal(b.state().book.records[0].amount,5000);
  await a.client.cycle();assert.equal(a.client.state().phase,'synced');
 }finally{await f.cleanup();}
});
