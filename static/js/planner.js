// "Sell Planner" tab: pick an amount with the slider or the box, and the server works out which
// items to sell, how many and where (see backend/planner.py). Items can be kept out of the plan.

import { api } from './api.js';
import * as dialogs from './dialogs.js';
import { noCashLabel } from './market-filter.js';
import { applySort } from './sortable.js';
import { debounce, esc, money, number, store, thumbHtml, toast } from './util.js';

export class PlannerView {
  constructor(root) {
    this.root = root;
    this.loaded = false;
    this.data = null;
    const saved = store.get('planner', {});
    this.keep = new Set(saved.keep || []);
    this.el = {
      slider: root.querySelector('#plan-slider'),
      target: root.querySelector('#plan-target'),
      wallet: root.querySelector('#plan-wallet'),
      currency: root.querySelector('#plan-currency'),
      limit: root.querySelector('#plan-limit'),
      stats: root.querySelector('#plan-stats'),
      table: root.querySelector('#plan-table'),
      empty: root.querySelector('#plan-empty'),
      marketsCard: root.querySelector('#plan-markets-card'),
      markets: root.querySelector('#plan-markets'),
      kept: root.querySelector('#plan-kept'),
    };
    this.el.wallet.checked = Boolean(saved.wallet);
    this.el.target.value = saved.target || 0;
    const update = debounce(() => this.reload(), 250);
    // The slider and the box always show the same amount.
    this.el.slider.addEventListener('input', () => { this.el.target.value = this.el.slider.value; update(); });
    this.el.target.addEventListener('input', () => {
      this.el.slider.value = String(Math.min(Number(this.el.target.value) || 0, Number(this.el.slider.max)));
      update();
    });
    this.el.wallet.addEventListener('change', () => this.reload());
    this.el.table.addEventListener('click', (event) => {
      const keep = event.target.closest('[data-keep]');
      if (keep) {
        keep.dataset.keep.split(',').forEach((id) => this.keep.add(Number(id)));
        this.reload();
        return;
      }
      const view = event.target.closest('[data-view]');
      if (view) this.view(view.dataset.view);
    });
    this.el.kept.addEventListener('click', (event) => {
      if (!event.target.closest('[data-unkeep]')) return;
      this.keep.clear();
      this.reload();
    });
  }

  persist() {
    store.set('planner', { target: Number(this.el.target.value) || 0, wallet: this.el.wallet.checked, keep: [...this.keep] });
  }

  async view(name) {
    try {
      const { items } = await api('/items', { params: { scope: 'total' } });
      const item = items.find((i) => i.market_hash_name === name);
      if (item) dialogs.openItem(item);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async reload() {
    this.persist();
    const target = Math.max(0, Number(this.el.target.value) || 0);
    try {
      this.data = await api('/sell-plan', {
        params: { target, wallet: this.el.wallet.checked ? 1 : 0, keep: [...this.keep].join(',') },
      });
      this.loaded = true;
      this.render();
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  render() {
    const d = this.data;
    const cur = d.currency;
    this.el.currency.textContent = cur;
    // The slider runs up to everything you could raise within about 30 days of sales.
    const max = Math.max(1, Math.ceil(d.max));
    this.el.slider.max = String(max);
    this.el.slider.step = max > 2000 ? '10' : '1';
    if (Number(this.el.slider.value) !== Math.min(Number(this.el.target.value) || 0, max)) {
      this.el.slider.value = String(Math.min(Number(this.el.target.value) || 0, max));
    }
    this.el.limit.textContent = `Up to ${money(d.max, cur)} can be raised from what you could sell `
      + `(${this.el.wallet.checked ? 'Steam Wallet sales included' : 'cash markets only'}).`
      + (d.target > d.max ? ' That target is more than everything you could sell.' : '')
      + (d.demo_note ? ` ${d.demo_note}` : '');

    const units = d.steps.reduce((sum, s) => sum + s.units, 0);
    const tile = (label, value, foot) => `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    this.el.stats.innerHTML = d.target > 0 ? [
      tile('You raise', money(d.achieved, cur), `<span>target ${esc(money(d.target, cur))}${d.achieved > d.target ? ` · ${esc(money(d.achieved - d.target, cur))} over` : ''}</span>`),
      tile('Items to sell', number(units), `<span>${number(d.steps.length)} different item${d.steps.length === 1 ? '' : 's'}</span>`),
      tile('Markets', number(d.by_market.length), `<span>${d.by_market.map((m) => esc(m.label)).join(', ') || '—'}</span>`),
    ].join('') : '';

    const body = this.el.table.querySelector('tbody');
    body.innerHTML = d.steps.map((s, i) => `<tr data-i="${i}">
        <td>${thumbHtml({ icon: s.icon, category: 'other' })}</td>
        <td data-v="${esc(s.name)}"><button type="button" class="link-btn" data-view="${esc(s.name)}">${esc(s.name)}</button></td>
        <td class="num strong" data-v="${s.units}">${number(s.units)}<div class="sub">of ${number(s.held)}</div></td>
        <td data-v="${esc(s.markets.map((m) => m.label).join(', '))}">${s.markets.map((m) => `${number(m.units)} on ${esc(m.label)}
          <span class="muted small">at ${money(m.net, cur)}${m.market === 'steam' ? ` · ${esc(noCashLabel('steam', true))}` : ''}</span>`).join('<br>')}</td>
        <td class="num strong" data-v="${s.proceeds}">${money(s.proceeds, cur)}</td>
        <td class="small" data-v="${esc(s.reasons.join('; '))}">${s.reasons.map(esc).join('<br>')}</td>
        <td><button type="button" class="btn btn-small" data-keep="${s.ids.join(',')}" title="Never sell this - plan without it">Keep</button></td>
      </tr>`).join('');
    applySort(this.el.table);
    this.el.empty.hidden = d.steps.length > 0;
    this.el.empty.textContent = d.target > 0 ? 'Nothing to sell: everything is kept, has no cash price, or is trade-locked.'
      : 'Move the slider or type an amount to see what to sell.';

    this.el.marketsCard.hidden = !d.by_market.length;
    this.el.markets.innerHTML = d.by_market.map((m) => {
      const lines = d.steps.flatMap((s) => s.markets.filter((x) => x.market === m.market)
        .map((x) => `<li>${number(x.units)} × ${esc(s.name)} <span class="muted small">at ${money(x.net, cur)} each</span></li>`));
      return `<div class="plan-market"><h3>${esc(m.label)} <span class="muted small">${number(m.units)} items · ${money(m.proceeds, cur)}</span></h3>
        <ul>${lines.join('')}</ul></div>`;
    }).join('');

    const excluded = d.excluded;
    const notes = [];
    if (this.keep.size) notes.push(`${number(this.keep.size)} item${this.keep.size === 1 ? '' : 's'} kept out of the plan <button type="button" class="link-btn" data-unkeep>Bring them back</button>`);
    if (excluded.locked) notes.push(`${number(excluded.locked)} trade-locked`);
    if (excluded.no_price) notes.push(`${number(excluded.no_price)} without a ${this.el.wallet.checked ? '' : 'cash '}price`);
    this.el.kept.innerHTML = notes.length ? `Not in the plan: ${notes.join(' · ')}.` : '';
  }
}
