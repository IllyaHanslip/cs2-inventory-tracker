// Dashboard bootstrap: status, tabs, stat tiles, background jobs.

import { api } from './api.js';
import * as containers from './containers.js';
import * as dialogs from './dialogs.js';
import { InventoryView } from './inventory.js';
import { initLayout, updateStickyOffsets } from './layout.js';
import { MarketsView } from './markets.js';
import { deltaHtml, esc, money, number, relTime, state, store, toast } from './util.js';

const app = {
  views: {},
  summaries: {},
  activeTab: 'public',
  pollTimer: null,

  async init() {
    try {
      await this.loadStatus();
    } catch (error) {
      toast(error.message, 'error', 15000);
      return;
    }
    dialogs.init(this);
    containers.init(this);
    dialogs.populateCurrencies();
    this.views.public = new InventoryView(document.getElementById('panel-public'), 'public', this);
    this.views.total = new InventoryView(document.getElementById('panel-total'), 'total', this);
    this.views.containers = new containers.ContainersView(document.getElementById('panel-containers'));
    this.views.markets = new MarketsView(document.getElementById('panel-markets'), this);
    this.bindTopbar();
    this.bindTabs();
    initLayout();
    await Promise.all([this.views.public.reload(), this.views.total.reload(), this.views.containers.reload()]);
    const job = state.status.job;
    if (job && job.status === 'running') this.watchJob(job);
  },

  async loadStatus() {
    state.status = await api('/status');
    state.currency = state.status.settings.base_currency;
    const input = document.getElementById('steam-id');
    if (!input.value) input.value = state.status.settings.steam_id || '';
    const s = state.status;
    const parts = [
      `Steam sync: ${s.last_steam_sync ? relTime(s.last_steam_sync) : 'never'}`,
      `Prices updated: ${s.prices_updated ? relTime(s.prices_updated) : 'never'}`,
      `Daily update: ${s.last_daily_update ? relTime(s.last_daily_update) : 'not yet'} (automatic from ${s.daily_update_time})`,
      `Last snapshot: ${s.last_snapshot ? relTime(s.last_snapshot) : 'none yet'}`,
      'Values: after selling fees',
      `Markets: ${s.markets.filter((m) => m.enabled && m.available).map((m) => m.label).join(', ') || 'none'}`,
    ];
    if (s.fx.date) parts.push(`FX: ${s.fx.source || 'stored rates'}`);
    document.getElementById('status-line').textContent = parts.join(' · ');
  },

  bindTopbar() {
    document.getElementById('sync-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const steamId = document.getElementById('steam-id').value.trim();
      if (!steamId) {
        toast('Paste your SteamID64 (17 digits) or Steam profile URL first.', 'warn');
        return;
      }
      this.startJob('/steam/sync', { steam_id: steamId });
    });
    document.getElementById('refresh-btn').addEventListener('click', (event) => {
      // Shift+click forces a full refresh, ignoring the per-item cache.
      this.startJob('/prices/refresh', { force: event.shiftKey });
    });
    document.getElementById('settings-btn').addEventListener('click', () => dialogs.openSettings());
    document.getElementById('job-cancel').addEventListener('click', async () => {
      try {
        await api('/jobs/cancel', { method: 'POST' });
      } catch (error) {
        toast(error.message, 'error');
      }
    });
  },

  bindTabs() {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const activate = (name, focus = false) => {
      this.activeTab = name;
      store.set('tab', name);
      if (window.location.hash !== `#${name}`) window.history.replaceState(null, '', `#${name}`);
      for (const tab of tabs) {
        const selected = tab.dataset.tab === name;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        document.getElementById(tab.getAttribute('aria-controls')).hidden = !selected;
        if (selected && focus) tab.focus();
      }
      if (name === 'markets' && !this.views.markets.loaded) this.views.markets.reload();
      // Charts drawn while hidden have no size - redraw on show.
      const view = this.views[name];
      if (view && view.chart) view.chart.resize();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => activate(tab.dataset.tab));
      tab.addEventListener('keydown', (event) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        activate(tabs[(i + step + tabs.length) % tabs.length].dataset.tab, true);
      });
    });
    const fromHash = window.location.hash.slice(1);
    const saved = tabs.some((t) => t.dataset.tab === fromHash) ? fromHash : store.get('tab', 'public');
    activate(tabs.some((t) => t.dataset.tab === saved) ? saved : 'public');
    window.addEventListener('hashchange', () => {
      const name = window.location.hash.slice(1);
      if (tabs.some((t) => t.dataset.tab === name)) activate(name);
    });
  },

  updateSummary(scope, summary) {
    this.summaries[scope] = summary;
    this.renderStats();
  },

  renderStats() {
    const pub = this.summaries.public;
    const total = this.summaries.total;
    const cur = state.currency;
    const tile = (label, value, foot, title = '') => `<div class="stat"${title ? ` title="${esc(title)}"` : ''}><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    const afterFees = (summary) => `What you'd receive selling everything where it pays most, after each market's fees. `
      + `At buying prices (market value) it would be ${money(summary.market_value, cur)}.`;
    const tiles = [];
    tiles.push(tile('Public inventory', pub ? money(pub.total_value, cur) : '…',
      pub ? `${deltaHtml(pub.change_24h)} <span>24h · ${number(pub.item_count)} items · after fees</span>` : '',
      pub ? afterFees(pub) : ''));
    tiles.push(tile('Total portfolio', total ? money(total.total_value, cur) : '…',
      total ? `${deltaHtml(total.change_24h)} <span>24h · after fees · incl. manual &amp; containers</span>` : '',
      total ? afterFees(total) : ''));
    tiles.push(tile('Cash-out value', total ? money(total.cash_out_value, cur) : '…',
      total ? `<span>money you could withdraw · Steam Wallet sales left out${total.cash_out_missing ? ` · ${number(total.cash_out_missing)} Steam-only` : ''}</span>` : '',
      'Like Total portfolio, but each item is sold on the best market whose money you can withdraw. Steam pays into your Steam Wallet, and some markets (such as BUFF163) only pay out to certain accounts, so those are left out here.'));
    tiles.push(tile('7-week change', total ? deltaHtml(total.change_7w) : '…',
      '<span>total portfolio vs. 49 days ago</span>'));
    tiles.push(tile('Items tracked', total ? number(total.item_count) : '…',
      total ? `<span>${number(total.unique_items)} unique${total.unpriced_items ? ` · ${number(total.unpriced_items)} unpriced` : ''}</span>` : ''));
    tiles.push(tile('In containers', total ? money(total.value_in_containers, cur) : '…',
      total ? `<span>${number(total.items_in_containers)} items in ${number(total.storage_containers)} container${total.storage_containers === 1 ? '' : 's'} · after fees</span>` : ''));
    if (total && total.purchase_value) {
      const pct = (total.profit / total.purchase_value) * 100;
      tiles.push(tile('Profit / loss', money(total.profit, cur), `${deltaHtml(pct)} <span>if sold now, after fees · items with a purchase price</span>`));
    }
    document.getElementById('stats').innerHTML = tiles.join('');
  },

  async refreshAll() {
    try {
      await this.loadStatus();
    } catch (error) {
      toast(error.message, 'error');
    }
    dialogs.populateCurrencies();
    const work = [this.views.public.reload(), this.views.total.reload(), this.views.containers.reload()];
    if (this.views.markets.loaded) work.push(this.views.markets.reload());
    await Promise.all(work);
  },

  async startJob(path, body) {
    try {
      const { job } = await api(path, { method: 'POST', body });
      this.watchJob(job);
    } catch (error) {
      toast(error.message, error.status === 409 ? 'warn' : 'error', 9000);
    }
  },

  showJob(job) {
    const banner = document.getElementById('job-banner');
    banner.hidden = false;
    document.getElementById('job-label').textContent = `${job.label}:`;
    document.getElementById('job-message').textContent = job.message || '';
    const bar = document.getElementById('job-progress');
    const progress = bar.parentElement;
    if (job.total > 0) {
      progress.classList.remove('indeterminate');
      bar.style.width = `${Math.min(100, (job.progress / job.total) * 100)}%`;
    } else {
      progress.classList.add('indeterminate');
      bar.style.width = '';
    }
    document.getElementById('sync-form').querySelector('button').disabled = true;
    document.getElementById('refresh-btn').disabled = true;
    updateStickyOffsets();
  },

  watchJob(job) {
    this.showJob(job);
    clearInterval(this.pollTimer);
    this.pollTimer = setInterval(async () => {
      let current;
      try {
        ({ job: current } = await api('/jobs/current'));
      } catch {
        return; // server briefly unavailable - keep polling
      }
      if (!current) return;
      if (current.status === 'running') {
        this.showJob(current);
        return;
      }
      clearInterval(this.pollTimer);
      this.finishJob(current);
    }, 1500);
  },

  async finishJob(job) {
    document.getElementById('job-banner').hidden = true;
    updateStickyOffsets();
    document.getElementById('sync-form').querySelector('button').disabled = false;
    document.getElementById('refresh-btn').disabled = false;
    if (job.status === 'error') {
      toast(job.error || `${job.label} failed`, 'error', 15000);
    } else if (job.status === 'cancelled') {
      toast(`${job.label} cancelled`, 'warn');
    } else {
      const r = job.result || {};
      if (job.kind === 'sync' && r.fetched !== undefined) {
        toast(`Steam sync done: ${r.fetched} items (${r.added} new, ${r.removed} removed).`, 'ok', 8000);
      } else {
        toast(`${job.label} finished.`, 'ok');
      }
    }
    for (const warning of job.warnings || []) toast(warning, 'warn', 12000);
    await this.refreshAll();
  },
};

window.addEventListener('DOMContentLoaded', () => app.init());
