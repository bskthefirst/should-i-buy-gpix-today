// Run with: node --test scripts/test_retire.cjs
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const html = readFileSync(join(__dirname, '../docs/retire.html'), 'utf8');
const model = html.split('// ==== model:start')[1].split('\n').slice(1).join('\n').split('// ==== model:end')[0];
const settings = html.split('// ---- plan, defaults and saving ----')[1].split('// ---- formatting ----')[0];
function load(saved = {}) {
  const storage = new Map(Object.entries(saved));
  const context = vm.createContext({ localStorage: {
    getItem: k => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, v),
    removeItem: k => storage.delete(k),
  } });
  const api = vm.runInContext(model + settings + '\n({ initialHoldings, holdingMetrics, simulate, migrateHoldings, saveSettings, settings: () => S })', context);
  return { ...api, storage };
}
const market = { gpix: 8, gpiq: 10, pxGpix: 50, pxGpiq: 60 };
const plan = { monthly: 1000, gpixPct: 80, taxPct: 15, sharesGpix: 56.125, sharesGpiq: 3, whole: true };
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);

test('existing fractions survive price changes and the whole-share switch', () => {
  const api = load();
  for (const whole of [true, false]) {
    const h = api.initialHoldings({ ...market, pxGpix: 90 }, { ...plan, whole });
    close(h.sg, 56.125); close(h.sq, 3); close(h.cg + h.cq, 0);
  }
});
test('purchase cost changes gain and yield on cost, but never the distribution', () => {
  const { holdingMetrics: metrics } = load();
  const first = metrics(56.125, 40, 50, 0.4, 4.8, 15);
  const other = metrics(56.125, 45, 50, 0.4, 4.8, 15);
  close(first.basis, 2245); close(first.value, 2806.25);
  close(first.gain, 561.25); close(first.gainPct, 25);
  close(first.latestNet, 56.125 * 0.4 * 0.85);
  close(first.annualNet, 56.125 * 4.8 * 0.85);
  close(first.averageNet, first.annualNet / 12);
  close(first.yieldOnCost, first.annualNet / first.basis * 100);
  close(first.latestNet, other.latestNet);
  assert.notEqual(first.yieldOnCost, other.yieldOnCost);
});
test('15.4% is accepted and deducted once', () => {
  const m = load().holdingMetrics(100, 50, 55, 1, 12, 15.4);
  close(m.latestNet, 84.6); close(m.annualNet, 1015.2);
});
test('unknown distributions and zero purchase costs do not create income or infinite yields', () => {
  const m = load().holdingMetrics(10, 0, 50, null, null, 15);
  assert.equal(m.latestNet, null); assert.equal(m.annualNet, null);
  assert.equal(m.yieldOnCost, null); assert.equal(m.gainPct, null);
  const zero = load().holdingMetrics(0, 50, 60, 0.4, 4.8, 15);
  close(zero.latestNet, 0); close(zero.value, 0);
});
test('the forecast starts with actual shares and conserves cash after whole-share purchases', () => {
  const api = load();
  const sim = api.simulate(market, plan, { target: 250, drift: 0, inflation: 0 });
  const initial = 56.125 * 50 + 3 * 60;
  const net = (56.125 * 50 * 0.08 + 3 * 60 * 0.10) / 12 * 0.85;
  close(sim.months[0].portfolio, initial); close(sim.months[0].net, net);
  close(sim.months[1].portfolio, initial + 1000 + net);
  close(sim.months[1].cumReinv, net);
  assert.ok(sim.months[1].cash >= 0 && sim.months[1].cash < 110);
});
test('legacy dollar settings migrate once without inventing a purchase cost', () => {
  const api = load({ 'retire-settings-v2': JSON.stringify({ startGpix: 3107, startGpiq: 185, monthly: 1500, whole: true }) });
  api.migrateHoldings(market);
  const s = api.settings();
  close(s.sharesGpix, 62); close(s.sharesGpiq, 3);
  close(s.costGpix, 0); close(s.costGpiq, 0); close(s.monthly, 1500);
  assert.equal(s.holdingsReview, true);
  api.migrateHoldings({ ...market, pxGpix: 90 });
  close(s.sharesGpix, 62);
  api.saveSettings();
  const reloaded = load(Object.fromEntries(api.storage));
  reloaded.migrateHoldings({ ...market, pxGpix: 90 });
  close(reloaded.settings().sharesGpix, 62);
});
test('saved fractional shares and costs survive a new market price', () => {
  const api = load({ 'retire-settings-v3': JSON.stringify({ sharesGpix: 12.12345678, sharesGpiq: 0, costGpix: 41.25, taxPct: 15.4 }) });
  api.migrateHoldings({ ...market, pxGpix: 75 });
  close(api.settings().sharesGpix, 12.12345678);
  close(api.settings().costGpix, 41.25); close(api.settings().taxPct, 15.4);
});

test('the removed cash input cannot affect a saved plan', () => {
  const api = load({ 'retire-settings-v3': JSON.stringify({ sharesGpix: 56.125, costGpix: 54.62, monthly: 1500, startCash: 500 }) });
  const saved = api.settings();
  close(saved.sharesGpix, 56.125); close(saved.costGpix, 54.62); close(saved.monthly, 1500);
  assert.equal(saved.startCash, undefined);
  const sim = api.simulate(market, saved, { target: 250, drift: 0, inflation: 0 });
  close(sim.months[0].portfolio, 56.125 * 50);
  api.saveSettings();
  assert.equal(JSON.parse(api.storage.get('retire-settings-v3')).startCash, undefined);
});
