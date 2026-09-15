// UI-language tests for the shared site logic. Run: node tests/ui/ph.test.js
// The interface must never turn a missing record into $0, "no lien", "paid", "clear", "sold" or "for sale".
const assert = require('assert');
const PH = require('../../docs/ph.js');

const base = { a: '1 Test St', pid: '400-1', cf: '05051', cn: 'Garland', o: '', tv: null, iv: null, lien: 0, vac: 0, cc: 0, ab: 0, d: [], s: 70, r: 40, ts: null, tax: null, lines: [], f: null };
let n = 0; const ok = (name, fn) => { fn(); n++; };

ok('unknown owner is named, not blank', () => {
  assert.strictEqual(PH.owner('').text, 'NOT FOUND on the roll');
  assert.strictEqual(PH.owner(null).missing, true);
  assert.strictEqual(PH.owner('SMITH, JOHN').text, 'Smith, John');
});
ok('missing money is a word, never $0', () => {
  assert.strictEqual(PH.money(null), null);
  assert.strictEqual(PH.moneyOr(null, 'NOT ON RECORD'), 'NOT ON RECORD');
  assert.strictEqual(PH.money(0), '$0');
  assert.strictEqual(PH.money(6050), '$6,050');
});

// ---- P0-1 tax state honesty ----
ok('never checked stays UNKNOWN; never "current", "$0" or "clear"', () => {
  const t = PH.taxState(base);
  assert.strictEqual(t.state, 'UNKNOWN'); assert.match(t.text, /UNKNOWN — never checked/); assert.strictEqual(t.kind, 'unknown');
  assert.doesNotMatch(t.text + t.note, /\$0|paid|clear|current\b/i);
  assert.strictEqual(t.distress, false);
});
ok('source down is SOURCE UNAVAILABLE, not a finding', () => {
  const t = PH.taxState(base, { open: false, down_since: '2026-09-14T14:25:00-0500' });
  assert.strictEqual(t.state, 'SOURCE_UNAVAILABLE'); assert.match(t.text, /down since 2026-09-14/); assert.strictEqual(t.kind, 'unknown');
});
ok('a current-year open bill is NOT delinquent and NOT tax distress', () => {
  const r = { ...base, taxs: { st: 'CURRENT_BILL_OPEN', src: 'County Collector', as_of: '2026-09-15', amt: 66.65 } };
  const t = PH.taxState(r);
  assert.strictEqual(t.state, 'CURRENT_BILL_OPEN'); assert.match(t.text, /CURRENT BILL OPEN — \$66\.65 — not delinquent \[2026-09-15\]/);
  assert.doesNotMatch(t.text, /DELINQUENT —/);
  assert.strictEqual(t.distress, false); assert.strictEqual(t.tone, 'neutral');
  assert.notStrictEqual(PH.taxLine(r).tone, 'bad');
  assert.strictEqual(PH.priority(r).cats.find(c => c.name === 'Tax status').value, 0);
  assert.ok(PH.whySteps(r).find(s => s.t === 'Current-year county bill open (not delinquent)'));
});
ok('verified delinquency and State tax sale are distress, with source and date', () => {
  const d = PH.taxState({ ...base, taxs: { st: 'DELINQUENT_VERIFIED', src: 'County Collector', as_of: '2026-09-15', amt: 212.4 } });
  assert.match(d.text, /DELINQUENT — County Collector verified, \$212\.40 \[2026-09-15\]/); assert.strictEqual(d.distress, true);
  const s = PH.taxState({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE', taxs: { st: 'TAX_SALE_VERIFIED', src: 'Commissioner of State Lands', as_of: '2026-09-12', amt: 297.69 } });
  assert.match(s.text, /TAX SALE — State certified, \$297\.69 owed \[2026-09-12\]/); assert.strictEqual(s.source, 'Commissioner of State Lands');
  assert.strictEqual(PH.taxState({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE' }).state, 'TAX_SALE_VERIFIED');   // legacy rows
});
ok('a Collector clean answer is CURRENT with its date; a State "not held" check never becomes a Collector answer', () => {
  const c = PH.taxState({ ...base, taxs: { st: 'CURRENT_VERIFIED', src: 'County Collector', as_of: '2026-09-14' } });
  assert.match(c.text, /CURRENT — no open Collector bill \[2026-09-14\]/);
  const k = PH.taxState({ ...base, taxs: { st: 'UNKNOWN', cosl_check: '2026-09-14' } });
  assert.strictEqual(k.state, 'UNKNOWN'); assert.match(k.note, /not held by the State Lands as of 2026-09-14 \(says nothing about the county bill\)/);
  assert.doesNotMatch(k.text, /Collector bill|no open/);
});
ok('a stale Collector answer says STALE and what it used to say', () => {
  const t = PH.taxState({ ...base, taxs: { st: 'STALE', was: 'CURRENT_VERIFIED', src: 'County Collector', as_of: '2026-01-05', days: 250 } });
  assert.match(t.text, /STALE — last Collector check 2026-01-05 said CURRENT · 250 days ago/); assert.strictEqual(t.kind, 'derived');
});

// ---- P0-9 sale status ----
ok('sale status is UNKNOWN by default and says so in words', () => {
  const s = PH.saleStatus(base);
  assert.strictEqual(s.state, 'UNKNOWN'); assert.match(s.text, /no listing source connected/); assert.match(s.text, /not saying this is for sale or not for sale/);
  assert.match(PH.searchListingsUrl(base), /^https:\/\/www\.google\.com\/search\?q=/);
});
ok('a State-certified parcel is for sale BY THE STATE, never a private listing', () => {
  const s = PH.saleStatus({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE' });
  assert.strictEqual(s.state, 'FOR_SALE_BY_STATE'); assert.match(s.text, /not a private listing/);
});
ok('FOR SALE only with a named source', () => {
  assert.strictEqual(PH.saleStatus({ ...base, sale: { st: 'FOR_SALE', src: null } }).state, 'UNKNOWN');
  assert.strictEqual(PH.saleStatus({ ...base, sale: { st: 'FOR_SALE', src: 'MLS export 2026-09-15', price: 50000 } }).state, 'FOR_SALE');
});

// ---- P0-8 signal stack ----
ok('signal stack counts verified and derived honestly and never sells a deal', () => {
  const r = { ...base, s: 93, vac: 1, lien: 4490, ab: 1, d: ['low_improvement_value', 'flood_zone'] };
  const st = PH.stack(r, 100);
  assert.strictEqual(st.verified, 3); assert.strictEqual(st.derived, 1); assert.strictEqual(st.countyMax, 100);
  const html = PH.stackHtml(r, 100);
  assert.match(html, /<b>4<\/b><span>public-record signals · 3 verified · 1 derived · county maximum score 100/);
  assert.match(html, /not a probability of profit, of sale, or of value/); assert.match(html, /many records, not a good deal/);
  assert.doesNotMatch(html, /research priority|best to buy|investment|profitable/i);
  assert.match(PH.stackHtml({ ...base, s: null }), /Not scored yet/);
  assert.match(PH.stackHtml({ ...base, d: [] }), /No public-record signals; here because of roll patterns only/);
});
ok('a stale State certification does not count as a signal once the row is no longer certified', () => {
  assert.ok(!PH.signals({ ...base, d: ['tax_delinquent'], ts: null }).all.some(x => x.key === 'tax_delinquent'));
});

// ---- P0-10 / P0-11 why row + next action ----
ok('every row gets a why, a sale line, a tax line and one next action with a real destination', () => {
  const r = { ...base, vac: 1, lat: 34.5, lon: -93.0 };
  const html = PH.whyRow(r, { open: false, down_since: '2026-09-14' });
  assert.match(html, /1 verified public-record signal/); assert.match(html, /UNKNOWN — no listing source connected/); assert.match(html, /SOURCE UNAVAILABLE/);
  assert.match(html, /Drive by →/); assert.match(html, /google\.com\/maps\/dir/);
  const bare = PH.whyRow(base);
  assert.match(bare, /Here because of public-record patterns only; no verified opportunity signal found/);
  assert.match(bare, /Check Collector \/ request county list →/); assert.match(bare, /request\.html/);
});
ok('next action verbs map to existing destinations only', () => {
  assert.deepStrictEqual(PH.nextAction({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE', pid: '400-1', cn: 'Garland' }), { label: 'Open sale file', href: 'state-lands.html?county=GARLAND&q=400-1' });
  assert.strictEqual(PH.nextAction({ ...base, lien: 900, taxs: { st: 'CURRENT_VERIFIED' } }).label, 'Investigate lien');
  assert.strictEqual(PH.nextAction({ ...base, cc: 1, taxs: { st: 'CURRENT_VERIFIED' } }).label, 'Review case');
  assert.strictEqual(PH.nextAction({ ...base, taxs: { st: 'DELINQUENT_VERIFIED' } }).label, 'Inspect delinquent record');
  assert.strictEqual(PH.nextAction({ ...base, taxs: { st: 'CURRENT_VERIFIED' }, d: ['low_improvement_value'] }).label, 'Search public listings');
  for (const r of [base, { ...base, vac: 1 }, { ...base, lien: 5 }]) { const nx = PH.nextAction(r); assert.ok(nx.label && nx.href, JSON.stringify(nx)); }
});

// ---- earlier guarantees kept ----
ok('why-steps label every step; no vacancy claim without a record; sale step present', () => {
  const steps = PH.whySteps(base);
  assert.ok(steps.every(s => ['fact', 'derived', 'estimate', 'unknown', 'risk'].includes(s.k)));
  assert.ok(steps.find(s => s.t === 'Tax status not established' && s.k === 'unknown'));
  assert.ok(steps.find(s => s.t === 'No vacancy record' && s.k === 'unknown'));
  assert.ok(steps.find(s => s.t === 'Sale status: UNKNOWN' && s.k === 'unknown'));
  const v = PH.whySteps({ ...base, vac: 1, f: 'X', lien: 4490 });
  assert.ok(v.find(s => s.t === 'Vacancy signal found' && s.k === 'fact'));
  assert.ok(v.find(s => /Signal stack: \d+ public-record signal/.test(s.t) && /not a probability of sale or profit/.test(s.s)));
});
ok('inferred condition is derived, not verified', () => {
  const c = PH.whySteps({ ...base, d: ['low_improvement_value'] }).find(s => s.t === 'Condition inferred from the roll');
  assert.strictEqual(c.k, 'derived'); assert.match(c.s, /Not verified/);
});
ok('evidence chain names what it does not establish', () => {
  const h = PH.chain({ fact: 'State Lands lists the parcel', source: 'Commissioner of State Lands', date: '2026-09-14', interp: 'two years behind', next: 'read the legal description', notEstablish: 'clear title, condition, profit' });
  assert.match(h, /Does not establish/); assert.match(h, /clear title/);
});
ok('excluded areas keep their pill', () => { assert.match(PH.pill('excluded', 'Hot Springs Village'), /excluded/); });
console.log(`ph.test.js: ${n} checks passed`);
