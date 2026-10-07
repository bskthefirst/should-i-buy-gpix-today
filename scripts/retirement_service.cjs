#!/usr/bin/env node
// Private service used by OpenClaw commands and the single weekly job.
const fs = require('node:fs');
const path = require('node:path');
const J = require('./retirement-journal.js');
const BASE = 'https://bskthefirst.github.io/should-i-buy-gpix-today/';
const signed = n => (n < -1e-8 ? '−' : '+') + '$' + Math.abs(n).toFixed(2);
const quantity = n => Number(n.toFixed(6)).toString();
const money = n => '$' + n.toFixed(2);
const help = 'Update total holdings, not the number just bought:\n/holdings GPIX 57.71 54.70 GPIQ 3 56.43\nEach pair is total shares and average purchase price in USD. Use your brokerage figures.\n\n/dividends — current progress\n/expense Claude 20 — choose one monthly expense\n/retire_history — reported changes\n/retire_export — file for the website\nPaste the website’s copied /retire_import command to update the bot.';
function read(file) { return JSON.parse(fs.readFileSync(file,'utf8')); }
function write(file,value) {
  const temporary = file + '.' + process.pid + '.tmp';
  fs.writeFileSync(temporary,JSON.stringify(value,null,2),{mode:0o600});
  fs.renameSync(temporary,file); fs.chmodSync(file,0o600);
}
async function feeds(directory) {
  const results = await Promise.all(['GPIX','GPIQ'].map(async ticker => {
    const name=ticker==='GPIX'?'data.json':'data-gpiq.json';
    if(directory) return [ticker,read(path.join(directory,name))];
    const res=await fetch(BASE+name,{headers:{'Cache-Control':'no-cache'},signal:AbortSignal.timeout(20000)});
    if(!res.ok) throw new Error('Could not refresh '+ticker+' market data.');
    return [ticker,await res.json()];
  }));
  return Object.fromEntries(results);
}
function card(p,ob,old,celebrations=[]) {
  const rows=J.milestones(p),next=rows.find(r=>ob.average<r.threshold-1e-9);
  const lines=['Your weekly dividend journey',money(ob.average)+'/month estimated after '+p.taxPct+'% tax','Based on the past year’s payouts. Latest monthly estimate: '+money(ob.latest)+'.'];
  if(next) {
    const percent=Math.min(99,Math.floor(ob.average/next.threshold*100)),bars=Math.round(percent/10);
    lines.push('',next.title+' · '+percent+'% covered','▰'.repeat(bars)+'▱'.repeat(10-bars),'About '+money(Math.max(.01,Math.ceil((next.threshold-ob.average)*100-1e-9)/100))+'/month left.');
  } else lines.push('All current milestones reached.');
  if(p.selectedExpense) {
    const e=p.selectedExpense,pct=Math.min(100,ob.average/e.amount*100);
    lines.push('Your chosen expense: '+e.name+' · '+pct.toFixed(1)+'% of '+money(e.amount)+'/month.',ob.average>=e.amount?'Your current estimate covers this expense.':'Remaining: '+money(Math.ceil((e.amount-ob.average)*100-1e-9)/100)+'/month.');
  }
  const diff=J.changes(old,p,ob.rates);
  if(diff) lines.push('','What changed since '+old.at.slice(0,10)+'?',signed(diff.total)+'/month in total.','Reported share changes: GPIX '+(diff.deltaGpix<0?'':'+')+quantity(diff.deltaGpix)+', GPIQ '+(diff.deltaGpiq<0?'':'+')+quantity(diff.deltaGpiq)+'.','Share-count effect: '+signed(diff.shares)+'. Payout effect: '+signed(diff.payouts)+'. Tax-rate effect: '+signed(diff.tax)+'.');
  else lines.push('','This is your first comparison. Future digests will explain changes from this baseline.');
  if(celebrations.length) lines.push('','First recorded this week: '+celebrations.map(r=>r.title).join(', ')+'.');
  lines.push('',rows.filter(r=>ob.average>=r.threshold-1e-9).length+'/'+rows.length+' current income milestones. Past milestones remain in your timeline.','Saved holdings: GPIX '+quantity(p.sharesGpix)+' · GPIQ '+quantity(p.sharesGpiq)+'.','No brokerage connection. Changes are your reports, not verified trades.','Data: '+ob.at.replace('T',' ').slice(0,16)+' UTC. Future payouts can change.',BASE+'retire.html#sec-journey','Use /holdings to report changes or /retire_export to update the website.');
  return lines.join('\n');
}
async function operate(file,action,args='',dataDir=null) {
  const raw=read(file),p=J.profile(raw);
  if(action==='help') return {text:help};
  if(action==='history') {
    const rows=p.journal.snapshots;
    const lines=['Reported holdings history (not verified trades)'];
    rows.slice(-8).forEach((r,i)=> {
      const index=rows.indexOf(r),old=rows[index-1];
      lines.push(r.at.slice(0,10)+' · '+r.source+' · GPIX '+quantity(r.sharesGpix)+' @ '+money(r.costGpix)+' · GPIQ '+quantity(r.sharesGpiq)+' @ '+money(r.costGpiq)+(old?' · share change '+(r.sharesGpix-old.sharesGpix>=0?'+':'')+quantity(r.sharesGpix-old.sharesGpix)+' / '+(r.sharesGpiq-old.sharesGpiq>=0?'+':'')+quantity(r.sharesGpiq-old.sharesGpiq):' · baseline'));
    });
    lines.push('Use /retire_export for the full record.'); return {text:lines.join('\n')};
  }
  if(action==='export') {
    p.exportedAt=new Date().toISOString();
    const output=path.join(path.dirname(file),'retirement-telegram-plan.json'); write(output,p);
    return {text:'Your private holdings, average purchase prices, chosen expense, and history. On the website, choose Import from Telegram, then review and apply this file. Website and bot updates are manual.',mediaUrl:output};
  }
  const before=JSON.parse(JSON.stringify(p));
  let changed=false,confirmation='';
  if(action==='holdings') {
    const match=/^GPIX\s+([\d.]+)\s+([\d.]+)\s+GPIQ\s+([\d.]+)\s+([\d.]+)$/i.exec(args.trim());
    if(!match) return {text:help};
    [p.sharesGpix,p.costGpix,p.sharesGpiq,p.costGpiq]=match.slice(1).map(Number);
    J.profile(p); changed=true;
    confirmation='Saved your reported TOTAL holdings: GPIX '+quantity(p.sharesGpix)+' @ '+money(p.costGpix)+'; GPIQ '+quantity(p.sharesGpiq)+' @ '+money(p.costGpiq)+'.\nAverage prices describe all shares you hold now. This is not a trade confirmation.\n\n';
  } else if(action==='expense') {
    const match=/^(.{1,30})\s+([\d.]+)$/.exec(args.trim());
    if(!match) return {text:'Choose one monthly expense:\n/expense Claude 20\nThe amount is USD per month.'};
    p.selectedExpense=J.expense({name:match[1],amount:Number(match[2])}); changed=true;
    confirmation='Chosen expense saved: '+p.selectedExpense.name+' · '+money(p.selectedExpense.amount)+'/month.\n\n';
  } else if(action==='import') {
    if(args.length>10000 || !/^[A-Za-z0-9_-]+$/.test(args)) throw new Error('Invalid website update. Copy a fresh command from the website.');
    const imported=J.profile(JSON.parse(Buffer.from(args,'base64url').toString('utf8')));
    if(raw.updatedAt && Date.parse(imported.exportedAt)<Date.parse(raw.updatedAt)) throw new Error('This website update is older than the bot’s last edit. Copy a fresh update after reviewing the current holdings.');
    for(const k of ['sharesGpix','sharesGpiq','costGpix','costGpiq','taxPct','target','costs','selectedExpense']) p[k]=imported[k];
    p.journal=J.merge(p.journal,imported.journal); changed=true;
    confirmation='Website update saved. GPIX '+quantity(p.sharesGpix)+' @ '+money(p.costGpix)+'; GPIQ '+quantity(p.sharesGpiq)+' @ '+money(p.costGpiq)+'.\nThe weekly digest now uses these numbers.\n\n';
  } else if(!['weekly','progress','baseline'].includes(action)) throw new Error('Unknown retirement action.');
  // Holdings can still be recorded during a data outage. Income milestones cannot.
  let ob;
  try { ob=J.observe(p,await feeds(dataDir)); }
  catch(e) {
    if(!changed) throw e;
    J.record(p.journal,p,null,'telegram');
    write(file,{...p,updatedAt:new Date().toISOString(),weekly:raw.weekly});
    return {text:confirmation+'Market data unavailable. Your reported holdings were saved. No income milestone was awarded.'};
  }
  const baseline=p.journal.snapshots.length===0;
  J.record(p.journal,p,ob.rates,'telegram');
  const newAwards=J.award(p.journal,J.milestones(p),ob.average,new Date().toISOString(),baseline);
  let weekly=raw.weekly || {announced:[],comparison:null};
  const pending=p.journal.achievements.filter(r=>!r.baseline&&!weekly.announced.includes(r.key));
  const comparison=action==='weekly'?weekly.comparison:(before.journal.snapshots.at(-1));
  const result={text:confirmation+card(p,ob,comparison,action==='weekly'?pending:[])};
  if(action==='weekly'||action==='baseline') {
    weekly={announced:[...new Set([...weekly.announced,...p.journal.achievements.map(r=>r.key)])],comparison:J.snapshot({...p,id:'weekly-comparison',at:new Date().toISOString(),source:'weekly',rates:ob.rates})};
  }
  write(file,{...p,updatedAt:changed?new Date().toISOString():raw.updatedAt,weekly});
  if(action==='baseline') return {text:'Baseline saved without sending a message.'};
  return result;
}
async function main() {
  const [file,action,args='',dataDir]=process.argv.slice(2);
  if(!file||!action) throw new Error('A private plan path and action are required.');
  const lock=file+'.lock';
  let fd;
  try { fd=fs.openSync(lock,'wx',0o600); }
  catch(e) { throw new Error('Another holdings update is running. Try again shortly.'); }
  try {
    const result=await operate(file,action,args,dataDir);
    console.log(action==='weekly'?result.text:JSON.stringify(result));
  } finally { fs.closeSync(fd); fs.unlinkSync(lock); }
}
if(require.main===module) main().catch(e=>{ console.error('Retirement update stopped: '+e.message);process.exitCode=1; });
module.exports={operate,card};
