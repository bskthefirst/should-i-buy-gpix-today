const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const J=require('./retirement-journal.js');
const {operate,card}=require('./retirement_service.cjs');
const at=new Date().toISOString();
const profile=()=>J.profile({schemaVersion:2,exportedAt:at,sharesGpix:50.25,sharesGpiq:3,costGpix:52,costGpiq:56,taxPct:15,target:250,costs:[{name:'Claude',amount:20,times:1,per:'month'}]});
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);
function fixture(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'retirement-test-')),file=path.join(dir,'plan.json');
  fs.writeFileSync(file,JSON.stringify(profile()));
  for(const [ticker,name] of [['GPIX','data.json'],['GPIQ','data-gpiq.json']])fs.writeFileSync(path.join(dir,name),JSON.stringify({ticker,generated_at:at,fund:{price:55},distributions:{last_amount:.4,ttm_sum:4.8,last_ex:at.slice(0,10)}}));
  return {dir,file,cleanup:()=>fs.rmSync(dir,{recursive:true,force:true})};
}
test('reports preserve fractions and averages without inventing trades or duplicate reports',()=>{
  const p=profile(),j=p.journal;
  assert.equal(J.record(j,p,{Gpix:.4,Gpiq:.5}),true);
  assert.equal(J.record(j,p,{Gpix:.6,Gpiq:.7}),false);
  p.sharesGpix+=1;p.costGpix=52.05;
  assert.equal(J.record(j,p,{Gpix:.6,Gpiq:.7}),true);
  close(j.snapshots[0].sharesGpix,50.25);close(j.snapshots[0].costGpix,52);
  close(j.snapshots[1].sharesGpix-j.snapshots[0].sharesGpix,1);
  assert.equal('purchasePrice' in j.snapshots[1],false);
});
test('income change attribution sums exactly across shares, payouts and tax, ignoring basis',()=>{
  const p=profile(),old=J.snapshot({...p,id:'old',source:'test',at,rates:{Gpix:.4,Gpiq:.5}});
  p.sharesGpix+=3.5;p.sharesGpiq-=1;p.taxPct=15.4;p.costGpix=90;
  const changes=J.changes(old,p,{Gpix:.45,Gpiq:.47});
  close(changes.total,changes.shares+changes.payouts+changes.tax);
  close(changes.total,J.income(p,{Gpix:.45,Gpiq:.47})-old.income);
  p.costGpix=0;close(J.changes(old,p,{Gpix:.45,Gpiq:.47}).total,changes.total);
});
test('milestone memories persist through declines and never repeat',()=>{
  const p=profile(),rows=J.milestones(p),j=p.journal;
  J.award(j,rows,15,at,true);
  assert.equal(j.achievements[0].baseline,true);
  assert.equal(J.award(j,rows,30).length,2);
  assert.equal(J.award(j,rows,0).length,0);
  assert.equal(J.award(j,rows,30).length,0);
  assert.equal(j.achievements.length,3);
});
test('Unicode profiles and histories round-trip, merge idempotently, and reject changed records',()=>{
  const p=profile();p.selectedExpense={name:'커피',amount:25};J.record(p.journal,p,{Gpix:.4,Gpiq:.5});
  const restored=J.profile(JSON.parse(JSON.stringify(p)));
  assert.deepEqual(restored,p);
  assert.equal(J.merge(p.journal,restored.journal).snapshots.length,1);
  restored.journal.snapshots[0].sharesGpix+=1;
  assert.throws(()=>J.merge(p.journal,restored.journal),/conflicts/);
  const lite=J.transport(p);
  assert.equal(lite.journal,undefined);assert.equal(lite.costGpix,52);
  assert.equal(J.profile(JSON.parse(Buffer.from(Buffer.from(JSON.stringify(lite)).toString('base64url'),'base64url').toString())).selectedExpense.name,'커피');
});
test('bad numbers and stale feeds cannot award income milestones',()=>{
  for(const n of [true,NaN,Infinity,-1,'50'])assert.throws(()=>J.profile({...profile(),sharesGpix:n}));
  const p=profile(),feed={ticker:'GPIX',generated_at:new Date(Date.now()-5*86400000).toISOString(),fund:{price:55},distributions:{last_amount:.4,ttm_sum:4.8,last_ex:at.slice(0,10)}};
  assert.throws(()=>J.observe(p,{GPIX:feed,GPIQ:{...feed,ticker:'GPIQ'}}),/stale/);
});
test('Telegram updates totals, preserves history, supports expenses and exports a website file',async()=>{
  const f=fixture();try{
    await operate(f.file,'baseline','',f.dir);
    const reply=await operate(f.file,'holdings','GPIX 51.25 52.10 GPIQ 3 56',f.dir);
    assert.match(reply.text,/TOTAL holdings/);
    let p=J.profile(JSON.parse(fs.readFileSync(f.file)));
    assert.equal(p.journal.snapshots.length,2);close(p.sharesGpix,51.25);
    await operate(f.file,'holdings','GPIX 51.25 52.10 GPIQ 3 56',f.dir);
    await operate(f.file,'expense','Phone 25',f.dir);
    const result=await operate(f.file,'export','',f.dir);
    p=J.profile(JSON.parse(fs.readFileSync(result.mediaUrl)));
    assert.equal(p.selectedExpense.name,'Phone');assert.equal(p.journal.snapshots.length,2);
    assert.equal(fs.statSync(result.mediaUrl).mode&0o777,0o600);
    assert.match((await operate(f.file,'history','',f.dir)).text,/not verified trades/);
  }finally{f.cleanup();}
});
test('invalid and stale Telegram imports leave the saved record untouched',async()=>{
  const f=fixture();try{
    await operate(f.file,'holdings','GPIX 51.25 52.10 GPIQ 3 56',f.dir);
    const before=fs.readFileSync(f.file,'utf8');
    await assert.rejects(operate(f.file,'holdings','GPIX 1..2 52 GPIQ 3 56',f.dir));
    await assert.rejects(operate(f.file,'import','invalid!',f.dir));
    const old={...profile(),exportedAt:'2020-01-01T00:00:00.000Z'};
    await assert.rejects(operate(f.file,'import',Buffer.from(JSON.stringify(old)).toString('base64url'),f.dir),/older/);
    assert.equal(fs.readFileSync(f.file,'utf8'),before);
  }finally{f.cleanup();}
});
test('weekly summaries celebrate once, compare prior reports, and retain milestone dates after recovery',async()=>{
  const f=fixture();try{
    await operate(f.file,'baseline','',f.dir);
    await operate(f.file,'holdings','GPIX 100 52 GPIQ 3 56',f.dir);
    let reply=await operate(f.file,'weekly','',f.dir);
    assert.match(reply.text,/First recorded this week/);assert.match(reply.text,/Share-count effect/);
    assert.doesNotMatch((await operate(f.file,'weekly','',f.dir)).text,/First recorded this week/);
    await operate(f.file,'holdings','GPIX 1 52 GPIQ 3 56',f.dir);
    await operate(f.file,'weekly','',f.dir);
    await operate(f.file,'holdings','GPIX 100 52 GPIQ 3 56',f.dir);
    assert.doesNotMatch((await operate(f.file,'weekly','',f.dir)).text,/First recorded this week/);
  }finally{f.cleanup();}
});
test('the OpenClaw plugin refuses other senders, groups and bot accounts',async()=>{
  const f=fixture();try{
    const {default:plugin}=await import('./openclaw-retirement/index.js');const commands={};
    plugin.register({pluginConfig:{ownerId:'1234',nodePath:process.execPath,servicePath:path.join(__dirname,'retirement_service.cjs'),planPath:f.file,mediaDir:path.join(f.dir,'media')},registerCommand:c=>commands[c.name]=c});
    const owner={channel:'telegram',isAuthorizedSender:true,senderId:'1234',accountId:'default',to:'telegram:1234',args:''};
    assert.match((await commands.holdings.handler(owner)).text,/Update total holdings/);
    const exported=await commands.retire_export.handler(owner);
    assert.equal(path.dirname(exported.mediaUrl),path.join(f.dir,'media'));
    for(const patch of [{senderId:'999'},{to:'telegram:-999'},{accountId:'other'},{isAuthorizedSender:false},{channel:'discord'}])assert.match((await commands.holdings.handler({...owner,...patch})).text,/owner’s private/);
  }finally{f.cleanup();}
});
