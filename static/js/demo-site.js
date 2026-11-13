// The read-only live demo (GitHub Pages). There's no server there, so requests are answered from
// JSON files saved by scripts/build_demo_site.py from the made-up demo inventory. Filters and
// search run here, the same way the server applies them; anything that would change data says
// it's a demo instead.

export const DEMO_SITE = document.documentElement.dataset.demoSite === '1';

const files = new Map();

function load(name) {
  if (!files.has(name)) {
    files.set(name, fetch(`data/${name}`).then((response) => {
      if (!response.ok) throw new Error(`The demo has no data for this (${name}).`);
      return response.json();
    }));
  }
  return files.get(name);
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

export async function demoApi(path, { method = 'GET', params = {} } = {}) {
  if (method !== 'GET') {
    throw new DemoError('This is a read-only demo with made-up items. Download the app from GitHub to try it with your own inventory.');
  }
  const scope = params.scope || 'total';
  const range = params.range || '90d';
  if (path === '/status') return load('status.json');
  if (path === '/jobs/current') return { job: null };
  if (path === '/names') return { names: [] };
  if (path === '/items') return filterItems(await load(`items-${scope}.json`), params);
  if (path === '/history/portfolio') return load(`history-${scope}-${range}.json`);
  if (path === '/insights') return load(`insights-${scope}-${range}.json`);
  if (path === '/market-comparison') return load(`market-${scope}.json`);
  if (path === '/containers') return load('containers.json');
  if (path.startsWith('/containers/')) return load(`container-${Number(path.split('/')[2])}.json`);
  if (path === '/quotes') return load(`quotes-${Number(params.item)}.json`);
  if (path === '/history/item') {
    const index = await load('item-index.json');
    return load(`item-history-${index[params.name]}-${range}.json`);
  }
  if (path === '/sell-plan') {
    // Plans were worked out for a set of amounts: use the closest one.
    const plans = await load(`plans-${params.wallet === 1 || params.wallet === '1' ? 'wallet' : 'cash'}.json`);
    const target = Number(params.target) || 0;
    const nearest = plans.reduce((best, plan) => (Math.abs(plan.target - target) < Math.abs(best.target - target) ? plan : best));
    return { ...nearest, demo_note: 'In the demo, plans are worked out for a set of amounts; the closest one is shown.' };
  }
  throw new DemoError('That part of the app needs the real thing - download it from GitHub.');
}

/** A banner saying what this page is, with a link to the project. */
export function showDemoBanner() {
  const banner = document.createElement('div');
  banner.className = 'demo-banner';
  banner.innerHTML = 'Live demo with a <strong>made-up inventory</strong> and synthetic prices - nothing here can be changed. '
    + '<a href="https://github.com/IllyaHanslip/cs2-inventory-tracker" rel="noopener">Get the app on GitHub</a> to track your own items.';
  document.body.prepend(banner);
}
