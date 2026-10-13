(function(){
  'use strict';
  const B=BusinessData,$=id=>document.getElementById(id),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const now=new Date(),month=B.localMonth(now);let currency='USD',client=null,current=null,inputDirty=false,selectedMonth=month,viewConfigured=false;
  try{const view=JSON.parse(localStorage.getItem('business-view-v1')||'null');if(view&&['USD','KRW'].includes(view.currency)&&/^\d{4}-(0[1-9]|1[0-2])$/.test(view.month)&&view.month<=month){currency=view.currency;selectedMonth=view.month;viewConfigured=true;}}catch(e){}
  $('revenue-month').value=selectedMonth;$('revenue-month').max=month;$('revenue-currency').value=currency;
  const label=m=>new Date(m+'-01T12:00:00Z').toLocaleDateString('en-US',{month:'short',year:'numeric',timeZone:'UTC'});
  const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency,maximumFractionDigits:currency==='KRW'?0:2,minimumFractionDigits:currency==='KRW'?0:2}).format(n);
  const compact=n=>new Intl.NumberFormat('en-US',{style:'currency',currency,notation:'compact',maximumFractionDigits:1}).format(n);
  const signed=n=>(n<0?'−':'+')+money(Math.abs(n));
  function selectMonth(persistView=true){
    inputDirty=false;if(persistView!==false){viewConfigured=true;try{localStorage.setItem('business-view-v1',JSON.stringify({month:$('revenue-month').value,currency}));}catch(e){}}
    const r=B.entry(current?.book||B.book(),currency+':'+$('revenue-month').value);
    $('revenue-amount').value=r?r.amount:'';$('save-revenue').textContent=r?'Update revenue':'Save revenue';
    $('entry-status').textContent='';$('entry-status').classList.remove('error');render();
  }
  function chart(stats){
    const series=innerWidth<620?stats.series.slice(-6):stats.series;
    $('chart-period').textContent=label(series[0].month)+' – '+label(series.at(-1).month);
    if(!series.some(r=>r.amount!==null)){
      $('revenue-chart').innerHTML='<div class="empty-chart"><div><b>Your first month starts the chart.</b>Save a revenue entry above to see your trend.</div></div>';return;
    }
    const w=660,h=240,l=62,t=12,b=32,pw=w-l-12,ph=h-t-b;
    const largest=Math.max(...series.map(r=>r.amount||0)),magnitude=10**Math.floor(Math.log10(largest||1));
    const top=largest?Math.ceil(largest/magnitude/2)*magnitude*2:1;
    const step=pw/series.length,bw=Math.min(34,step*.54);
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Saved monthly after-tax revenue in ${currency}">`;
    for(let i=0;i<=4;i++){const y=t+ph-ph*i/4;svg+=`<line x1="${l}" x2="${w-12}" y1="${y}" y2="${y}" stroke="#d9e4ed"/><text x="${l-10}" y="${y+3}" text-anchor="end">${esc(compact(top*i/4))}</text>`;}
    series.forEach((r,i)=>{
      const x=l+step*(i+.5),height=r.amount===null?0:Math.max(2,ph*r.amount/top),selected=r.month===$('revenue-month').value;
      if(r.amount!==null)svg+=`<rect x="${x-bw/2}" y="${t+ph-height}" width="${bw}" height="${height}" rx="1" fill="${selected?'#245f88':'#79a4bf'}"><title>${esc(label(r.month)+': '+money(r.amount))}</title></rect>`;
      else svg+=`<line x1="${x-5}" x2="${x+5}" y1="${t+ph}" y2="${t+ph}" stroke="#b5c8d5" stroke-width="2"><title>${esc(label(r.month))}: no entry</title></line>`;
      svg+=`<text x="${x}" y="${h-8}" text-anchor="middle">${new Date(r.month+'-01T12:00:00Z').toLocaleDateString('en-US',{month:'short',timeZone:'UTC'})}</text>`;
    });
    let segment=[];const line=()=>{if(segment.length>1)svg+=`<polyline points="${segment.join(' ')}" fill="none" stroke="#245f88" stroke-width="1.4" opacity=".7"/>`;segment=[];};
    series.forEach((r,i)=>{if(r.amount===null){line();return;}segment.push((l+step*(i+.5))+','+(t+ph-ph*r.amount/top));});line();
    $('revenue-chart').innerHTML=svg+'</svg>';
  }
  function render(){
    if(!current)return;
    const m=$('revenue-month').value;if(!m)return;
    const s=B.stats(current.book,currency,m),year=m.slice(0,4);
    $('dashboard-title').textContent=label(m)+' overview';$('dashboard-period').textContent='Based on your saved revenue entries.';$('currency-label').textContent=currency+' · after tax';
    const delta=s.change===null?'—':s.percent===null?signed(s.change):(s.percent>0?'+':'')+s.percent.toFixed(1)+'%';
    const deltaNote=s.change===null?'Enter both months to compare.':s.previous.amount===0?'Previous month was '+money(0)+'.':signed(s.change)+' vs '+label(s.previous.month);
    $('stats').innerHTML=[
      {name:'Monthly revenue',value:s.current?money(s.current.amount):'—',note:s.current?'Your saved after-tax amount.':'No entry for this month.',class:'highlight'},
      {name:'Change from previous month',value:delta,note:deltaNote,class:s.change===null?'':s.change<0?'down':'up'},
      {name:year+' revenue to date',value:s.count?money(s.ytd):'—',note:s.count+' recorded '+(s.count===1?'month':'months')+' through '+label(m)+'.'},
      {name:'Average recorded month',value:s.average===null?'—':money(s.average),note:'Only entered months in '+year+' count.'}
    ].map(r=>`<article class="card stat ${r.class==='highlight'?'highlight':''}"><div class="stat-label">${esc(r.name)}</div><div class="stat-value ${r.class==='highlight'?'':r.class||''}">${esc(r.value)}</div><div class="stat-note">${esc(r.note)}</div></article>`).join('');
    chart(s);$('quarter-year').textContent=year+' · recorded revenue';
    const max=Math.max(1,...s.quarters.map(q=>q.amount));
    $('quarters').innerHTML=s.quarters.map(q=>`<div><div class="quarter-top"><span>${q.label}</span><strong>${q.count?esc(money(q.amount)):'—'}</strong></div><div class="quarter-bar"><i style="width:${q.amount/max*100}%"></i></div><span class="quarter-count">${q.count} of 3 months recorded</span></div>`).join('');
    $('insights').innerHTML=`<div class="insight"><span>Best recorded month · ${year}</span><strong>${s.best?esc(money(s.best.amount)):'—'}</strong><p>${s.best?label(s.best.month):'Your saved months will appear here.'}</p></div><div class="insight"><span>Annual revenue at this month’s pace</span><strong>${s.runRate===null?'—':esc(money(s.runRate))}</strong><p>${s.current?'This month × 12. A comparison, not a forecast.':'Save the selected month to calculate this.'}</p></div>`;
    const rows=current.book.records.filter(r=>r.currency===currency).sort((a,b)=>b.month.localeCompare(a.month));
    $('ledger').innerHTML=rows.length?`<table><thead><tr><th scope="col">Month</th><th scope="col">After-tax revenue</th><th scope="col">Change</th><th scope="col"><span aria-label="Actions">Edit</span></th></tr></thead><tbody>${rows.map(r=>{const prev=rows.find(p=>p.month===B.shift(r.month,-1)),diff=prev?r.amount-prev.amount:null;return `<tr><td>${label(r.month)}</td><td><strong>${esc(money(r.amount))}</strong></td><td class="${diff===null?'':diff<0?'down':'up'}">${diff===null?'—':esc(signed(diff))}</td><td><button class="edit-month" type="button" data-month="${r.month}" aria-label="Edit ${label(r.month)}">Edit</button></td></tr>`;}).join('')}</tbody></table>`:'<div class="empty-ledger">No '+currency+' revenue recorded yet. Your entries will appear here.</div>';
    $('export-business').disabled=!current.book.records.length;
    const phases={local:'Saved on this device only',checking:'Checking Mac mini connection',saving:'Saved locally · syncing',synced:'Synced with Mac mini',offline:'Offline · saved on this device only',conflict:'Different entries · review required',error:'Saved record needs attention'};
    $('save-status').textContent=phases[current.phase];$('save-status').dataset.phase=current.phase;
    $('last-sync').textContent=current.lastSyncedAt?'Last synced '+new Date(current.lastSyncedAt).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):current.paired?'The page will check for updates automatically.':'Connect once to share records across devices.';
    $('connection-description').textContent=current.paired?'This browser uses your private Mac mini connection. Open pages check for changes every 30 seconds. Offline entries remain here and retry when the connection returns.':'This browser saves revenue locally. Connect it to your Mac mini to use the same records on your phone and computer.';
    $('conflicts').hidden=!current.conflicts.length;
    $('conflicts').innerHTML=current.conflicts.map(c=>`<div class="conflict"><b>Review ${label(c.local.month)} · ${c.local.currency}</b><p>This device: ${new Intl.NumberFormat('en-US',{style:'currency',currency:c.local.currency}).format(c.local.amount)}. Mac mini: ${c.remote?new Intl.NumberFormat('en-US',{style:'currency',currency:c.remote.currency}).format(c.remote.amount):'No saved entry'}.</p><button type="button" data-choice="local" data-key="${esc(c.key)}">Keep this device’s entry</button><button type="button" data-choice="remote" data-key="${esc(c.key)}">Use Mac mini’s entry</button></div>`).join('');
    $('save-revenue').disabled=!!current.error||!!current.conflicts.length;
  }
  try{
    client=BusinessSync.create({storage:localStorage,status:(state,message)=>{current=state;if(!viewConfigured&&!inputDirty&&!document.activeElement?.closest('#revenue-form')){const latest=state.book.records.filter(r=>r.month<=month).sort((a,b)=>Date.parse(b.updatedAt)-Date.parse(a.updatedAt))[0];if(latest){currency=latest.currency;$('revenue-currency').value=currency;$('revenue-month').value=latest.month;selectMonth();}}render();if(state.phase==='synced'&&!inputDirty&&!document.activeElement?.closest('#revenue-form')){const r=B.entry(state.book,currency+':'+$('revenue-month').value);$('revenue-amount').value=r?r.amount:'';$('save-revenue').textContent=r?'Update revenue':'Save revenue';}if(state.error||message){$('entry-status').textContent=state.error||message;$('entry-status').classList.toggle('error',state.phase==='error');}}});selectMonth(false);client.cycle();
  }catch(e){$('entry-status').textContent='This browser could not save data. '+e.message;$('entry-status').classList.add('error');$('save-revenue').disabled=true;}
  $('revenue-amount').addEventListener('input',()=>{inputDirty=true;});
  $('revenue-month').addEventListener('change',selectMonth);
  $('revenue-currency').addEventListener('change',()=>{currency=$('revenue-currency').value;selectMonth();});
  $('revenue-form').addEventListener('submit',async e=>{
    e.preventDefault();$('entry-status').classList.remove('error');
    try{
      const raw=$('revenue-amount').value.trim();
      if(!/^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$/.test(raw))throw new Error('Enter a valid amount, for example 3,500.00.');
      const amount=Number(raw.replace(/,/g,''));if(currency==='KRW'&&!Number.isInteger(amount))throw new Error('Enter a whole number of won.');
      const input={month:$('revenue-month').value,amount,currency};
      if(!client)throw new Error('Browser storage is unavailable.');
      const saved=await client.save(input);
      $('entry-status').textContent=saved.phase==='synced'?'Revenue saved on Mac mini.':saved.phase==='conflict'?'Your entry is preserved. Review the different copies below.':'Revenue saved on this device.';
      inputDirty=false;$('save-revenue').textContent='Update revenue';
    }catch(error){$('entry-status').textContent=error.message;$('entry-status').classList.add('error');}
  });
  $('ledger').addEventListener('click',e=>{const button=e.target.closest('[data-month]');if(!button)return;$('revenue-month').value=button.dataset.month;selectMonth();$('revenue-amount').focus();$('revenue-form').scrollIntoView({behavior:'smooth',block:'center'});});
  $('conflicts').addEventListener('click',async e=>{const button=e.target.closest('[data-choice]');if(!button)return;await client.resolve(button.dataset.key,button.dataset.choice);selectMonth();});
  $('export-business').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(current.book,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='business-revenue-backup.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  setInterval(()=>{if(!document.hidden)client?.cycle();},30000);
  window.addEventListener('online',()=>client?.cycle());window.addEventListener('storage',e=>{if(['retire-sync-v1','business-records-v1'].includes(e.key))client?.cycle();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)client?.cycle();});
  window.addEventListener('resize',()=>{if(current)chart(B.stats(current.book,currency,$('revenue-month').value));});
  document.querySelector('.fundtoggle .active').scrollIntoView({inline:'nearest',block:'nearest'});
})();
