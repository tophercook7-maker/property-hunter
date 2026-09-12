/* TOPHER PROPERTY HUNTER - front end.
   Rules this file follows:
   - never shows a number the API did not return
   - never animates progress that is not backed by a real backend stage
   - always shows where a fact came from and how sure we are  */
'use strict';

const S = { status:null, view:'home', props:[], filters:{}, map:null, layer:null,
            boundaries:null, current:null, scanES:null, compare:new Set() };

const $  = (s,r=document)=>r.querySelector(s);
const el = (t,c,h)=>{const e=document.createElement(t); if(c)e.className=c;
                     if(h!==undefined)e.innerHTML=h; return e;};
const esc = s => (s===null||s===undefined?'':String(s))
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const money = n => (n===null||n===undefined||n==='')?'&mdash;':'$'+Math.round(n).toLocaleString();
const num = (n,d=2)=>(n===null||n===undefined||n==='')?'&mdash;':Number(n).toFixed(d);
const pct = n => (n===null||n===undefined)?'&mdash;':(n*100).toFixed(1)+'%';
const until = t => { if(!t) return 'never';
  const s=(new Date(t)-Date.now())/1000;
  if(s<0) return 'due now'; if(s<3600) return 'in '+Math.round(s/60)+' min';
  if(s<172800) return 'in '+Math.round(s/3600)+' hr'; return 'in '+Math.round(s/86400)+' days'; };
const ago = t => { if(!t) return 'never';
  const s=(Date.now()-new Date(t.endsWith('Z')||t.includes('+')?t:t+'Z'))/1000;
  if(s<90) return 'just now'; if(s<5400) return Math.round(s/60)+' min ago';
  if(s<172800) return Math.round(s/3600)+' hr ago'; return Math.round(s/86400)+' days ago'; };

async function api(path, opts){
  const r = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
  if(!r.ok){ const t = await r.text(); throw new Error(t.slice(0,300) || r.statusText); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
}
function toast(msg, ms=3200){
  const t = el('div','toast',esc(msg)); document.body.appendChild(t);
  setTimeout(()=>{t.style.opacity=0;t.style.transition='opacity .3s';
                  setTimeout(()=>t.remove(),320);}, ms);
}
function modal(html){
  const m = el('div','modal'); m.innerHTML = `<div class="box">${html}</div>`;
  m.addEventListener('click', e=>{ if(e.target===m) m.remove(); });
  document.body.appendChild(m); return m;
}
const scoreCls = s => s>=68?'hi':s>=52?'mid':'lo';

/* ------------------------------------------------------------------- nav */
const NAV = [
  {g:'Hunt'},
  {k:'home',  t:'Dashboard',      i:'&#128200;'},
  {k:'scan',  t:'Scan',           i:'&#128269;'},
  {k:'map',   t:'Map',            i:'&#128506;'},
  {k:'props', t:'Properties',     i:'&#127968;'},
  {g:'Lists'},
  {k:'newprops', t:'New',         i:'&#10024;'},
  {k:'vacant',   t:'Vacant houses',i:'&#127761;'},
  {k:'tax',      t:'Tax & state',  i:'&#127974;'},
  {k:'land',     t:'Cheap land',   i:'&#127807;'},
  {k:'snowcone', t:'Snow-cone sites',i:'&#127847;'},
  {k:'watch',    t:'Watchlist',    i:'&#11088;', badge:'watchlist'},
  {k:'field',    t:'Near me',      i:'&#128205;'},
  {g:'Work'},
  {k:'tasks',  t:'To do',       i:'&#9989;', badge:'open_tasks'},
  {k:'alerts', t:'Alerts',      i:'&#128276;', badge:'alerts'},
  {k:'deals',  t:'Money',       i:'&#128176;'},
  {k:'portfolio', t:'My properties', i:'&#128273;'},
  {g:'System'},
  {k:'sources', t:'Sources',    i:'&#128225;'},
  {k:'health',  t:'Health',     i:'&#129658;'},
  {k:'learn',   t:'Glossary',   i:'&#10067;'},
];
function renderNav(){
  const n = $('#nav'); n.innerHTML='';
  NAV.forEach(item=>{
    if(item.g){ n.appendChild(el('div','grp',item.g)); return; }
    const b = el('button', S.view===item.k?'on':'', `<span>${item.i}</span> ${item.t}`);
    const c = S.status?.counts?.[item.badge];
    if(item.badge && c) b.appendChild(el('span','pill'+(item.badge==='alerts'?' warn':''), c));
    b.onclick = ()=>go(item.k);
    n.appendChild(b);
  });
}
function go(view, arg){ if(view!=='scan') S.funnelDrawn=false; S.view=view; S.arg=arg; renderNav();
  location.hash = arg?`${view}/${arg}`:view; render(); }

/* ------------------------------------------------------------------ boot */
async function boot(){
  try { S.status = await api('/api/status'); } catch(e){
    $('#view').innerHTML = `<div class="banner danger">Backend not answering: ${esc(e.message)}</div>`;
    return; }
  $('#terrLabel').textContent = S.status.territory.label;
  const h = location.hash.replace('#','').split('/');
  if(h[0]) { S.view=h[0]; S.arg=h[1]; }
  renderNav(); render();
  setInterval(refreshStatus, 25000);
}
async function refreshStatus(){
  try{ S.status = await api('/api/status'); renderNav(); }catch(e){}
}

const VIEWS = {};
async function render(){
  const v = $('#view');
  v.innerHTML = '<div class="row"><span class="spin"></span><span class="muted">Loading&hellip;</span></div>';
  try { await (VIEWS[S.view] || VIEWS.home)(v, S.arg); }
  catch(e){ v.innerHTML = `<div class="banner danger"><b>Something broke.</b><br>${esc(e.message)}</div>`; }
  window.scrollTo({top:0,behavior:'smooth'});
}

/* -------------------------------------------------------------- dashboard */
VIEWS.home = async (v)=>{
  const [st, brief, picks] = await Promise.all([
    api('/api/status'), api('/api/briefing'), api('/api/picks?limit=3')]);
  S.status = st; renderNav();
  const c = st.counts;
  v.innerHTML = `
  <h1>Property Hunter</h1>
  <p class="lede">${esc(brief.greeting)} ${esc(brief.headline)}
    <span class="dimmer">&middot; ${esc(st.territory.label)}, excluding
    ${st.exclusions.map(e=>esc(e.label)).join(' and ')}.</span></p>

  <div class="card" style="margin-bottom:16px">
    <div class="spread" style="flex-wrap:wrap">
      <div class="row">
        <span class="tag ${st.scan.running?'blue':'green'}">
          ${st.scan.running?'<span class="spin" style="width:11px;height:11px"></span> scanning now'
                           :'&#9679; idle'}</span>
        <span class="muted tiny">Last scan ${esc(ago(st.scan.last))}
          ${st.scan.last_mode?`(${esc(st.scan.last_mode)}, ${esc(st.scan.last_status)})`:''}</span>
        <span class="muted tiny">&middot; next ${st.scan.schedule_enabled && st.scan.next_scheduled
          ? esc(until(st.scan.next_scheduled)) : 'not scheduled'}</span>
        <span class="muted tiny">&middot; AI: ${st.ai.model?esc(st.ai.model):'not running'}</span>
      </div>
      <div class="row">
        <button class="btn primary big" onclick="go('scan')">SCAN NOW</button>
        <button class="btn big" onclick="go('map')">VIEW MAP</button>
      </div>
    </div>
  </div>

  <div class="grid g4">
    ${stat(c.properties,'properties in play','accent')}
    ${stat(c.new,'new in last 2 days','green')}
    ${stat(c.distressed,'showing distress','orange')}
    ${stat(c.cheap_lots,'vacant lots')}
    ${stat(c.rental_candidates,'rental candidates')}
    ${stat(c.business_candidates,'business sites')}
    ${stat(c.tax_opportunities_pending,'tax checks pending','gold')}
    ${stat(c.excluded,'excluded by rule','red')}
  </div>

  <div class="card" style="margin-top:16px">
    <div class="row">
      <input type="text" id="askQ" placeholder="Ask: what changed? what should I investigate? find me land for storage&hellip;" style="flex:1;min-width:240px">
      <button class="btn primary sm" id="askGo">Ask</button>
    </div>
    <div id="askOut"></div>
  </div>

  <h2>&#9733; Topher picks</h2>
  <div class="grid g3" id="picks">${
    picks.picks.length ? picks.picks.map(pickCard).join('')
      : `<div class="card muted">Nothing scored yet. Run a scan.</div>`}</div>

  <div class="grid g2" style="margin-top:22px">
    <div class="card">
      <h3>What changed</h3>
      ${brief.important_changes.length ? brief.important_changes.map(ch=>`
        <div class="line"><span class="tag ${ch.severity==='high'?'red':'yellow'}">${esc(ch.severity)}</span>
          <span><a href="#property/${ch.property_id}" onclick="go('property',${ch.property_id})">
          ${esc(ch.address||ch.parcel_id)}</a> &mdash; ${esc(ch.field)}:
          <span class="dimmer">${esc(ch.old_value)}</span> &rarr; <b>${esc(ch.new_value)}</b></span></div>`).join('')
        : '<p class="muted tiny">Nothing has changed since the last look.</p>'}
    </div>
    <div class="card">
      <h3>Your next three actions</h3>
      ${brief.next_three_actions.length ? brief.next_three_actions.map((t,i)=>`
        <div class="line"><span class="p">${i+1}</span><span>${esc(t.title)}
        ${t.property?`<span class="dimmer">&mdash; ${esc(t.property)}</span>`:''}</span></div>`).join('')
        : '<p class="muted tiny">Nothing on the list. Run a scan.</p>'}
      <button class="btn sm ghost" style="margin-top:10px" onclick="go('tasks')">All tasks &rarr;</button>
    </div>
  </div>

  <div class="banner warn" style="margin-top:22px">${esc(st.disclaimer)}</div>`;
  const runAsk = async ()=>{
    const q = $('#askQ').value.trim(); if(!q) return;
    $('#askOut').innerHTML = '<div class="row tiny muted" style="margin-top:10px"><span class="spin"></span> thinking&hellip;</div>';
    const r = await api(`/api/ask?q=${encodeURIComponent(q)}`);
    $('#askOut').innerHTML = `<div class="banner info" style="margin:10px 0 0">
      <div class="tiny dimmer">${esc(r.intent.replace('_',' '))}${r.interpreted?.length?` &middot; ${r.interpreted.map(esc).join(' &middot; ')}`:''}</div>
      <div style="white-space:pre-wrap">${esc(r.answer)}</div>
      ${(r.results||[]).filter(x=>x&&x.address).slice(0,6).map(x=>`<div class="line">
        <span class="score ${scoreCls(x.overall||x.score||0)}">${num(x.overall??x.score,0)}</span>
        <span><a href="#property/${x.id}" onclick="go('property',${x.id})">${esc(x.address)}</a>
          <span class="dimmer tiny">${esc(x.recommendation||'')} ${x.signals?'&middot; '+x.signals.slice(0,2).map(esc).join('; '):''}</span></span></div>`).join('')}
      </div>`;
  };
  $('#askGo').onclick = runAsk; $('#askQ').onkeydown = e=>{ if(e.key==='Enter') runAsk(); };
};
const stat = (n,l,cls='')=>`<div class="stat ${cls}"><div class="n">${n??0}</div><div class="l">${l}</div></div>`;
const pickCard = p=>`
  <div class="card" style="animation:rise .3s backwards">
    <div class="spread"><b>${esc(p.address)}</b>
      <span class="score ${scoreCls(p.score)}">${num(p.score,0)}</span></div>
    <div class="tiny muted" style="margin:5px 0 9px">${esc(p.recommendation)}
      &middot; risk ${num(p.risk,0)}</div>
    <div><b class="tiny">WHY</b><ul style="margin:4px 0 8px;padding-left:17px;font-size:12.5px">
      ${p.why.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>
    <div><b class="tiny">STILL UNKNOWN</b>
      <div class="tiny muted">${p.unknown.map(esc).join(' &middot; ')||'&mdash;'}</div></div>
    <div class="tiny" style="margin-top:8px"><b>Next:</b> ${esc(p.next_step)}</div>
    <button class="btn sm primary" style="margin-top:11px"
      onclick="go('property',${p.id})">OPEN</button>
  </div>`;

/* ------------------------------------------------------------------ scan */
VIEWS.scan = async (v)=>{
  const modes = await api('/api/scan/modes');
  const cur = await api('/api/scan/current');
  v.innerHTML = `
  <h1>Scan</h1>
  <p class="lede">Every stage below is real work against a real source. If a source
    cannot be read, it says so &mdash; it never pretends.</p>
  <div class="card" style="margin-bottom:16px">
    <div class="row" style="margin-bottom:12px">
      <div style="flex:1;min-width:220px">
        <label class="fl">What to look for</label>
        <select id="mode">${modes.modes.map(m=>
          `<option value="${esc(m.key)}">${esc(m.label)} &mdash; ${esc(m.description)}</option>`).join('')}</select>
      </div>
      <div style="width:150px">
        <label class="fl">Max records</label>
        <input type="number" id="limit" value="300" min="10" max="80000">
      </div>
      <div style="width:150px">
        <label class="fl">Deep-check top</label>
        <input type="number" id="etop" value="8" min="0" max="60">
      </div>
      <button class="btn primary big" id="goScan">SCAN EVERYTHING NOW</button>
    </div>
    <p class="tiny dimmer">Deep checks (flood, buildings, roads) hit outside services one
      property at a time and are deliberately limited. "Everything" reads all 76,651
      Garland County parcels and takes a long while.</p>
  </div>
  <div class="card" style="margin-bottom:16px" id="schedCard"></div>
  <div id="scanBody"></div>`;
  drawSchedule();
  $('#goScan').onclick = async ()=>{
    $('#goScan').disabled = true;
    try{
      await api('/api/scan',{method:'POST',body:JSON.stringify({
        mode:$('#mode').value, limit:Number($('#limit').value)||300,
        enrich_top:Number($('#etop').value)||0 })});
      streamScan();
    }catch(e){ toast(e.message); $('#goScan').disabled=false; }
  };
  drawScan(cur.id?cur:(cur.last?normLast(cur.last):null));
  if(cur.running) streamScan();
};
const normLast = l => ({...l, funnel:null, stats:l.stats, stages:l.stages});

async function drawSchedule(){
  const c = $('#schedCard'); if(!c) return;
  const s = await api('/api/schedule');
  const modes = await api('/api/scan/modes');
  c.innerHTML = `
    <div class="spread" style="flex-wrap:wrap">
      <div>
        <h3 style="margin:0">Keep looking on its own</h3>
        <div class="tiny muted" style="margin-top:4px">
          ${s.enabled ? `Next automatic scan <b>${esc(until(s.next_run))}</b>
            (${esc((s.next_run||'').replace('T',' ').slice(0,16))} UTC) &middot; every ${s.interval_hours} h
            &middot; ${esc(s.mode)}` : 'Automatic scanning is off.'}
          ${s.last_auto_run?` &middot; last automatic run ${esc(ago(s.last_auto_run))}`:''}
          ${s.thread_alive?'':' &middot; <span style="color:#ff9b9b">scheduler thread not running</span>'}
        </div>
        <div class="tiny dimmer" style="margin-top:3px">When a scheduled scan finishes, the
          morning briefing is filed under Alerts.</div>
      </div>
      <div class="row">
        <label class="chk"><input type="checkbox" id="schEnabled" ${s.enabled?'checked':''}> on</label>
        <select id="schMode" style="width:150px">${modes.modes.filter(m=>m.key!=='full').map(m=>
          `<option value="${esc(m.key)}" ${m.key===s.mode?'selected':''}>${esc(m.label)}</option>`).join('')}</select>
        <select id="schHours" style="width:130px">${[6,12,24,48,168].map(h=>
          `<option value="${h}" ${h==s.interval_hours?'selected':''}>every ${h<24?h+' h':(h/24)+' day'+(h>24?'s':'')}</option>`).join('')}</select>
        <button class="btn sm" id="schSave">Save</button>
        <button class="btn sm ghost" id="schNow">Run now</button>
      </div>
    </div>`;
  $('#schSave').onclick = async ()=>{
    await api('/api/schedule',{method:'POST',body:JSON.stringify({
      enabled:$('#schEnabled').checked, mode:$('#schMode').value,
      interval_hours:Number($('#schHours').value)})});
    toast('Schedule saved'); drawSchedule(); refreshStatus();
  };
  $('#schNow').onclick = async ()=>{
    const r = await api('/api/schedule/run-now',{method:'POST',body:'{}'});
    if(!r.started) return toast(r.reason);
    streamScan();
  };
}
function streamScan(){
  if(S.scanES) S.scanES.close();
  const es = new EventSource('/api/scan/stream'); S.scanES = es;
  es.onmessage = ev=>{
    const d = JSON.parse(ev.data);
    if(d.done){ es.close(); S.scanES=null; refreshStatus();
                const b=$('#goScan'); if(b) b.disabled=false; return; }
    drawScan(d);
  };
  es.onerror = ()=>{ es.close(); S.scanES=null; const b=$('#goScan'); if(b) b.disabled=false; };
}
function drawScan(d){
  const box = $('#scanBody'); if(!box) return;
  const firstPaint = !S.funnelDrawn;
  if(!d || !d.stages){ box.innerHTML = '<div class="card muted">No scan has been run yet.</div>'; return; }
  const st = d.stats||{};
  const funnel = d.funnel || [
    {label:'records examined',value:st.records_examined},
    {label:'properties matched',value:st.properties_matched},
    {label:'after exclusions',value:(st.properties_matched||0)-(st.excluded||0)},
    {label:'investigation candidates',value:st.candidates},
    {label:'strong candidates',value:st.strong_candidates},
    {label:'Topher picks',value:st.picks}];
  box.innerHTML = `
  <div class="grid g-scan">
    <div class="card">
      <h3>Stages ${d.status==='running'?'<span class="spin"></span>':''}</h3>
      ${d.stages.map(s=>`
        <div class="stage ${esc(s.status)}">
          <span class="mk">${s.status==='done'?'&#10003;':
            (s.status==='failed'||s.status==='unavailable')?'!':''}</span>
          <span class="txt"><b>${esc(s.label)}</b>
            <div class="d">${esc(s.detail||statusWord(s.status))}</div></span>
          ${s.total?`<span class="bar"><i style="width:${Math.min(100,100*s.done/s.total)}%"></i></span>
            <span class="tiny mono dimmer">${s.done}/${s.total}</span>`:''}
        </div>`).join('')}
      ${d.error?`<div class="banner danger" style="margin-top:12px">${esc(d.error)}</div>`:''}
    </div>
    <div class="card">
      <h3>The funnel</h3>
      <div class="funnel">
        ${funnel.map((f,i)=>`
          ${i?'<span class="arw">&darr;</span>':''}
          <div class="step" style="${S.funnelDrawn?'animation:none':`animation-delay:${i*60}ms`}">
            <b>${(f.value??0).toLocaleString()}</b><span>${esc(f.label)}</span></div>`).join('')}
      </div>
      ${d.status==='complete'?`<div class="row" style="margin-top:14px">
        <button class="btn primary" onclick="go('props')">See what it found</button>
        <button class="btn" onclick="go('home')">Top picks</button></div>`:''}
    </div>
  </div>`;
  if(firstPaint) S.funnelDrawn = true;
}
const statusWord = s => ({waiting:'waiting', running:'working', done:'done',
  failed:'failed', unavailable:'source unavailable', skipped:'skipped'})[s]||s;

/* ------------------------------------------------------------------- map */
VIEWS.map = async (v)=>{
  v.innerHTML = `
  <h1>Map</h1>
  <p class="lede">Everything we are tracking. The red hatched areas are the places we
     never look: Hot Springs Village and Diamondhead.</p>
  <div class="card" style="margin-bottom:12px">
    <div class="row">
      <input type="text" id="mq" placeholder="Search address, owner, parcel&hellip;" style="flex:1;min-width:200px">
      <select id="mtype" style="width:160px">
        <option value="">Any type</option><option value="house">Houses</option>
        <option value="lot">Lots</option><option value="commercial">Commercial</option></select>
      <select id="msort" style="width:170px">
        <option value="overall">Best overall</option><option value="land">Best land</option>
        <option value="rental">Best rental</option><option value="storage">Best storage</option>
        <option value="snowcone">Best snow-cone site</option></select>
      <label class="chk"><input type="checkbox" id="mdist"> distress only</label>
      <button class="btn sm" id="mgo">Filter</button>
    </div>
    <div class="legend" style="margin-top:11px">
      ${[['green','strong opportunity'],['yellow','investigate'],['orange','distressed'],
         ['red','serious problem'],['blue','tax / institutional owner'],
         ['purple','vacant land'],['gold','on your watchlist']]
        .map(([c,l])=>`<span><i class="bg-${c}"></i>${l}</span>`).join('')}
    </div>
  </div>
  <div id="map"></div>
  <p class="tiny dimmer" style="margin-top:8px" id="mapcount"></p>`;

  if(window.__noLeaflet || typeof L === 'undefined'){
    $('#map').innerHTML = `<div style="padding:30px" class="muted">
      The map library could not load (no internet, or it is blocked). Everything else
      still works &mdash; use <a href="#props" onclick="go('props')">Properties</a>.</div>`;
    return;
  }
  const data = await api('/api/map');
  const m = L.map('map',{zoomControl:true}).setView(S.status.territory.center, 11);
  S.map = m;
  // Keyless basemaps only. Esri's public tile services need no API key, and the
  // aerial layer is genuinely useful - you can see the roof and the overgrowth.
  const dark = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:16, attribution:'Esri, HERE, Garmin, &copy; OpenStreetMap contributors'});
  const street = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom:19, attribution:'&copy; OpenStreetMap contributors'});
  const aerial = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxZoom:19, attribution:'Esri, Maxar, Earthstar Geographics'});
  dark.addTo(m);
  L.control.layers({'Dark':dark, 'Street':street, 'Aerial':aerial}, null,
                   {position:'topright'}).addTo(m);
  (data.excluded_boundaries||[]).forEach(b=>{
    (b.rings||[]).forEach(ring=>{
      L.polygon(ring.map(p=>[p[1],p[0]]), {color:'#ef5b5b',weight:1.5,fillOpacity:.13,
        dashArray:'5,5'}).addTo(m).bindTooltip(b.name+' - excluded, never searched');
    });
  });
  S.layer = L.layerGroup().addTo(m);
  drawMarkers(data.properties);
  $('#mgo').onclick = async ()=>{
    const p = new URLSearchParams();
    if($('#mq').value) p.set('q',$('#mq').value);
    if($('#mtype').value) p.set('property_type',$('#mtype').value);
    if($('#mdist').checked) p.set('has_distress','true');
    p.set('sort',$('#msort').value); p.set('limit','1500');
    const r = await api('/api/properties?'+p);
    drawMarkers(r.properties.map(x=>({...x, marker:null})));
  };
};
function drawMarkers(props){
  if(!S.layer) return;
  S.layer.clearLayers();
  const colors = {green:'#2fbf71',yellow:'#e8c547',orange:'#f0883e',red:'#ef5b5b',
                  blue:'#4aa8ff',purple:'#a77bff',gold:'#f5c344'};
  let n=0;
  props.forEach(p=>{
    if(p.lat===null||p.lon===null) return; n++;
    const kind = p.marker || markerOf(p);
    L.circleMarker([p.lat,p.lon],{radius:kind==='gold'?8:6,color:'#0c0f16',weight:1.2,
      fillColor:colors[kind]||'#95a1bb',fillOpacity:.92})
      .addTo(S.layer)
      .bindPopup(`<b>${esc(p.address||'No street address')}</b><br>
        <span style="color:#95a1bb">${esc(p.city||'')} &middot; ${num(p.acreage)} ac
        &middot; assessed ${money(p.total_value)}</span><br>
        score <b>${num(p.overall_score,0)}</b> &middot; risk ${num(p.risk_score,0)}<br>
        <span style="color:#95a1bb">${esc(p.recommendation||'not scored')}</span><br>
        ${(p.distress||[]).slice(0,3).map(s=>'&bull; '+esc(s.label)).join('<br>')}
        <br><a href="#property/${p.id}" onclick="document.querySelector('.leaflet-popup-close-button').click();go('property',${p.id})">OPEN PROPERTY &rarr;</a>`);
  });
  const c = $('#mapcount'); if(c) c.textContent = `${n} properties plotted.`;
}
function markerOf(p){
  const keys = new Set((p.distress||[]).map(s=>s.key));
  if(p.watched) return 'gold';
  if(keys.has('possible_no_access') || /^[AV]/.test(p.flood_zone||'')) return 'red';
  if(keys.has('institutional_owner')||keys.has('estate_owner')||keys.has('government_owner')) return 'blue';
  if((p.overall_score||0)>=65) return 'green';
  if(keys.size) return 'orange';
  if(p.property_type==='lot') return 'purple';
  return 'yellow';
}

/* ------------------------------------------------------------- properties */
const PRESET_VIEWS = {
  props:   {title:'Properties', lede:'Everything in play.', q:{}},
  newprops:{title:'New properties', lede:'Everything seen for the first time recently.',
            q:{sort:'newest'}},
  vacant:  {title:'Vacant houses', lede:'Improved parcels the record says are barely worth anything, plus every other distress signal we can see.',
            q:{has_distress:'true', property_type:'house'}},
  land:    {title:'Cheap land', lede:'Vacant lots, cheapest first. Check access and zoning before you get excited.',
            q:{property_type:'lot', sort:'cheapest'}},
  snowcone:{title:'Snow-cone sites', lede:'Ranked by traffic, room to park and what is already nearby. Zoning and a health permit are still on you.',
            q:{sort:'snowcone'}},
  watch:   {title:'Watchlist', lede:'The ones you asked to keep an eye on.', q:{watchlist:'true'}},
};
['props','newprops','vacant','land','snowcone','watch'].forEach(k=>{
  VIEWS[k] = async (v)=>propList(v, PRESET_VIEWS[k]);
});

async function propList(v, cfg){
  v.innerHTML = `
  <h1>${esc(cfg.title)}</h1>
  <p class="lede">${esc(cfg.lede)}</p>
  <div class="card" style="margin-bottom:14px">
    <div class="row">
      <input type="text" id="nlq" placeholder="Ask in plain English: &quot;houses under $60,000 that need work&quot;"
        style="flex:1;min-width:240px">
      <button class="btn primary sm" id="nlgo">Search</button>
      <button class="btn sm ghost" id="nlai" title="Let the local model interpret it">AI</button>
      <span style="flex:1"></span>
      <button class="btn sm ghost" id="moreF">Filters</button>
      <button class="btn sm ghost" id="viewT" title="table / cards">&#9776;</button>
      <select id="sort" style="width:170px">
        <option value="overall">Best overall</option><option value="newest">Newest</option>
        <option value="cheapest">Cheapest</option><option value="acreage">Most acreage</option>
        <option value="rental">Best rental</option><option value="land">Best land</option>
        <option value="storage">Best storage</option><option value="business">Best business</option>
        <option value="workshop">Best workshop</option><option value="snowcone">Best snow-cone</option>
        <option value="risk">Lowest risk</option></select>
      <button class="btn sm" id="exp">Export CSV</button>
    </div>
    <div id="interp"></div>
    <div id="moreBox" class="hide" style="margin-top:12px;border-top:1px solid var(--line);padding-top:12px">
      <div class="row">
        <div><label class="fl">Min value</label><input type="number" id="fMin" style="width:120px" placeholder="$"></div>
        <div><label class="fl">Max value</label><input type="number" id="fMax" style="width:120px" placeholder="$"></div>
        <div><label class="fl">Min acres</label><input type="number" id="fAcMin" style="width:100px" step="0.1"></div>
        <div><label class="fl">Min score</label><input type="number" id="fScore" style="width:100px" min="0" max="100"></div>
        <div><label class="fl">City</label><select id="fCity" style="width:170px"><option value="">Any</option></select></div>
        <div><label class="fl">Type</label><select id="fType" style="width:130px"><option value="">Any</option>
          <option value="house">House</option><option value="lot">Lot</option><option value="commercial">Commercial</option></select></div>
        <div><label class="fl">Our call</label><select id="fRec" style="width:150px"><option value="">Any</option>
          ${['BUY CANDIDATE','INVESTIGATE','WATCH','NEGOTIATE','PASS','DO NOT TOUCH'].map(r=>`<option>${r}</option>`).join('')}</select></div>
        <label class="chk" style="align-self:flex-end"><input type="checkbox" id="fDist"> distress only</label>
        <label class="chk" style="align-self:flex-end"><input type="checkbox" id="fFlood"> no flood</label>
        <label class="chk" style="align-self:flex-end"><input type="checkbox" id="fRoad"> has road</label>
        <button class="btn sm primary" id="fApply" style="align-self:flex-end">Apply</button>
        <button class="btn sm ghost" id="fSave" style="align-self:flex-end">Save filter</button>
      </div>
      <div class="row" id="savedRow" style="margin-top:8px"></div>
    </div>
  </div>
  <div id="listBox"></div>`;
  if(cfg.q.sort) $('#sort').value = cfg.q.sort;
  S.table = S.table || false;
  $('#moreF').onclick = ()=>$('#moreBox').classList.toggle('hide');
  $('#viewT').onclick = ()=>{ S.table = !S.table; load(currentExtra()); };
  api('/api/cities').then(c=>{ const sel=$('#fCity'); if(!sel) return;
    c.cities.forEach(x=>{ const o=document.createElement('option'); o.textContent=x; sel.appendChild(o); }); });
  const currentExtra = ()=>{
    const e = {};
    if($('#fMin').value) e.min_value=$('#fMin').value; if($('#fMax').value) e.max_value=$('#fMax').value;
    if($('#fAcMin').value) e.min_acres=$('#fAcMin').value; if($('#fScore').value) e.min_score=$('#fScore').value;
    if($('#fCity').value) e.city=$('#fCity').value; if($('#fType').value) e.property_type=$('#fType').value;
    if($('#fRec').value) e.recommendation=$('#fRec').value;
    if($('#fDist').checked) e.has_distress='true'; if($('#fFlood').checked) e.no_flood='true';
    if($('#fRoad').checked) e.road_frontage='true';
    return e;
  };
  $('#fApply').onclick = ()=>load(currentExtra());
  $('#fSave').onclick = async ()=>{
    const name = prompt('Name this filter'); if(!name) return;
    await api('/api/filters/saved',{method:'POST',body:JSON.stringify({name, params:{...currentExtra(), sort:$('#sort').value}})});
    drawSaved();
  };
  const drawSaved = async ()=>{
    const r = await api('/api/filters/saved'); const row = $('#savedRow'); if(!row) return;
    row.innerHTML = r.filters.length ? '<span class="tiny dimmer">Saved:</span> ' + r.filters.map(f=>
      `<span class="tag" style="cursor:pointer" onclick='applySaved(${JSON.stringify(f.params)})'>${esc(f.name)}
        <b style="cursor:pointer;margin-left:4px" onclick="event.stopPropagation();delSaved('${esc(f.name)}')">&times;</b></span>`).join('') : '';
  };
  window.applySaved = (params)=>{ const p={...params}; if(p.sort){ $('#sort').value=p.sort; delete p.sort; } load(p); };
  window.delSaved = async (name)=>{ await api('/api/filters/saved/'+encodeURIComponent(name),{method:'DELETE'}); drawSaved(); };
  drawSaved();
  const load = async (extra, interp)=>{
    const box = $('#listBox');
    box.innerHTML = '<div class="grid g3"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>';
    const p = new URLSearchParams({limit:'120', sort:$('#sort').value, ...cfg.q, ...(extra||{})});
    p.delete('sort'); p.set('sort',$('#sort').value);
    const r = await api('/api/properties?'+p);
    S.props = r.properties;
    $('#interp').innerHTML = interp || '';
    box.innerHTML = r.properties.length ? `
      ${r.preferences?.influenced ? `<div class="banner info" style="margin-bottom:10px">
        <b>Your preferences have influenced this ranking.</b> ${esc(r.preferences.note.replace('Your preferences have influenced this ranking. ',''))}
        <span class="dimmer">(you have passed for: ${r.preferences.reasons.map(esc).join(', ')})</span>
        <button class="btn sm ghost" style="margin-left:8px" onclick="toggleLearning(false)">turn off</button></div>`:''}
      <div class="spread" style="margin-bottom:10px">
        <span class="muted tiny">${r.total.toLocaleString()} match &middot; showing ${r.count}</span>
        <span class="row"><button class="btn sm ghost" id="cmpBtn">Compare selected (0)</button></span>
      </div>
      ${S.table ? propTable(r.properties) : `<div class="grid g3">${r.properties.map(propCard).join('')}</div>`}`
      : `<div class="card muted">Nothing matches. ${cfg.q.watchlist?'Add something to the watchlist.':'Try a scan, or loosen the filter.'}</div>`;
    const cb = $('#cmpBtn'); if(cb) cb.onclick = doCompare;
    updateCmpBtn();
  };
  $('#sort').onchange = ()=>load();
  $('#nlgo').onclick = ()=>runNL(false);
  $('#nlai').onclick = ()=>runNL(true);
  $('#nlq').onkeydown = e=>{ if(e.key==='Enter') runNL(false); };
  $('#exp').onclick = ()=>{ location.href='/api/export/properties.csv'; };
  async function runNL(useAI){
    const q = $('#nlq').value.trim(); if(!q) return load();
    $('#interp').innerHTML = '<div class="row tiny muted" style="margin-top:9px"><span class="spin"></span> reading that&hellip;</div>';
    const r = await api(`/api/search/natural?q=${encodeURIComponent(q)}&use_ai=${useAI}&limit=120`);
    S.props = r.properties;
    const i = r.interpretation;
    $('#interp').innerHTML = `<div class="banner info" style="margin:11px 0 0">
      <b>I read that as:</b> ${i.interpreted.length?i.interpreted.map(esc).join(' &middot; ')
        :'no filters I recognised &mdash; showing everything'}${i.via?` <span class="dimmer">(via ${esc(i.via)})</span>`:''}
      <br><span class="tiny dimmer">${esc(i.note)}</span></div>`;
    $('#listBox').innerHTML = r.properties.length
      ? `<div class="muted tiny" style="margin:10px 0">${r.total} match</div>
         <div class="grid g3">${r.properties.map(propCard).join('')}</div>`
      : '<div class="card muted">Nothing matched that.</div>';
  }
  await load();
}

function propTable(props){
  return `<div class="card pad0"><div class="wrap-x"><table class="ev">
    <tr><th>Address</th><th>City</th><th>Type</th><th>Acres</th><th>Assessed</th><th>Owner</th>
        <th>Score</th><th>Risk</th><th>Our call</th><th>Signals</th><th></th></tr>
    ${props.map(p=>`<tr>
      <td><a href="#property/${p.id}" onclick="go('property',${p.id})">${esc(p.address||'No street address')}</a></td>
      <td>${esc(p.city||'')}</td><td>${esc(p.property_type||'')}</td><td>${num(p.acreage)}</td>
      <td>${money(p.total_value)}</td><td class="tiny">${esc(p.owner_name||'')}</td>
      <td><span class="score ${scoreCls(p.overall_score||0)}">${num(p.overall_score,0)}</span></td>
      <td>${num(p.risk_score,0)}</td><td><span class="tag ${recCls(p.recommendation)}">${esc(p.recommendation||'')}</span></td>
      <td class="tiny">${(p.distress||[]).length}</td>
      <td><label class="chk tiny"><input type="checkbox" ${S.compare.has(p.id)?'checked':''} onchange="toggleCompare(${p.id},this.checked)"> cmp</label></td>
    </tr>`).join('')}
  </table></div></div>`;
}
function propCard(p){
  const sigs = (p.distress||[]).filter(s=>s.kind!=='opportunity').slice(0,3);
  const w = p.watch_priority ? `<div class="watchbox">
      <span class="tag gold">&#11088; ${esc(p.watch_priority)}</span>
      ${p.watch_target_price?`<span class="tag">target ${money(p.watch_target_price)}</span>`:''}
      ${p.watch_desired_use?`<span class="tag purple">${esc(p.watch_desired_use)}</span>`:''}
      ${p.watch_notes?`<div class="tiny muted" style="margin-top:4px">${esc(p.watch_notes)}</div>`:''}
      <button class="btn sm ghost" style="margin-top:4px" onclick="editWatch(${p.id})">edit</button>
    </div>` : '';
  const icon = p.property_type==='lot'?'&#127807;':p.property_type==='commercial'?'&#127970;':'&#127968;';
  return `<div class="pcard">
    <div class="top">
      <span class="dot bg-${markerOf(p)}"></span>
      <span class="ico">${icon}</span>
      ${p.data_class==='demo'?'<span class="badge tag yellow">DEMO DATA</span>'
        :'<span class="badge tag green">REAL DATA</span>'}
    </div>
    <div class="body">
      <div class="spread"><span class="addr">${esc(p.address||'No street address')}</span>
        <span class="score ${scoreCls(p.overall_score||0)}">${num(p.overall_score,0)}</span></div>
      <div class="meta">${esc(p.city||'')} &middot; ${num(p.acreage)} ac &middot;
        assessed ${money(p.total_value)} &middot; ${esc(p.parcel_id||'')}</div>
      <div class="meta">${esc(p.owner_name||'owner unknown')}</div>
      <div class="sigs">
        ${p.recommendation?`<span class="tag ${recCls(p.recommendation)}">${esc(p.recommendation)}</span>`:''}
        ${p.flood_zone&&/^[AV]/.test(p.flood_zone)?'<span class="tag red">flood</span>':''}
        ${sigs.map(s=>`<span class="tag" title="${esc(s.why)}">${esc(shortSig(s.label))}</span>`).join('')}
        ${(p.preference_flags||[]).map(f=>`<span class="tag purple" title="You have passed on deals like this before (${esc(f)}). Score unchanged; ranked lower.">&#8595; ${esc(f)}</span>`).join('')}
      </div>
      ${w}
    </div>
    <div class="acts">
      <button class="btn sm primary" onclick="go('property',${p.id})">OPEN</button>
      <button class="btn sm" onclick="quickInvestigate(${p.id})">INVESTIGATE</button>
      <button class="btn sm ghost" onclick="toggleWatch(${p.id},this)">${p.watched?'&#11088;':'WATCH'}</button>
      <label class="chk tiny" style="margin-left:auto"><input type="checkbox"
        ${S.compare.has(p.id)?'checked':''} onchange="toggleCompare(${p.id},this.checked)"> cmp</label>
    </div></div>`;
}
const recCls = r => ({'BUY CANDIDATE':'green','INVESTIGATE':'blue','WATCH':'yellow',
  'NEGOTIATE':'purple','PASS':'','DO NOT TOUCH':'red'})[r]||'';
const shortSig = l => l.length>34 ? l.slice(0,32)+'…' : l;

window.toggleLearning = async (on)=>{
  await api('/api/decisions/learning',{method:'POST',body:JSON.stringify({enabled:on})});
  toast(on?'Preferences back on':'Preferences ignored - lists are score order only'); render();
};
window.toggleCompare = (id,on)=>{ on?S.compare.add(id):S.compare.delete(id); updateCmpBtn(); };
function updateCmpBtn(){ const b=$('#cmpBtn'); if(b){ b.textContent=`Compare selected (${S.compare.size})`;
  b.disabled = S.compare.size<2; } }
window.doCompare = async ()=>{
  const r = await api('/api/compare',{method:'POST',body:JSON.stringify({ids:[...S.compare]})});
  if(r.error) return toast(r.error);
  modal(`<h2 style="margin-top:0">Which one is better?</h2>
    <div class="banner info"><b>${esc(r.winner.address)}</b><ul style="margin:7px 0 0;padding-left:18px">
      ${r.winner.why.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></div>
    <div class="wrap-x"><table class="ev"><tr><th>Field</th>
      ${r.properties.map(p=>`<th>${esc(p.address)}</th>`).join('')}</tr>
      ${r.rows.map(row=>`<tr><td class="muted">${esc(row.field)}</td>
        ${row.values.map(v=>`<td>${v===null||v===undefined?'&mdash;':esc(v)}</td>`).join('')}</tr>`).join('')}
    </table></div>`);
};
window.toggleWatch = async (id, btn)=>{
  const on = btn.textContent.trim()!=='WATCH';
  if(on){
    await api(`/api/property/${id}/watch`, {method:'DELETE'});
    btn.innerHTML = 'WATCH'; toast('Removed from watchlist'); refreshStatus(); return;
  }
  watchDialog(id, btn);
};
window.editWatch = (id)=>{
  const p = (S.props||[]).find(x=>x.id===id) || {};
  watchDialog(id, null, {priority:p.watch_priority, notes:p.watch_notes||'',
                         target_price:p.watch_target_price, desired_use:p.watch_desired_use||''});
};
window.watchDialog = async (id, btn, existing)=>{
  const w = existing || {};
  const m = modal(`<h3>Watch this property</h3>
    <p class="tiny muted">The scanner keeps re-reading it and tells you when anything moves.</p>
    <div class="grid g-form">
      <div><label class="fl">Priority</label><select id="wPri">
        ${['low','normal','high','urgent'].map(x=>`<option ${x===(w.priority||'normal')?'selected':''}>${x}</option>`).join('')}</select></div>
      <div><label class="fl">Target price</label><input type="number" id="wPrice" value="${w.target_price??''}" placeholder="what you'd pay"></div>
      <div><label class="fl">Desired use</label><select id="wUse">
        ${['','rental','flip','storage','workshop','office','snow-cone','land bank','personal'].map(x=>`<option value="${x}" ${x===(w.desired_use||'')?'selected':''}>${x||'undecided'}</option>`).join('')}</select></div>
    </div>
    <div class="field" style="margin-top:8px"><label class="fl">Notes</label>
      <textarea id="wNotes" rows="3" placeholder="Why you're watching it, what would make you move">${esc(w.notes||'')}</textarea></div>
    <div class="row"><button class="btn primary" id="wSave">${existing?'Update':'Watch'}</button>
      <button class="btn ghost" onclick="this.closest('.modal').remove()">Cancel</button></div>`);
  $('#wSave').onclick = async ()=>{
    await api(`/api/property/${id}/watch`,{method:'POST',body:JSON.stringify({
      priority:$('#wPri').value, notes:$('#wNotes').value,
      target_price:$('#wPrice').value?Number($('#wPrice').value):null,
      desired_use:$('#wUse').value||null})});
    m.remove(); if(btn) btn.innerHTML='&#11088;'; toast(existing?'Watch updated':'Added to watchlist');
    refreshStatus(); if(S.view==='watch'||S.view==='property') render();
  };
};
window.quickInvestigate = async (id)=>{ go('property',id); setTimeout(()=>runInvestigation(id), 350); };

/* --------------------------------------------------------------- dossier */
VIEWS.property = async (v, id)=>{
  const d = await api(`/api/property/${id}`);
  S.current = d;
  const p = d.property, sc = d.scores, dot = d.deal_or_trap;
  v.innerHTML = `
  <div class="spread" style="align-items:flex-start">
    <div>
      <h1>${esc(p.address||'No street address')}</h1>
      <p class="lede">${esc(p.city||'')} &middot; parcel ${esc(p.parcel_id||'unknown')}
        &middot; ${num(p.acreage)} acres &middot;
        <span class="tag ${p.data_class==='demo'?'yellow':'green'}">${p.data_class==='demo'?'DEMO DATA':'REAL DATA'}</span></p>
    </div>
    <div class="row">
      <button class="btn" onclick="${d.watched?`watchDialog(${p.id},this,S.current.watch[0]||{})`:`toggleWatch(${p.id},this)`}">${d.watched?'&#11088; watching':'WATCH'}</button>
      <button class="btn" onclick="location.href='/api/property/${p.id}/report.html'">REPORT</button>
      <button class="btn" onclick="downloadPdf(${p.id},this)">PDF</button>
      <button class="btn primary" onclick="runInvestigation(${p.id})">INVESTIGATE</button>
    </div>
  </div>

  <div class="grid g4" style="margin-bottom:16px">
    <div class="stat accent"><div class="n">${num(sc.overall?.score,0)}</div>
      <div class="l">opportunity &middot; ${esc(sc.overall?.confidence||'')} confidence</div></div>
    <div class="stat ${dot.colour==='green'?'green':dot.colour==='red'?'red':'orange'}">
      <div class="n" style="font-size:19px;padding-top:5px">${esc(dot.verdict)}</div>
      <div class="l">deal or trap?</div></div>
    <div class="stat"><div class="n" style="font-size:17px;padding-top:7px">${esc(p.recommendation||'&mdash;')}</div>
      <div class="l">our call</div></div>
    <div class="stat red"><div class="n">${num(sc.risk?.score,0)}</div><div class="l">risk</div></div>
  </div>
  <div class="banner ${dot.colour==='red'?'danger':dot.colour==='green'?'info':'warn'}">
    <b>${esc(dot.verdict)}.</b> ${esc(dot.why)}
    ${dot.hard_stops.length?`<ul style="margin:7px 0 0;padding-left:18px">${
      dot.hard_stops.map(h=>`<li>${esc(h)}</li>`).join('')}</ul>`:''}
  </div>

  <div class="tabs" id="dtabs"></div>
  <div id="dbody"></div>`;

  const tabs = {
    'Overview': ()=>tabOverview(d),
    'Evidence': ()=>tabEvidence(d),
    'Scores': ()=>tabScores(d),
    'Money': ()=>tabMoney(d),
    'Uses': ()=>tabUses(d),
    'Timeline': ()=>tabTimeline(d),
    'To do': ()=>tabTasks(d),
    'Field': ()=>tabField(d),
    'Own it': ()=>tabOwn(d),
    'AI': ()=>tabAI(d),
  };
  const tb = $('#dtabs');
  Object.keys(tabs).forEach((name,i)=>{
    const b = el('button', i===0?'on':'', name);
    b.onclick = ()=>{ [...tb.children].forEach(c=>c.classList.remove('on'));
                      b.classList.add('on'); $('#dbody').innerHTML=''; tabs[name](); };
    tb.appendChild(b);
  });
  tabs['Overview']();
};

function tabOverview(d){
  const p = d.property, wc = d.why_cheap;
  const aerials = (d.photos||[]).filter(x=>x.kind==='aerial').sort((x,y)=>(y.captured_at||'').localeCompare(x.captured_at||''));
  $('#dbody').innerHTML = `
  ${aerials.length ? `<div class="hero">
      ${aerials.slice(0,2).map(ph=>`<figure class="heroimg">
        <a href="${esc(ph.url)}" target="_blank" rel="noopener"><img src="${esc(ph.url)}" alt="aerial ${esc(ph.captured_at)}"></a>
        <figcaption><b>Aerial, flown ${esc(ph.captured_at)}</b> &middot; <span class="dimmer">${esc(ph.source)}</span>
          ${ph.caption&&ph.caption.includes('AI (low confidence)')?`<div class="tiny" style="margin-top:3px">${esc(ph.caption.split('AI (low confidence): ')[1]||'')} <span class="tag orange">AI &middot; low confidence</span></div>`:''}
          <button class="btn sm ghost" onclick="lookAt(${ph.id},this)">Look at it</button></figcaption></figure>`).join('')}
      ${aerials.length>=2?'<div class="tiny dimmer" style="grid-column:1/-1">Then and now: the same frame flown six years apart. What is missing, new, or overgrown?</div>':''}
    </div>`
    : `<div class="banner info" style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
        <span>No aerial photos on file yet. The State publishes 2017 and 2023 imagery for every parcel.</span>
        <button class="btn sm primary" onclick="getAerials(${p.id},this)">Get aerials</button></div>`}
  <div class="grid g2">
    <div class="card">
      <h3>Snapshot</h3>
      <dl class="kv">
        ${kv('Owner of record', p.owner_name || 'unknown')}
        ${kv('Parcel ID', p.parcel_id)}
        ${kv('RPID', p.rpid || 'not matched yet')}
        ${kv('Legal description', p.legal)}
        ${kv('Subdivision', p.subdivision)}
        ${kv('Acreage', p.acreage)}
        ${kv('Municipality', p.city)}
        ${kv('County assessed total', money(p.total_value), true)}
        ${kv('Land value', money(p.land_value), true)}
        ${kv('Improvement value', money(p.imp_value), true)}
        ${kv('Implied market value', money(d.financials.implied_market_value), true)}
        ${kv('Parcel type code', p.parcel_type)}
        ${kv('Property type', p.property_type)}
        ${kv('Building footprint', p.building_sqft?Math.round(p.building_sqft).toLocaleString()+' sqft':'not measured')}
        ${kv('Flood zone', p.flood_zone || 'NOT CHECKED')}
        ${kv('Zoning', p.zoning ? `${esc(p.zoning)}${evVal(d,'zoning_plain')?` <span class="dimmer">(${esc(evVal(d,'zoning_plain'))})</span>`:''}` : 'NOT CHECKED', true)}
        ${kv('City water', evVal(d,'city_water') || 'not checked')}
        ${kv('City sewer', evVal(d,'city_sewer') || 'not checked')}
        ${kv('Vacancy register', evVal(d,'vacant_structure') ? '<span class="tag red">ON THE REGISTER</span>' : (evVal(d,'vacant_structure_check') || 'not checked'), true)}
        ${kv('City liens', evVal(d,'cleanup_lien_total') ? '$'+Number(evVal(d,'cleanup_lien_total')).toLocaleString() : (evVal(d,'cleanup_lien_check') || 'not checked'))}
        ${kv('Code cases', evVal(d,'code_case_open') || evVal(d,'code_case') || evVal(d,'code_case_check') || 'not checked')}
        ${kv('Tax status', p.tax_status || 'NOT CHECKED')}
        ${kv('Listing status', p.listing_status || 'not known to be listed')}
        ${kv('Road', p.road_class || 'not checked')}
        ${kv('State', p.state)}
        ${kv('First seen / last seen', `${esc((p.first_seen||'').slice(0,10))} / ${esc((p.last_seen||'').slice(0,10))}`, true)}
      </dl>
      <p class="tiny dimmer" style="margin-top:11px">${esc(d.financials.assessed_note)}</p>
    </div>
    <div>
      <div class="card" style="margin-bottom:14px">
        <h3>What caught our attention</h3>
        ${(p.distress||[]).length ? (p.distress||[]).map(s=>`
          <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">
            <div class="row"><b style="font-size:13.5px">${esc(s.label)}</b>
              <span class="tag ${s.confidence==='HIGH'?'green':s.confidence==='MEDIUM'?'yellow':''}">${esc(s.confidence)}</span>
              <span class="tag ${s.kind==='risk'?'red':s.kind==='opportunity'?'blue':'orange'}">${esc(s.kind)}</span></div>
            <div class="tiny muted">${esc(s.why)}</div>
            <div class="tiny dimmer"><b>To confirm:</b> ${esc(s.verify)}</div>
          </div>`).join('') : '<p class="muted tiny">Nothing in the record stands out.</p>'}
      </div>
      <div class="card" style="margin-bottom:14px">
        <h3>Why might it be cheap?</h3>
        <p style="margin:0 0 8px"><b>Most likely:</b> ${esc(wc.most_likely)}</p>
        <p style="margin:0 0 8px"><b>Biggest unresolved concern:</b> ${esc(wc.biggest_unresolved_concern)}</p>
        <ol style="margin:0;padding-left:18px;font-size:13px">
          ${wc.ranked_reasons.map(r=>`<li>${esc(r.reason)}</li>`).join('')}</ol>
        <p class="tiny dimmer" style="margin-top:9px">${esc(wc.note)}</p>
      </div>
      <div class="card">
        <h3>What should I do next?</h3>
        <ol style="margin:0;padding-left:18px;font-size:13px">
          ${d.next_steps.map(s=>`<li style="margin-bottom:6px"><b>${esc(s.title)}</b>
            <div class="tiny muted">${esc(s.detail)}</div>
            ${s.source_url?`<a class="tiny" href="${esc(s.source_url)}" target="_blank" rel="noopener">${esc(s.where_to_look)} &rarr;</a>`:''}</li>`).join('')}
        </ol>
      </div>
    </div>
  </div>
  ${d.conflicts.length?`<div class="banner danger" style="margin-top:14px">
    <b>INFORMATION CONFLICT</b>
    ${d.conflicts.map(c=>`<div>${esc(c.field)}: "${esc(c.value_a)}" (${esc(c.source_a)})
      vs "${esc(c.value_b)}" (${esc(c.source_b)}) &mdash; ${esc(c.status)}</div>`).join('')}</div>`:''}
  <div class="banner warn" style="margin-top:14px">${esc(d.disclaimer)}</div>`;
}
const evVal = (d, field) => { const e = (d.evidence||[]).find(x=>x.field===field); return e ? e.value : null; };
const TERMS = {'County assessed total':'assessed value','Land value':'assessed value','Improvement value':'assessed value',
  'Implied market value':'market value','Parcel ID':'parcel','Flood zone':'flood zone','Zoning':'zoning',
  'Tax status':'delinquent tax','Legal description':'deed','Owner of record':'title',
  'City liens':'cleanup lien','Vacancy register':'condemnation','RPID':'rpid'};
const kv = (k,v,raw)=>`<dt>${esc(k)}${TERMS[k]?` <button class="whats" title="What's this?" onclick="whatsThis('${esc(TERMS[k])}')">?</button>`:''}</dt><dd>${raw?v:(v===null||v===undefined||v===''?'<span class="dimmer">&mdash;</span>':esc(v))}</dd>`;
window.whatsThis = async (term)=>{
  const pid = S.current?.property?.id;
  const r = await api(`/api/education?term=${encodeURIComponent(term)}${pid?`&property_id=${pid}`:''}`);
  modal(`<h3>&#10067; ${esc(r.term)}</h3><p style="margin:0 0 8px">${esc(r.text)}</p>
    ${r.example?`<div class="banner info"><b>Here:</b> ${esc(r.example)}</div>`:''}
    <button class="btn primary sm" onclick="this.closest('.modal').remove()">Got it</button>`);
};

function tabEvidence(d){
  $('#dbody').innerHTML = `
  <div class="card pad0">
    <div style="padding:14px 16px"><h3 style="margin:0">Every fact we hold &mdash; ${d.evidence_count} pieces</h3>
    <p class="tiny dimmer" style="margin:5px 0 0">FACT = a source said it. OBSERVATION = we
      noticed it. CALCULATION = we worked it out. ESTIMATE = a rule of thumb.
      UNKNOWN = nobody has checked.</p></div>
    <div class="scroll"><table class="ev">
      <tr><th>Field</th><th>Value</th><th>Type</th><th>Confidence</th><th>Source</th><th>As of</th></tr>
      ${d.evidence.map(e=>`<tr>
        <td class="mono">${esc(e.field)}</td>
        <td>${esc(e.value)}${e.raw_ref?`<div class="tiny dimmer">${esc(e.raw_ref)}</div>`:''}</td>
        <td><span class="tag ${evCls(e.evidence_type)}">${esc(e.evidence_type)}</span></td>
        <td>${esc(e.confidence)}</td>
        <td>${e.source_url?`<a href="${esc(e.source_url)}" target="_blank" rel="noopener">${esc(e.source)}</a>`:esc(e.source)}</td>
        <td class="tiny dimmer">${esc((e.effective_date||e.retrieved_at||'').slice(0,10))}</td></tr>`).join('')}
    </table></div>
  </div>`;
}
const evCls = t => ({FACT:'green',OBSERVATION:'blue',CALCULATION:'purple',
  ESTIMATE:'yellow',UNKNOWN:'',CONFLICTING:'red',AI_OPINION:'orange'})[t]||'';

function tabScores(d){
  $('#dbody').innerHTML = `<div class="grid g2">
    ${Object.entries(d.scores).map(([kind,s])=>`
      <div class="card">
        <div class="spread"><h3 style="margin:0">${esc(kind)}</h3>
          <span class="score ${kind==='risk'?(s.score>60?'lo':'hi'):scoreCls(s.score)}">${num(s.score,0)}</span></div>
        <div class="tiny dimmer" style="margin:4px 0 9px">confidence: ${esc(s.confidence)}</div>
        ${(s.lines||[]).map(l=>`<div class="line">
          <span class="p ${l.points>0?'pos':'neg'}">${l.points>0?'+':''}${l.points}</span>
          <span>${esc(l.reason)}${l.evidence?`<div class="tiny dimmer">${esc(l.evidence)}</div>`:''}</span></div>`).join('')}
        ${(s.unknowns||[]).length?`<div class="tiny dimmer" style="margin-top:9px">
          <b>Not checked:</b> ${s.unknowns.map(esc).join(' &middot; ')}</div>`:''}
      </div>`).join('')}
  </div>`;
}

function tabMoney(d){
  const p = d.property, f = d.financials;
  const price = f.starting_price_estimate || Math.round((p.total_value||0)*5*0.75) || 30000;
  const sqft = p.building_sqft || 0;
  const rent = Math.round(sqft*0.95) || 800;
  const rehab = f.rehab?.estimate || Math.round(sqft*55) || 25000;
  const arv = f.implied_market_value || price*2;
  $('#dbody').innerHTML = `
  <div class="banner warn">Every number on this tab is an estimate built on assumptions
    you can change. Nothing here is measured from this property, and none of it is a
    promise of a return.</div>
  <div class="grid g2">
    <div class="card">
      <h3>Rental &mdash; move the sliders</h3>
      ${slider('sPrice','Purchase price',price,1000,400000,1000,'$')}
      ${slider('sRehab','Rehab',rehab,0,250000,1000,'$')}
      ${slider('sRent','Monthly rent',rent,200,4000,25,'$')}
      ${slider('sRate','Interest rate',8.9,3,15,0.1,'%')}
      ${slider('sVac','Vacancy',8,0,30,1,'%')}
      <div id="rentOut" class="mono tiny" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3>What should I pay? (deal analyzer) <button class="whats" onclick="whatsThis('mao')">?</button></h3>
      <div class="tiny dimmer" style="margin-bottom:6px">ARV <button class="whats" onclick="whatsThis('arv')">?</button> = what it would be worth once the work is done.</div>
      ${slider('dArv','Finished value (ARV)',arv,5000,600000,1000,'$')}
      ${slider('dRehab','Rehab',rehab,0,250000,1000,'$')}
      ${slider('dRet','Target return',22,5,50,1,'%')}
      <div id="dealOut" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3>Storage build-out sketch</h3>
      ${slider('stAc','Acreage',p.acreage||1,0.1,40,0.1,'ac')}
      ${slider('stUse','Usable share',65,20,95,5,'%')}
      <div id="stOut" class="mono tiny" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3>&#127847; Snow-cone site</h3>
      ${slider('scRank','Road class (traffic proxy)',roadRank(p),0,9,1,'')}
      ${slider('scComp','Competitors nearby',0,0,8,1,'')}
      <div id="scOut" class="mono tiny" style="margin-top:12px"></div>
      <p class="tiny dimmer" style="margin-top:9px">A seasonal food business needs a
        zoning use that allows it and Arkansas Department of Health approval. This tool
        cannot confirm either.</p>
    </div>
  </div>`;
  const on = ids => ids.forEach(i=>{ const e=$('#'+i); e.oninput=()=>{ syncLabels(); calc(); }; });
  on(['sPrice','sRehab','sRent','sRate','sVac','dArv','dRehab','dRet','stAc','stUse','scRank','scComp']);
  syncLabels(); calc();

  async function calc(){
    const pid = p.id;
    const [r, deal, st, sc] = await Promise.all([
      api(`/api/property/${pid}/financials`,{method:'POST',body:JSON.stringify({
        kind:'rental', purchase_price:+$('#sPrice').value, rehab:+$('#sRehab').value,
        monthly_rent:+$('#sRent').value,
        overrides:{interest_rate:+$('#sRate').value/100, vacancy_pct:+$('#sVac').value/100}})}),
      api(`/api/property/${pid}/financials`,{method:'POST',body:JSON.stringify({
        kind:'deal', after_repair_value:+$('#dArv').value, rehab:+$('#dRehab').value,
        desired_return:+$('#dRet').value/100})}),
      api(`/api/property/${pid}/financials`,{method:'POST',body:JSON.stringify({
        kind:'storage', acreage:+$('#stAc').value, usable_pct:+$('#stUse').value/100,
        purchase_price:+$('#sPrice').value})}),
      api(`/api/property/${pid}/financials`,{method:'POST',body:JSON.stringify({
        kind:'snowcone', road_rank:+$('#scRank').value, competitors:+$('#scComp').value,
        acreage:p.acreage||0.25})}),
    ]);
    const ret = r.returns, inc = r.income;
    $('#rentOut').innerHTML = `
      <div class="line money"><span class="p">${money(r.basis.total_basis)}</span><span>total basis (all-in)</span></div>
      <div class="line money"><span class="p">${money(inc.noi)}</span><span>NOI per year <button class="whats" onclick="whatsThis('noi')">?</button></span></div>
      <div class="line money"><span class="p ${ret.monthly_cash_flow>0?'pos':'neg'}">${money(ret.monthly_cash_flow)}</span><span>cash flow per month</span></div>
      <div class="line money"><span class="p">${pct(ret.cap_rate)}</span><span>cap rate <button class="whats" onclick="whatsThis('cap rate')">?</button></span></div>
      <div class="line money"><span class="p">${pct(ret.cash_on_cash)}</span><span>cash-on-cash <button class="whats" onclick="whatsThis('cash-on-cash')">?</button></span></div>
      <div class="line money"><span class="p">${num(ret.dscr)}</span><span>DSCR (under 1.0 does not cover the loan) <button class="whats" onclick="whatsThis('dscr')">?</button></span></div>
      <div class="line money"><span class="p">${money(ret.break_even_rent)}</span><span>break-even rent</span></div>
      <div style="margin-top:9px" class="tiny dimmer">Sensitivity:
        ${r.sensitivity.map(s=>`${esc(s.scenario)} &rarr; ${money(s.cash_flow)}/yr`).join(' &middot; ')}</div>
      <div class="tiny dimmer" style="margin-top:7px">${esc(r.caveat)}</div>`;
    const marks = deal.works_at_any_price===false
      ? ['&#10060;','&#9888;','&#8505;'] : ['&#9989;','&#9888;','&#10060;'];
    $('#dealOut').innerHTML =
      (deal.works_at_any_price===false
        ? '<div class="banner danger" style="margin-bottom:9px">No workable purchase price on these inputs.</div>'
        : '') +
      deal.plain_english.map((t,i)=>
      `<div class="line"><span class="p">${i<3?marks[i]:''}</span><span>${esc(t)}</span></div>`).join('');
    $('#stOut').innerHTML = `
      <div class="line money"><span class="p">${st.estimated_units_10x10}</span><span>10x10 units (very rough)</span></div>
      <div class="line money"><span class="p">${money(st.estimated_build_cost)}</span><span>build cost</span></div>
      <div class="line money"><span class="p">${money(st.noi)}</span><span>NOI per year</span></div>
      <div class="line money"><span class="p">${pct(st.yield_on_cost)}</span><span>yield on cost</span></div>
      <div class="tiny dimmer" style="margin-top:7px">${esc(st.caveat)}</div>`;
    $('#scOut').innerHTML = `
      <div class="line money"><span class="p">${sc.assumed_daily_traffic.toLocaleString()}</span><span>assumed cars per day</span></div>
      <div class="line money"><span class="p">${sc.estimated_daily_customers}</span><span>customers per day</span></div>
      <div class="line money"><span class="p">${money(sc.estimated_season_revenue)}</span><span>season revenue</span></div>
      <div class="line money"><span class="p ${sc.estimated_season_profit>0?'pos':'neg'}">${money(sc.estimated_season_profit)}</span><span>season profit</span></div>
      <div class="tiny dimmer" style="margin-top:7px">${esc(sc.caveat)}</div>`;
  }
}
function slider(id,label,val,min,max,step,unit){
  return `<div class="slider"><label>${esc(label)}
    <b id="${id}L">${unit==='$'?'$'+Math.round(val).toLocaleString():val+(unit?' '+unit:'')}</b></label>
    <input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"
      data-unit="${unit}"></div>`;
}
function syncLabels(){
  document.querySelectorAll('input[type=range]').forEach(r=>{
    const l = $('#'+r.id+'L'); if(!l) return;
    const u = r.dataset.unit;
    l.textContent = u==='$' ? '$'+Number(r.value).toLocaleString()
                  : r.value + (u?' '+u:'');
  });
}
// Mirrors hunter/sources/roads.py RANK_LABEL - one shared vocabulary.
const ROAD_RANKS = [[9,'interstate'],[8,'us highway'],[7,'state highway'],
  [6,'major street'],[5,'through street'],[3,'local street'],
  [2,'alley or service drive'],[0,'no mapped road']];
const roadRank = p => {
  const v = (p.road_class||'').toLowerCase();
  for(const [r,label] of ROAD_RANKS) if(v.includes(label)) return r;
  return 3;
};

function tabUses(d){
  const u = d.business_use;
  $('#dbody').innerHTML = `
  <div class="banner warn">${esc(u.warning)}</div>
  <div class="grid g3">${u.ranked.map(r=>`
    <div class="card">
      <div class="spread"><b>${esc(r.use)}</b>
        <span class="score ${scoreCls(r.score||0)}">${num(r.score,0)}</span></div>
      <div class="tiny muted" style="margin:6px 0">Fit: ${esc(r.fit)}</div>
      <div class="tiny dimmer"><b>Needs:</b> ${r.needs.map(esc).join(' &middot; ')}</div>
    </div>`).join('')}</div>`;
}

function tabTimeline(d){
  $('#dbody').innerHTML = `<div class="grid g2">
    <div class="card"><h3>Timeline</h3>
      ${d.timeline.length?`<div class="tl">${d.timeline.map(e=>`
        <div class="ev"><div class="t">${esc((e.event_date||e.created_at||'').slice(0,10))}
          &middot; ${esc(e.kind)}</div>
          <b>${esc(e.title)}</b>
          ${e.detail?`<div class="d">${esc(e.detail)}</div>`:''}
          ${e.source_url?`<a class="tiny" href="${esc(e.source_url)}" target="_blank" rel="noopener">source &rarr;</a>`:''}
        </div>`).join('')}</div>`:'<p class="muted tiny">Nothing on the timeline yet.</p>'}
    </div>
    <div class="card"><h3>Changes detected</h3>
      ${d.changes.length?d.changes.map(c=>`<div class="line">
        <span class="tag ${c.severity==='high'?'red':c.severity==='medium'?'yellow':''}">${esc(c.severity)}</span>
        <span>${esc(c.field)}: <span class="dimmer">${esc(c.old_value)}</span> &rarr; <b>${esc(c.new_value)}</b>
        <div class="tiny dimmer">${esc((c.detected_at||'').slice(0,16))} &middot; ${esc(c.source)}</div></span>
      </div>`).join('')
      :'<p class="muted tiny">Nothing has changed since we first saw it.</p>'}
    </div></div>`;
}

function tabTasks(d){
  $('#dbody').innerHTML = `
  <div class="card"><h3>What has to be checked by hand</h3>
    ${d.tasks.length?d.tasks.map(t=>taskRow(t,false)).join('')
      :'<p class="muted tiny">No open tasks.</p>'}
  </div>`;
}
const taskRow = (t, showProp=true)=>`
  <div class="taskrow" id="task${t.id}">
    <div class="taskhead">
      <div style="min-width:0">
        ${showProp?`<div class="taskprop">${t.property_id
          ? `<a href="#property/${t.property_id}" onclick="go('property',${t.property_id})">${esc(t.address||('property '+t.property_id))}</a>`
          : '<span class="dimmer">applies to every property</span>'}</div>`:''}
        <div class="row" style="gap:7px">
          ${t.manual?'<span class="tag orange">MANUAL</span>':''}
          <b style="font-size:13.5px">${esc(t.title.replace(/^MANUAL VERIFICATION REQUIRED - /,''))}</b>
          <span class="tag">P${t.priority}</span>
        </div>
      </div>
      ${t.status==='open'?`<button class="btn sm ghost" onclick="completeTask(${t.id})">Mark done</button>`
        :'<span class="tag green">done</span>'}
    </div>
    ${t.detail?`<div class="tiny muted" style="margin-top:5px">${esc(t.detail)}</div>`:''}
    ${t.why?`<div class="tiny dimmer" style="margin-top:3px"><b>Why:</b> ${esc(t.why)}</div>`:''}
    ${t.where_to_look?`<div class="tiny" style="margin-top:3px"><b>Where:</b> ${t.source_url
      ?`<a href="${esc(t.source_url)}" target="_blank" rel="noopener">${esc(t.where_to_look)}</a>`
      :esc(t.where_to_look)}</div>`:''}
  </div>`;
window.completeTask = (id)=>{
  const m = modal(`<h3>Mark done &mdash; what did you find?</h3>
    <p class="tiny muted">Whatever you write becomes evidence with your name on it,
      stored as a FACT with HIGH confidence.</p>
    <div class="field"><label class="fl">What you found</label>
      <textarea id="tev" rows="4" placeholder="e.g. Taxes current through 2025, paid 03/2026. Collector's office, spoke to clerk."></textarea></div>
    <button class="btn primary" id="tsave">Save</button>`);
  $('#tsave').onclick = async ()=>{
    await api(`/api/task/${id}`,{method:'POST',body:JSON.stringify({
      status:'done', evidence:$('#tev').value })});
    m.remove(); toast('Recorded.'); render();
  };
};

function tabField(d){
  const p = d.property;
  const photos = d.photos||[];
  $('#dbody').innerHTML = `
  <div class="grid g2">
    <div>
      <div class="card" style="margin-bottom:14px">
        <h3>&#128247; Photos</h3>
        <p class="tiny dimmer">Your own photos, stored as observations. We do not copy listing
          or street-view imagery; we link to it instead.</p>
        <div class="row">
          <select id="phKind" style="width:150px">
            ${['inspection','street','before','during','after','rehab','code','owner'].map(k=>`<option>${k}</option>`).join('')}</select>
          <input type="text" id="phCap" placeholder="Caption (optional)" style="flex:1;min-width:140px">
          <label class="btn sm primary" style="cursor:pointer">Add photo
            <input type="file" id="phFile" accept="image/*" capture="environment" style="display:none" multiple></label>
        </div>
        <div id="phGrid" class="gallery">${photos.map(photoTile).join('') || '<p class="muted tiny" style="margin-top:10px">No photos yet.</p>'}</div>
      </div>
      <div class="card">
        <h3>&#128196; Documents</h3>
        <div class="row">
          <select id="docCat" style="width:140px">
            ${['deed','tax','gis','zoning','code','lien','auction','listing','inspection','contractor','receipt','financing','closing','lease','other'].map(k=>`<option>${k}</option>`).join('')}</select>
          <input type="text" id="docTitle" placeholder="Title (optional)" style="flex:1;min-width:140px">
          <label class="btn sm" style="cursor:pointer">Add document
            <input type="file" id="docFile" style="display:none"></label>
        </div>
        <div style="margin-top:10px">${(d.documents||[]).map(x=>`
          <div class="line"><span class="tag">${esc(x.category)}</span>
            <span><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
              <span class="tiny dimmer">&middot; ${esc((x.added_at||'').slice(0,10))}</span></span></div>`).join('')
          || '<p class="muted tiny">No documents yet.</p>'}</div>
      </div>
    </div>
    <div>
      <div class="card" style="margin-bottom:14px">
        <h3>&#127908; Field notes</h3>
        <p class="tiny dimmer">Anything you or a neighbour says is stored as UNVERIFIED. It never
          becomes a fact just because somebody said it.</p>
        <div class="field"><textarea id="fnote" rows="3"
          placeholder="Drove by. Roof looks bad. Neighbour says nobody has lived there in years."></textarea></div>
        <div class="row">
          <button class="btn primary sm" id="fsave">Save note</button>
          <button class="btn sm" id="recBtn">&#9679; Record voice note</button>
          <span id="recState" class="tiny muted"></span>
        </div>
        <div style="margin-top:14px">${(d.notes||[]).map(noteRow).join('')||'<p class="muted tiny">No notes yet.</p>'}</div>
      </div>
      <div class="card">
        <h3>Where it is &amp; quick actions</h3>
        ${p.lat?`<p class="mono tiny">${num(p.lat,6)}, ${num(p.lon,6)}</p>
          <div class="row" style="margin-bottom:12px">
            <a class="btn sm" target="_blank" rel="noopener"
               href="https://maps.apple.com/?ll=${p.lat},${p.lon}&q=${encodeURIComponent(p.address||'parcel')}">Open in Maps</a>
            <a class="btn sm" target="_blank" rel="noopener"
               href="https://www.openstreetmap.org/#map=18/${p.lat}/${p.lon}">OpenStreetMap</a>
          </div>`:'<p class="muted tiny">No coordinates.</p>'}
        <div class="row">
          <button class="btn sm" onclick="setState(${p.id},'INTERESTING')">Mark interesting</button>
          <button class="btn sm" onclick="setState(${p.id},'INVESTIGATING')">Investigating</button>
          <button class="btn sm" onclick="markVisited(${p.id})">Mark as visited</button>
          <button class="btn sm" onclick="passProperty(${p.id})">Pass</button>
        </div>
      </div>
    </div>
  </div>`;

  $('#fsave').onclick = async ()=>{
    const body = $('#fnote').value.trim(); if(!body) return;
    await api(`/api/property/${p.id}/note`,{method:'POST',
      body:JSON.stringify({body, kind:'field_note'})});
    toast('Note saved as an unverified observation.'); render();
  };
  $('#phFile').onchange = async (e)=>{
    for(const f of e.target.files){
      const fd = new FormData(); fd.append('file', f); fd.append('kind', $('#phKind').value);
      fd.append('caption', $('#phCap').value);
      try{ await fetch(`/api/property/${p.id}/photo`,{method:'POST',body:fd}).then(r=>{ if(!r.ok) throw new Error(r.statusText); }); }
      catch(err){ toast('Upload failed: '+err.message); }
    }
    toast('Photo saved.'); render();
  };
  $('#docFile').onchange = async (e)=>{
    const f = e.target.files[0]; if(!f) return;
    const fd = new FormData(); fd.append('file', f); fd.append('category', $('#docCat').value);
    fd.append('title', $('#docTitle').value);
    const r = await fetch(`/api/property/${p.id}/document`,{method:'POST',body:fd});
    toast(r.ok?'Document saved.':'Upload failed'); render();
  };
  setupRecorder(p.id);
}
const photoTile = ph => `
  <figure class="ptile">
    <a href="${esc(ph.url)}" target="_blank" rel="noopener"><img src="${esc(ph.url)}" loading="lazy" alt="${esc(ph.caption||ph.kind)}"></a>
    <figcaption><span class="tag ${{before:'orange',during:'yellow',after:'green',aerial:'blue'}[ph.kind]||''}">${esc(ph.kind)}</span>
      ${ph.caption?esc(ph.caption):''}<div class="tiny dimmer">${esc((ph.captured_at||ph.created_at||'').slice(0,10))} &middot; ${esc(ph.source||'')}</div>
      <button class="btn sm ghost" style="margin-top:4px" onclick="lookAt(${ph.id},this)">Look at it</button></figcaption>
  </figure>`;
const noteRow = n => `
  <div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05)">
    <div class="tiny dimmer">${esc((n.created_at||'').slice(0,16))} &middot; ${esc(n.author)}
      &middot; <span class="tag">${esc(n.confidence)}</span>
      ${n.kind==='voice_note'?'<span class="tag blue">voice</span>':''}</div>
    <div style="font-size:13.5px" id="noteBody${n.id}">${esc(n.body)}</div>
    ${n.audio_path?`<audio controls preload="none" src="${esc(n.audio_path)}" style="width:100%;height:32px;margin-top:6px"></audio>
      <button class="btn sm ghost" onclick="editTranscript(${n.id})">${n.body.startsWith('(voice note')?'Type transcript':'Edit transcript'}</button>`:''}
  </div>`;
window.editTranscript = (id)=>{
  const cur = $('#noteBody'+id)?.textContent || '';
  const m = modal(`<h3>Transcript</h3><p class="tiny muted">Stored as UNVERIFIED, like any field note.</p>
    <div class="field"><textarea id="trx" rows="4">${esc(cur.startsWith('(voice note')?'':cur)}</textarea></div>
    <button class="btn primary" id="trxSave">Save</button>`);
  $('#trxSave').onclick = async ()=>{
    await api(`/api/note/${id}`,{method:'POST',body:JSON.stringify({body:$('#trx').value})});
    m.remove(); render();
  };
};
window.markVisited = async (id)=>{
  await api(`/api/property/${id}/note`,{method:'POST',
    body:JSON.stringify({body:'Visited the property in person.', kind:'field_note'})});
  toast('Marked as visited.'); render();
};
function setupRecorder(pid){
  const btn = $('#recBtn'), st = $('#recState'); if(!btn) return;
  if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder){
    btn.disabled = true; st.textContent = 'recording not supported in this browser'; return;
  }
  let rec = null, chunks = [], t0 = 0, timer = null;
  btn.onclick = async ()=>{
    if(rec && rec.state === 'recording'){ rec.stop(); return; }
    try{
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      chunks = []; rec = new MediaRecorder(stream);
      rec.ondataavailable = e=>{ if(e.data.size) chunks.push(e.data); };
      rec.onstop = async ()=>{
        clearInterval(timer); stream.getTracks().forEach(t=>t.stop());
        btn.innerHTML = '&#9679; Record voice note'; st.innerHTML = '<span class="spin"></span> saving';
        const blob = new Blob(chunks, {type: rec.mimeType || 'audio/webm'});
        const fd = new FormData(); fd.append('file', blob, 'note.webm');
        const r = await fetch(`/api/property/${pid}/voice`, {method:'POST', body:fd});
        const j = await r.json().catch(()=>({}));
        toast(r.ok ? (j.auto_transcribed ? 'Voice note saved and transcribed locally.'
                                        : 'Voice note saved. '+(j.note||''))
                   : 'Upload failed: '+(j.detail||r.statusText), 5000);
        render();
      };
      rec.start(); t0 = Date.now();
      btn.innerHTML = '&#9632; Stop';
      timer = setInterval(()=>{ st.textContent = 'recording '+Math.round((Date.now()-t0)/1000)+'s'; }, 500);
    }catch(e){ st.textContent = 'microphone blocked: '+e.message; }
  };
}

/* ---------------------------------------------------------------- own it */
async function tabOwn(d){
  const p = d.property;
  const [pf, rehab, led, leases] = await Promise.all([
    api('/api/portfolio'), api(`/api/rehab/${p.id}`),
    api(`/api/property/${p.id}/ledger`), api(`/api/property/${p.id}/leases`)]);
  const mine = pf.properties.find(x=>x.property_id===p.id);
  const before = (d.photos||[]).filter(x=>x.kind==='before');
  const during = (d.photos||[]).filter(x=>x.kind==='during');
  const after  = (d.photos||[]).filter(x=>x.kind==='after');
  $('#dbody').innerHTML = `
  <div class="banner warn">Everything on this tab is what <b>you</b> entered. Nothing here comes
    from an outside source. Buying, borrowing, signing and contacting anybody still happen
    outside this app, by you.</div>
  <div class="grid g2">
    <div class="card">
      <h3>${mine?'In your portfolio':'Add to your portfolio'}</h3>
      <p class="tiny dimmer">Fill this in once you actually own it. It moves the property to
        the PORTFOLIO state.</p>
      <div class="grid g-form">
        ${ownField('purchase_price','Purchase price',mine)}${ownField('purchase_date','Purchase date',mine,'text')}
        ${ownField('closing_costs','Closing costs',mine)}${ownField('rehab_budget','Rehab budget',mine)}
        ${ownField('loan_amount','Loan amount',mine)}${ownField('interest_rate','Interest rate (e.g. 0.089)',mine)}
        ${ownField('insurance_annual','Insurance / yr',mine)}${ownField('taxes_annual','Taxes / yr',mine)}
        ${ownField('current_value','Current value',mine)}${ownField('units','Units',mine)}
      </div>
      <button class="btn primary sm" id="ownSave" style="margin-top:8px">${mine?'Update':'Add to portfolio'}</button>
    </div>
    <div class="card">
      <h3>Renovation &mdash; budget vs actual</h3>
      ${rehab.projects.length ? rehab.projects.map(pr=>`
        <div style="margin-bottom:12px">
          <div class="spread"><b>${esc(pr.name)}</b>
            <span class="mono tiny">${money(pr.actual_total)} of ${money(pr.budget_total||pr.budget)}
              <span class="${pr.actual_total>(pr.budget_total||pr.budget||0)?'neg':'pos'}" style="color:${pr.actual_total>(pr.budget_total||pr.budget||0)?'var(--red)':'var(--green)'}">
              ${pr.actual_total>(pr.budget_total||pr.budget||0)?'over':'on budget'}</span></span></div>
          <div class="bar" style="width:100%;flex:none;margin:6px 0"><i style="width:${Math.min(100,100*(pr.actual_total||0)/((pr.budget_total||pr.budget)||1))}%"></i></div>
          ${pr.tasks.map(t=>`<div class="line money">
            <span class="p">${money(t.actual)}</span>
            <span><span class="tag ${t.status==='done'?'green':''}">${esc(t.status)}</span>
              <b>${esc(t.title)}</b> <span class="dimmer tiny">${esc(t.category)} &middot; budget ${money(t.budget)}${t.contractor?' &middot; '+esc(t.contractor):''}</span>
              <button class="btn sm ghost" onclick="editRehabTask(${t.id},${p.id})">edit</button></span></div>`).join('')}
          <button class="btn sm ghost" onclick="addRehabTask(${pr.id},${p.id})">+ task</button>
        </div>`).join('') : '<p class="muted tiny">No renovation project yet.</p>'}
      <button class="btn sm" id="projAdd">+ New project</button>
      <h3 style="margin-top:18px">Before &rarr; during &rarr; after</h3>
      <div class="ba">
        ${[['before',before],['during',during],['after',after]].map(([k,list])=>`
          <div class="bacol"><div class="tiny dimmer" style="text-transform:uppercase;letter-spacing:.8px">${k}</div>
            ${list.length?list.map(ph=>`<a href="${esc(ph.url)}" target="_blank" rel="noopener"><img src="${esc(ph.url)}" loading="lazy"></a>`).join('')
              :`<div class="baempty">no ${k} photos yet</div>`}</div>`).join('')}
      </div>
      <p class="tiny dimmer">Tag photos on the Field tab as before / during / after and they line up here.</p>
    </div>
    <div class="card">
      <h3>Money in and out</h3>
      <div class="row" style="margin-bottom:8px">
        <select id="ledDir" style="width:110px"><option>expense</option><option>revenue</option></select>
        <input type="text" id="ledCat" placeholder="category" style="width:120px">
        <input type="number" id="ledAmt" placeholder="amount" style="width:110px">
        <input type="text" id="ledMemo" placeholder="memo" style="flex:1;min-width:120px">
        <button class="btn sm" id="ledAdd">Add</button>
      </div>
      <div class="grid g3" style="gap:8px;margin-bottom:8px">
        ${stat(money(led.expenses).replace('$',''),'spent','red')}${stat(money(led.revenue).replace('$',''),'collected','green')}
        ${stat(money(led.net).replace('$',''),'net',led.net>=0?'green':'red')}</div>
      ${led.entries.slice(0,12).map(e=>`<div class="line money">
        <span class="p ${e.direction==='revenue'?'pos':'neg'}">${e.direction==='revenue'?'+':'-'}${money(e.amount)}</span>
        <span>${esc(e.category)} <span class="dimmer tiny">${esc(e.occurred_on||'')} ${e.memo?'&middot; '+esc(e.memo):''}</span></span></div>`).join('')
        || '<p class="muted tiny">No entries yet.</p>'}
    </div>
    <div class="card">
      <h3>Tenants &amp; leases</h3>
      <div class="row" style="margin-bottom:8px">
        <input type="text" id="lsName" placeholder="tenant" style="flex:1;min-width:110px">
        <input type="number" id="lsRent" placeholder="rent / mo" style="width:110px">
        <input type="text" id="lsStart" placeholder="start YYYY-MM-DD" style="width:150px">
        <button class="btn sm" id="lsAdd">Add lease</button>
      </div>
      ${leases.leases.map(l=>`<div class="line money"><span class="p">${money(l.rent)}</span>
        <span><span class="tag ${l.status==='active'?'green':''}">${esc(l.status)}</span> <b>${esc(l.tenant_name||'')}</b>
          <span class="dimmer tiny">${esc(l.start_date||'')}${l.end_date?' to '+esc(l.end_date):''}</span></span></div>`).join('')
        || '<p class="muted tiny">No leases.</p>'}
    </div>
  </div>`;
  $('#ownSave').onclick = async ()=>{
    const body = {};
    document.querySelectorAll('[data-own]').forEach(i=>{ if(i.value!=='') body[i.dataset.own] = i.type==='number'?Number(i.value):i.value; });
    await api(`/api/portfolio/${p.id}`,{method:'POST',body:JSON.stringify(body)});
    toast('Saved to your portfolio.'); render();
  };
  $('#projAdd').onclick = async ()=>{
    const name = prompt('Project name', 'Make-ready'); if(!name) return;
    await api(`/api/rehab/${p.id}/project`,{method:'POST',body:JSON.stringify({name})}); render();
  };
  $('#ledAdd').onclick = async ()=>{
    const amt = Number($('#ledAmt').value); if(!amt) return toast('Enter an amount');
    await api(`/api/property/${p.id}/ledger`,{method:'POST',body:JSON.stringify({
      direction:$('#ledDir').value, category:$('#ledCat').value||'general', amount:amt, memo:$('#ledMemo').value})});
    render();
  };
  $('#lsAdd').onclick = async ()=>{
    await api(`/api/property/${p.id}/lease`,{method:'POST',body:JSON.stringify({
      tenant_name:$('#lsName').value, rent:Number($('#lsRent').value)||null, start_date:$('#lsStart').value||null})});
    render();
  };
}
const ownField = (k,label,mine,type='number')=>`<div><label class="fl">${esc(label)}</label>
  <input type="${type}" data-own="${k}" value="${mine&&mine[k]!==null&&mine[k]!==undefined?esc(mine[k]):''}"></div>`;
window.addRehabTask = (projectId, pid)=>{
  const m = modal(`<h3>New renovation task</h3>
    <div class="field"><label class="fl">Category</label><select id="rtCat">
      ${['demolition','roof','plumbing','electrical','HVAC','windows','doors','drywall','paint','flooring','kitchen','bathroom','landscaping','cleanup','inspections','other'].map(c=>`<option>${c}</option>`).join('')}</select></div>
    <div class="field"><label class="fl">Title</label><input type="text" id="rtTitle"></div>
    <div class="field"><label class="fl">Budget</label><input type="number" id="rtBudget"></div>
    <div class="field"><label class="fl">Contractor</label><input type="text" id="rtWho"></div>
    <button class="btn primary" id="rtSave">Add</button>`);
  $('#rtSave').onclick = async ()=>{
    await api('/api/rehab/task',{method:'POST',body:JSON.stringify({project_id:projectId, property_id:pid,
      category:$('#rtCat').value, title:$('#rtTitle').value||$('#rtCat').value,
      budget:Number($('#rtBudget').value)||null, contractor:$('#rtWho').value||null})});
    m.remove(); render();
  };
};
window.editRehabTask = (taskId)=>{
  const m = modal(`<h3>Update task</h3>
    <div class="field"><label class="fl">Status</label><select id="rtStatus">
      ${['todo','in progress','done','skipped'].map(s=>`<option>${s}</option>`).join('')}</select></div>
    <div class="field"><label class="fl">Actual cost</label><input type="number" id="rtActual"></div>
    <div class="field"><label class="fl">Notes</label><input type="text" id="rtNotes"></div>
    <button class="btn primary" id="rtSave">Save</button>`);
  $('#rtSave').onclick = async ()=>{
    const body = {status:$('#rtStatus').value};
    if($('#rtActual').value!=='') body.actual = Number($('#rtActual').value);
    if($('#rtNotes').value) body.notes = $('#rtNotes').value;
    await api(`/api/rehab/task/${taskId}`,{method:'POST',body:JSON.stringify(body)});
    m.remove(); render();
  };
};
window.setState = async (id,state)=>{
  await api(`/api/property/${id}/state`,{method:'POST',body:JSON.stringify({state})});
  toast('Moved to '+state); render();
};
window.passProperty = (id)=>{
  const m = modal(`<h3>Why are you passing?</h3>
    <p class="tiny muted">Recorded so your lists get better. The scoring model is not
      changed behind your back.</p>
    <div class="field"><select id="preason">
      ${['too expensive','bad title','too much rehab','bad location','not enough rent',
         'zoning','flood','no access','not my kind of deal']
        .map(r=>`<option>${r}</option>`).join('')}</select></div>
    <button class="btn primary" id="psave">Pass</button>`);
  $('#psave').onclick = async ()=>{
    await api(`/api/property/${id}/state`,{method:'POST',
      body:JSON.stringify({state:'PASS', reason:$('#preason').value})});
    m.remove(); toast('Passed.'); render();
  };
};

function tabAI(d){
  const p = d.property;
  $('#dbody').innerHTML = `
  <div class="row" style="margin-bottom:12px">
    <button class="btn primary" id="aiExplain">EXPLAIN THIS PROPERTY</button>
    <button class="btn" id="aiWwyd">WHAT WOULD YOU DO?</button>
    <button class="btn" id="aiMemo">MAKE MY DEAL MEMO</button>
  </div>
  <div id="aiOut"></div>`;
  const run = async (url, title)=>{
    $('#aiOut').innerHTML = `<div class="card"><div class="row">
      <span class="spin"></span><span class="muted">Reading the evidence&hellip;
      a local model on your own machine, so give it a moment.</span></div></div>`;
    const r = await api(url);
    $('#aiOut').innerHTML = `<div class="ai"><h4>${esc(title)}</h4>${esc(r.text||'')
      .replace(/\n(WHAT [A-Z' ]+|WHAT WE DON'T KNOW)\n?/g,'\n<h4>$1</h4>')}
      ${r.guard_note?`<div class="banner warn" style="margin-top:12px"><b>Guard:</b> ${esc(r.guard_note)}
        The model tried to state figures it was never given; they were cut before you saw them.</div>`:''}
      <div class="tiny dimmer" style="margin-top:14px">
        ${r.model?`Written by ${esc(r.model)} running locally, using only the stored evidence.`
                 :esc(r.source||'')}</div>
      <div class="tiny dimmer" style="margin-top:6px">${esc(r.disclaimer||'')}</div></div>`;
  };
  $('#aiExplain').onclick = ()=>run(`/api/property/${p.id}/explain`,'Explain this property');
  $('#aiWwyd').onclick = ()=>run(`/api/property/${p.id}/what-would-you-do`,'What would you do?');
  $('#aiMemo').onclick = async ()=>{
    $('#aiOut').innerHTML = '<div class="card"><span class="spin"></span> Writing the memo&hellip;</div>';
    const m = await api(`/api/property/${p.id}/memo`);
    $('#aiOut').innerHTML = `<div class="card">
      <h2 style="margin-top:0">Deal memo &mdash; ${esc(m.property)}</h2>
      <h3>Why it's interesting</h3><ul>${m.why_interesting.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>Nothing stands out.</li>'}</ul>
      <h3>What we know</h3><ul>${m.what_we_know.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
      <h3>What we don't know</h3><ul>${m.what_we_dont_know.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>
      <h3>Estimated numbers</h3>
      <dl class="kv">
        ${kv('Implied market value', money(m.estimated_numbers.implied_market_value), true)}
        ${kv('Rehab estimate', money(m.estimated_numbers.rehab), true)}
        ${kv('Max purchase price', money(m.estimated_numbers.max_purchase_price), true)}
        ${kv('Monthly cash flow', money(m.estimated_numbers.monthly_cash_flow), true)}
        ${kv('Cap rate', pct(m.estimated_numbers.cap_rate), true)}</dl>
      <h3>Risks</h3><ul>${m.risks.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>None flagged &mdash; which is not the same as none.</li>'}</ul>
      <h3>Verdict</h3><p><b>${esc(m.verdict)}</b> &middot; ${esc(m.recommendation)}
        &middot; confidence ${esc(m.confidence)}</p>
      ${m.narrative?`<div class="ai">${esc(m.narrative)}</div>`:''}
      <h3>Next steps</h3><ol>${m.next_steps.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>
      <div class="banner warn">${esc(m.disclaimer)}</div></div>`;
  };
}

window.getAerials = async (id, btn)=>{
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> fetching';
  try{ const r = await api(`/api/property/${id}/imagery`,{method:'POST',body:'{}'});
    const bad = Object.values(r.results).filter(x=>x.status!=='ok');
    toast(bad.length?('Some imagery unavailable: '+bad.map(x=>x.detail).join('; ')):'Aerials and terrain on file.'); render(); }
  catch(e){ toast('Imagery: '+e.message); btn.disabled=false; btn.textContent='Get aerials'; }
};
window.lookAt = async (photoId, btn)=>{
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> looking';
  try{ const r = await api(`/api/photo/${photoId}/analyse`,{method:'POST',body:'{}'});
    modal(`<h3>What the model saw <span class="tag orange">AI &middot; low confidence</span></h3>
      <p class="tiny muted">${esc(r.caveat)}</p>
      <ul>${r.observations.map(o=>`<li>${esc(o)}</li>`).join('')||'<li>Nothing notable stood out.</li>'}</ul>
      <div class="tiny dimmer">${esc(r.model)}${r.cached?' (cached)':''}</div>
      <button class="btn primary" style="margin-top:10px" onclick="this.closest('.modal').remove();render()">Close</button>`); }
  catch(e){ toast('Vision: '+(e.message||'unavailable'), 5000); btn.disabled=false; btn.textContent='Look at it'; }
};
window.downloadPdf = async (id, btn)=>{
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';
  try{
    const r = await fetch(`/api/property/${id}/report.pdf`);
    if(!r.ok){ const t = await r.json().catch(()=>({detail:r.statusText})); throw new Error(t.detail); }
    const blob = await r.blob(); const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = (r.headers.get('content-disposition')||'').match(/filename="([^"]+)"/)?.[1] || 'property.pdf';
    a.click(); setTimeout(()=>URL.revokeObjectURL(url), 5000);
  }catch(e){ toast('PDF: '+e.message, 5000); }
  finally{ btn.disabled=false; btn.innerHTML=old; }
};
window.runInvestigation = async (id)=>{
  const m = modal(`<h2 style="margin-top:0">Investigating</h2>
    <p class="tiny muted">Eighteen stages. Each one does real work or says plainly that
      only a human can answer it.</p><div id="invBody"><span class="spin"></span> starting&hellip;</div>`);
  try{
    const r = await api(`/api/property/${id}/investigate`,{method:'POST'});
    $('#invBody').innerHTML = `
      ${r.stages.map(s=>`<div class="stage ${s.status==='manual'?'unavailable':s.status}">
        <span class="mk">${s.status==='done'?'&#10003;':s.status==='manual'?'&#9995;':'!'}</span>
        <span class="txt"><b>${esc(s.label)}</b><div class="d">${esc(s.detail)}</div>
        ${s.findings.map(f=>`<div class="tiny" style="margin-top:3px">
          &bull; ${esc(f.text)} <span class="tag ${evCls(f.type)}">${esc(f.type)}</span></div>`).join('')}
        </span></div>`).join('')}
      <div class="banner info" style="margin-top:14px">
        <b>${esc(r.summary.recommendation)}</b> &mdash; ${esc(r.summary.deal_or_trap.why)}
        <div class="tiny" style="margin-top:8px"><b>Still open:</b>
          ${r.summary.open_questions.map(esc).join(' &middot; ')}</div></div>
      <button class="btn primary" onclick="this.closest('.modal').remove();render()">Close</button>`;
  }catch(e){ $('#invBody').innerHTML = `<div class="banner danger">${esc(e.message)}</div>`; }
};

/* ------------------------------------------------------------- tax & misc */
VIEWS.field = async (v)=>{
  v.innerHTML = `<h1>Near me</h1>
  <p class="lede">Driving or walking an area? This lists what we are tracking around you,
    nearest first, with the actions you need on the spot.</p>
  <div class="card" style="margin-bottom:14px">
    <div class="row">
      <button class="btn primary" id="locBtn">&#128205; Use my location</button>
      <span class="dimmer tiny">or</span>
      <input type="text" id="locLat" placeholder="lat" style="width:120px">
      <input type="text" id="locLon" placeholder="lon" style="width:120px">
      <select id="locR" style="width:120px"><option value="500">500 m</option><option value="1500" selected>1.5 km</option>
        <option value="4000">4 km</option><option value="10000">10 km</option></select>
      <button class="btn sm" id="locGo">Search</button>
      <span id="locState" class="tiny muted"></span>
    </div>
  </div>
  <div id="nearBox"><p class="muted tiny">No location yet.</p></div>`;
  const search = async (lat,lon)=>{
    $('#nearBox').innerHTML = '<div class="row"><span class="spin"></span><span class="muted">looking around&hellip;</span></div>';
    const r = await api(`/api/near?lat=${lat}&lon=${lon}&radius_m=${$('#locR').value}`);
    $('#nearBox').innerHTML = r.count ? r.properties.map(p=>`
      <div class="card" style="margin-bottom:10px">
        <div class="spread"><div><span class="pdot bg-${p.marker}"></span> <b>${esc(p.address||'No street address')}</b>
          <span class="dimmer tiny">&middot; ${p.distance_m<1000?p.distance_m+' m':(p.distance_m/1000).toFixed(1)+' km'} away
          &middot; ${esc(p.city||'')} &middot; ${num(p.acreage)} ac</span></div>
          <span class="score ${scoreCls(p.overall_score||0)}">${num(p.overall_score,0)}</span></div>
        <div class="tiny muted" style="margin:4px 0 8px">${esc(p.recommendation||'')} ${(p.distress||[]).slice(0,2).map(s=>'&middot; '+esc(s.label)).join(' ')}</div>
        <div class="row">
          <button class="btn sm primary" onclick="go('property',${p.id})">Open</button>
          <button class="btn sm" onclick="go('property',${p.id});setTimeout(()=>document.querySelector('#dtabs button:nth-child(8)')?.click(),400)">Field tab</button>
          <button class="btn sm ghost" onclick="toggleWatch(${p.id},this)">${p.watched?'&#11088;':'WATCH'}</button>
          <button class="btn sm ghost" onclick="markVisited(${p.id})">Visited</button>
          <a class="btn sm ghost" target="_blank" rel="noopener" href="https://maps.apple.com/?daddr=${p.lat},${p.lon}">Directions</a>
        </div></div>`).join('')
      : '<div class="card muted">Nothing we track within that distance.</div>';
  };
  $('#locGo').onclick = ()=>{ const la=+$('#locLat').value, lo=+$('#locLon').value; if(la&&lo) search(la,lo); else toast('Enter lat and lon'); };
  $('#locBtn').onclick = ()=>{
    if(!navigator.geolocation) return $('#locState').textContent = 'no geolocation in this browser';
    $('#locState').innerHTML = '<span class="spin"></span> locating';
    navigator.geolocation.getCurrentPosition(pos=>{
      const {latitude:la, longitude:lo} = pos.coords;
      $('#locLat').value = la.toFixed(6); $('#locLon').value = lo.toFixed(6);
      $('#locState').textContent = `you are at ${la.toFixed(4)}, ${lo.toFixed(4)}`; search(la,lo);
    }, err=>{ $('#locState').textContent = 'location blocked: '+err.message; }, {enableHighAccuracy:true, timeout:12000});
  };
};

VIEWS.tax = async (v)=>{
  const t = await api('/api/tasks?status=open&limit=300');
  const taxTasks = t.tasks.filter(x=>['cosl','garland_tax_collector'].includes(x.source));
  v.innerHTML = `
  <h1>Tax &amp; state opportunities</h1>
  <p class="lede">Delinquent taxes and Commissioner of State Lands activity.</p>
  <div class="banner danger">
    <b>Paying somebody's delinquent taxes does NOT make you the owner in Arkansas.</b>
    Only a completed purchase from the Commissioner of State Lands and the deed that
    follows transfers ownership &mdash; and even then, have an Arkansas real-estate
    attorney check it before you rely on it.</div>
  <div class="banner warn">
    <b>MANUAL VERIFICATION REQUIRED.</b> Neither the County Tax Collector nor COSL
    publishes a data feed we can lawfully read. We do not scrape them and we do not
    guess a tax status. Every parcel below has a task pointing at exactly where to look.</div>
  <div class="card"><h3>${taxTasks.length} open tax checks</h3>
    ${taxTasks.map(t=>taskRow(t)).join('') || '<p class="muted tiny">No tax checks queued. Run a scan.</p>'}
  </div>`;
};

VIEWS.tasks = async (v)=>{
  const t = await api('/api/tasks?status=open&limit=300');
  const manual = t.tasks.filter(x=>x.manual), rest = t.tasks.filter(x=>!x.manual);
  v.innerHTML = `<h1>To do</h1>
  <p class="lede">${t.tasks.length} open. The orange ones are things no machine can do.</p>
  <div class="card" style="margin-bottom:14px"><h3>Manual verification required</h3>
    ${manual.map(t=>taskRow(t)).join('')||'<p class="muted tiny">Nothing.</p>'}</div>
  <div class="card"><h3>Everything else</h3>
    ${rest.map(t=>taskRow(t)).join('')||'<p class="muted tiny">Nothing.</p>'}</div>`;
};

VIEWS.alerts = async (v)=>{
  const a = await api('/api/alerts?limit=150');
  v.innerHTML = `<div class="spread"><h1>Alerts</h1>
    <button class="btn sm" onclick="markRead()">Mark all read</button></div>
  <p class="lede">Everything that moved.</p>
  <div class="card">${a.alerts.length?a.alerts.map(x=>`
    <div class="line" style="${x.read_at?'opacity:.55':''}">
      <span class="tag ${x.severity==='high'?'red':x.severity==='medium'?'yellow':'blue'}">${esc(x.kind)}</span>
      <span><b>${esc(x.title)}</b>
        ${x.body?`<div class="tiny muted">${esc(x.body)}</div>`:''}
        <div class="tiny dimmer">${esc(ago(x.created_at))}
          ${x.property_id?`&middot; <a href="#property/${x.property_id}" onclick="go('property',${x.property_id})">open</a>`:''}</div>
      </span></div>`).join(''):'<p class="muted tiny">No alerts.</p>'}</div>`;
};
window.markRead = async ()=>{ await api('/api/alerts/read',{method:'POST',body:'{}'});
  toast('Marked read'); refreshStatus(); render(); };

VIEWS.deals = async (v)=>{
  const f = await api('/api/finance/defaults');
  v.innerHTML = `<h1>Money</h1>
  <p class="lede">The assumptions behind every number in this app. Change them here and
    the analyses change with them.</p>
  <div class="banner warn">${esc(f.note)}</div>
  <div class="card"><div class="grid g3">
    ${Object.entries(f.defaults).map(([k,val])=>`
      <div><div class="tiny dimmer">${esc(k.replace(/_/g,' '))}</div>
        <div class="mono">${typeof val==='number'&&val<1&&val>0?(val*100).toFixed(2)+'%':val}</div></div>`).join('')}
  </div></div>
  <h2>Open a property to run the numbers</h2>
  <p class="muted tiny">Every property has a Money tab with live sliders for the rental
    model, the maximum purchase price, a storage build-out sketch and a snow-cone site.</p>`;
};

VIEWS.portfolio = async (v)=>{
  const p = await api('/api/portfolio');
  const rehabs = await Promise.all(p.properties.map(x=>api(`/api/rehab/${x.property_id}`)));
  v.innerHTML = `<h1>My properties</h1>
  <p class="lede">${esc(p.note)}</p>
  <div class="grid g4" style="margin-bottom:16px">
    ${stat(p.count,'properties')}${stat(p.units,'units')}
    ${stat(money(p.monthly_rent).replace('$',''),'monthly rent','green')}
    ${stat(money(p.annual_rent).replace('$',''),'annual rent')}
    ${stat(money(p.portfolio_value).replace('$',''),'portfolio value','accent')}
    ${stat(money(p.debt).replace('$',''),'debt','red')}
    ${stat(money(p.equity).replace('$',''),'equity','gold')}
    ${stat(money(p.expenses_to_date).replace('$',''),'spent to date')}</div>
  ${p.properties.length?`<div class="grid g3">${p.properties.map((x,i)=>{
      const r = rehabs[i]; const budget = r.projects.reduce((s,pr)=>s+(pr.budget_total||pr.budget||0),0);
      const actual = r.projects.reduce((s,pr)=>s+(pr.actual_total||0),0);
      return `<div class="card"><div class="spread"><b>${esc(x.address||x.parcel_id)}</b>
        <span class="tag ${x.state==='RENTED'?'green':'yellow'}">${esc(x.state)}</span></div>
      <dl class="kv" style="margin-top:9px">
        ${kv('Purchased', money(x.purchase_price)+(x.purchase_date?' &middot; '+esc(x.purchase_date):''), true)}
        ${kv('Current value', money(x.current_value), true)}
        ${kv('Loan', money(x.loan_amount), true)}
        ${kv('Equity', money((x.current_value||0)-(x.loan_amount||0)), true)}
        ${kv('Rehab', budget||actual ? `${money(actual)} of ${money(budget)}` : 'none tracked', true)}</dl>
      <button class="btn sm primary" style="margin-top:10px" onclick="go('property',${x.property_id})">Open</button></div>`;}).join('')}</div>`
    :`<div class="card muted">Nothing owned yet. When you buy one, open it and fill in the
      <b>Own it</b> tab &mdash; renovation tracking, before/after photos and the money ledger
      all hang off that.</div>`}`;
};

VIEWS.sources = async (v)=>{
  const s = await api('/api/sources');
  const ex = await api('/api/exclusions');
  const byAccess = {automated:[], manual:[], blocked:[]};
  s.sources.forEach(x=>(byAccess[x.access]||byAccess.manual).push(x));
  v.innerHTML = `<div class="spread"><h1>Sources</h1>
    <button class="btn sm" onclick="checkSources(this)">Re-check all</button></div>
  <p class="lede">Whether the scanner is actually working, and exactly which parts of the
    job a machine is not allowed to do.</p>
  ${['automated','manual','blocked'].map(k=>byAccess[k].length?`
    <h2>${{automated:'Reading automatically',manual:'A human has to look',
          blocked:'Automation blocked by the operator'}[k]}</h2>
    <div class="grid g2">${byAccess[k].map(x=>`
      <div class="card">
        <div class="spread"><b>${esc(x.label)}</b>
          <span class="tag ${x.status==='ok'?'green':x.status==='manual'?'orange':
            x.status==='unavailable'?'red':''}">${esc(x.status)}</span></div>
        <div class="tiny dimmer mono" style="margin:4px 0 8px">${esc(x.name)} &middot; ${esc(x.kind)}</div>
        ${x.status_detail?`<div class="tiny">${esc(x.status_detail)}</div>`:''}
        ${x.why_manual?`<div class="tiny muted" style="margin-top:6px"><b>Why:</b> ${esc(x.why_manual)}</div>`:''}
        ${x.what_to_check?`<div class="tiny dimmer" style="margin-top:6px"><b>What to check:</b> ${esc(x.what_to_check)}</div>`:''}
        <div class="tiny dimmer" style="margin-top:8px">
          last success ${esc(ago(x.last_success))} &middot; last attempt ${esc(ago(x.last_attempt))}
          &middot; ${x.records_found} records</div>
        ${x.last_error?`<div class="tiny" style="color:#ff9b9b;margin-top:5px">${esc(x.last_error)}</div>`:''}
        ${x.url?`<a class="tiny" href="${esc(x.url)}" target="_blank" rel="noopener">open source &rarr;</a>`:''}
      </div>`).join('')}</div>`:'').join('')}
  <h2>Exclusion rules</h2>
  <div class="card">
    <p class="tiny muted">${ex.stats.total} properties have been excluded so far.
      Boundaries come from official US Census polygons, not from matching text.</p>
    <div class="row" style="margin-bottom:10px">${ex.boundaries.map(b=>
      `<span class="tag red">${esc(b.name)} &middot; ${b.points} boundary points</span>`).join('')}</div>
    <table class="ev"><tr><th>Area</th><th>Signal</th><th>Value</th><th>Active</th></tr>
      ${ex.rules.map(r=>`<tr><td>${esc(r.label)}</td><td>${esc(r.rule_kind)}</td>
        <td class="mono">${esc(r.rule_value||'polygon')}</td>
        <td>${r.active?'<span class="tag green">on</span>':'<span class="tag">off</span>'}</td></tr>`).join('')}
    </table></div>`;
};
window.checkSources = async (btn)=>{
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> checking';
  try{ await api('/api/sources/check',{method:'POST',body:'{}'}); render(); }
  finally{ btn.disabled=false; }
};

VIEWS.health = async (v)=>{
  const h = await api('/api/health');
  v.innerHTML = `<h1>System health</h1>
  <p class="lede">Overall: <span class="tag ${h.overall==='GREEN'?'green':h.overall==='YELLOW'?'yellow':'red'}">${esc(h.overall)}</span></p>
  <div class="grid g3">${h.checks.map(c=>`
    <div class="card"><div class="spread"><b>${esc(c.name)}</b>
      <span class="tag ${c.status==='GREEN'?'green':c.status==='YELLOW'?'yellow':'red'}">${esc(c.status)}</span></div>
      <div class="tiny muted" style="margin-top:6px">${esc(c.detail)}</div>
      <div class="tiny dimmer">${c.ms} ms</div></div>`).join('')}</div>`;
};

VIEWS.learn = async (v)=>{
  const g = await api('/api/education');
  v.innerHTML = `<h1>Glossary</h1>
  <p class="lede">Every term in plain English. Tap one.</p>
  <div class="row">${g.terms.map(t=>
    `<button class="btn sm ghost" onclick="explainTerm('${esc(t)}')">${esc(t)}</button>`).join('')}</div>
  <div id="termOut" style="margin-top:16px"></div>`;
};
window.explainTerm = async (t)=>{
  const r = await api(`/api/education?term=${encodeURIComponent(t)}`);
  $('#termOut').innerHTML = `<div class="card"><h3>${esc(r.term)}</h3>
    <p style="margin:0">${esc(r.text)}</p>
    ${r.example?`<p class="tiny muted" style="margin-top:8px"><b>Here:</b> ${esc(r.example)}</p>`:''}</div>`;
};

window.go = go;
window.addEventListener('hashchange', ()=>{
  const h = location.hash.replace('#','').split('/');
  if(h[0] && (h[0]!==S.view || h[1]!==S.arg)){ S.view=h[0]; S.arg=h[1]; renderNav(); render(); }
});
boot();
