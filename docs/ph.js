/* Property Hunter shared behaviour. Pure functions first (tested), DOM helpers after.
   Rules baked in: missing data is named, never zeroed; inference is never dressed as fact. */
(function (root) {
  const PH = root.PH || (root.PH = {});

  // ---------- formatting that refuses to lie ----------
  PH.esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  PH.money = v => (v === null || v === undefined || v === '' || isNaN(v)) ? null : '$' + Math.round(+v).toLocaleString('en-US');
  PH.moneyOr = (v, word) => PH.money(v) ?? (word || 'not on record');
  PH.title = s => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim().replace(/\b\w/g, c => c.toUpperCase());
  PH.owner = raw => { const t = PH.title(raw); if (!t) return { text: 'NOT FOUND on the roll', missing: true }; return { text: t, missing: false }; };
  PH.when = v => { if (!v) return ''; const d = new Date(String(v).replace(/(\d{2})(\d{2})$/, '$1:$2')); return isNaN(d) ? String(v).slice(0, 16) : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); };

  // ---------- the vocabulary ----------
  PH.LABEL = {
    vacant_structure: ["On the City's vacant-structure register", 'fact'],
    cleanup_lien: ['City cleanup / demolition lien on record', 'fact'],
    code_case_open: ['Open 2025 code-enforcement case', 'fact'],
    code_case_history: ['A 2025 code case, since closed', 'fact'],
    tax_delinquent: ['Certified to the State for unpaid taxes', 'fact'],
    absentee_owner: ['Tax bill goes outside the county', 'fact'],
    estate_owner: ['Owner reads as an estate or heirs', 'derived'],
    trust_owner: ['Owner is a trust', 'derived'],
    entity_owner: ['Owner is a company', 'derived'],
    institutional_owner: ['Owner reads as a bank or lender', 'derived'],
    government_owner: ['Owner reads as a government body', 'derived'],
    building_worth_less_than_dirt: ['Building valued well under the land', 'derived'],
    low_improvement_value: ['Very low building value on the roll', 'derived'],
    stale_assessment: ['Roll record not updated in years', 'derived'],
    vacant_land: ['No building value on the roll', 'derived'],
    vacant_per_lien_record: ["Marked vacant on the City's lien record", 'fact'],
    opportunity_zone: ['Inside a federal Opportunity Zone', 'fact'],
    owner_occupied: ['Tax bill goes to the property', 'derived'],
    unknown_owner: ['No owner name on the roll', 'unknown'],
    flood_zone: ['FEMA maps a flood zone here', 'fact'],
    septic_likely: ['No City sewer main nearby', 'derived'],
    tiny_lot: ['Very small parcel', 'fact'],
    no_address: ['No situs address on the roll', 'unknown'],
    useful_acreage: ['Usable acreage', 'fact'],
    city_water: ['City water meter at the address', 'fact'],
    overlay_district: ['Inside an overlay district', 'fact'],
    record_says_building_map_says_none: ['Roll says improved, footprints show none', 'derived'],
  };
  PH.RISK_KEYS = ['flood_zone', 'tiny_lot', 'no_address', 'septic_likely', 'overlay_district', 'unknown_owner', 'record_says_building_map_says_none', 'government_owner'];

  // ---------- score categories (presentation over the existing score; tests only) ----------
  // Categories are derived from the score lines and signals the scanner already produced.
  PH.priority = r => {
    const lines = r.lines || [], d = r.d || [];
    const sum = (re) => lines.filter(l => re.test(l.r || '')).reduce((s, l) => s + Math.max(0, l.p || 0), 0);
    const cats = [
      ['Tax status', (PH.taxState(r).distress ? 16 : 0) + (r.lien > 0 ? 6 : 0), false],
      ['Vacancy signal', sum(/vacant|code/i) + (r.vac ? 8 : 0), false],
      ['Property value', sum(/valued|value|appraised|dirt/i), false],
      ['Land potential', Math.round((r.land || 0) / 5), false],
      ['Market signal', Math.round((r.rent || 0) / 8), false],
      ['Acquisition access', sum(/estate|heirs|trust|company|absentee|lender|bank/i) + (r.ab ? 6 : 0), false],
      ['Risk', Math.round((r.r || 0) / 4) + d.filter(k => PH.RISK_KEYS.includes(k)).length * 4, true],
    ];
    const max = Math.max(20, ...cats.map(c => c[1]));
    return { score: r.s, confidence: r.conf || null, cats: cats.map(([n, v, risk]) => ({ name: n, value: v, pct: Math.round(100 * v / max), risk })) };
  };

  // ---------- WHY THIS ONE? real steps, each labelled ----------
  PH.whySteps = r => {
    const d = r.d || [], steps = [];
    const st = String(r.ts || '');
    const tx = PH.taxState(r);
    steps.push(tx.state === 'TAX_SALE_VERIFIED' ? { k: 'fact', t: 'State tax sale found', s: 'The Commissioner of State Lands lists this parcel for sale for unpaid taxes.' }
      : tx.state === 'DELINQUENT_VERIFIED' ? { k: 'fact', t: 'Delinquent at the county (verified)', s: tx.text }
      : tx.state === 'CURRENT_BILL_OPEN' ? { k: 'fact', t: 'Current-year county bill open (not delinquent)', s: tx.text }
      : tx.state === 'CURRENT_VERIFIED' ? { k: 'fact', t: 'No open Collector bill when checked', s: tx.text }
      : { k: 'unknown', t: 'Tax status not established', s: tx.text + tx.note });
    steps.push(r.pid ? { k: 'fact', t: 'Parcel matched', s: `County parcel ${r.pid} on the State roll.` } : { k: 'unknown', t: 'Parcel not matched', s: 'No parcel id could be tied to this record.' });
    steps.push(r.tv ? { k: 'fact', t: 'Value read from the roll', s: `County appraised ${PH.money(r.tv)}${r.iv > 0 ? `, building ${PH.money(r.iv)}` : ', no building value'}. An assessor's figure, not a sale price.` } : { k: 'unknown', t: 'No value on the roll', s: 'The roll carries no appraised value for this parcel.' });
    if (r.vac) steps.push({ k: 'fact', t: 'Vacancy signal found', s: "On the City of Hot Springs vacant-structure register." });
    else if (d.includes('vacant_per_lien_record')) steps.push({ k: 'fact', t: 'Vacancy signal found', s: "Marked vacant on the City's lien record." });
    else if (d.includes('low_improvement_value') || d.includes('building_worth_less_than_dirt')) steps.push({ k: 'derived', t: 'Condition inferred from the roll', s: 'The building carries a very low value; that often goes with neglect. Not verified in person.' });
    else steps.push({ k: 'unknown', t: 'No vacancy record', s: 'Nothing on the City registers; condition unknown.' });
    if (r.lien > 0) steps.push({ k: 'fact', t: 'City lien on record', s: `${PH.money(r.lien)} in cleanup or demolition liens.` });
    const owner = d.find(k => ['estate_owner', 'trust_owner', 'entity_owner', 'institutional_owner', 'absentee_owner'].includes(k));
    if (owner) steps.push({ k: owner === 'absentee_owner' ? 'fact' : 'derived', t: 'Owner situation noted', s: PH.LABEL[owner][0] + (owner === 'absentee_owner' ? '.' : ' (read from the name; confirm).') });
    steps.push({ k: 'fact', t: 'Location eligibility checked', s: 'Not inside Hot Springs Village or Diamondhead (Census boundary).' });
    steps.push(r.f ? { k: 'fact', t: 'Flood zone checked', s: `FEMA zone ${r.f}${r.f === 'AE' ? ' · special flood hazard area' : ''}.` } : { k: 'unknown', t: 'Flood not checked', s: 'FEMA was not asked for this parcel yet.' });
    const risks = d.filter(k => PH.RISK_KEYS.includes(k));
    steps.push(risks.length ? { k: 'risk', t: `${risks.length} risk flag${risks.length > 1 ? 's' : ''}`, s: risks.map(k => (PH.LABEL[k] || [k])[0]).join(' · ') } : { k: 'derived', t: 'No risk flags raised', s: 'From the records read so far. Title, condition and taxes remain to be checked.' });
    const sg = PH.signals(r);
    steps.push({ k: 'derived', t: `Signal stack: ${sg.all.length} public-record signal${sg.all.length === 1 ? '' : 's'} (${sg.verified.length} verified, ${sg.derived.length} derived)`, s: `Weighted score ${Math.round(r.s || 0)}. A count of what the records say, not a valuation, not a probability of sale or profit, not advice to buy.` });
    steps.push({ k: PH.saleStatus(r).kind === 'fact' ? 'fact' : 'unknown', t: 'Sale status: ' + PH.saleStatus(r).state.replace(/_/g, ' '), s: PH.saleStatus(r).text });
    return steps;
  };

  // ---------- TAX STATE: one honest state per parcel, with source and date ----------
  // Facts only. A missing check is UNKNOWN; a source being down is SOURCE_UNAVAILABLE; a dollar
  // amount never appears without the state word beside it. The State's sale list and the county
  // Collector are two different sources and are never merged into one sentence.
  PH.TAX_STATES = {
    TAX_SALE_VERIFIED:   { word: 'TAX SALE',           tone: 'bad',     kind: 'fact' },
    DELINQUENT_VERIFIED: { word: 'DELINQUENT',         tone: 'bad',     kind: 'fact' },
    CURRENT_BILL_OPEN:   { word: 'CURRENT BILL OPEN',  tone: 'neutral', kind: 'fact' },
    CURRENT_VERIFIED:    { word: 'CURRENT',            tone: 'good',    kind: 'fact' },
    STALE:               { word: 'STALE',              tone: 'muted',   kind: 'derived' },
    SOURCE_UNAVAILABLE:  { word: 'SOURCE UNAVAILABLE', tone: 'muted',   kind: 'unknown' },
    UNKNOWN:             { word: 'UNKNOWN',            tone: 'muted',   kind: 'unknown' },
  };
  PH.taxState = (r, cp) => {
    const t = (r && r.taxs) || null;
    const ts = String((r && r.ts) || '');
    let st = t ? t.st : (ts.startsWith('CERTIFIED') ? 'TAX_SALE_VERIFIED' : 'UNKNOWN');
    let src = t && t.src, asOf = t && t.as_of, amt = t && t.amt, was = t && t.was, days = t && t.days;
    if (st === 'UNKNOWN' && cp && cp.open === false) { st = 'SOURCE_UNAVAILABLE'; src = 'County Collector (CountyPay)'; asOf = (cp.down_since || '').slice(0, 10); }
    const meta = PH.TAX_STATES[st] || PH.TAX_STATES.UNKNOWN;
    const money = amt != null && !isNaN(amt) ? '$' + Number(amt).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : null;
    const dated = asOf ? ` [${asOf}]` : '';
    let text;
    switch (st) {
      case 'TAX_SALE_VERIFIED': text = `TAX SALE — State certified${money ? ', ' + money + ' owed' : ''}${dated}`; break;
      case 'DELINQUENT_VERIFIED': text = `DELINQUENT — ${src || 'county record'} verified${money ? ', ' + money : ''}${dated}`; break;
      case 'CURRENT_BILL_OPEN': text = `CURRENT BILL OPEN — ${money || 'amount on record'} — not delinquent${dated}`; break;
      case 'CURRENT_VERIFIED': text = `CURRENT — no open Collector bill${dated}`; break;
      case 'STALE': text = `STALE — last Collector check ${asOf || 'undated'} said ${(PH.TAX_STATES[was] || {}).word || was || 'unknown'}${days ? ' · ' + days + ' days ago' : ''}`; break;
      case 'SOURCE_UNAVAILABLE': text = `SOURCE UNAVAILABLE — Collector search down since ${asOf || 'an unknown date'}`; break;
      default: text = 'UNKNOWN — never checked at the Collector';
    }
    const stateNote = (t && t.cosl_check) ? ` · not held by the State Lands as of ${t.cosl_check} (says nothing about the county bill)` : (st !== 'TAX_SALE_VERIFIED' ? ' · not on the State sale list' : '');
    return { state: st, word: meta.word, text, note: stateNote, kind: meta.kind, tone: meta.tone, source: src || null, asOf: asOf || null, amount: amt ?? null,
             distress: st === 'TAX_SALE_VERIFIED' || st === 'DELINQUENT_VERIFIED' };
  };
  // legacy shape used by older cards: text/kind/tone. CURRENT_BILL_OPEN is neutral, never 'bad'.
  PH.taxLine = (r, cp) => { const t = PH.taxState(r, cp); return { text: t.text + (t.state === 'UNKNOWN' || t.state === 'SOURCE_UNAVAILABLE' ? t.note : ''), kind: t.kind, tone: t.tone === 'neutral' ? 'muted' : t.tone, state: t.state }; };

  // ---------- SALE STATUS: never inferred from silence ----------
  PH.saleStatus = r => {
    const s = (r && r.sale) || {};
    if (s.st === 'FOR_SALE' && s.src) return { state: 'FOR_SALE', text: `FOR SALE — ${s.src}${s.price ? ', asking ' + PH.money(s.price) : ''}`, source: s.src, kind: 'fact' };
    if (s.st === 'NOT_FOR_SALE' && s.src) return { state: 'NOT_FOR_SALE', text: `NOT FOR SALE — ${s.src}`, source: s.src, kind: 'fact' };
    const certified = r && r.taxs ? r.taxs.st === 'TAX_SALE_VERIFIED' : String((r && r.ts) || '').startsWith('CERTIFIED');   // the tax model already knows when a certification ended
    if (certified) return { state: 'FOR_SALE_BY_STATE', text: 'FOR SALE BY THE STATE — tax sale (Commissioner of State Lands); not a private listing', source: 'Commissioner of State Lands', kind: 'fact' };
    return { state: 'UNKNOWN', text: 'UNKNOWN — no listing source connected. Property Hunter is not saying this is for sale or not for sale.', source: null, kind: 'unknown' };
  };
  PH.searchListingsUrl = r => { const q = [r && r.a, r && r.c, 'Arkansas'].filter(Boolean).join(' '); return 'https://www.google.com/search?q=' + encodeURIComponent(q + ' listing'); };

  // ---------- SIGNALS: what the records actually say, counted honestly ----------
  PH.signals = r => {
    const d = (r && r.d) || [], out = [];
    const add = (key, text, kind) => { if (!out.some(x => x.key === key)) out.push({ key, text, kind }); };
    if (String((r && r.ts) || '').startsWith('CERTIFIED')) add('tax_delinquent', PH.LABEL.tax_delinquent[0], 'fact');
    const tx = PH.taxState(r); if (tx.state === 'DELINQUENT_VERIFIED') add('tax_delinquent_county', 'Delinquent at the county (verified)', 'fact');
    if (r && r.vac) add('vacant_structure', PH.LABEL.vacant_structure[0], 'fact');
    if (r && r.lien > 0) add('cleanup_lien', `${PH.money(r.lien)} City cleanup / demolition lien on record`, 'fact');
    if (r && r.cc) add('code_case_open', PH.LABEL.code_case_open[0], 'fact');
    if (r && r.ab) add('absentee_owner', PH.LABEL.absentee_owner[0], 'fact');
    for (const k of d) { if (k === 'tax_delinquent' && tx.state !== 'TAX_SALE_VERIFIED') continue; const L = PH.LABEL[k]; if (L && !PH.RISK_KEYS.includes(k)) add(k, L[0], L[1]); }
    const verified = out.filter(x => x.kind === 'fact'), derived = out.filter(x => x.kind === 'derived');
    const strongest = verified[0] || derived[0] || null;
    return { all: out, verified, derived, risks: d.filter(k => PH.RISK_KEYS.includes(k)).map(k => (PH.LABEL[k] || [k])[0]), strongest };
  };

  // ---------- NEXT ACTION: one verb, one existing destination ----------
  PH.nextAction = (r, cp) => {
    const tx = PH.taxState(r, cp), sg = PH.signals(r);
    const file = `lookup.html?county=${encodeURIComponent(r.cf || '05051')}&q=${encodeURIComponent(r.pid || r.a || '')}`;
    if (tx.state === 'TAX_SALE_VERIFIED') return { label: 'Open sale file', href: `state-lands.html?county=${encodeURIComponent(String(r.cn || 'GARLAND').toUpperCase())}&q=${encodeURIComponent(r.pid || r.a || '')}` };
    if (r.vac) return { label: 'Drive by', href: r.lat ? `https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lon}` : file };
    if (r.lien > 0) return { label: 'Investigate lien', href: file };
    if (r.cc) return { label: 'Review case', href: file };
    if (tx.state === 'DELINQUENT_VERIFIED') return { label: 'Inspect delinquent record', href: file };
    if (tx.state === 'UNKNOWN' || tx.state === 'SOURCE_UNAVAILABLE' || tx.state === 'STALE') return { label: 'Check Collector / request county list', href: 'request.html' };
    if (sg.verified.length === 0) return { label: 'Search public listings', href: PH.searchListingsUrl(r) };
    return { label: 'Open file', href: file };
  };

  // ---------- WHY THIS PROPERTY? one reusable block ----------
  PH.whyRow = (r, cp, opts) => {
    const o = opts || {}, sg = PH.signals(r), tx = PH.taxState(r, cp), sale = PH.saleStatus(r), nx = PH.nextAction(r, cp);
    const reason = sg.verified.length ? `${sg.verified.length} verified public-record signal${sg.verified.length > 1 ? 's' : ''}${sg.derived.length ? ` · ${sg.derived.length} derived` : ''}`
      : sg.derived.length ? `Here because of public-record patterns only; no verified opportunity signal found (${sg.derived.length} derived)`
      : 'Here because of public-record patterns only; no verified opportunity signal found';
    const strongest = sg.strongest ? `<div class="wr-line"><span>Strongest</span><b>${PH.esc(sg.strongest.text)}</b> ${PH.pill(sg.strongest.kind === 'fact' ? 'fact' : 'derived', sg.strongest.kind === 'fact' ? 'verified' : 'derived')}</div>` : '';
    return `<div class="whyrow ${o.compact ? 'compact' : ''}">
      <div class="wr-line wr-head"><span>Why</span><b>${PH.esc(reason)}</b></div>${o.compact ? '' : strongest}
      <div class="wr-line"><span>Sale</span><b class="${sale.kind}">${PH.esc(sale.text)}</b>${sale.state === 'UNKNOWN' ? ` <a href="${PH.esc(PH.searchListingsUrl(r))}" target="_blank" rel="noopener">Search public listings</a>` : ''}</div>
      <div class="wr-line"><span>Taxes</span><b class="tx-${tx.tone}">${PH.esc(tx.text)}</b>${o.compact ? '' : `<small>${PH.esc(tx.note)}</small>`}</div>
      <div class="wr-line"><span>Next</span><a class="wr-next" href="${PH.esc(nx.href)}"${/^https?:/.test(nx.href) ? ' target="_blank" rel="noopener"' : ''}>${PH.esc(nx.label)} →</a></div>
    </div>`;
  };

  // ---------- SIGNAL STACK: the score, presented as what it is ----------
  PH.stack = (r, countyMax) => {
    const sg = PH.signals(r);
    return { score: r && r.s != null ? Math.round(r.s) : null, n: sg.all.length, verified: sg.verified.length, derived: sg.derived.length,
             countyMax: countyMax != null ? Math.round(countyMax) : null, signals: sg.all, risks: sg.risks, confidence: (r && r.conf) || null };
  };
  PH.stackHtml = (r, countyMax) => {
    if (r == null || r.s == null || isNaN(+r.s)) return '<div class="empty">Not scored yet. The scanner has not read enough records about this parcel to rank it; that is not a low score.</div>';
    const s = PH.stack(r, countyMax);
    return `<div class="rp"><b>${s.n}</b><span>public-record signal${s.n === 1 ? '' : 's'} · ${s.verified} verified · ${s.derived} derived${s.countyMax != null ? ` · county maximum score ${s.countyMax}` : ''}</span></div>
      <ul class="stack">${s.signals.map(x => `<li>${PH.pill(x.kind === 'fact' ? 'fact' : 'derived', x.kind === 'fact' ? 'verified' : 'derived')}<span>${PH.esc(x.text)}</span></li>`).join('') || '<li class="none">No public-record signals; here because of roll patterns only.</li>'}${s.risks.map(x => `<li>${PH.pill('risk', 'risk')}<span>${PH.esc(x)}</span></li>`).join('')}</ul>
      <div class="rp-note">Signal stack score ${s.score}${s.confidence ? ' · ' + PH.esc(String(s.confidence).toLowerCase()) + ' confidence' : ''}: a count of public-record signals the scanner could read, weighted. It is not a probability of profit, of sale, or of value; a high number means many records, not a good deal.</div>`;
  };
  PH.priorityHtml = (r, countyMax) => PH.stackHtml(r, countyMax);

  // ---------- DISCOVERY FEED: shared vocabulary, filters and cards ----------
  PH.EVENT = {
    NEW_TAX_SALE: ['New State tax-sale listing', 'state'], STATE_SOLD: ['State sold', 'state'], STATE_REDEEMED: ['State redeemed', 'state'], STATE_LEFT: ['State inventory exit', 'state'],
    NEW_VACANCY_RECORD: ['New vacancy record', 'city'], NEW_LIEN: ['New lien', 'city'], NEW_CODE_CASE: ['New code case', 'city'], VERIFIED_DELINQUENCY: ['Verified delinquency', 'county'],
  };
  PH.CLASS = { WORLD_EVENT: 'REAL-WORLD EVENT', FIRST_DISCOVERY: 'FIRST DISCOVERY', INFORMATIONAL: 'INFORMATIONAL' };
  PH.CLASS_NOTE = {
    WORLD_EVENT: 'the public record itself changed inside the window',
    FIRST_DISCOVERY: 'Property Hunter read this record for the first time inside the window; the record may be older',
    INFORMATIONAL: 'a reading changed; not an opportunity by itself',
  };
  // pure filter over feed rows; every key optional. Used by discover.html and tests.
  PH.filterSignals = (rows, f) => {
    f = f || {};
    return (rows || []).filter(r => {
      if (f.county && r.cf !== f.county && String(r.cn || '').toLowerCase() !== String(f.county).toLowerCase()) return false;
      if (f.event && r.event !== f.event) return false;
      if (f.cls && r.cls !== f.cls) return false;
      if (f.kind && r.kind !== f.kind) return false;
      if (f.tax && PH.taxState(r).state !== f.tax) return false;
      if (f.sale && PH.saleStatus(r).state !== f.sale) return false;
      if (f.since && (r.date || '') < f.since) return false;
      if (f.until && (r.date || '') > f.until) return false;
      return true;
    });
  };
  PH.sigCard = (x, cp) => {
    const [label, fam] = PH.EVENT[x.event] || [x.event, ''];
    const tx = PH.taxState(x, cp), sale = PH.saleStatus(x);
    const ext = /^https?:/.test(x.next.href);
    return `<article class="sig sig-${fam}" data-id="${PH.esc(x.id)}">
      <span class="ev">${PH.esc(label)}<small>${PH.esc(PH.CLASS[x.cls] || x.cls)}</small></span>
      <span>
        <span class="a">${PH.esc(x.a || 'No situs address')}</span> · ${PH.esc(x.cn || '')} County · parcel <span class="mono">${PH.esc(x.pid || '?')}</span>
        <div>${PH.esc(x.label)}. ${PH.esc(x.why)}</div>
        <div class="meta">event ${PH.esc(x.date)} · read ${PH.esc((x.discovered_at || '').replace('T', ' '))} · source: ${x.src_url ? `<a href="${PH.esc(x.src_url)}" target="_blank" rel="noopener">${PH.esc(x.src)}</a>` : PH.esc(x.src)} · ${PH.esc(x.status)}${x.evidence_ref ? ' · ref ' + PH.esc(x.evidence_ref) : ''}</div>
        <div class="meta"><b>Taxes:</b> <span class="tx-${tx.tone}">${PH.esc(tx.text)}</span> · <b>Sale:</b> ${PH.esc(sale.text.split('.')[0])}${x.tv ? ` · appraised ${PH.money(x.tv)}` : ''}</div>
      </span>
      <span class="acts"><a class="next" href="${PH.esc(x.next.href)}"${ext ? ' target="_blank" rel="noopener"' : ''}>${PH.esc(x.next.label)} →</a>${PH.investigateBtn(x, x)}<a class="file" href="lookup.html?county=${PH.esc(x.cf)}&q=${encodeURIComponent(x.pid || x.a || '')}">Open property file</a></span>
    </article>`;
  };
  // ---------- EVIDENCE TIMELINE ----------
  PH.timelineHtml = (events, cp) => {
    const ev = (events || []).slice().sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    if (cp && cp.open === false) ev.push({ date: (cp.checked_at || '').slice(0, 16), cls: 'SOURCE_CHECK', title: 'Collector source unavailable', detail: `CountyPay has said "${cp.detail || 'unavailable'}" since ${(cp.down_since || '').slice(0, 10)}; this parcel was not checked`, src: 'docs/data/status.json (poller)', ref: 'status:countypay' });
    if (!ev.length) return '<div class="empty">No dated evidence beyond the county roll for this parcel.</div>';
    const L = { WORLD_EVENT: 'WORLD EVENT', FIRST_DISCOVERY: 'FIRST DISCOVERED BY PROPERTY HUNTER', INFORMATIONAL: 'INFORMATIONAL RECORD CHANGE', SOURCE_CHECK: 'SOURCE CHECK', MANUAL: 'MANUAL VERIFICATION' };
    return `<ol class="tl">${ev.map(e => `<li class="${PH.esc(e.cls)}"><time>${PH.esc((e.date || '').replace('T', ' '))}</time><b>${PH.esc(L[e.cls] || e.cls)}</b><span>${PH.esc(e.title)}${e.detail ? ` — ${PH.esc(e.detail)}` : ''}<small>source: ${e.url ? `<a href="${PH.esc(e.url)}" target="_blank" rel="noopener">${PH.esc(e.src || '')}</a>` : PH.esc(e.src || '')}${e.ref ? ' · ' + PH.esc(e.ref) : ''}</small></span></li>`).join('')}</ol>`;
  };
  // ---------- WHAT WE KNOW / DON'T KNOW / NEXT ----------
  PH.knowBlock = (r, cp, tl) => {
    const sg = PH.signals(r), tx = PH.taxState(r, cp), sale = PH.saleStatus(r), nx = PH.nextAction(r, cp);
    const dated = (tl || []).filter(e => e.cls === 'WORLD_EVENT');
    const know = [];
    if (r.o) know.push(`Owner of record on the county roll: ${PH.title(r.o)} (not a title opinion)`);
    if (r.tv != null) know.push(`County appraised ${PH.money(r.tv)}${r.iv > 0 ? `, building ${PH.money(r.iv)}` : r.iv === 0 ? ', no building value' : ''} (assessor's figure, not a price)`);
    for (const x of sg.verified) know.push(x.text + ' (verified)');
    for (const x of sg.derived) know.push(x.text + ' (derived from the roll; not verified)');
    if (tx.kind === 'fact') know.push('Taxes: ' + tx.text);
    if (sale.kind === 'fact') know.push('Sale: ' + sale.text);
    const dont = [];
    if (tx.kind !== 'fact') dont.push('Taxes: ' + tx.text + tx.note);
    if (sale.kind !== 'fact') dont.push('Sale: ' + sale.text);
    dont.push('Title, liens and judgments at the Circuit Clerk: not read by this site');
    if (!r.vac && !sg.verified.some(x => x.key === 'vacant_structure')) dont.push('Occupancy and physical condition: no register record; aerials are dated');
    if (!r.f) dont.push('Flood zone: FEMA not asked for this parcel yet');
    const next = [nx.label];
    if (tx.kind !== 'fact' && nx.label !== 'Check Collector / request county list') next.push('Check Collector / request county list');
    if (sale.state === 'UNKNOWN' && nx.label !== 'Search public listings') next.push('Search public listings');
    next.push('Work the "Before you buy" checklist below; write the owner only after taxes and title are checked');
    const li = xs => xs.map(t => `<li>${PH.esc(t)}</li>`).join('');
    return `<div class="know"><div><h4>What we know</h4><ul>${li(know) || '<li>Only the county roll reading.</li>'}</ul></div>
      <div><h4>What we don't know</h4><ul>${li(dont)}</ul></div>
      <div><h4>Next steps</h4><ol>${li(next)}</ol>${dated.length ? `<small>Newest dated event ${PH.esc(dated[dated.length - 1].date)} · oldest ${PH.esc(dated[0].date)} · ${PH.esc(String((tl || []).length))} evidence items</small>` : ''}</div></div>`;
  };

  // ---------- P2: INVESTIGATION CASES (durable research objects in the local app) ----------
  PH.API = 'http://127.0.0.1:8234';
  PH.CASES = null;                       // {by_property:{pid:{id,status,updated_at,unknown,signals}}, by_parcel:{'cf:pid':id}, live:bool}
  PH.CASE_STATUS = { OPEN: 'OPEN', RESEARCHING: 'RESEARCHING', WAITING_ON_SOURCE: 'WAITING ON SOURCE', READY_FOR_REVIEW: 'READY FOR REVIEW', CLOSED: 'CLOSED' };
  PH.apiFetch = async (path, opts, ms) => {
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), ms || 1500) : null;
    try { return await fetch(PH.API + path, Object.assign({ signal: ctl && ctl.signal }, opts || {})); }
    finally { if (t) clearTimeout(t); }
  };
  PH.loadCaseIndex = async () => {
    if (PH.CASES) return PH.CASES;
    try { const r = await PH.apiFetch('/api/cases/index'); if (r.ok) { PH.CASES = Object.assign(await r.json(), { live: true }); return PH.CASES; } } catch (e) {}
    try { const r = await fetch('data/investigations.json', { cache: 'no-store' }); if (r.ok) { const d = await r.json(); PH.CASES = { by_property: d.by_property || {}, by_parcel: d.by_parcel || {}, built_at: d.built_at, live: false }; return PH.CASES; } } catch (e) {}
    PH.CASES = { by_property: {}, by_parcel: {}, live: false, unavailable: true };
    return PH.CASES;
  };
  // the case for a scan/signal row (r.i or r.id = property id; cf:pid fallback for watch rows)
  PH.caseFor = (r, idx) => {
    const c = idx || PH.CASES; if (!c || !r) return null;
    const pid = r.i != null ? r.i : (r.property_id != null ? r.property_id : r.id);
    if (pid != null && c.by_property && c.by_property[String(pid)]) return c.by_property[String(pid)];
    const key = `${r.cf || r.fips || ''}:${r.pid || r.parcel_id || ''}`;
    const id = c.by_parcel && c.by_parcel[key];
    if (id) { const hit = Object.values(c.by_property || {}).find(x => x.id === id); return hit || { id }; }
    return null;
  };
  // INVESTIGATE PROPERTY opens or creates the one case; OPEN INVESTIGATION when it already exists. Never two.
  PH.investigateHref = (r, sig, idx) => {
    const ex = PH.caseFor(r, idx);
    if (ex) return `investigation.html?id=${ex.id}`;
    const pid = r.i != null ? r.i : (r.property_id != null ? r.property_id : r.id);
    if (pid == null) return null;
    const s = sig && sig.event ? `&sig=${encodeURIComponent(sig.event + '|' + (sig.evidence_ref || ''))}` : '';
    return `investigation.html?property=${encodeURIComponent(pid)}${s}`;
  };
  PH.investigateBtn = (r, sig, idx) => {
    const ex = PH.caseFor(r, idx), href = PH.investigateHref(r, sig, idx);
    if (!href) return '';
    return ex ? `<a class="btn ghost inv is-on" href="${PH.esc(href)}">OPEN INVESTIGATION · ${PH.esc(PH.CASE_STATUS[ex.status] || ex.status)}${ex.unknown != null ? ` · ${ex.unknown} unknown` : ''}</a>`
              : `<a class="btn ghost inv" href="${PH.esc(href)}">INVESTIGATE PROPERTY</a>`;
  };
  PH.activeCaseHtml = (r, idx) => {
    const ex = PH.caseFor(r, idx); if (!ex) return '';
    return `<div class="active-inv"><b>ACTIVE INVESTIGATION</b> · ${PH.esc(PH.CASE_STATUS[ex.status] || ex.status)}${ex.updated_at ? ` · updated ${PH.esc(String(ex.updated_at).slice(0, 16).replace('T', ' '))}` : ''}${ex.unknown != null ? ` · ${ex.unknown} question${ex.unknown === 1 ? '' : 's'} unknown` : ''} · <a href="investigation.html?id=${ex.id}">Open investigation</a></div>`;
  };

  // ---------- DOM ----------
  PH.pill = (kind, text) => `<span class="pill ${kind}">${PH.esc(text)}</span>`;
  PH.means = (yes, no) => `<div class="means"><div class="yes"><b>What this establishes</b><ul>${yes.map(x => `<li>${PH.esc(x)}</li>`).join('')}</ul></div><div class="no"><b>What it does not</b><ul>${no.map(x => `<li>${PH.esc(x)}</li>`).join('')}</ul></div></div>`;
  PH.chain = ({ fact, source, date, interp, next, notEstablish }) => `<dl class="chain">
    <dt class="step">Fact</dt><dd class="step">${PH.esc(fact)}</dd>
    <dt class="step">Source</dt><dd class="step">${PH.esc(source)}</dd>
    <dt class="step">Date</dt><dd class="step">${PH.esc(date || 'not stated')}</dd>
    <dt class="step">Interpretation</dt><dd class="step">${PH.esc(interp)}</dd>
    <dt class="step">Next check</dt><dd class="step">${PH.esc(next)}</dd>
    ${notEstablish ? `<dt class="step">Does not establish</dt><dd class="step not">${PH.esc(notEstablish)}</dd>` : ''}
  </dl>`;
  PH.whyOpen = (r, label) => {
    const steps = PH.whySteps(r);
    const wrap = document.createElement('div'); wrap.className = 'why-scrim'; wrap.setAttribute('role', 'dialog'); wrap.setAttribute('aria-label', 'Why this property surfaced');
    wrap.innerHTML = `<div class="why"><header><div><h2>Why this one?</h2><div class="sub">${PH.esc(label || r.a || r.pid || '')} · each step says whether it is verified, derived, estimated or unknown</div></div><button class="x" aria-label="Close">×</button></header>
      <ol>${steps.map((s, i) => `<li class="${s.k}" style="animation-delay:${i * 90}ms"><span class="n">${s.k === 'fact' ? '✓' : s.k === 'unknown' ? '?' : s.k === 'risk' ? '!' : '≈'}</span><div><b>${PH.esc(s.t)} ${PH.pill(s.k === 'risk' ? 'risk' : s.k, s.k === 'fact' ? 'verified' : s.k)}</b><span>${PH.esc(s.s)}</span></div></li>`).join('')}</ol>
      <div class="foot">Verified = read from a named public record. Derived = a rule applied to records. Unknown = no record read yet. Nothing here is an appraisal, a title opinion, or advice to buy.</div></div>`;
    const close = () => { wrap.remove(); document.body.style.overflow = ''; };
    wrap.addEventListener('click', e => { if (e.target === wrap) close(); });
    wrap.querySelector('.x').addEventListener('click', close);
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });
    document.body.appendChild(wrap); document.body.style.overflow = 'hidden'; wrap.querySelector('.x').focus();
  };
  PH.statusBlock = (k, v, text, tone) => `<div class="status-block ${tone || ''}" role="status"><span class="k">${PH.esc(k)}</span><span class="v">${PH.esc(v)}</span>${PH.esc(text)}</div>`;
  PH.countyGrid = counties => `<div class="cty">${counties.map(c => `<div class="${c.status}" title="${PH.esc(c.county)} · ${c.status}"><span>${PH.esc(c.county)}</span><i>${c.status === 'done' ? (c.strong || 0) + ' strong' : c.status}</i></div>`).join('')}</div>`;

  // ---------- reduced motion ----------
  PH.reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
})(typeof window !== 'undefined' ? window : globalThis);
if (typeof module !== 'undefined') module.exports = globalThis.PH;
