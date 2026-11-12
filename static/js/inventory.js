// Inventory tab (used for both "Public Inventory" and "Total Portfolio").

import { api } from './api.js';
import { chartsAvailable, dataTableHtml, renderLineChart } from './charts.js';
import * as containers from './containers.js';
import * as dialogs from './dialogs.js';
import { MarketFilter, availableMarkets, cheapest, cheapestHtml } from './market-filter.js';
import { applySort, applySortWithin } from './sortable.js';
import {
  CATEGORY_FILTERS, debounce, deltaHtml, esc, floatText, money, number, state, store, thumbHtml, toast,
} from './util.js';

export class InventoryView {
  constructor(root, scope, app) {
    this.root = root;
    this.scope = scope;
    this.app = app;
    this.items = [];
    this.chart = null;
    this.history = null;
    root.append(document.getElementById('inventory-panel-tpl').content.cloneNode(true));
    this.refs = {};
    root.querySelectorAll('[data-ref]').forEach((el) => { this.refs[el.dataset.ref] = el; });
    this.state = {
      categories: [], q: '', stack: true, range: '90d', showContents: true,
      ...store.get(`view:${scope}`, {}),
    };
    this.state.q = '';
    this.setup();
  }

  persist() {
    const { categories, stack, range, showContents } = this.state;
    store.set(`view:${this.scope}`, { categories, stack, range, showContents });
  }

  /** Stored items are listed alongside everything else (Total tab, toggle on). */
  get listingContents() {
    return this.scope === 'total' && this.state.showContents;
  }

  setup() {
    const r = this.refs;
    const isPublic = this.scope === 'public';
    r.chartTitle.textContent = isPublic ? 'Public inventory value' : 'Total portfolio value';
    r.chart.setAttribute('aria-label', `${r.chartTitle.textContent} over time`);
    r.table.dataset.sortId = `inventory-${this.scope}`;
    if (isPublic) {
      r.addItem.remove();
      r.addContainer.remove();
      r.contentsLabel.remove();
    } else {
      r.addItem.addEventListener('click', () => dialogs.editItem(null));
      r.addContainer.addEventListener('click', () => containers.newContainer());
      r.contents.checked = this.state.showContents;
      r.contents.addEventListener('change', () => {
        this.state.showContents = r.contents.checked;
        this.persist();
        this.load();
      });
    }
    r.export.href = `/api/export.csv?scope=${this.scope}`;

    // Category chips (multi-select, combined with OR).
    const chips = CATEGORY_FILTERS.filter(([key]) => !(isPublic && key === 'in_container'));
    r.chips.innerHTML = `<button type="button" class="chip" data-cat="">All</button>${chips
      .map(([key, label]) => `<button type="button" class="chip" data-cat="${key}">${esc(label)} <span class="count" data-count="${key}"></span></button>`)
      .join('')}`;
    r.chips.addEventListener('click', (event) => {
      const chip = event.target.closest('.chip');
      if (!chip) return;
      const cat = chip.dataset.cat;
      if (!cat) this.state.categories = [];
      else if (this.state.categories.includes(cat)) this.state.categories = this.state.categories.filter((c) => c !== cat);
      else this.state.categories = [...this.state.categories, cat];
      this.persist();
      this.load();
    });

    // Market chips: which markets the "3 cheapest" column compares.
    this.marketFilter = new MarketFilter(r.marketChips, `inventory-${this.scope}`, () => this.renderTable());

    r.stack.checked = this.state.stack;
    r.stack.addEventListener('change', () => { this.state.stack = r.stack.checked; this.persist(); this.renderTable(); });

    r.search.addEventListener('input', debounce(() => { this.state.q = r.search.value.trim(); this.load(); }, 250));

    r.ranges.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-range]');
      if (!button) return;
      this.state.range = button.dataset.range;
      this.persist();
      this.loadChart();
    });

    r.tableDetails.addEventListener('toggle', () => this.renderChartTable());

    r.tbody.addEventListener('click', (event) => {
      const row = event.target.closest('tr.row');
      if (!row) return;
      const item = this.rowItems.get(row.dataset.key);
      if (!item) return;
      const action = event.target.closest('[data-action]')?.dataset.action;
      const run = (promise) => Promise.resolve(promise).catch((error) => toast(error.message, 'error'));
      if (action === 'open') run(containers.openContainer(item.id));
      else if (action === 'open-container') run(containers.openContainer(item.container_id));
      else if (action === 'remove') run(containers.removeItem(item));
      else if (!event.target.closest('a')) dialogs.openItem(item);
    });
    r.tbody.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('tr.row')) return;
      const item = this.rowItems.get(event.target.dataset.key);
      if (item) dialogs.openItem(item);
    });
    this.updateControls();
  }

  updateControls() {
    const r = this.refs;
    r.chips.querySelectorAll('.chip').forEach((chip) => {
      const cat = chip.dataset.cat;
      chip.setAttribute('aria-pressed', String(cat ? this.state.categories.includes(cat) : this.state.categories.length === 0));
    });
    r.ranges.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.range === this.state.range));
  }

  async reload() {
    await Promise.all([this.load(), this.loadChart()]);
  }

  async load() {
    this.updateControls();
    const r = this.refs;
    r.tableCard.classList.add('is-loading');
    try {
      // The server's order (most valuable first) is the "unsorted" order; header clicks sort locally.
      const data = await api('/items', {
        params: {
          scope: this.scope,
          category: this.state.categories.join(','),
          q: this.state.q,
          contents: this.scope === 'total' && !this.state.showContents ? 'hide' : 'show',
        },
      });
      this.items = data.items;
      this.summary = data.summary;
      for (const el of r.chips.querySelectorAll('[data-count]')) {
        const n = data.counts[el.dataset.count] || 0;
        el.textContent = n ? String(n) : '';
      }
      this.marketFilter.setMarkets(availableMarkets(state.status, this.items.map((i) => i.quotes)));
      this.renderTable();
      this.app.updateSummary(this.scope, data.summary);
      this.renderHeadline();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      r.tableCard.classList.remove('is-loading');
    }
  }

  rows() {
    if (!this.state.stack) return this.items;
    const out = [];
    const groups = new Map();
    for (const item of this.items) {
      const stackable = !item.is_container && !item.stickers.length && !item.name_tag
        && item.float_value === null && item.price_source !== 'custom' && !item.notes;
      if (!stackable) { out.push(item); continue; }
      const key = `${item.source}|${item.market_hash_name}|${item.container_id ?? ''}`;
      const group = groups.get(key);
      if (group) {
        group.quantity += item.quantity;
        group.stack_ids.push(item.id);
        if (item.display_value !== null) group.display_value = (group.display_value || 0) + item.display_value;
        if (item.total_value !== null) group.total_value = (group.total_value || 0) + item.total_value;
        if (item.payout_value !== null) group.payout_value = (group.payout_value || 0) + item.payout_value;
      } else {
        const copy = { ...item, stack_ids: [item.id] };
        groups.set(key, copy);
        out.push(copy);
      }
    }
    return out;
  }

  renderTable() {
    const r = this.refs;
    const rows = this.rows();
    this.rowItems = new Map();
    r.tbody.innerHTML = rows.map((item, i) => {
      const key = `${item.id}-${i}`;
      this.rowItems.set(key, item);
      return this.rowHtml(item, key, i);
    }).join('');
    applySort(r.table);

    if (rows.length) {
      r.empty.hidden = true;
    } else {
      r.empty.hidden = false;
      const filtered = this.state.q || this.state.categories.length;
      if (filtered) {
        r.empty.innerHTML = '<strong>No items match these filters</strong>Clear the search or pick another category.';
      } else if (this.scope === 'public') {
        r.empty.innerHTML = '<strong>No Steam items yet</strong>Paste your SteamID64 or profile URL at the top and press <em>Sync Steam</em>. '
          + 'Your Steam inventory must be set to Public.';
      } else {
        r.empty.innerHTML = '<strong>Your portfolio is empty</strong>Sync your Steam inventory, or use <em>+ Add item</em> / '
          + '<em>+ New container</em> to track items manually.';
      }
    }
  }

  rowHtml(item, key, index) {
    const meta = [];
    if (item.exterior) meta.push(esc(item.exterior));
    const f = floatText(item.float_value);
    if (f) meta.push(`Float ${esc(f)}`);
    if (item.paint_seed !== null) meta.push(`Seed ${esc(item.paint_seed)}`);
    if (item.name_tag) meta.push(`“${esc(item.name_tag)}”`);
    const stored = item.container_name
      ? ` <button type="button" class="badge badge-stored link-badge" data-action="open-container"
          title="Open this container">In ${esc(item.container_name)}</button>`
      : '';
    const stickers = item.stickers.length
      ? `<div class="sticker-list">${item.stickers.map((s) => `<span class="sticker-chip" title="${esc(
        `${s.kind}: ${s.name}${s.loose_price ? ` · cheapest loose ${money(s.loose_price)} · counted +${money(s.counted)}` : ' · no price yet'}${s.thin ? ' · few listings' : ''}`,
      )}">${esc(s.name)}</span>`).join('')}</div>`
      : '';
    const sourceBadge = this.scope === 'total'
      ? `<span class="badge ${item.source === 'steam' ? 'badge-steam' : ''}">${item.source === 'steam' ? 'Steam' : 'Manual'}</span>`
      : '';
    const stacked = item.stack_ids && item.stack_ids.length > 1;
    const qty = `${stacked ? '<span class="stack-badge">×</span>' : ''}${number(item.quantity)}`;
    // When stored items are listed separately, a container row shows only its own value
    // (so nothing looks counted twice); otherwise it shows its value including contents.
    let value = money(item.display_value);
    let valueNumber = item.display_value;
    if (item.is_container && item.contents_count) {
      if (this.listingContents) {
        value = `${money(item.total_value)}<div class="sub" title="The stored items are listed separately">+ ${money(item.contents_value)} stored</div>`;
        valueNumber = item.total_value;
      } else {
        value = `${money(item.display_value)}<div class="sub">incl. ${number(item.contents_count)} stored inside</div>`;
      }
    }
    const actions = [];
    if (item.is_container && item.container_id === null) {
      actions.push('<button type="button" class="btn btn-small" data-action="open">Open</button>');
    }
    if (item.source === 'manual' && !stacked) {
      actions.push(`<button type="button" class="icon-btn danger" data-action="remove" aria-label="Remove ${esc(item.name)}"
        title="Remove">${containers.ICONS.trash}</button>`);
    }
    const stickerCell = item.sticker_included ? `<span class="muted small" title="${item.price_source === 'evidence' ? 'Valued from CSFloat listings/sales with the same stickers' : 'Your custom price includes the stickers'}">${item.price_source === 'evidence' ? 'in CSFloat value' : 'in custom price'}</span>`
      : item.sticker_premium > 0
        ? `+${money(item.sticker_premium)}${item.sticker_capped ? '<div class="sub">capped</div>' : ''}`
        : '<span class="muted">—</span>';
    const top = cheapest(item.quotes, this.marketFilter);
    const v = (n) => (n === null || n === undefined ? '' : n);
    return `<tr class="row" tabindex="0" data-key="${esc(key)}" data-i="${index}">
      <td>${thumbHtml(item)}</td>
      <td data-v="${esc(item.name)}"><div class="item-name">${esc(item.name)} ${sourceBadge}${stored}</div>
        ${meta.length ? `<div class="item-meta">${meta.join(' · ')}</div>` : ''}${stickers}</td>
      <td class="col-cat">${esc(item.category_label)}</td>
      <td class="num" data-v="${item.quantity}">${qty}</td>
      <td class="num" data-v="${v(item.unit_price)}">${money(item.unit_price)}${item.price_source === 'custom' ? '<span class="tag-custom">CUSTOM</span>' : ''}</td>
      <td data-v="${v(top[0]?.price)}">${cheapestHtml(top, state.currency)}</td>
      <td class="num" data-v="${item.sticker_premium || ''}">${stickerCell}</td>
      <td class="num strong" data-v="${v(valueNumber)}">${value}</td>
      <td class="num" data-v="${v(item.payout_value)}">${item.payout_value !== null
        ? `${money(item.payout_value)}<div class="sub">via ${esc(item.payout_market_label)}</div>`
        : `<span class="muted" title="${item.quotes.some((q) => q.market === 'steam') ? 'Only Steam has a price - Steam Wallet money can\'t be withdrawn' : 'No price yet'}">—</span>`}</td>
      <td class="num" data-v="${v(item.change_24h)}">${deltaHtml(item.change_24h)}</td>
      <td class="num" data-v="${v(item.change_7w)}">${deltaHtml(item.change_7w)}</td>
      <td><div class="row-actions-cell">${actions.join('')}</div></td>
    </tr>`;
  }

  renderHeadline() {
    const r = this.refs;
    if (!this.summary) return;
    r.chartValue.textContent = money(this.summary.total_value);
    const h = this.history;
    if (h && h.values.length > 1) {
      const first = h.values.find((x) => x !== null && x > 0);
      const last = this.summary.total_value;
      const label = { '7d': '7 days', '30d': '30 days', '90d': '90 days', '1y': '1 year', all: 'all time' }[this.state.range];
      r.chartDelta.innerHTML = first ? `${deltaHtml(((last - first) / first) * 100)} <span class="muted small">${esc(label)}</span>` : '';
    } else {
      r.chartDelta.innerHTML = '';
    }
  }

  async loadChart() {
    this.updateControls();
    const r = this.refs;
    r.chartCard.classList.add('is-loading');
    try {
      this.history = await api('/history/portfolio', { params: { scope: this.scope, range: this.state.range } });
      const h = this.history;
      const has = h.labels.length > 0;
      r.chartEmpty.hidden = has && chartsAvailable();
      if (!chartsAvailable()) {
        r.chartEmpty.textContent = 'Charts could not load (Chart.js is fetched from cdn.jsdelivr.net - check your internet connection).';
      } else if (!has) {
        r.chartEmpty.textContent = 'No history yet. A snapshot is saved after every price refresh and once a day while the app is running.';
      }
      this.chart = renderLineChart(this.chart, r.chart, {
        labels: h.labels,
        series: [{ key: 'valuation', label: r.chartTitle.textContent, values: h.values, fill: true }],
        currency: h.currency,
      });
      this.renderHeadline();
      this.renderChartTable();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      r.chartCard.classList.remove('is-loading');
    }
  }

  renderChartTable() {
    const r = this.refs;
    if (!r.tableDetails.open || !this.history) return;
    r.chartTable.innerHTML = dataTableHtml(this.history.labels, [{ label: 'Value', values: this.history.values }],
      this.history.currency, `chart-${this.scope}`);
    applySortWithin(r.chartTable);
  }
}
