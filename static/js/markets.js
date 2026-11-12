// "Market Comparison & Best Deals" tab. Everything here (columns, the top 3, spread, saving vs
// Steam, the deals summary) is computed for the markets selected in the chips, in one of two modes:
//   buy  - each market's cheapest listing; lower is better.
//   sell - what you'd receive selling there after selling + withdrawal fees; higher is better.
//          Markets you can't cash out from (Steam Wallet) are shown but not ranked.

import { api } from './api.js';
import * as dialogs from './dialogs.js';
import { MarketFilter } from './market-filter.js';
import { applySort } from './sortable.js';
import {
  debounce, esc, longDate, money, number, relTime, safeUrl, state, store, thumbHtml, toast,
} from './util.js';

const MEDALS = ['Gold', 'Silver', 'Bronze'];

export class MarketsView {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.data = null;
    this.loaded = false;
    this.el = {
      search: root.querySelector('#mk-search'),
      scope: root.querySelector('#mk-scope'),
      multi: root.querySelector('#mk-multi'),
      mode: root.querySelector('#mk-mode'),
      table: root.querySelector('#mk-table'),
      legend: root.querySelector('#mk-legend'),
      empty: root.querySelector('#mk-empty'),
      deals: root.querySelector('#deal-stats'),
      fx: root.querySelector('#fx-list'),
      fxMeta: root.querySelector('#fx-meta'),
    };
    const saved = store.get('view:markets', {});
    this.el.scope.value = saved.scope || 'total';
    this.el.multi.checked = Boolean(saved.multi);
    this.mode = saved.mode === 'sell' ? 'sell' : 'buy';
    this.filter = new MarketFilter(root.querySelector('#mk-markets'), 'comparison', () => this.render());

    const persist = () => store.set('view:markets', {
      scope: this.el.scope.value, multi: this.el.multi.checked, mode: this.mode,
    });
    this.el.scope.addEventListener('change', () => { persist(); this.reload(); });
    this.el.multi.addEventListener('change', () => { persist(); this.render(); });
    this.el.mode.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-mode]');
      if (!button) return;
      this.mode = button.dataset.mode;
      persist();
      this.render();
    });
    this.el.search.addEventListener('input', debounce(() => this.render(), 200));
    root.querySelector('#mk-add-quote').addEventListener('click', () => dialogs.addQuote());

    this.el.table.addEventListener('click', async (event) => {
      const remove = event.target.closest('button[data-remove]');
      if (!remove) return;
      event.stopPropagation();
      try {
        await api('/quotes', { method: 'DELETE', body: { market: remove.dataset.remove, market_hash_name: remove.dataset.name } });
        toast('Manual quote removed', 'ok');
        this.app.refreshAll();
      } catch (error) {
        toast(error.message, 'error');
      }
    });
  }

  async reload() {
    const card = this.root.querySelector('.table-card');
    card.classList.add('is-loading');
    try {
      this.data = await api('/market-comparison', { params: { scope: this.el.scope.value } });
      this.filter.setMarkets(this.data.markets.map((m) => ({ key: m.key, label: m.label })));
      this.loaded = true;
      this.render();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      card.classList.remove('is-loading');
    }
  }

  /** Per-row figures for the selected markets only, in the current mode. */
  computeRows(markets) {
    const selling = this.mode === 'sell';
    const keys = new Set(markets.map((m) => m.key));
    const labels = Object.fromEntries(this.data.markets.map((m) => [m.key, m.label]));
    const terms = this.el.search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return this.data.rows
      .filter((row) => terms.every((t) => row.market_hash_name.toLowerCase().includes(t)))
      .map((row) => {
        const quotes = Object.entries(row.prices)
          .filter(([key]) => keys.has(key))
          .map(([key, q]) => ({
            market: key, label: labels[key] || key, url: q.url, cash_out: q.cash_out,
            value: selling ? q.net : q.price_base,
          }))
          .filter((q) => q.value !== null && q.value !== undefined && (!selling || q.cash_out));
        quotes.sort((a, b) => (selling ? b.value - a.value : a.value - b.value));
        const values = quotes.map((q) => q.value);
        const low = values.length ? Math.min(...values) : null;
        const high = values.length ? Math.max(...values) : null;
        const steam = !selling && keys.has('steam') ? row.prices.steam?.price_base ?? null : null;
        return {
          ...row,
          top: quotes.slice(0, 3),
          count: quotes.length,
          spread: quotes.length >= 2 && low > 0 ? ((high - low) / low) * 100 : null,
          saving: steam !== null && quotes.length ? steam - quotes[0].value : null,
          payout: selling && quotes.length ? quotes[0].value * row.quantity : null,
        };
      })
      .filter((row) => !this.el.multi.checked || row.count >= 2);
  }

  render() {
    if (!this.data) return;
    const { currency } = this.data;
    const selling = this.mode === 'sell';
    this.el.mode.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.mode === this.mode));
    const markets = this.data.markets.filter((m) => this.filter.includes(m.key));
    const rows = this.computeRows(markets);
    this.renderFx();
    this.renderDeals(rows, markets);

    const head = markets.map((m) => `<th class="num" data-sort="num" data-key="m:${esc(m.key)}"
      ${selling && !m.cash_out ? 'title="Steam Wallet money can\'t be withdrawn"' : ''}>${esc(m.label)}${selling && !m.cash_out ? ' <span class="muted">(wallet)</span>' : ''}</th>`).join('');
    const lastHead = selling
      ? '<th class="num" data-sort="num" data-key="payout" title="Best payout after fees x quantity, at plain listing prices">Cash-out (listings)</th>'
      : '<th class="num" data-sort="num" data-key="saving">Save vs Steam</th>';
    this.el.table.querySelector('thead').innerHTML = `<tr><th class="col-icon"><span class="sr-only">Image</span></th>
      <th data-sort="text" data-key="name">Item</th><th class="num" data-sort="num" data-key="qty">Qty</th>${head}
      <th class="num" data-sort="num" data-key="spread">Spread</th>${lastHead}</tr>`;
    this.el.legend.innerHTML = `<span>${selling ? 'Best payouts after fees' : 'Cheapest places to buy'}:</span>
      ${MEDALS.map((label, i) => `<span><span class="medal m${i + 1}">${i + 1}</span>${label}</span>`).join('')}
      ${selling ? '<span class="muted">Steam Wallet money cannot be withdrawn, so Steam is not ranked here. These are plain listing prices; the Cash-out value at the top of the page also counts your custom prices and sticker values, so it can be higher.</span>' : ''}`;

    this.el.table.querySelector('tbody').innerHTML = rows.map((row, index) => {
      const cells = markets.map((m) => {
        const quote = row.prices[m.key];
        if (!quote) return '<td class="num price-cell" data-v=""><span class="muted">—</span></td>';
        const shown = selling ? quote.net : quote.price_base;
        const url = safeUrl(quote.url);
        const value = `<span class="val">${money(shown, currency)}</span>`;
        let sub = '';
        if (selling) sub = `<span class="orig">listed ${money(quote.price_base, currency)}${quote.cash_out ? '' : ' · wallet only'}</span>`;
        else if (quote.currency !== currency) sub = `<span class="orig">${money(quote.price, quote.currency)}</span>`;
        const title = `Updated ${relTime(quote.fetched_at)}${quote.volume ? ` · ${number(quote.volume)} listed/sold` : ''}`;
        const remove = m.key.startsWith('manual:')
          ? `<button type="button" class="rm" data-remove="${esc(m.key)}" data-name="${esc(row.market_hash_name)}" aria-label="Remove manual quote" title="Remove manual quote">×</button>`
          : '';
        const inner = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${value}</a>` : value;
        // Gold / silver / bronze for this item's 3 cheapest (or best-paying) markets.
        const rank = row.count > 1 ? row.top.findIndex((q) => q.market === m.key) : -1;
        const medal = rank >= 0 ? ` medal-${rank + 1}` : '';
        const badge = rank >= 0 ? `<span class="medal m${rank + 1}" title="${MEDALS[rank]}: ${['cheapest', '2nd cheapest', '3rd cheapest'][rank]}">${rank + 1}</span>` : '';
        const rankTitle = rank >= 0 ? `${MEDALS[rank]} · ` : '';
        return `<td class="num price-cell${medal}" data-v="${shown ?? ''}" title="${esc(rankTitle + title)}">${badge}${inner}${remove}${sub}</td>`;
      }).join('');
      const spread = row.spread !== null ? `${row.spread.toFixed(1)}%` : '<span class="muted">—</span>';
      let last;
      if (selling) {
        last = `<td class="num" data-v="${row.payout ?? ''}">${row.payout !== null ? money(row.payout, currency) : '<span class="muted">—</span>'}</td>`;
      } else {
        const saving = row.saving
          ? `${money(row.saving, currency)}${row.quantity > 1 ? `<div class="sub">${money(row.saving * row.quantity, currency)} total</div>` : ''}`
          : '<span class="muted">—</span>';
        last = `<td class="num" data-v="${row.saving ?? ''}">${saving}</td>`;
      }
      return `<tr data-i="${index}">
        <td>${thumbHtml(row)}</td>
        <td data-v="${esc(row.market_hash_name)}"><div class="item-name">${esc(row.market_hash_name)}</div></td>
        <td class="num" data-v="${row.quantity}">${number(row.quantity)}</td>${cells}
        <td class="num" data-v="${row.spread ?? ''}">${spread}</td>${last}</tr>`;
    }).join('');
    applySort(this.el.table);

    this.el.empty.hidden = rows.length > 0;
    if (!rows.length) {
      this.el.empty.innerHTML = this.data.rows.length
        ? '<strong>No items match</strong>Try clearing the search, selecting more markets, or unticking "2+ markets".'
        : '<strong>Nothing to compare yet</strong>Sync your Steam inventory or add items, then press <em>Refresh prices</em>.';
    }
  }

  renderDeals(rows, markets) {
    const { currency } = this.data;
    const priced = rows.filter((r) => r.count);
    const widest = rows.filter((r) => r.spread !== null).sort((a, b) => b.spread - a.spread)[0];
    const counts = {};
    for (const r of rows) if (r.count > 1) counts[r.top[0].label] = (counts[r.top[0].label] || 0) + 1;
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    const tile = (label, value, foot) => `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    if (this.mode === 'sell') {
      const total = rows.reduce((sum, r) => sum + (r.payout || 0), 0);
      this.el.deals.innerHTML = [
        tile('Cash-out at listing prices', money(total, currency),
          'plain-skin listings on the selected markets · no custom prices or sticker value'),
        tile('Items with a payout', `${number(priced.length)} / ${number(rows.length)}`, 'on markets you can withdraw from'),
        tile('Usually best to sell on', top ? esc(top[0]) : '—', top ? `best payout on ${number(top[1])} items` : 'needs 2+ markets per item'),
        tile('Widest payout gap', widest ? `${widest.spread.toFixed(0)}%` : '—', widest ? esc(widest.market_hash_name) : 'between best and worst market'),
      ].join('');
      return;
    }
    const cheaper = rows.filter((r) => r.saving > 0.005);
    const totalSaving = cheaper.reduce((sum, r) => sum + r.saving * r.quantity, 0);
    const steamSelected = markets.some((m) => m.key === 'steam');
    this.el.deals.innerHTML = [
      tile('Items priced', `${number(priced.length)} / ${number(rows.length)}`, 'on the selected markets'),
      tile('Cheaper than Steam', steamSelected ? number(cheaper.length) : '—',
        steamSelected ? `could be bought for ${money(totalSaving, currency)} less` : 'select Steam Market to compare'),
      tile('Usually cheapest', top ? esc(top[0]) : '—', top ? `cheapest on ${number(top[1])} items` : 'needs 2+ markets per item'),
      tile('Widest spread', widest ? `${widest.spread.toFixed(0)}%` : '—', widest ? esc(widest.market_hash_name) : 'between cheapest and dearest market'),
    ].join('');
  }

  renderFx() {
    const { fx } = this.data;
    this.el.fxMeta.textContent = fx.date ? `${fx.source || 'Daily rates'} · ${longDate(fx.date)}` : 'No rates loaded yet';
    const entries = Object.entries(fx.rates);
    this.el.fx.innerHTML = entries.length
      ? entries.map(([code, rate]) => `<div class="fx-item"><div class="code">1 ${esc(code)}</div>
          <div>${rate >= 0.01 ? rate.toFixed(4) : rate.toPrecision(3)} ${esc(fx.base)}</div></div>`).join('')
      : '<p class="muted">Rates load automatically on the first price refresh.</p>';
    state.fx = fx;
  }
}
