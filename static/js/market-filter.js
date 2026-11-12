// Market filter chips ("compare on specific markets") and the "3 cheapest" helpers
// shared by every price list.

import { esc, money, store } from './util.js';

/** Markets to offer: the enabled automatic sources plus any manual-quote markets seen in the data. */
export function availableMarkets(status, quoteLists = []) {
  const markets = (status?.markets || []).filter((m) => m.enabled).map((m) => ({ key: m.key, label: m.label }));
  const manual = new Map();
  for (const quotes of quoteLists) {
    for (const q of quotes || []) {
      if (q.market.startsWith('manual:') && !manual.has(q.market)) manual.set(q.market, q.label);
    }
  }
  return markets.concat([...manual].sort((a, b) => a[1].localeCompare(b[1])).map(([key, label]) => ({ key, label })));
}

/**
 * A row of toggle chips. Nothing selected = all markets. Calls onChange(selectedKeys) on change.
 * The selection is remembered per filter id.
 */
export class MarketFilter {
  constructor(container, id, onChange) {
    this.container = container;
    this.id = id;
    this.onChange = onChange;
    this.selected = new Set(store.get(`markets:${id}`, []));
    this.markets = [];
    container.addEventListener('click', (event) => {
      const chip = event.target.closest('.chip[data-market]');
      if (!chip) return;
      const key = chip.dataset.market;
      if (!key) this.selected.clear();
      else if (this.selected.has(key)) this.selected.delete(key);
      else this.selected.add(key);
      store.set(`markets:${this.id}`, [...this.selected]);
      this.render();
      this.onChange(this.keys());
    });
  }

  setMarkets(markets) {
    this.markets = markets;
    // Forget selections for markets that no longer exist.
    for (const key of [...this.selected]) if (!markets.some((m) => m.key === key)) this.selected.delete(key);
    this.render();
  }

  /** Selected market keys, or null when showing all markets. */
  keys() {
    return this.selected.size ? [...this.selected] : null;
  }

  includes(key) {
    return !this.selected.size || this.selected.has(key);
  }

  render() {
    this.container.innerHTML = `<span class="filter-label">Markets</span>
      <button type="button" class="chip" data-market="" aria-pressed="${!this.selected.size}">All</button>
      ${this.markets.map((m) => `<button type="button" class="chip" data-market="${esc(m.key)}"
        aria-pressed="${this.selected.has(m.key)}">${esc(m.label)}</button>`).join('')}`;
  }
}

/** The cheapest quotes among the selected markets (quotes arrive sorted cheapest first). */
export function cheapest(quotes, filter, count = 3) {
  return (quotes || []).filter((q) => !filter || filter.includes(q.market)).slice(0, count);
}

/** Compact "£4.48 Skinport" lines for the 3-cheapest column. */
export function cheapestHtml(top, currency) {
  if (!top.length) return '<span class="muted">—</span>';
  return `<ol class="cheapest">${top.map((q, i) => `<li class="${i === 0 ? 'first' : ''}"><span class="val">${money(q.price, currency)}</span>
    <span class="mkt">${esc(q.label)}</span></li>`).join('')}</ol>`;
}
