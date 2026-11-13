// Market filter chips ("compare on specific markets") and the "best 3 to sell" / "3 cheapest"
// helpers shared by every price list.

import { esc, money, number, store } from './util.js';

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

/** "34 on Skinport at £0.11, 497 on CSFloat at £0.05": how selling every copy at once is estimated. */
export function bulkText(plan, currency) {
  return (plan || []).map((s) => `${number(s.units)} on ${s.label} at ${money(s.net, currency)}`).join(', ');
}

/** A note under a Value cell when selling every copy at once can't all happen at the best price. */
export function bulkNote(item, currency) {
  if (!item.bulk_limited) return '';
  const title = `Selling all ${number(item.bulk_units)} at once: ${bulkText(item.bulk_plan, currency)}. `
    + 'Each market is counted for about as many as it sells in 30 days.';
  return `<div class="sub bulk-note" title="${esc(title)}">selling all: estimate</div>`;
}

/** The best-paying markets after fees among the selected ones (sell quotes arrive best first). */
export function bestToSell(sellQuotes, filter, count = 3) {
  return (sellQuotes || []).filter((q) => !filter || filter.includes(q.market)).slice(0, count);
}

/**
 * Why money from a market can't be cashed out: Steam pays into your Steam Wallet; others
 * (e.g. BUFF163) only pay out to some accounts, or you've marked them that way in Settings.
 */
export function noCashLabel(market, short = false) {
  if (market === 'steam') return short ? 'wallet' : 'Steam Wallet only';
  return short ? 'no cash-out' : 'no cash-out for most accounts';
}

/** Compact "£4.12 CSFloat" lines for the best-3-to-sell column; money you can't withdraw is tagged. */
export function sellHtml(top, currency) {
  if (!top.length) return '<span class="muted">—</span>';
  return `<ol class="cheapest">${top.map((q, i) => `<li class="${i === 0 ? 'first' : ''}"><span class="val">${money(q.net, currency)}</span>
    <span class="mkt">${esc(q.label)}${q.wallet ? ` <span class="wallet-tag" title="${esc(noCashLabel(q.market))}">${esc(noCashLabel(q.market, true))}</span>` : ''}</span></li>`).join('')}</ol>`;
}
