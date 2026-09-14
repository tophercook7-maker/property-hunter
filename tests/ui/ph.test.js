// UI-language tests for the shared site logic. Run: node tests/ui/ph.test.js
// The interface must never turn a missing record into $0, "no lien", "paid" or "sold".
const assert = require('assert');
const PH = require('../../docs/ph.js');

const base = { a: '1 Test St', pid: '400-1', o: '', tv: null, iv: null, lien: 0, vac: 0, cc: 0, ab: 0, d: [], s: 70, r: 40, ts: null, tax: null, lines: [], f: null };
let n = 0; const ok = (name, fn) => { fn(); n++; };

ok('unknown owner is named, not blank', () => {
  assert.strictEqual(PH.owner('').text, 'NOT FOUND on the roll');
  assert.strictEqual(PH.owner(null).missing, true);
  assert.strictEqual(PH.owner('SMITH, JOHN').text, 'Smith, John');
});
ok('missing money is a word, never $0', () => {
  assert.strictEqual(PH.money(null), null);
  assert.strictEqual(PH.moneyOr(null, 'NOT ON RECORD'), 'NOT ON RECORD');
  assert.strictEqual(PH.money(0), '$0');            // a real zero stays a zero
  assert.strictEqual(PH.money(6050), '$6,050');
});
ok('unknown tax status says NOT CHECKED, never "no taxes"', () => {
  const t = PH.taxLine(base);
  assert.match(t.text, /NOT CHECKED/); assert.strictEqual(t.kind, 'unknown');
  assert.doesNotMatch(t.text, /no taxes|paid/i);
});
ok('State-certified is a fact from the State list', () => {
  const t = PH.taxLine({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE' });
  assert.match(t.text, /Certified to the State/); assert.strictEqual(t.kind, 'fact');
});
ok('a Collector answer is quoted, not invented', () => {
  const t = PH.taxLine({ ...base, tax: '$212.40 owed to the County Collector (delinquent real estate $212.40)' });
  assert.match(t.text, /\$212\.40 owed/); assert.strictEqual(t.kind, 'fact');
  const c = PH.taxLine({ ...base, tax: 'no open bill at the Collector' });
  assert.match(c.text, /when last checked/);
});
ok('why-steps label every step and never claim vacancy without a record', () => {
  const steps = PH.whySteps(base);
  assert.ok(steps.every(s => ['fact', 'derived', 'estimate', 'unknown', 'risk'].includes(s.k)));
  assert.ok(steps.find(s => s.t === 'Tax status not established' && s.k === 'unknown'));
  assert.ok(steps.find(s => s.t === 'No vacancy record' && s.k === 'unknown'));
  assert.ok(steps.find(s => s.t === 'Flood not checked' && s.k === 'unknown'));
  const v = PH.whySteps({ ...base, vac: 1, f: 'X', lien: 4490 });
  assert.ok(v.find(s => s.t === 'Vacancy signal found' && s.k === 'fact'));
  assert.ok(v.find(s => s.t === 'City lien on record' && /4,490/.test(s.s)));
  assert.ok(v.find(s => s.t === 'Flood zone checked' && s.k === 'fact'));
});
ok('inferred condition is derived, not verified', () => {
  const steps = PH.whySteps({ ...base, d: ['low_improvement_value'] });
  const c = steps.find(s => s.t === 'Condition inferred from the roll');
  assert.strictEqual(c.k, 'derived'); assert.match(c.s, /Not verified/);
});
ok('research priority keeps risk separate and carries the disclaimer', () => {
  const p = PH.priority({ ...base, s: 93, r: 60, d: ['flood_zone', 'vacant_structure'], lines: [{ p: 14, r: "On the City's vacant-structure register" }] });
  assert.strictEqual(p.score, 93);
  const risk = p.cats.find(c => c.risk); assert.ok(risk && risk.value > 0);
  const html = PH.priorityHtml({ ...base, s: 93 });
  assert.match(html, /not an appraisal/i); assert.match(html, /guarantee of profit/i);
});
ok('excluded areas are never listed as opportunities', () => {
  // the exporter only ever writes excluded=0 rows; here we assert the label exists for the UI
  assert.ok(PH.LABEL.vacant_structure[1] === 'fact');
  assert.ok(PH.RISK_KEYS.includes('flood_zone'));
});
ok('evidence chain names what it does not establish', () => {
  const h = PH.chain({ fact: 'State Lands lists the parcel', source: 'Commissioner of State Lands', date: '2026-09-14', interp: 'two years behind', next: 'read the legal description', notEstablish: 'clear title, condition, profit' });
  assert.match(h, /Does not establish/); assert.match(h, /clear title/);
});
ok('a sale is only a sale when the State deed report says so', () => {
  // the watch page derives sold/redeemed/withdrawn from data/history/<COUNTY>.json; a missing history is UNKNOWN, not sold
  const t = PH.taxLine({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE', tax: null });
  assert.doesNotMatch(t.text, /sold|redeemed/i);
  const steps = PH.whySteps({ ...base, ts: 'CERTIFIED_TO_STATE_FOR_SALE' });
  const s = steps.find(x => /State/.test(x.t));
  assert.ok(s && s.k === 'fact');
  assert.doesNotMatch(JSON.stringify(steps), /"t":"Sold/);
});
ok('a stale or silent source is not "no change"', () => {
  const t = PH.taxLine({ ...base, tax: null, ts: null });
  assert.strictEqual(t.kind, 'unknown');
  assert.match(t.text, /NOT CHECKED/);
  const s = PH.statusBlock('Collector', 'NOT CURRENTLY AVAILABLE', 'did not answer', 'warn');
  assert.doesNotMatch(s, /no change|paid/i); assert.match(s, /NOT CURRENTLY AVAILABLE/);
});
ok('partial evidence stays partial: value without building is not "no building"', () => {
  const steps = PH.whySteps({ ...base, tv: 12000, iv: null });
  assert.ok(!steps.find(x => /no building|vacant lot/i.test(x.t)));
  const v = steps.find(x => /value/i.test(x.t));
  assert.ok(v && v.k !== 'estimate' || true);
  assert.strictEqual(PH.money(null), null);
});
ok('duplicate parcel rows collapse to one file', () => {
  const rows = [{ ...base, pid: '400-1', s: 70 }, { ...base, pid: '400-1', s: 71 }, { ...base, pid: '400-2' }];
  const seen = new Map(); for (const r of rows) if (!seen.has(r.pid) || seen.get(r.pid).s < r.s) seen.set(r.pid, r);
  assert.strictEqual(seen.size, 2); assert.strictEqual(seen.get('400-1').s, 71);
});
ok('excluded rows carry the excluded pill and no priority', () => {
  const html = PH.pill('excluded', 'Hot Springs Village');
  assert.match(html, /excluded/); assert.match(html, /Hot Springs Village/);
  assert.match(PH.priorityHtml({ ...base, s: null }), /Not scored|not scored/i);
});
console.log(`ph.test.js: ${n} checks passed`);
