const test=require('node:test'),assert=require('node:assert/strict'),J=require('../docs/retirement-journal.js'),B=require('../docs/retirement-badges.js');
const at=new Date().toISOString(),month=new Date().toISOString().slice(0,7);
function profile(){const p=J.profile({schemaVersion:2,sharesGpix:10,costGpix:50,sharesGpiq:2,costGpiq:55,sharesGoog:3,costGoog:100,sharesNvda:4,costNvda:150,taxPct:15,target:250,costs:[{name:'One',amount:10,times:1,per:'month'},{name:'Two',amount:20,times:1,per:'month'},{name:'Three',amount:30,times:1,per:'month'}]});J.record(p.journal,p,{Gpix:1,Gpiq:0,Goog:0,Nvda:0},'website',at);return p;}
function months(p,count){for(let i=0;i<count;i++){const d=new Date();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()-i);d.setUTCHours(1,0,0,0);J.checkIn(p,d.toISOString());}}
test('Four Engines rewards reported positions and ignores unsaved holding previews',()=>{
 const p=profile();assert.equal(B.catalog(p,10).find(r=>r.id==='engines').qualifies,true);
 p.sharesGpix++;assert.equal(B.award(p,100).some(r=>r.key==='fun:engines'),false);assert.equal(p.journal.achievements.length,0);
 J.record(p.journal,p,null);assert.equal(B.award(p,null).some(r=>r.key==='fun:engines'),true);
});
test('Dollar a Day uses an after-tax $365 yearly equivalent rather than a daily payment',()=>{
 const p=profile(),target=365/12;assert.equal(B.catalog(p,target-.001).find(r=>r.id==='dollar-day').qualifies,false);assert.equal(B.catalog(p,target).find(r=>r.id==='dollar-day').qualifies,true);
 assert.equal(B.award(p,NaN).some(r=>r.category==='income'),false);
});
test('personal costs require explicit positive amounts and permanently remember the original reward',()=>{
 const p=profile();assert.equal(B.catalog(p,1000).find(r=>r.id==='subscription').configured,false);
 p.badgeGoals={subscription:{name:'My subscription',amount:20},weekend:{name:'My meals',amount:70}};
 B.award(p,25);const earned=p.journal.achievements.find(r=>r.key==='fun:subscription');assert.equal(earned.threshold,20);
 p.badgeGoals.subscription.amount=100;const row=B.catalog(p,1).find(r=>r.id==='subscription');assert.equal(row.on,true);assert.match(row.earned.detail,/20.00/);assert.equal(B.award(p,100).some(r=>r.key==='fun:subscription'),false);
 assert.throws(()=>J.profile({...p,badgeGoals:{subscription:{name:'Invalid',amount:0}}}),/positive/);
});
test('three bills need three distinct saved expenses and use their actual monthly totals',()=>{
 const p=profile();assert.equal(B.catalog(p,60).find(r=>r.id==='bills').qualifies,true);assert.equal(B.catalog(p,59.99).find(r=>r.id==='bills').qualifies,false);
 p.costs[2].name='One';assert.equal(B.catalog(p,1000).find(r=>r.id==='bills').configured,false);
});
test('monthly habits count once per month, survive merges, and do not require purchases',()=>{
 const p=profile();assert.equal(J.checkIn(p),true);assert.equal(J.checkIn(p),false);assert.equal(p.journal.checkins.length,1);
 months(p,3);assert.equal(B.catalog(p,0).find(r=>r.id==='groove').qualifies,true);assert.equal(B.catalog(p,0).find(r=>r.id==='yearbook').qualifies,false);
 months(p,12);B.award(p,0);assert.equal(B.catalog(p,0).find(r=>r.id==='yearbook').on,true);assert.equal(p.journal.purchases.length,0);
 const round=J.profile(JSON.parse(JSON.stringify(p)));assert.equal(J.merge(p.journal,round.journal).checkins.length,12);assert.equal(J.transport(p).journal.checkins.length,12);
});
test('Double Take uses the first positive saved income and stays earned after a decline',()=>{
 const p=profile(),baseline=p.journal.snapshots[0].income;assert.equal(B.catalog(p,baseline*2-.01).find(r=>r.id==='double').qualifies,false);
 B.award(p,baseline*2);assert.equal(B.catalog(p,0).find(r=>r.id==='double').on,true);assert.equal(B.award(p,baseline*3).some(r=>r.key==='fun:double'),false);
});
test('closest goals exclude earned and unset badges and do not mutate the profile',()=>{
 const p=profile(),before=JSON.stringify(p);assert.equal(B.closest(p,20).length,3);assert.equal(JSON.stringify(p),before);B.award(p,40);
 assert.ok(B.closest(p,40).every(r=>r.configured&&!r.on));
});
test('old two-fund profiles keep working with empty confirmations and reward goals',()=>{
 const p=profile();delete p.badgeGoals;delete p.journal.checkins;const old=J.profile(p);assert.deepEqual(old.badgeGoals,{subscription:null,weekend:null});assert.deepEqual(old.journal.checkins,[]);
});
