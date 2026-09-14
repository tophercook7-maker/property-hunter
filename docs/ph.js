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

  // ---------- taxes: three honest states ----------
  PH.taxLine = r => {
    const ts = String(r.ts || '');
    if (r.tax && /owed to the County Collector/i.test(r.tax)) return { text: r.tax.replace(/ \(.*$/, ''), kind: 'fact', tone: 'bad' };
    if (r.tax && /delinquent at the county/i.test(r.tax)) return { text: r.tax.slice(0, 80), kind: 'fact', tone: 'bad' };
    if (ts.startsWith('CERTIFIED')) return { text: 'Certified to the State for unpaid taxes, for sale', kind: 'fact', tone: 'bad' };
    if (r.tax && /no open/i.test(r.tax)) return { text: 'No open bill at the Collector when last checked', kind: 'fact', tone: 'good' };
    return { text: 'County bill NOT CHECKED yet · not in the State sale list', kind: 'unknown', tone: 'muted' };
  };

  // ---------- research priority: presentation over the existing score ----------
  // Categories are derived from the score lines and signals the scanner already produced.
  PH.priority = r => {
    const lines = r.lines || [], d = r.d || [];
    const sum = (re) => lines.filter(l => re.test(l.r || '')).reduce((s, l) => s + Math.max(0, l.p || 0), 0);
    const cats = [
      ['Tax distress', sum(/tax|State|lien/i) + (d.includes('tax_delinquent') ? 10 : 0) + (r.lien > 0 ? 6 : 0), false],
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
    steps.push(st.startsWith('CERTIFIED') ? { k: 'fact', t: 'State tax delinquency found', s: 'The Commissioner of State Lands lists this parcel for sale for unpaid taxes.' }
      : r.tax && /owed|delinquent/i.test(r.tax) ? { k: 'fact', t: 'County tax bill open', s: r.tax }
      : { k: 'unknown', t: 'Tax status not established', s: 'Not in the State sale list; the county bill is ' + (r.tax && /no open/i.test(r.tax) ? 'clear when last checked.' : 'not checked yet.') });
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
    steps.push({ k: 'derived', t: `Research priority ${Math.round(r.s || 0)}`, s: 'A ranking of what to look at first. Not a valuation, not a recommendation to buy.' });
    return steps;
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
  PH.priorityHtml = r => {
    if (r == null || r.s == null || isNaN(+r.s)) return '<div class="empty">Not scored yet. The scanner has not read enough records about this parcel to rank it; that is not a low score.</div>';
    const p = PH.priority(r);
    return `<div class="rp"><b>${Math.round(p.score || 0)}</b><span>research priority${p.confidence ? ' · ' + PH.esc(p.confidence.toLowerCase()) + ' confidence' : ''}</span></div>
      <div class="rp-bars">${p.cats.map(c => `<div class="rp-bar ${c.risk ? 'risk' : ''}" role="img" aria-label="${PH.esc(c.name)} ${c.risk ? 'risk' : 'signal'} ${c.pct} of 100"><span>${c.risk ? '⚠ ' : '▲ '}${PH.esc(c.name)}</span><span class="track"><span class="fill" style="width:${c.pct}%"></span></span><span class="v">${c.value}</span></div>`).join('')}</div>
      <div class="rp-note">Research-priority score from public records the scanner could read. It is not an appraisal, an investment recommendation, a title opinion, or a guarantee of profit. ▲ signals raise it; ⚠ risks are shown separately and never hidden.</div>`;
  };
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
