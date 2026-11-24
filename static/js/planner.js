// "Sell Planner" tab: choose which items and markets to use, then an amount (the slider or the box),
// and the page works out which items to sell, how many and where (plan-calc.js) from what the server
// sends once (/api/sell-candidates). Items can be kept out of the plan ("Keep" on a row); "Kept items"
// above the plan lists them, each with Unkeep to bring it back.
//
// The slider always runs to everything you could sell on the chosen markets. The item filters (and
// kept items) colour the part they can raise, with a marker at its end; an amount beyond it stays as
// typed, and a note says the most those items can raise.

import { api } from './api.js';
import * as dialogs from './dialogs.js';
import { marketLink, noCashLabel } from './market-filter.js';
import { collapseOut, setBadge, steady } from './motion.js';
import { choose, prepare } from './plan-calc.js';
import { applySort } from './sortable.js';
import { pageDoneToast } from './undo.js';
import { CATEGORY_FILTERS, debounce, esc, groupOf, money, number, store, thumbHtml, toast } from './util.js';

const SNAP_PX = 8;             // dragging this close to the filters' maximum lands on it exactly
const CATEGORIES = CATEGORY_FILTERS.filter(([key]) => key !== 'storage');   // storage units aren't sold

export class PlannerView {
  constructor(root) {
    this.root = root;
    this.loaded = false;
    this.data = null;
    const saved = store.get('planner', {});
    this.keep = new Set(saved.keep || []);
    this.filters = {
      q: '',
      categories: Array.isArray(saved.categories) ? saved.categories : [],
      by: saved.filterBy || '',
      only: saved.filterOnly || '',
      stored: saved.stored !== false,
    };
    this.marketsOff = new Set(saved.marketsOff || []);
    const $ = (id) => root.querySelector(`#${id}`);
    this.el = {
      search: $('plan-search'), chips: $('plan-chips'), by: $('plan-filter-by'), only: $('plan-filter-only'),
      stored: $('plan-stored'), clear: $('plan-clear'), marketChips: $('plan-market-chips'), wallet: $('plan-wallet'),
      bar: $('plan-bar'), slider: $('plan-slider'), target: $('plan-target'), undercut: $('plan-undercut'), hold: $('plan-hold'),
      currency: $('plan-currency'), over: $('plan-over'), limit: $('plan-limit'), stats: $('plan-stats'),
      table: $('plan-table'), empty: $('plan-empty'), marketsCard: $('plan-markets-card'), markets: $('plan-markets'),
      kept: $('plan-kept'), keptBtn: $('plan-kept-btn'), keptCount: $('plan-kept-count'),
      keptDialog: document.getElementById('kept-dialog'), keptBody: document.getElementById('kept-body'),
      keptAll: document.getElementById('kept-all'),
    };
    const zones = root.querySelectorAll('.plan-zone');
    this.el.zones = { sel: zones[0], avail: zones[1], over: zones[2] };
    this.el.marker = root.querySelector('.plan-marker');

    this.el.wallet.checked = Boolean(saved.wallet);
    this.el.target.value = saved.target || 0;
    if ([...this.el.undercut.options].some((o) => Number(o.value) === saved.undercut)) {
      this.el.undercut.value = String(saved.undercut);   // a choice no longer offered: the default
    }
    if ([...this.el.hold.options].some((o) => Number(o.value) === saved.hold)) this.el.hold.value = String(saved.hold);
    this.el.by.value = this.filters.by;
    this.el.stored.checked = this.filters.stored;
    this.el.chips.innerHTML = `<button type="button" class="chip" data-cat="">All</button>${CATEGORIES
      .map(([key, label]) => `<button type="button" class="chip" data-cat="${key}">${esc(label)} <span class="count" data-count="${key}"></span></button>`)
      .join('')}`;

    // Item filters, markets and the undercut change what can be sold: work it all out again.
    const rework = (this.rework = () => { this.persist(); this.prepare(); this.render(); });
    this.el.search.addEventListener('input', debounce(() => { this.filters.q = this.el.search.value.trim(); rework(); }, 150));
    this.el.chips.addEventListener('click', (event) => {
      const chip = event.target.closest('.chip');
      if (!chip) return;
      const cat = chip.dataset.cat;
      const cats = this.filters.categories;
      if (!cat) this.filters.categories = [];
      else this.filters.categories = cats.includes(cat) ? cats.filter((c) => c !== cat) : [...cats, cat];
      rework();
    });
    this.el.by.addEventListener('change', () => { this.filters.by = this.el.by.value; this.filters.only = ''; rework(); });
    this.el.only.addEventListener('change', () => { this.filters.only = this.el.only.value; rework(); });
    this.el.stored.addEventListener('change', () => { this.filters.stored = this.el.stored.checked; rework(); });
    this.el.clear.addEventListener('click', () => {
      this.filters = { q: '', categories: [], by: '', only: '', stored: true };
      this.el.search.value = '';
      this.el.by.value = '';
      this.el.stored.checked = true;
      rework();
    });
    this.el.marketChips.addEventListener('click', (event) => {
      const chip = event.target.closest('.chip[data-market]');
      if (!chip || chip.disabled) return;
      const key = chip.dataset.market;
      if (this.marketsOff.has(key)) this.marketsOff.delete(key);
      else this.marketsOff.add(key);
      rework();
    });
    this.el.wallet.addEventListener('change', rework);
    this.el.undercut.addEventListener('change', rework);
    this.el.hold.addEventListener('change', rework);

    // The amount: the slider and the box show the same amount (the box can go past the slider's end).
    this.dragging = false;
    this.el.slider.addEventListener('pointerdown', () => { this.dragging = true; });
    window.addEventListener('pointerup', () => { this.dragging = false; });
    window.addEventListener('pointercancel', () => { this.dragging = false; });
    this.el.slider.addEventListener('input', () => {
      let value = Number(this.el.slider.value);
      const snapped = this.dragging ? this.snap(value) : null;
      if (snapped != null) {
        value = snapped;
        this.el.slider.value = String(snapped);   // the thumb sits on the marker
      }
      this.el.target.value = String(value);
      this.amountChanged(false);
    });
    this.el.target.addEventListener('input', () => this.amountChanged(true));

    this.el.table.addEventListener('click', (event) => {
      const keep = event.target.closest('[data-keep]');
      if (keep) {
        const ids = keep.dataset.keep.split(',').map(Number).filter((id) => !this.keep.has(id));
        const name = keep.closest('tr')?.dataset.view || 'That item';
        ids.forEach((id) => this.keep.add(id));
        rework();
        pageDoneToast(`${name} is kept out of the plan. "Kept items" above the plan lists it.`, () => {
          ids.forEach((id) => this.keep.delete(id));
          this.rework();
        });
        return;
      }
      if (event.target.closest('a')) return;            // a market link: just open it
      const view = event.target.closest('[data-view]');
      if (view) this.view(view.dataset.view);
    });
    this.el.table.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target.matches('tr[data-view]')) this.view(event.target.dataset.view);
    });
    // Kept items: a window listing them, each with Unkeep.
    this.el.keptBtn.addEventListener('click', () => this.openKept());
    this.el.keptBody.addEventListener('click', async (event) => {
      const button = event.target.closest('[data-unkeep]');
      if (!button || button.disabled) return;
      button.disabled = true;
      const lot = button.dataset.unkeep;
      await collapseOut(button.closest('li'));
      for (const row of this.data.rows) if (row.lot === lot) this.keep.delete(row.id);
      this.rework();
    });
    this.el.keptAll.addEventListener('click', () => {
      const before = [...this.keep];
      const count = this.keptItems().length;
      this.keep.clear();
      this.el.keptDialog.close();
      this.rework();
      pageDoneToast(`${number(count)} kept item${count === 1 ? ' is' : 's are'} back in the plan.`, () => {
        before.forEach((id) => this.keep.add(id));
        this.rework();
      });
    });
    this.savePlan = debounce(() => this.persist(), 300);
  }

  persist() {
    store.set('planner', {
      target: this.target(), undercut: Number(this.el.undercut.value), hold: Number(this.el.hold.value), keep: [...this.keep],
      categories: this.filters.categories, filterBy: this.filters.by, filterOnly: this.filters.only,
      stored: this.filters.stored, wallet: this.el.wallet.checked, marketsOff: [...this.marketsOff],
    });
  }

  view(name) {
    return dialogs.openItemByName(name);
  }

  target() {
    return Math.max(0, Number(this.el.target.value) || 0);
  }

  /** Fetch what to plan from (on first showing the tab, and after prices or the inventory change). */
  async reload() {
    const ask = (this.ask = (this.ask || 0) + 1);
    try {
      const data = await api('/sell-candidates');
      if (ask !== this.ask) return;
      this.data = data;
      this.loaded = true;
      this.prepare();
      this.render();
    } catch (error) {
      if (ask === this.ask) toast(error.message, 'error');
    }
  }

  /** Market keys you'll sell on: those switched on, and the ones money can't be withdrawn from only
   *  with the tick box on. */
  markets() {
    const wallet = this.el.wallet.checked;
    return new Set(this.data.markets.filter((m) => (m.cash_out || wallet) && !this.marketsOff.has(m.key)).map((m) => m.key));
  }

  /** Rows passing the item filters, except ``skip`` (one filter, for its own counts and choices). */
  filtered(skip = '') {
    const f = this.filters;
    const terms = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    return this.data.rows.filter((row) => {
      if (!f.stored && row.in_container && !f.categories.includes('in_container') && !terms.length) return false;
      if (skip !== 'categories' && f.categories.length) {
        const plain = f.categories.filter((c) => c !== 'in_container');
        if (!(plain.includes(row.category) || (f.categories.includes('in_container') && row.in_container))) return false;
      }
      if (terms.length && !terms.every((t) => row.search.includes(t))) return false;
      if (skip !== 'group' && f.by && f.only && groupOf(row, f.by) !== f.only) return false;
      return true;
    });
  }

  filtering() {
    const f = this.filters;
    return Boolean(f.q || f.categories.length || (f.by && f.only) || !f.stored);
  }

  /** Everything that doesn't depend on the amount: what the filtered items can raise, and everything. */
  prepare() {
    if (!this.data) return;
    const options = { markets: this.markets(), undercut: Number(this.el.undercut.value), keep: this.keep,
      holdBack: Number(this.el.hold.value) };
    this.picked = prepare(this.data, this.filtered(), options);
    this.everything = prepare(this.data, this.data.rows, { ...options, keep: new Set() });
  }

  /** The filters' maximum when it's short of the slider's end (else null: no marker). */
  limit() {
    const all = this.everything.max;
    return all > 0 && this.picked.max < all - 0.005 ? this.picked.max : null;
  }

  /** While dragging: the filters' maximum when the thumb is within a few pixels of its marker. */
  snap(value) {
    const limit = this.limit();
    if (limit == null) return null;
    const width = this.el.slider.getBoundingClientRect().width - 18;   // the thumb's travel
    const px = (Math.abs(value - limit) / Number(this.el.slider.max)) * width;
    return px <= SNAP_PX ? Math.round(limit * 100) / 100 : null;
  }

  amountChanged(typed) {
    if (!this.data) return;
    if (typed) this.el.slider.value = String(Math.min(this.target(), Number(this.el.slider.max)));
    this.savePlan();
    this.renderBar();
    this.renderPlan();
  }

  render() {
    if (!this.data) return;
    this.renderFilters();
    this.renderBar();
    this.renderPlan();
    this.renderKept();
  }

  /** The kept items still in the inventory, one entry per item (most recently kept first), with how
   *  many, where they're stored and what they'd fetch on the chosen markets. */
  keptItems() {
    if (!this.data || !this.keep.size) return [];
    const rows = new Map(this.data.rows.map((row) => [row.id, row]));
    const items = new Map();
    for (const id of [...this.keep].reverse()) {
      const row = rows.get(id);
      if (!row) continue;                       // sold or gone since it was kept
      let item = items.get(row.lot);
      if (!item) {
        const lot = this.data.lots[row.lot];
        item = { lot: row.lot, name: lot?.name || row.lot, icon: lot?.icon || null, rows: [] };
        items.set(row.lot, item);
      }
      item.rows.push(row);
    }
    if (!items.size) return [];
    const worth = prepare(this.data, [...items.values()].flatMap((item) => item.rows),
      { markets: this.markets(), undercut: Number(this.el.undercut.value) });
    return [...items.values()].map((item) => {
      const lot = worth.lots.get(item.lot);
      return { ...item, quantity: item.rows.reduce((sum, row) => sum + row.quantity, 0),
        value: lot?.steps ? lot.steps.reduce((sum, step) => sum + step.net * step.units, 0) : null,
        locked: item.rows.every((row) => row.locked) };
    });
  }

  /** The count on "Kept items", and the window's list when it's open. */
  renderKept() {
    const items = this.keptItems();
    setBadge(this.el.keptCount, items.length);
    if (this.el.keptDialog.open) this.renderKeptList(items);
  }

  openKept() {
    this.renderKeptList(this.keptItems());
    this.el.keptDialog.showModal();
  }

  renderKeptList(items) {
    const cur = this.data?.currency;
    this.el.keptAll.hidden = !items.length;
    if (!items.length) {
      this.el.keptBody.innerHTML = `<p class="muted">Nothing is kept out of the plan. Press <strong>Keep</strong> on a row of the
        plan and the plan never sells that item; it's listed here, so you can bring it back whenever you like.</p>`;
      return;
    }
    const where = (item) => {
      const boxes = [...new Set(item.rows.filter((row) => row.in_container).map((row) => row.container_name || 'a container'))];
      if (!boxes.length) return '';
      return ` · in ${boxes.join(', ')}${item.rows.some((row) => !row.in_container) ? ' and your inventory' : ''}`;
    };
    const worth = (item) => (item.value != null
      ? `you'd get ${money(item.value, cur)} for ${item.quantity === 1 ? 'it' : 'all of them'} on these markets`
      : item.locked ? 'trade-locked' : 'no price on the markets chosen');
    this.el.keptBody.innerHTML = `<p class="muted small">The plan never sells these. Unkeep one to let the plan use it again.
        Most recently kept first.</p>
      <ul class="kept-list">${items.map((item) => `<li class="kept-item">
        ${thumbHtml({ icon: item.icon, category: 'other' })}
        <div class="kept-what"><div class="item-name" title="${esc(item.name)}">${esc(item.name)}</div>
          <div class="sub">${number(item.quantity)} kept${esc(where(item))} · ${esc(worth(item))}</div></div>
        <button type="button" class="btn btn-small" data-unkeep="${esc(item.lot)}" title="Let the plan sell this again">Unkeep</button>
      </li>`).join('')}</ul>`;
  }

  renderFilters() {
    const f = this.filters;
    const d = this.data;
    // Category chips: pressed state and how many of each the other filters leave.
    const counts = {};
    for (const row of this.filtered('categories')) {
      counts[row.category] = (counts[row.category] || 0) + 1;
      if (row.in_container) counts.in_container = (counts.in_container || 0) + 1;
    }
    this.el.chips.querySelectorAll('.chip').forEach((chip) => {
      const cat = chip.dataset.cat;
      chip.setAttribute('aria-pressed', String(cat ? f.categories.includes(cat) : f.categories.length === 0));
    });
    for (const el of this.el.chips.querySelectorAll('[data-count]')) {
      const n = counts[el.dataset.count] || 0;
      el.textContent = n ? String(n) : '';
    }
    // "Filter by": the groups the other filters leave.
    this.el.only.hidden = !f.by;
    if (f.by) {
      const groups = new Map();
      for (const row of this.filtered('group')) {
        const name = groupOf(row, f.by);
        groups.set(name, (groups.get(name) || 0) + row.quantity);
      }
      const names = [...groups.keys()].sort((a, b) => a.localeCompare(b));
      if (f.only && !groups.has(f.only)) names.unshift(f.only);   // keep a saved choice visible
      this.el.only.innerHTML = `<option value="">All groups (${number(groups.size)})</option>${names
        .map((n) => `<option value="${esc(n)}"${n === f.only ? ' selected' : ''}>${esc(n)}${groups.has(n) ? ` (${number(groups.get(n))})` : ''}</option>`)
        .join('')}`;
    }
    this.el.clear.hidden = !this.filtering();
    // Markets: on/off chips (built once per list of markets, so a pressed chip keeps the focus); the
    // ones money can't be withdrawn from only work with the tick box on.
    const signature = d.markets.map((m) => m.key).join('|');
    if (signature !== this.marketSignature) {
      this.marketSignature = signature;
      this.el.marketChips.innerHTML = d.markets.map((m) => `<button type="button" class="chip" data-market="${esc(m.key)}">${esc(m.label)}${
        m.cash_out ? '' : ` <span class="wallet-tag">${esc(noCashLabel(m.key, true))}</span>`}</button>`).join('')
        || '<span class="muted small">No market has a price for anything you could sell yet.</span>';
    }
    const wallet = this.el.wallet.checked;
    for (const m of d.markets) {
      const chip = this.el.marketChips.querySelector(`[data-market="${CSS.escape(m.key)}"]`);
      if (!chip) continue;
      const usable = m.cash_out || wallet;
      const on = usable && !this.marketsOff.has(m.key);
      chip.setAttribute('aria-pressed', String(on));
      chip.disabled = !usable;
      chip.title = m.cash_out ? (on ? 'Click to leave this market out' : 'Click to sell here too')
        : (usable ? `${noCashLabel(m.key)}. Click to ${on ? 'leave it out' : 'sell here too'}`
          : `${noCashLabel(m.key)}: tick "Include markets you can't withdraw from" to use it`);
    }
  }

  /** The slider: its end is everything you could sell on these markets; colours show what's chosen. */
  renderBar() {
    const cur = this.data.currency;
    this.el.currency.textContent = cur;
    const all = this.everything.max;
    const max = Math.max(1, Math.ceil(all));
    this.el.slider.max = String(max);
    this.el.slider.step = max > 2000 ? '10' : '1';
    const target = this.target();
    if (document.activeElement !== this.el.slider) this.el.slider.value = String(Math.min(target, max));
    const limit = this.limit();
    const reach = limit == null ? all : limit;
    const pct = (v) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
    const shown = Math.min(target, max);
    const place = (zone, from, to) => {
      zone.hidden = !(to > from);
      zone.style.left = pct(from);
      zone.style.width = to > from ? `calc(${pct(to)} - ${pct(from)})` : '0';
    };
    place(this.el.zones.sel, 0, Math.min(shown, reach));
    place(this.el.zones.avail, Math.min(shown, reach), reach);
    place(this.el.zones.over, reach, limit == null ? 0 : shown);
    this.el.marker.hidden = limit == null;
    if (limit != null) {
      this.el.marker.style.left = pct(limit);
      this.el.marker.title = `The most these items can raise: ${money(limit, cur)}`;
    }
    this.el.bar.classList.toggle('is-over', target > reach + 0.005);

    // Going past what can be raised: say so, and what the most is.
    const over = target > 0 && target > reach + 0.005;
    this.el.over.hidden = !over;
    if (over) {
      const nothing = reach <= 0;
      this.el.over.innerHTML = nothing
        ? (this.filtering() || this.keep.size
          ? '<strong>Nothing to sell with these filters.</strong> No item they leave has a price on the markets you\'ve chosen.'
          : '<strong>Nothing to sell on these markets.</strong> Switch on another market above.')
        : limit != null
          ? `<strong>${esc(money(target, cur))} is more than the items you've picked can raise.</strong> The most you can get with
            these filters is <strong>${esc(money(reach, cur))}</strong> - the plan below sells all of them.`
          : `<strong>${esc(money(target, cur))} is more than everything you could sell can raise.</strong> The most you can get on
            these markets is <strong>${esc(money(reach, cur))}</strong> - the plan below sells all of it.`;
    }
    const undercut = Number(this.el.undercut.value);
    const held = this.el.hold.selectedOptions[0]?.textContent.replace(/\s*\(.*\)$/, '');
    const how = (undercut > 0 ? `listing each copy ${number(undercut)}% under the market price so it sells first`
      : 'listing at the market price')
      + (Number(this.el.hold.value) > 0 ? `, and keeping ${held} item for later unless the amount needs it.` : '.');
    this.el.limit.textContent = limit != null
      ? `The items you've picked can raise up to ${money(limit, cur)} (everything on these markets: ${money(all, cur)}), ${how}`
      : `Up to ${money(all, cur)} can be raised from everything you could sell on these markets, ${how}`;
    steady(this.el.limit);
  }

  renderPlan() {
    const d = choose(this.picked, this.target());
    const cur = this.data.currency;
    const target = this.target();
    const units = d.steps.reduce((sum, s) => sum + s.units, 0);
    const tile = (label, value, foot) => `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    this.el.stats.innerHTML = target > 0 ? [
      tile('You raise', money(d.achieved, cur), `<span>target ${esc(money(target, cur))}${d.achieved > target ? ` · ${esc(money(d.achieved - target, cur))} over` : ''}</span>`),
      tile('Items to sell', number(units), `<span>${number(d.steps.length)} different item${d.steps.length === 1 ? '' : 's'}</span>`),
      tile('Markets', number(d.by_market.length), `<span>${d.by_market.map((m) => esc(m.label)).join(', ') || '—'}</span>`),
    ].join('') : '';
    steady(this.el.stats);

    const body = this.el.table.querySelector('tbody');
    body.innerHTML = d.steps.map((s, i) => `<tr class="row" data-i="${i}" data-view="${esc(s.name)}" tabindex="0" title="Show details">
        <td>${thumbHtml({ icon: s.icon, category: 'other' })}</td>
        <td data-v="${esc(s.name)}"><div class="item-name">${esc(s.name)}</div></td>
        <td class="num strong" data-v="${s.units}">${number(s.units)}<div class="sub">of ${number(s.held)}</div></td>
        <td data-v="${esc(s.markets.map((m) => m.label).join(', '))}">${s.markets.map((m) => `${number(m.units)} on ${marketLink(m.label, m.url)}
          <span class="muted small">at ${money(m.net, cur)}${m.wallet ? ` · ${esc(noCashLabel(m.market, true))}` : ''}</span>`).join('<br>')}</td>
        <td class="num strong" data-v="${s.proceeds}">${money(s.proceeds, cur)}</td>
        <td class="small" data-v="${esc(s.reasons.join('; '))}">${s.reasons.map(esc).join('<br>')}</td>
        <td><button type="button" class="btn btn-small" data-keep="${s.ids.join(',')}" title="Never sell this - plan without it (Kept items, above the plan, brings it back)">Keep</button></td>
      </tr>`).join('');
    applySort(this.el.table);
    this.el.empty.hidden = d.steps.length > 0;
    this.el.empty.textContent = target > 0
      ? 'Nothing to sell: everything these filters leave is kept, has no price on the chosen markets, or is trade-locked.'
      : 'Move the slider or type an amount to see what to sell.';

    this.el.marketsCard.hidden = !d.by_market.length;
    this.el.markets.innerHTML = d.by_market.map((m) => {
      const lines = d.steps.flatMap((s) => s.markets.filter((x) => x.market === m.market)
        .map((x) => `<li>${number(x.units)} × ${esc(s.name)} <span class="muted small">list at ${money(x.listPrice, cur)}
          · you get ${money(x.net, cur)} each</span></li>`));
      return `<div class="plan-market"><h3>${esc(m.label)} <span class="muted small">${number(m.units)} items · ${money(m.proceeds, cur)}</span></h3>
        <ul>${lines.join('')}</ul></div>`;
    }).join('');

    const excluded = d.excluded;
    const notes = [];
    if (excluded.locked) notes.push(`${number(excluded.locked)} trade-locked`);
    if (excluded.no_price) notes.push(`${number(excluded.no_price)} without a price on the chosen markets`);
    this.el.kept.innerHTML = notes.length ? `Not in the plan: ${notes.join(' · ')}.`
      : 'Press Keep on a row to leave that item out of the plan.';
  }
}
