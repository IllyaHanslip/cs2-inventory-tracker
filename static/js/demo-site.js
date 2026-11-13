// The read-only live demo (GitHub Pages). There's no server there, so requests are answered from
// JSON files saved by scripts/build_demo_site.py from the made-up demo inventory. Filters and
// search run here, the same way the server applies them; anything that would change data says
// it's a demo instead.
//
// One thing can be tried for real: confirming the changes a Steam sync noticed (Containers tab).
// Every combination of them was worked out when the demo was built (data/states/), so the page
// shows exactly what the app would. Reloading the page starts the demo afresh.

import { toast } from './util.js';

export const DEMO_SITE = document.documentElement.dataset.demoSite === '1';

const files = new Map();
const demo = { applied: new Set(), dismissed: new Set(), key: '', states: {} };
const PAGE_LOADED = new Date().toISOString();   // the demo "restarts" when you reload the page

function load(name) {
  if (!files.has(name)) {
    files.set(name, fetch(`data/${name}`).then((response) => {
      if (!response.ok) throw new Error(`The demo has no data for this (${name}).`);
      return response.json();
    }));
  }
  return files.get(name);
}

/** The answer for the changes confirmed so far (falls back to the starting one when it's the same). */
async function loadState(name) {
  if (demo.applied.size) {
    const { states } = await load('states.json');
    const key = [...demo.applied].sort((a, b) => a - b).join('-');
    if (states[key] && states[key].includes(name)) return load(`states/${key}/${name}`);
  }
  return load(name);
}

class DemoError extends Error {
  constructor(message) {
    super(message);
    this.status = 403;
  }
}

/** Search text, as the server builds it: names, name tag, notes and sticker names. */
function searchText(item) {
  return [item.name, item.market_hash_name, item.name_tag, item.notes, ...item.stickers.map((s) => s.name)]
    .filter(Boolean).join(' ').toLowerCase();
}

function filterItems(data, params) {
  const categories = (params.category || '').split(',').filter(Boolean);
  const search = (params.q || '').trim().toLowerCase();
  const showContents = params.contents !== 'hide';
  const plain = categories.filter((c) => c !== 'in_container');
  const items = data.items.filter((item) => {
    if (!showContents && !categories.includes('in_container') && !search && item.container_id !== null) return false;
    if (categories.length && !(plain.includes(item.category) || (categories.includes('in_container') && item.container_id !== null))) return false;
    return search.split(/\s+/).filter(Boolean).every((term) => searchText(item).includes(term));
  });
  const listed = showContents ? data.items : data.items.filter((i) => i.container_id === null);
  const counts = {};
  for (const item of listed) counts[item.category] = (counts[item.category] || 0) + 1;
  counts.in_container = data.items.filter((i) => i.container_id !== null).length;
  return { ...data, counts, items };
}

async function pendingChanges() {
  const data = await loadState('changes.json');
  return { ...data, changes: data.changes.filter((c) => !demo.applied.has(c.id) && !demo.dismissed.has(c.id)) };
}

/** Confirming a change: data-changing ones switch to the matching saved state; the rest are just hidden. */
async function resolveChange(id, action, unit) {
  const { changes } = await pendingChanges();
  const change = changes.find((c) => c.id === id);
  if (!change) throw new DemoError('That change has already been dealt with.');
  if (action === 'store' || action === 'take_out') {
    if (!change.container_id) {
      throw new DemoError("The demo only has the suggested choices worked out, and this one has no suggestion - try 'Sold or traded' here. In the app you'd pick any of your storage units.");
    }
    if (unit && unit !== change.container_id) {
      throw new DemoError(`In the demo you can try the suggested storage unit (${change.container_label}); the app lets you pick any of yours.`);
    }
    const { changes: doable } = await load('states.json');
    if (!doable.includes(id)) throw new DemoError('The demo can only try the suggested choice for this one.');
    demo.applied.add(id);
    demo.key = [...demo.applied].sort((a, b) => a - b).join('-');
    demo.states = (await load('states.json')).states;
  } else {
    demo.dismissed.add(id);
    if (['gone', 'bought', 'keep'].includes(action)) {
      toast('In the demo, purchases and sales aren\'t saved - in the app this goes on the Profit & Loss tab.', 'info', 7000);
    }
  }
}

async function demoWrite(path, body = {}) {
  if (path === '/changes/apply-suggested') {
    const { changes } = await pendingChanges();
    let applied = 0;
    for (const change of changes.filter((c) => c.suggestion)) {
      await resolveChange(change.id, change.suggestion, null);
      applied += 1;
    }
    return { applied, problems: [], pending: (await pendingChanges()).changes.length };
  }
  const match = path.match(/^\/changes\/(\d+)$/);
  if (match) {
    await resolveChange(Number(match[1]), body.action, body.container_id ? Number(body.container_id) : null);
    return { result: { id: Number(match[1]), action: body.action }, pending: (await pendingChanges()).changes.length };
  }
  throw new DemoError('This is a read-only demo with made-up items. Download the app from GitHub to try it with your own inventory.');
}

export async function demoApi(path, { method = 'GET', params = {}, body } = {}) {
  if (method !== 'GET') return demoWrite(path, body);
  const scope = params.scope || 'total';
  const range = params.range || '90d';
  if (path === '/status') {
    const status = await loadState('status.json');
    return { ...status, started_at: PAGE_LOADED, pending_changes: (await pendingChanges()).changes.length };
  }
  if (path === '/jobs/current') return { job: null };
  if (path === '/names') return { names: [] };
  if (path === '/changes') return pendingChanges();
  if (path === '/ledger') return loadState('ledger.json');
  if (path === '/sets') return load('sets.json');
  if (path === '/sets/items') {
    const { sets } = await load('sets.json');
    const entry = sets.find((s) => s.event === params.event);
    if (!entry) throw new DemoError('The demo has no items for that event.');
    return load(`set-${entry.file}.json`);
  }
  if (path === '/items') return filterItems(await loadState(`items-${scope}.json`), params);
  if (path === '/history/portfolio') return loadState(`history-${scope}-${range}.json`);
  if (path === '/insights') return loadState(`insights-${scope}-${range}.json`);
  if (path === '/market-comparison') return loadState(`market-${scope}.json`);
  if (path === '/containers') return loadState('containers.json');
  if (path.startsWith('/containers/')) return loadState(`container-${Number(path.split('/')[2])}.json`);
  if (path === '/quotes') return loadState(`quotes-${Number(params.item)}.json`);
  if (path === '/history/item') {
    const index = await loadState('item-index.json');
    return load(`item-history-${index[params.name]}-${range}.json`);
  }
  if (path === '/sell-plan') {
    // Plans were worked out for a set of amounts: use the closest one.
    const plans = await loadState(`plans-${params.wallet === 1 || params.wallet === '1' ? 'wallet' : 'cash'}.json`);
    const target = Number(params.target) || 0;
    const nearest = plans.reduce((best, plan) => (Math.abs(plan.target - target) < Math.abs(best.target - target) ? plan : best));
    return { ...nearest, demo_note: 'In the demo, plans are worked out for a set of amounts; the closest one is shown.' };
  }
  throw new DemoError('That part of the app needs the real thing - download it from GitHub.');
}

/** The export link in the demo: the saved CSV for the changes confirmed so far. */
export function demoExportPath(scope) {
  const name = `export-${scope}.csv`;
  return demo.key && demo.states[demo.key]?.includes(name) ? `data/states/${demo.key}/${name}` : `data/${name}`;
}

/** A banner saying what this page is, with a link to the project. */
export function showDemoBanner() {
  const banner = document.createElement('div');
  banner.className = 'demo-banner';
  banner.innerHTML = 'Live demo with a <strong>made-up inventory</strong> and synthetic prices - on the Containers tab you can '
    + 'try confirming the changes, but nothing else changes. '
    + '<a href="https://github.com/IllyaHanslip/cs2-inventory-tracker" rel="noopener">Get the app on GitHub</a> to track your own items.';
  document.body.prepend(banner);
}
