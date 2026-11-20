// "Profit & loss" tab: items bought and sold, priced where you gave a price (see backend/ledger.py).
// Three ways to look at it: actual profit (sales with both prices), money gained (everything you
// sold for, with a price) and money spent (every purchase with a price).

import { api, exportUrl } from './api.js';
import { chartsAvailable, dataTableHtml, renderLineChart } from './charts.js';
import { confirmDialog, openItem, showInItemWindow, suggestNames } from './dialogs.js';
import { markClean, protectForm } from './guard.js';
import { collapseOut, steady } from './motion.js';
import { applySortWithin } from './sortable.js';
import { doneToast } from './undo.js';
import { deltaHtml, esc, longDate, money, number, store, thumbHtml, toast } from './util.js';

const MODES = {
  profit: { label: 'Actual profit', chart: 'Actual profit so far' },
  gained: { label: 'Money gained', chart: 'Money gained so far' },
  spent: { label: 'Money spent', chart: 'Money spent so far' },
};

const signed = (value, currency) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${money(Math.abs(value), currency)}`;
const tone = (value) => (value > 0 ? 'up' : value < 0 ? 'down' : '');

export class PnlView {
  constructor(root, app) {
    this.root = root;
    this.app = app;
    this.loaded = false;
    this.data = null;
    this.chart = null;
    this.mode = MODES[store.get('pnl:mode', 'profit')] ? store.get('pnl:mode', 'profit') : 'profit';
    this.filter = { kind: '', q: '' };
    const $ = (sel) => root.querySelector(sel);
    this.el = {
      modes: $('#pnl-mode'), figure: $('#pnl-figure'), explain: $('#pnl-explain'), chart: $('#pnl-chart'),
      chartEmpty: $('#pnl-chart-empty'), stats: $('#pnl-stats'), groups: $('#pnl-groups'), best: $('#pnl-best'),
      table: $('#pnl-table'), kinds: $('#pnl-kinds'), search: $('#pnl-search'), details: $('#pnl-chart-details'),
      chartTable: $('#pnl-chart-table'),
    };
    this.el.modes.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-mode]');
      if (!button) return;
      this.mode = button.dataset.mode;
      store.set('pnl:mode', this.mode);
      this.render();
    });
    this.el.kinds.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-kind]');
      if (!chip) return;
      this.filter.kind = chip.dataset.kind;
      this.renderTable();
    });
    this.el.search.addEventListener('input', () => { this.filter.q = this.el.search.value.trim().toLowerCase(); this.renderTable(); });
    this.el.details.addEventListener('toggle', () => this.renderChartTable());
    root.querySelector('#pnl-export').href = exportUrl('ledger');
    root.querySelector('#pnl-add-in').addEventListener('click', () => openEntry({ kind: 'in' }, () => this.reload()));
    root.querySelector('#pnl-add-out').addEventListener('click', () => openEntry({ kind: 'out' }, () => this.reload()));
    this.el.table.addEventListener('click', (event) => {
      const button = event.target.closest('[data-act]');
      const row = event.target.closest('tr[data-id]');
      const entry = this.data.entries.find((e) => String(e.id) === (button || row)?.dataset.id);
      if (!entry) return;
      if (button?.dataset.act === 'edit') openEntry(entry, () => this.reload());
      else if (button?.dataset.act === 'delete') this.remove(entry);
      else if (!button && !event.target.closest('a')) this.show(entry);   // anywhere else on the row: its details
    });
    this.el.table.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('tr[data-id]')) return;
      const entry = this.data.entries.find((e) => String(e.id) === event.target.dataset.id);
      if (entry) this.show(entry);
    });
    // Best and worst sales open the same view.
    this.el.best.addEventListener('click', (event) => {
      const button = event.target.closest('[data-id]');
      const entry = button && this.data.entries.find((e) => String(e.id) === button.dataset.id);
      if (entry) this.show(entry);
    });
  }

  async reload() {
    try {
      this.data = await api('/ledger');
      this.loaded = true;
      this.render();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  render() {
    const d = this.data;
    if (!d) return;
    const cur = d.currency;
    const t = d.totals;
    this.el.modes.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.mode === this.mode));

    // The headline figure for the chosen view, and what it's made of.
    const figure = t[this.mode].value;
    this.el.figure.className = `pnl-figure ${this.mode === 'spent' ? '' : tone(figure)}`;
    this.el.figure.textContent = this.mode === 'spent' ? money(figure, cur) : signed(figure, cur);
    const items = (n) => `${number(n)} item${n === 1 ? '' : 's'}`;
    this.el.explain.innerHTML = {
      profit: t.profit.items
        ? `From ${items(t.profit.items)} sold where you gave both what you paid (${money(t.profit.cost, cur)}) and what you got
           (${money(t.profit.sold_for, cur)})${t.profit.margin !== null ? ` · ${t.profit.margin >= 0 ? '+' : '−'}${Math.abs(t.profit.margin).toFixed(1)}% on what you paid` : ''}.`
        : 'Nothing yet: this counts items sold where you gave both what you paid and what you got.',
      gained: t.gained.items
        ? `Everything you've sold for: ${items(t.gained.items)} sold with a price, whatever you paid for them.`
        : 'Nothing yet: this adds up everything you sell with a price, whatever you paid.',
      spent: t.spent.items
        ? `On ${items(t.spent.items)} bought with a price.`
        : 'Nothing yet: this counts every item bought with a price.',
    }[this.mode];
    steady(this.el.explain);

    this.renderChart();
    this.renderStats();
    this.renderGroups();
    this.renderBest();
    this.renderTable();
  }

  /** The chosen figure, added up over time. */
  series() {
    const rows = [...this.data.entries].reverse();      // oldest first
    const key = this.mode === 'spent' ? 'spent' : this.mode;
    const days = new Map();
    let running = 0;
    for (const e of rows) {
      const step = key === 'spent' ? (e.kind === 'in' ? e.total : null) : e[key];
      if (step === null || step === undefined) continue;
      running += step;
      days.set(e.happened_at.slice(0, 10), running);
    }
    return { labels: [...days.keys()], values: [...days.values()].map((v) => Math.round(v * 100) / 100) };
  }

  renderChart() {
    const { labels, values } = this.series();
    const has = labels.length > 0;
    this.el.chartEmpty.hidden = has && chartsAvailable();
    this.el.chartEmpty.textContent = chartsAvailable() ? 'The chart starts with your first priced entry.' : 'Charts could not load.';
    this.chart = renderLineChart(this.chart, this.el.chart, {
      labels, series: [{ key: 'valuation', label: MODES[this.mode].chart, values, fill: true }], currency: this.data.currency,
    });
    this.renderChartTable();
  }

  renderChartTable() {
    if (!this.el.details.open || !this.data) return;
    const { labels, values } = this.series();
    this.el.chartTable.innerHTML = dataTableHtml(labels, [{ label: MODES[this.mode].chart, values }], this.data.currency, 'pnl-chart');
    applySortWithin(this.el.chartTable);
  }

  renderStats() {
    const { counts: c, totals: t, holding: h, currency: cur } = this.data;
    const tile = (label, value, foot) => `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    this.el.stats.innerHTML = [
      tile('Bought', number(c.in), `<span>${number(c.in_priced)} with a price · ${money(t.spent.value, cur)}</span>`),
      tile('Sold', number(c.out), `<span>${number(c.out_priced)} with a price · ${money(t.gained.sold_for, cur)}</span>`),
      tile('Actual profit', `<span class="${tone(t.profit.value)}">${signed(t.profit.value, cur)}</span>`,
        `<span>${number(t.profit.items)} sold with both prices</span>`),
      tile('Money gained', `<span class="${tone(t.gained.value)}">${signed(t.gained.value, cur)}</span>`,
        `<span>everything sold with a price</span>`),
      tile('Still holding', h ? `<span class="${tone(h.profit)}">${signed(h.profit, cur)}</span>` : '—',
        h ? `<span>if sold now · items you paid ${money(h.cost, cur)} for</span>` : '<span>items you hold with a purchase price</span>'),
    ].join('');
  }

  renderGroups() {
    const { by_group: groups, currency: cur } = this.data;
    this.el.groups.innerHTML = groups.length ? `<div class="table-scroll"><table class="data" data-sort-id="pnl-groups">
      <thead><tr><th data-sort="text" data-key="g">Kind</th><th class="num" data-sort="num" data-key="b">Bought</th>
        <th class="num" data-sort="num" data-key="s">Spent</th><th class="num" data-sort="num" data-key="o">Sold</th>
        <th class="num" data-sort="num" data-key="g2">Gained</th><th class="num" data-sort="num" data-key="p">Actual profit</th></tr></thead>
      <tbody>${groups.map((g) => `<tr><td data-v="${esc(g.group)}">${esc(g.group)}</td>
        <td class="num" data-v="${g.bought}">${number(g.bought)}</td><td class="num" data-v="${g.spent}">${money(g.spent, cur)}</td>
        <td class="num" data-v="${g.sold}">${number(g.sold)}</td>
        <td class="num ${tone(g.gained)}" data-v="${g.gained}">${signed(g.gained, cur)}</td>
        <td class="num ${tone(g.profit)}" data-v="${g.profit}">${signed(g.profit, cur)}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted small">Nothing bought or sold yet.</p>';
    applySortWithin(this.el.groups);
  }

  renderBest() {
    const { best, worst, currency: cur } = this.data;
    const list = (rows, title, empty) => `<div class="mover-list"><h3>${title}</h3>${rows.length ? `<ol>${rows.map((e) => `
      <li><button type="button" class="mover" data-id="${e.id}" title="Show details">${thumbHtml({ icon: e.icon, category: 'other' })}
        <span class="mover-name">${esc(e.name)}${e.quantity > 1 ? ` <span class="muted small">×${number(e.quantity)}</span>` : ''}</span>
        <span class="mover-change"><span class="${tone(e.profit)}">${signed(e.profit, cur)}</span>
          <span class="sub">${money(e.cost, cur)} → ${money(e.price, cur)}</span></span></button></li>`).join('')}</ol>`
      : `<p class="muted small">${empty}</p>`}</div>`;
    this.el.best.innerHTML = list(best, 'Best sales', 'No profitable sales with both prices yet.')
      + list(worst, 'Worst sales', 'No sales at a loss.');
  }

  renderTable() {
    const { entries, currency: cur } = this.data;
    const { kind, q } = this.filter;
    this.el.kinds.querySelectorAll('[data-kind]').forEach((chip) => chip.setAttribute('aria-pressed', String(chip.dataset.kind === kind)));
    const shown = entries.filter((e) => (!kind || e.kind === kind) && (!q || e.name.toLowerCase().includes(q)));
    const priceCell = (value) => (value === null ? '<span class="muted small" title="No price given">—</span>' : money(value, cur));
    this.el.table.innerHTML = shown.length ? `<div class="table-scroll pnl-scroll"><table class="data" data-sort-id="pnl-entries">
      <thead><tr><th data-sort="text" data-key="date">Date</th><th data-sort="text" data-key="kind">What</th>
        <th class="col-icon"><span class="sr-only">Image</span></th><th data-sort="text" data-key="name">Item</th>
        <th class="num" data-sort="num" data-key="qty">Qty</th><th class="num" data-sort="num" data-key="price">Price each</th>
        <th class="num" data-sort="num" data-key="total">Total</th><th class="num" data-sort="num" data-key="cost">Paid each</th>
        <th class="num" data-sort="num" data-key="profit">Profit</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${shown.map((e) => `<tr class="row" tabindex="0" data-id="${e.id}" title="Show details">
        <td data-v="${esc(e.happened_at)}">${esc(longDate(e.happened_at))}</td>
        <td data-v="${e.kind}"><span class="badge ${e.kind === 'in' ? 'badge-steam' : 'badge-best'}">${e.kind === 'in' ? 'Bought' : 'Sold'}</span></td>
        <td>${thumbHtml({ icon: e.icon, category: 'other' })}</td>
        <td data-v="${esc(e.name)}">${esc(e.name)}<div class="item-meta">${esc(e.group)}${e.source === 'steam' ? ' · noticed by a Steam sync' : ''}</div></td>
        <td class="num" data-v="${e.quantity}">${number(e.quantity)}</td>
        <td class="num" data-v="${e.price ?? ''}">${priceCell(e.price)}</td>
        <td class="num" data-v="${e.total ?? ''}">${priceCell(e.total)}</td>
        <td class="num" data-v="${e.cost ?? ''}">${e.kind === 'out' ? priceCell(e.cost) : ''}</td>
        <td class="num ${tone(e.profit ?? 0)}" data-v="${e.profit ?? ''}">${e.kind === 'out'
          ? (e.profit !== null ? signed(e.profit, cur) : '<span class="muted small" title="Needs both what you paid and what you got">—</span>') : ''}</td>
        <td><div class="row-actions-cell">
          <button type="button" class="link-btn small" data-act="edit" data-id="${e.id}">Edit</button>
          <button type="button" class="link-btn small danger-text" data-act="delete" data-id="${e.id}">Delete</button></div></td>
      </tr>`).join('')}</tbody></table></div>
      <p class="hint">Profit is shown where a sale has both what you paid and what you got; Edit an entry to add a missing price.</p>`
      : `<div class="empty"><strong>Nothing here yet</strong>Items you buy or sell show up here: confirm them on the Containers
          tab after a Steam sync, give a purchase price when you add an item, or record one with the buttons above.</div>`;
    applySortWithin(this.el.table);
  }

  /** An entry's details. A purchase of something you still hold opens that item's full details, with
   *  what you paid first; a sale (or a purchase of something you no longer have) shows the deal itself,
   *  without market prices - there's nothing left to sell. */
  async show(entry) {
    const history = this.data.entries.filter((e) => e.market_hash_name === entry.market_hash_name);
    const cur = this.data.currency;
    if (entry.kind === 'in') {
      showInItemWindow(entry.name, '<p class="muted item-loading">Loading…</p>', '');
      let item = null;
      try {
        ({ item } = await api('/item', { params: { name: entry.market_hash_name, scope: 'total', optional: 1 } }));
      } catch (error) {
        toast(error.message, 'error');
        return;
      }
      if (item) {
        await openItem(item, {
          purchase: {
            html: purchasePanelHtml(entry, item, history, cur),
            onAct: (act) => this.entryAction(act, entry),
          },
        });
        return;
      }
    }
    const body = showInItemWindow(entry.name, dealHtml(entry, history, cur), `
      <button type="button" class="btn btn-danger" data-act="entry-delete">Delete entry</button>
      <span class="spacer"></span>
      <button type="button" class="btn btn-primary" data-act="entry-edit">Edit entry</button>`);
    applySortWithin(body);
    const act = (event) => {
      const button = event.target.closest('[data-act]');
      if (button) this.entryAction(button.dataset.act, entry);
    };
    body.onclick = act;
    document.getElementById('item-actions').onclick = act;
  }

  entryAction(act, entry) {
    if (act === 'entry-edit') {
      document.getElementById('item-dialog').close();
      openEntry(entry, () => this.reload());
    } else if (act === 'entry-delete') {
      document.getElementById('item-dialog').close();
      this.remove(entry);
    }
  }

  async remove(entry) {
    const ok = await confirmDialog({
      title: 'Delete this entry?',
      message: `${entry.kind === 'in' ? 'Bought' : 'Sold'}: ${number(entry.quantity)} × ${entry.name}. Only this record goes - your items don't change. You can undo it straight after.`,
      buttons: [{ label: 'Cancel', value: null }, { label: 'Delete', value: true, kind: 'danger' }],
    });
    if (!ok) return;
    try {
      const result = await api(`/ledger/${entry.id}`, { method: 'DELETE' });
      await collapseOut(this.root.querySelector(`tr.row[data-id="${entry.id}"]`));   // slides away first
      doneToast(`Deleted the entry for ${number(entry.quantity)} × ${entry.name}.`, result.undo);
      await this.reload();
    } catch (error) {
      toast(error.message, 'error');
    }
  }
}

// ---------------------------------------------------------------------------
// An entry's details
// ---------------------------------------------------------------------------
const figure = (label, value, sub = '', cls = '') => `<div class="deal-figure${cls ? ` ${cls}` : ''}">
  <span class="deal-label">${label}</span><span class="deal-value">${value}</span>${sub ? `<span class="deal-sub">${sub}</span>` : ''}</div>`;

function source(entry) {
  return entry.source === 'steam' ? 'noticed by a Steam sync' : 'added by hand';
}

/** Every entry for this item, newest first (the one you opened marked). */
function historyHtml(entry, history, cur) {
  if (history.length < 2) return '';
  return `<div class="panel"><h4>Your history with this item</h4>
    <div class="table-scroll"><table class="data" data-sort-id="pnl-item-history"><thead><tr>
      <th data-sort="text" data-key="date">Date</th><th data-sort="text" data-key="kind">What</th>
      <th class="num" data-sort="num" data-key="qty">Qty</th><th class="num" data-sort="num" data-key="price">Price each</th>
      <th class="num" data-sort="num" data-key="profit">Profit</th></tr></thead>
    <tbody>${history.map((e, i) => `<tr data-i="${i}"${e.id === entry.id ? ' class="this-entry"' : ''}>
      <td data-v="${esc(e.happened_at)}">${esc(longDate(e.happened_at))}${e.id === entry.id ? ' <span class="muted small">(this one)</span>' : ''}</td>
      <td data-v="${e.kind}"><span class="badge ${e.kind === 'in' ? 'badge-steam' : 'badge-best'}">${e.kind === 'in' ? 'Bought' : 'Sold'}</span></td>
      <td class="num" data-v="${e.quantity}">${number(e.quantity)}</td>
      <td class="num" data-v="${e.price ?? ''}">${e.price === null ? '<span class="muted">—</span>' : money(e.price, cur)}</td>
      <td class="num ${tone(e.profit ?? 0)}" data-v="${e.profit ?? ''}">${e.kind === 'out' && e.profit !== null ? signed(e.profit, cur) : ''}</td></tr>`).join('')}
    </tbody></table></div></div>`;
}

/** A purchase of something you still hold: what you paid, what it's worth now, and the difference -
 *  shown first in its details (the market data follows as usual). */
function purchasePanelHtml(entry, item, history, cur) {
  const qty = entry.quantity;
  const paid = entry.price;
  const now = item.wallet_only ? null : item.unit_value;
  const diff = paid !== null && now !== null ? (now - paid) * qty : null;
  const pct = paid ? ((now - paid) / paid) * 100 : null;
  return `<div class="panel deal-panel">
      <h4>Bought ${esc(longDate(entry.happened_at))} <span class="muted small">· ${esc(source(entry))}</span></h4>
      <div class="deal-figures">
        ${figure('You paid', paid !== null ? money(paid, cur) : '<span class="muted">not recorded</span>',
          paid !== null ? `each${qty > 1 ? ` · ${number(qty)} for ${money(paid * qty, cur)}` : ''}` : `<button type="button" class="link-btn" data-act="entry-edit">add what you paid</button>`, 'lead')}
        ${figure('Worth now', now !== null ? money(now, cur) : '<span class="muted">—</span>',
          now !== null ? `each, after fees${item.sell_market_label ? ` on ${esc(item.sell_market_label)}` : ''}` : 'no price that counts yet')}
        ${figure(qty > 1 ? `If you sold all ${number(qty)} now` : 'If you sold it now', diff !== null ? `<span class="${tone(diff)}">${signed(diff, cur)}</span>` : '<span class="muted">—</span>',
          diff !== null ? `${deltaHtml(pct)} on what you paid` : 'needs both prices')}
      </div>
      <p class="hint">From Profit &amp; Loss. <button type="button" class="link-btn" data-act="entry-edit">Edit this entry</button></p>
    </div>
    ${historyHtml(entry, history, cur)}`;
}

/** A sale, or a purchase of something you no longer have: the deal itself, no market prices. */
function dealHtml(entry, history, cur) {
  const qty = entry.quantity;
  const sold = entry.kind === 'out';
  const badges = [
    `<span class="badge ${sold ? 'badge-best' : 'badge-steam'}">${sold ? 'Sold' : 'Bought'}</span>`,
    `<span class="badge">${esc(entry.group)}</span>`,
    `<span class="badge">${esc(source(entry))}</span>`,
  ].join('');
  const pct = sold && entry.price !== null && entry.cost ? ((entry.price - entry.cost) / entry.cost) * 100 : null;
  const figures = sold ? [
    figure('Sold for', entry.price !== null ? money(entry.price, cur) : '<span class="muted">not recorded</span>',
      entry.price !== null ? `each${qty > 1 ? ` · ${number(qty)} for ${money(entry.total, cur)}` : ''}` : '<button type="button" class="link-btn" data-act="entry-edit">add what you got</button>', 'lead'),
    figure('You\u2019d paid', entry.cost !== null ? money(entry.cost, cur) : '<span class="muted">not recorded</span>',
      entry.cost !== null ? `each${qty > 1 ? ` · ${money(entry.cost * qty, cur)} in all` : ''}` : '<button type="button" class="link-btn" data-act="entry-edit">add what you paid</button>'),
    figure('Profit', entry.profit !== null ? `<span class="${tone(entry.profit)}">${signed(entry.profit, cur)}</span>` : '<span class="muted">—</span>',
      entry.profit !== null ? `${deltaHtml(pct)} on what you paid` : 'needs both prices'),
  ] : [
    figure('You paid', entry.price !== null ? money(entry.price, cur) : '<span class="muted">not recorded</span>',
      entry.price !== null ? `each${qty > 1 ? ` · ${number(qty)} for ${money(entry.total, cur)}` : ''}` : '<button type="button" class="link-btn" data-act="entry-edit">add what you paid</button>', 'lead'),
  ];
  return `<div class="detail-head">${thumbHtml({ icon: entry.icon, category: 'other' }, 'thumb-lg')}
      <div><h3>${esc(entry.name)}</h3><div class="badges">${badges}</div></div></div>
    <div class="panel deal-panel">
      <h4>${sold ? 'Sold' : 'Bought'} ${esc(longDate(entry.happened_at))}${qty > 1 ? ` <span class="muted small">· ${number(qty)} of them</span>` : ''}</h4>
      <div class="deal-figures">${figures.join('')}</div>
      <p class="hint">${sold ? 'Already sold, so market prices aren\u2019t shown.'
        : 'No longer in your inventory (it left without a recorded sale), so market prices aren\u2019t shown.'}</p>
    </div>
    ${historyHtml(entry, history, cur)}`;
}

// ---------------------------------------------------------------------------
// Recording or editing an entry
// ---------------------------------------------------------------------------
let entryReady = false;

function openEntry(entry, done) {
  const dialog = document.getElementById('pnl-dialog');
  const form = document.getElementById('pnl-form');
  if (!entryReady) {
    entryReady = true;
    protectForm(dialog, 'You have unsaved changes - press Save, or Cancel to discard them.');
    form.elements.name.addEventListener('input', () => suggestNames(form.elements.name, 'name-suggestions'));
  }
  const editing = Boolean(entry.id);
  const out = entry.kind === 'out';
  document.getElementById('pnl-title').textContent = `${editing ? 'Edit' : 'Record'} ${out ? 'a sale' : 'a purchase'}`;
  form.reset();
  form.elements.name.value = entry.name || '';
  form.elements.name.disabled = editing;
  form.elements.quantity.value = entry.quantity || 1;
  form.elements.price.value = entry.price ?? '';
  form.elements.cost.value = entry.cost ?? '';
  form.elements.date.value = (entry.happened_at || new Date().toISOString()).slice(0, 10);
  document.getElementById('pnl-price-label').textContent = out ? 'Sold for (each)' : 'Paid (each)';
  document.getElementById('pnl-cost-field').hidden = !out;
  const error = document.getElementById('pnl-error');
  error.hidden = true;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const body = {
      kind: entry.kind, name: form.elements.name.value.trim(), quantity: form.elements.quantity.value,
      price: form.elements.price.value === '' ? null : form.elements.price.value,
      cost: form.elements.cost.value === '' ? null : form.elements.cost.value, date: form.elements.date.value,
    };
    try {
      const result = editing ? await api(`/ledger/${entry.id}`, { method: 'PUT', body }) : await api('/ledger', { method: 'POST', body });
      dialog.close();
      if (editing) doneToast('Entry updated.', result.undo);
      else toast('Recorded.', 'ok');
      await done();
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  };
  markClean(dialog);
  dialog.showModal();
  (editing ? form.elements.price : form.elements.name).focus();
}
