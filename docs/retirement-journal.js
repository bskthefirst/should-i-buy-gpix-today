/* Shared by the browser and the private Telegram service. No personal data here. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RetirementJournal = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const KEYS = ['sharesGpix', 'sharesGpiq', 'costGpix', 'costGpiq'];
  const MAX_RECORDS = 2000;
  function number(v, name, max = 1e8) {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > max) throw new Error('Invalid ' + name + '.');
    return v;
  }
  function text(v, name, max = 60) {
    if (typeof v !== 'string' || !v.trim() || v.length > max || /[\u0000-\u001f]/.test(v)) throw new Error('Invalid ' + name + '.');
    return v.trim();
  }
  function date(v) {
    if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(v) || !Number.isFinite(Date.parse(v)) || Date.parse(v) > Date.now() + 3600000) throw new Error('Invalid record date.');
    return v;
  }
  function expense(v) {
    if (v == null) return null;
    const name = text(v.name, 'expense name', 30), amount = number(v.amount, 'monthly expense', 1e6);
    if (amount <= 0) throw new Error('The monthly expense must be positive.');
    return { name, amount };
  }
  function snapshot(v) {
    const s = { id: text(v.id, 'record ID', 100), at: date(v.at), source: text(v.source, 'record source', 30) };
    for (const k of KEYS) s[k] = number(v[k], k, k.startsWith('cost') ? 1e6 : 1e8);
    s.taxPct = number(v.taxPct, 'tax', 60);
    s.rates = v.rates == null ? null : { Gpix: number(v.rates.Gpix, 'GPIX payout rate', 1e6), Gpiq: number(v.rates.Gpiq, 'GPIQ payout rate', 1e6) };
    s.income = s.rates ? income(s, s.rates) : null;
    return s;
  }
  function journal(v = {}) {
    const snapshots = v.snapshots || [], achievements = v.achievements || [];
    if (!Array.isArray(snapshots) || snapshots.length > MAX_RECORDS || !Array.isArray(achievements) || achievements.length > MAX_RECORDS) throw new Error('The history is too large. Export a backup before adding records.');
    const seen = new Set();
    const records = snapshots.map(snapshot).sort((a,b) => Date.parse(a.at) - Date.parse(b.at));
    for (const r of records) { if (seen.has(r.id)) throw new Error('Duplicate record ID.'); seen.add(r.id); }
    const awards = achievements.map(r => ({ key: text(r.key, 'milestone ID', 300), title: text(r.title, 'milestone title', 120), threshold: number(r.threshold, 'milestone threshold', 1e9), at: date(r.at), baseline: r.baseline === true }));
    if (new Set(awards.map(r => r.key)).size !== awards.length) throw new Error('Duplicate milestone ID.');
    return { snapshots: records, achievements: awards };
  }
  function profile(v) {
    if (!v || ![1,2].includes(v.schemaVersion)) throw new Error('Choose a retirement holdings file.');
    const p = { schemaVersion: 2, exportedAt: v.exportedAt ? date(v.exportedAt) : new Date().toISOString() };
    for (const k of KEYS) p[k] = number(v[k] == null && k.startsWith('cost') ? 0 : v[k], k, k.startsWith('cost') ? 1e6 : 1e8);
    p.taxPct = number(v.taxPct, 'tax rate', 60); p.target = number(v.target, 'income goal', 1e6);
    if (!p.target) throw new Error('The income goal must be positive.');
    if (!Array.isArray(v.costs) || v.costs.length > 12) throw new Error('Invalid expense list.');
    p.costs = v.costs.map(c => {
      if (typeof c.name !== 'string' || c.name.length > 30 || /[\u0000-\u001f]/.test(c.name) || !['week','month'].includes(c.per)) throw new Error('Invalid expense.');
      return { name: c.name, amount: number(c.amount,'expense price',1e6), times: number(c.times,'expense count',100), per:c.per };
    });
    p.selectedExpense = expense(v.selectedExpense);
    if (v.forecast != null) {
      p.forecast = {};
      for (const [k,max] of [['monthly',1e6],['gpixPct',100],['inflation',15],['lunchbox',7],['homeCost',1000]]) p.forecast[k] = number(v.forecast[k],k,max);
      if (!['flat','base','strong'].includes(v.forecast.scenario) || typeof v.forecast.whole !== 'boolean') throw new Error('Invalid forecast settings.');
      p.forecast.scenario=v.forecast.scenario; p.forecast.whole=v.forecast.whole;
    }
    p.journal = journal(v.journal);
    return p;
  }
  function sameHoldings(a,b) { return !!a && !!b && KEYS.every(k => Math.abs(a[k] - b[k]) < 1e-9) && a.taxPct === b.taxPct; }
  function income(p, rates) { return (p.sharesGpix * rates.Gpix + p.sharesGpiq * rates.Gpiq) * (1 - p.taxPct / 100); }
  function observe(p, feeds, now = Date.now()) {
    let latest = 0; const rates = {}, dates = [];
    for (const [key,ticker] of [['Gpix','GPIX'],['Gpiq','GPIQ']]) {
      const f = feeds[ticker], generated = Date.parse(f?.generated_at), d = f?.distributions;
      if (f?.ticker !== ticker || !Number.isFinite(generated) || now - generated > 4*86400000 || generated - now > 3600000) throw new Error(ticker + ' market data is unavailable or stale.');
      if (!(number(f.fund?.price,ticker + ' price') > 0)) throw new Error(ticker + ' price is unavailable.');
      if (f.health?.sources?.['yahoo:' + ticker]?.ok === false) throw new Error(ticker + ' market-data source reported a failure.');
      if (p['shares'+key] > 0 && (!d?.last_ex || now - Date.parse(d.last_ex + 'T12:00:00Z') > 45*86400000 || !Number.isFinite(Date.parse(d.last_ex)))) throw new Error(ticker + ' latest payout is missing or old.');
      rates[key] = p['shares'+key] === 0 && d?.ttm_sum == null ? 0 : number(d?.ttm_sum,ticker + ' yearly payouts',1e6) / 12;
      const last = p['shares'+key] === 0 && d?.last_amount == null ? 0 : number(d?.last_amount,ticker + ' latest payout',1e6);
      latest += p['shares'+key] * last * (1-p.taxPct/100); dates.push(generated);
    }
    return { rates, average: income(p,rates), latest, at: new Date(Math.min(...dates)).toISOString() };
  }
  function record(j,p,rates,source='website',at=new Date().toISOString()) {
    const previous = j.snapshots.at(-1);
    if (sameHoldings(previous,p)) return false;
    if (j.snapshots.length >= MAX_RECORDS) throw new Error('History is full. Export a backup before adding records.');
    j.snapshots.push(snapshot({ ...p, id: at + '-' + Math.random().toString(36).slice(2,10), at, source, rates }));
    return true;
  }
  function milestones(p) {
    const rows = [10,25,50,100,250,500,1000].map(v => ({ threshold:v,title:'$'+v.toLocaleString('en-US')+' a month' }));
    if (![10,25,50,100,250,500,1000].includes(p.target)) rows.push({threshold:p.target,title:'Your goal: $'+p.target.toFixed(2)+' a month'});
    const costs = p.costs.map(c => ({...c,m:c.amount*c.times*(c.per==='week'?52/12:1)})).filter(c=>c.name.trim()&&c.m>0).sort((a,b)=>a.m-b.m);
    let total=0;
    costs.forEach((c,i)=> {
      total+=c.m;
      rows.push({threshold:total,title:i===0?c.name.trim()+' paid':i===1?costs[0].name.trim()+' and '+c.name.trim()+' paid':i===costs.length-1?'All '+costs.length+' costs paid':(i+1)+' costs paid'});
      if (c.per==='week') for(let k=1;k<=Math.min(7,Math.floor(c.times));k++) {
        const name=c.name.trim().toLowerCase(), plural=k===1?name:/(ch|sh|s|x|z)$/.test(name)?name+'es':name+'s';
        rows.push({threshold:k*c.amount*52/12,title:k+' free '+plural+' a week'});
      }
    });
    if (p.selectedExpense && !rows.some(r=>Math.abs(r.threshold-p.selectedExpense.amount)<1e-9 && r.title===p.selectedExpense.name+' paid')) rows.push({threshold:p.selectedExpense.amount,title:p.selectedExpense.name+' covered'});
    return rows.sort((a,b)=>a.threshold-b.threshold).map(r=>({...r,key:r.threshold.toFixed(8)+':'+r.title}));
  }
  function award(j,rows,net,at=new Date().toISOString(),baseline=false) {
    const added=[];
    for(const r of rows) if(net>=r.threshold-1e-9&&!j.achievements.some(a=>a.key===r.key)) {
      if(j.achievements.length>=MAX_RECORDS) throw new Error('Milestone history is full. Export a backup.');
      const a={key:r.key,title:r.title,threshold:r.threshold,at,baseline}; j.achievements.push(a); added.push(a);
    }
    return added;
  }
  function changes(old,p,rates) {
    if(!old?.rates) return null;
    const oldNet=1-old.taxPct/100, net=1-p.taxPct/100;
    const shares=(p.sharesGpix-old.sharesGpix)*old.rates.Gpix*oldNet+(p.sharesGpiq-old.sharesGpiq)*old.rates.Gpiq*oldNet;
    const payouts=(p.sharesGpix*(rates.Gpix-old.rates.Gpix)+p.sharesGpiq*(rates.Gpiq-old.rates.Gpiq))*oldNet;
    const tax=(p.sharesGpix*rates.Gpix+p.sharesGpiq*rates.Gpiq)*(net-oldNet);
    return {shares,payouts,tax,total:income(p,rates)-income(old,old.rates),deltaGpix:p.sharesGpix-old.sharesGpix,deltaGpiq:p.sharesGpiq-old.sharesGpiq};
  }
  function merge(a,b) {
    const records=new Map(a.snapshots.map(r=>[r.id,r]));
    for(const r of b.snapshots) {
      if(records.has(r.id)&&JSON.stringify(records.get(r.id))!==JSON.stringify(r)) throw new Error('A saved record conflicts with this file.');
      records.set(r.id,r);
    }
    const awards=new Map(a.achievements.map(r=>[r.key,r]));
    for(const r of b.achievements) if(!awards.has(r.key)||Date.parse(r.at)<Date.parse(awards.get(r.key).at)) awards.set(r.key,r);
    return journal({snapshots:[...records.values()],achievements:[...awards.values()]});
  }
  function transport(p) { const v=profile(p); delete v.journal; return v; }
  function commitBrowser(storage,values) {
    const previous=Object.fromEntries(Object.keys(values).map(k=>[k,storage.getItem(k)]));
    try { for(const [k,v] of Object.entries(values))storage.setItem(k,v); }
    catch(e) {
      for(const [k,v] of Object.entries(previous))try{if(v===null)storage.removeItem(k);else storage.setItem(k,v);}catch(restoreError){}
      throw new Error('Browser storage failed. The import could not be saved. Download a backup before reloading.');
    }
  }
  return {number,profile,journal,expense,snapshot,income,observe,record,sameHoldings,milestones,award,changes,merge,transport,commitBrowser};
});
