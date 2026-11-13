// Inventory tab (used for both "Public Inventory" and "Total Portfolio").

import { api, exportUrl } from './api.js';
import { MIX_COLORS, chartsAvailable, dataTableHtml, renderLineChart, renderStackedBars } from './charts.js';
import * as containers from './containers.js';
import * as dialogs from './dialogs.js';
import { MarketFilter, availableMarkets, bestToSell, bulkNote, noCashLabel, sellHtml } from './market-filter.js';
import { applySort, applySortWithin } from './sortable.js';
import {
  CATEGORY_FILTERS, debounce, deltaHtml, esc, floatText, isStorage, money, number, state, store, thumbHtml, toast,
} from './util.js';

/** % change along daily values, leaving out each day the number of items changed: adding 50
 *  capsules isn't a gain. With nothing added or removed it's simply last / first. Same rule as the
 *  server's 24h and 7-week figures (history.chained_change). */
export function chainedChange(values, counts = []) {
  let growth = 1;
  let used = false;
  let skipped = false;
  for (let i = 1; i < values.length; i += 1) {
    const before = values[i - 1];
    const after = values[i];
    if (before === null || after === null || !(before > 0)) continue;
    if ((counts[i - 1] ?? null) !== (counts[i] ?? null)) { skipped = true; continue; }
    growth *= after / before;
    used = true;
  }
  return { pct: used ? (growth - 1) * 100 : null, skipped };
}

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
      categories: [], q: '', stack: true, range: '90d', showContents: true, groupBy: '', groupOnly: '',
      moverWindow: 'change_24h',
      ...store.get(`view:${scope}`, {}),
    };
    this.collapsed = new Set();
    this.mixChart = null;
    this.state.q = '';
    this.setup();
  }

  persist() {
    const { categories, stack, range, showContents, groupBy, groupOnly, moverWindow } = this.state;
    store.set(`view:${this.scope}`, { categories, stack, range, showContents, groupBy, groupOnly, moverWindow });
  }

  /** Stored items are listed alongside everything else (Total tab, toggle on). */
  get listingContents() {
    return this.scope === 'total' && this.state.showContents;
  }

  setup() {
    const r = this.refs;
    const isPublic = this.scope === 'public';
    r.chartTitle.textContent = isPublic ? 'Public inventory value after fees' : 'Total portfolio value after fees';
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
    r.export.href = exportUrl(this.scope);

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

    // Market chips: which markets the "Best 3 to sell" column compares.
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
      this.loadInsights();
    });

    // Group by anything: event, collection, type, weapon, rarity, wear or container.
    r.groupBy.value = this.state.groupBy;
    r.groupBy.addEventListener('change', () => {
      this.state.groupBy = r.groupBy.value;
      this.state.groupOnly = '';
      this.collapsed.clear();
      this.persist();
      this.renderTable();
    });
    r.groupOnly.addEventListener('change', () => {
      this.state.groupOnly = r.groupOnly.value;
      this.persist();
      this.renderTable();
    });
    r.moverWindow.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-window]');
      if (!button) return;
      this.state.moverWindow = button.dataset.window;
      this.persist();
      this.renderInsights();
    });
    r.movers.addEventListener('click', (event) => {
      const row = event.target.closest('[data-name]');
      const item = row && this.items.find((i) => i.market_hash_name === row.dataset.name);
      if (item) dialogs.openItem(item);
    });
    r.mixDetails.addEventListener('toggle', () => this.renderMixTable());

    r.tableDetails.addEventListener('toggle', () => this.renderChartTable());

    r.table.addEventListener('click', (event) => {
      const toggle = event.target.closest('.group-toggle');
      if (toggle) {
        const key = toggle.dataset.group;
        if (this.collapsed.has(key)) this.collapsed.delete(key); else this.collapsed.add(key);
        toggle.closest('tbody').classList.toggle('collapsed', this.collapsed.has(key));
        toggle.setAttribute('aria-expanded', String(!this.collapsed.has(key)));
        return;
      }
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
    r.table.addEventListener('keydown', (event) => {
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
    await Promise.all([this.load(), this.loadChart(), this.loadInsights()]);
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
      this.renderChart();
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
      const stackable = !isStorage(item) && !item.stickers.length && !item.name_tag
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
        if (item.market_total_value !== null) group.market_total_value = (group.market_total_value || 0) + item.market_total_value;
        if (item.profit !== null) group.profit = (group.profit || 0) + item.profit;
        if (item.purchase_value !== null) group.purchase_value = (group.purchase_value || 0) + item.purchase_value;
      } else {
        const copy = { ...item, stack_ids: [item.id] };
        groups.set(key, copy);
        out.push(copy);
      }
    }
    return out;
  }

  /** The group an item falls in, for the "Group by" choice. */
  groupOf(item) {
    const by = this.state.groupBy;
    if (by === 'exterior') return item.exterior || 'No wear';
    if (by === 'container') return item.container_name || (isStorage(item) ? 'Containers' : 'Not in a container');
    const value = item.tags?.[by];
    return value || { event: 'No event', collection: 'No collection', weapon: 'Not a weapon', rarity: 'Unknown rarity' }[by] || 'Other';
  }

  /** Value as shown in the Value column (a container's own value when its contents are listed too). */
  shownValue(item) {
    return item.is_container && item.contents_count && this.listingContents ? item.total_value : item.display_value;
  }

  renderTable() {
    const r = this.refs;
    let rows = this.rows();
    this.rowItems = new Map();
    r.table.querySelectorAll('tbody.group-body').forEach((body) => body.remove());
    const by = this.state.groupBy;
    r.groupOnly.hidden = !by;
    if (!by) {
      r.tbody.hidden = false;
      r.tbody.innerHTML = rows.map((item, i) => {
        const key = `${item.id}-${i}`;
        this.rowItems.set(key, item);
        return this.rowHtml(item, key, i);
      }).join('');
    } else {
      // One <tbody> per group (most valuable first), each with a collapsible header and subtotal.
      const groups = new Map();
      rows.forEach((item) => {
        const name = this.groupOf(item);
        if (!groups.has(name)) groups.set(name, { name, items: [], value: 0, count: 0 });
        const group = groups.get(name);
        group.items.push(item);
        group.value += this.shownValue(item) || 0;
        group.count += item.quantity;
      });
      const ordered = [...groups.values()].sort((a, b) => b.value - a.value || a.name.localeCompare(b.name));
      const only = ordered.some((g) => g.name === this.state.groupOnly) ? this.state.groupOnly : '';
      r.groupOnly.innerHTML = `<option value="">All groups (${number(ordered.length)})</option>${ordered.map((g) => `<option value="${esc(g.name)}"${g.name === only ? ' selected' : ''}>${esc(g.name)}</option>`).join('')}`;
      r.tbody.hidden = true;
      r.tbody.innerHTML = '';
      const columns = r.table.querySelectorAll('thead th').length;
      let index = 0;
      rows = [];
      for (const group of ordered) {
        if (only && group.name !== only) continue;
        const body = document.createElement('tbody');
        body.className = `group-body${this.collapsed.has(group.name) ? ' collapsed' : ''}`;
        body.innerHTML = `<tr class="group-row"><th colspan="${columns}" scope="rowgroup">
            <button type="button" class="group-toggle" data-group="${esc(group.name)}" aria-expanded="${!this.collapsed.has(group.name)}">
              <span class="caret" aria-hidden="true">▾</span> ${esc(group.name)}</button>
            <span class="group-meta">${number(group.count)} item${group.count === 1 ? '' : 's'} · <strong>${money(group.value)}</strong></span>
          </th></tr>${group.items.map((item) => {
            const key = `${item.id}-${index}`;
            this.rowItems.set(key, item);
            const html = this.rowHtml(item, key, index);
            index += 1;
            return html;
          }).join('')}`;
        r.table.appendChild(body);
        rows.push(...group.items);
      }
    }
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
    } else {
      value += bulkNote(item);
    }
    const actions = [];
    if (isStorage(item) && item.container_id === null) {
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
    const top = bestToSell(item.sell_quotes, this.marketFilter);
    const v = (n) => (n === null || n === undefined ? '' : n);
    const best = item.sell_quotes[0];
    const basisNote = { sales: ' · recent sales', sticker_inside: ' · sticker inside' }[best?.basis] || '';
    const via = best
      ? `<div class="sub"${item.set_aside.length ? ` title="${number(item.set_aside.length)} unrealistic listing(s) ignored"` : ''}>via ${esc(item.sell_market_label)}${best.wallet ? ` · ${esc(noCashLabel(best.market, true))}` : ''}${basisNote}</div>`
      : '';
    return `<tr class="row" tabindex="0" data-key="${esc(key)}" data-i="${index}">
      <td>${thumbHtml(item)}</td>
      <td data-v="${esc(item.name)}"><div class="item-name">${esc(item.name)} ${sourceBadge}${stored}</div>
        ${meta.length ? `<div class="item-meta">${meta.join(' · ')}</div>` : ''}${stickers}</td>
      <td class="col-cat">${esc(item.category_label)}</td>
      <td class="num" data-v="${item.quantity}">${qty}</td>
      <td class="num" data-v="${v(item.unit_value)}">${money(item.unit_value)}${item.price_source === 'custom' ? '<span class="tag-custom">CUSTOM</span>' : ''}${via}</td>
      <td data-v="${v(top[0]?.net)}">${sellHtml(top, state.currency)}</td>
      <td class="num" data-v="${item.sticker_premium || ''}">${stickerCell}</td>
      <td class="num strong" data-v="${v(valueNumber)}">${value}</td>
      <td class="num" data-v="${v(item.change_24h)}">${deltaHtml(item.change_24h)}</td>
      <td class="num" data-v="${v(item.change_7w)}">${deltaHtml(item.change_7w)}</td>
      <td><div class="row-actions-cell">${actions.join('')}</div></td>
    </tr>`;
  }

  async loadInsights() {
    try {
      this.insights = await api('/insights', { params: { scope: this.scope, range: this.state.range } });
      this.renderInsights();
    } catch (error) {
      this.refs.movers.innerHTML = `<p class="muted small">${esc(error.message)}</p>`;
    }
  }

  renderInsights() {
    const r = this.refs;
    const data = this.insights;
    if (!data) return;
    const cur = data.currency;
    r.moverWindow.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.window === this.state.moverWindow));

    // Biggest movers: ranked lists, the money change carries an arrow and sign (never colour alone).
    const moved = data.movers?.[this.state.moverWindow] || { gainers: [], losers: [] };
    const list = (rows, title) => `<div class="mover-list"><h3>${title}</h3>${rows.length ? `<ol>${rows.map((m) => `
        <li><button type="button" class="mover" data-name="${esc(m.name)}" title="Show details">
          ${thumbHtml({ icon: m.icon, category: 'other' })}
          <span class="mover-name">${esc(m.name)}${m.quantity > 1 ? ` <span class="muted small">×${number(m.quantity)}</span>` : ''}</span>
          <span class="mover-change">${deltaHtml(m.pct)}<span class="sub">${m.change > 0 ? '+' : '−'}${money(Math.abs(m.change), cur)}</span></span>
        </button></li>`).join('')}</ol>` : '<p class="muted small">Nothing yet - changes appear once the app has a few days of prices.</p>'}</div>`;
    r.movers.innerHTML = list(moved.gainers, 'Up the most') + list(moved.losers, 'Down the most');

    // Portfolio mix now: a thin segmented bar above a list that carries the names and figures.
    const groups = data.mix?.groups || [];
    const total = groups.reduce((sum, g) => sum + g.value, 0);
    r.mixNow.innerHTML = total > 0 ? `
      <div class="mix-bar" role="img" aria-label="${esc(groups.map((g) => `${g.label} ${Math.round((g.value / total) * 100)}%`).join(', '))}">
        ${groups.map((g) => `<span style="flex-grow:${g.value};background:${MIX_COLORS[g.key]}" title="${esc(g.label)}: ${esc(money(g.value, cur))}"></span>`).join('')}
      </div>
      <ul class="mix-list">${groups.map((g) => `<li><span class="swatch" style="background:${MIX_COLORS[g.key]}"></span>
        <span class="mix-label">${esc(g.label)}</span><span class="num">${money(g.value, cur)}</span>
        <span class="num muted">${((g.value / total) * 100).toFixed(1)}%</span></li>`).join('')}</ul>`
      : '<p class="muted small">No priced items yet.</p>';

    // The split over time: stacked bars, with a table view.
    const hist = data.mix?.history || { labels: [], series: [] };
    const has = hist.labels.length > 0;
    r.mixEmpty.hidden = has && chartsAvailable();
    r.mixEmpty.textContent = chartsAvailable() ? 'History builds up as daily prices are saved.' : 'Charts could not load.';
    r.mixNote.hidden = !has;
    this.mixChart = renderStackedBars(this.mixChart, r.mixChart, { labels: hist.labels, series: hist.series, currency: cur });
    this.renderMixTable();
  }

  renderMixTable() {
    const r = this.refs;
    const hist = this.insights?.mix?.history;
    if (!r.mixDetails.open || !hist) return;
    r.mixTable.innerHTML = dataTableHtml(hist.labels, hist.series, this.insights.currency, `mix-${this.scope}`);
    applySortWithin(r.mixTable);
  }

  /** The saved daily values with today's point set to the value shown above the chart (it also
   *  counts anything changed since today's snapshot), so the headline and the line always agree. */
  chartSeries() {
    const h = this.history;
    if (!h || !this.summary || !h.today || !h.labels.length) return h;
    const now = Math.round(this.summary.total_value * 100) / 100;
    const labels = [...h.labels];
    const values = [...h.values];
    const counts = [...(h.counts || [])];
    const last = labels[labels.length - 1];
    if (last === h.today) {
      values[values.length - 1] = now;
      counts[counts.length - 1] = this.summary.item_count;
    } else if (last < h.today) {
      // Days with no snapshot carry the last value across, as the server does between snapshots.
      const day = new Date(`${last}T00:00:00Z`);
      for (;;) {
        day.setUTCDate(day.getUTCDate() + 1);
        const label = day.toISOString().slice(0, 10);
        if (label > h.today) break;
        labels.push(label);
        values.push(label === h.today ? now : values[values.length - 1]);
        counts.push(label === h.today ? this.summary.item_count : (counts[counts.length - 1] ?? null));
      }
    }
    return { ...h, labels, values, counts };
  }

  renderHeadline() {
    const r = this.refs;
    if (!this.summary) return;
    r.chartValue.textContent = money(this.summary.total_value);
    const h = this.chartSeries();
    const change = h ? chainedChange(h.values, h.counts) : null;
    if (change && change.pct !== null) {
      const label = { '7d': '7 days', '30d': '30 days', '90d': '90 days', '1y': '1 year', all: 'all time' }[this.state.range];
      const note = change.skipped
        ? ` <span class="muted small" title="Days you added or removed items aren't counted as gains or losses, so this is how prices moved for what you held.">· price moves only</span>`
        : '';
      r.chartDelta.innerHTML = `${deltaHtml(change.pct)} <span class="muted small">${esc(label)}</span>${note}`;
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
      const has = this.history.labels.length > 0;
      r.chartEmpty.hidden = has && chartsAvailable();
      if (!chartsAvailable()) {
        r.chartEmpty.textContent = 'Charts could not load (Chart.js is fetched from cdn.jsdelivr.net - check your internet connection).';
      } else if (!has) {
        r.chartEmpty.textContent = 'No history yet. A snapshot is saved after every price refresh and once a day while the app is running.';
      }
      this.renderChart();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      r.chartCard.classList.remove('is-loading');
    }
  }

  /** Draws the line, headline and table from the history plus the latest headline value. Runs when
   *  either arrives (they're fetched side by side) and whenever the items are reloaded. */
  renderChart() {
    const r = this.refs;
    const h = this.chartSeries();
    if (!h) { this.renderHeadline(); return; }
    this.chart = renderLineChart(this.chart, r.chart, {
      labels: h.labels,
      series: [{ key: 'valuation', label: r.chartTitle.textContent, values: h.values, fill: true }],
      currency: h.currency,
    });
    this.renderHeadline();
    this.renderChartTable();
  }

  renderChartTable() {
    const r = this.refs;
    const h = this.chartSeries();
    if (!r.tableDetails.open || !h) return;
    r.chartTable.innerHTML = dataTableHtml(h.labels, [{ label: 'Value', values: h.values }],
      h.currency, `chart-${this.scope}`);
    applySortWithin(r.chartTable);
  }
}
