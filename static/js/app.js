// Dashboard bootstrap: status, tabs, stat tiles, background jobs.

import { announceFired, initAlerts, openAlerts, updateBell } from './alerts.js';
import { api } from './api.js';
import { DEMO_SITE, showDemoBanner } from './demo-site.js';
import * as containers from './containers.js';
import * as dialogs from './dialogs.js';
import { InventoryView } from './inventory.js';
import { initShortcuts, markKeys, openShortcuts } from './keys.js';
import { initLayout, updateStickyOffsets } from './layout.js';
import { MarketsView } from './markets.js';
import { initMotion } from './motion.js';
import { PlannerView } from './planner.js';
import { PnlView } from './pnl.js';
import { onUndone, undoLast } from './undo.js';
import { cashOnly, deltaHtml, esc, money, number, relTime, state, store, toast } from './util.js';

const TAB_ORDER = ['public', 'total', 'containers', 'markets', 'planner', 'pnl'];

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
    this.views.planner = new PlannerView(document.getElementById('panel-planner'));
    this.views.pnl = new PnlView(document.getElementById('panel-pnl'), this);
    this.bindTopbar();
    // Every control with a keyboard shortcut shows its key (windows close with Esc).
    document.querySelectorAll('.btn-close').forEach((button) => { button.dataset.shortcut = 'Esc'; });
    markKeys(document);
    this.bindTabs();
    this.bindShortcuts();
    initAlerts({ openItem: (name) => dialogs.openItemByName(name) });
    onUndone(async () => {
      await containers.reopenIfOpen();   // what you're looking at first, then everything else
      await this.refreshAll();
    });
    initMotion();
    initLayout();
    await Promise.all([this.views.public.reload(), this.views.total.reload(), this.views.containers.reload(), this.firstLoad]);
    this.restoreScroll(this.activeTab);
    const job = state.status.job;
    if (job && job.status === 'running') this.watchJob(job);
    // The app also refreshes prices by itself (when you start it, and its hourly check): show
    // those too, so you can always see what's running and how far it's got.
    if (!DEMO_SITE) setInterval(() => this.checkForJob(), 15000);
  },

  async checkForJob() {
    if (this.pollTimer) return;
    try {
      const { job } = await api('/jobs/current');
      if (job && job.status === 'running') this.watchJob(job);
    } catch { /* server briefly unavailable */ }
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
      `Values: after selling fees${cashOnly() ? ', cash-out markets only' : ''}`,
      `Markets: ${s.markets.filter((m) => m.enabled && m.available).map((m) => m.label).join(', ') || 'none'}`,
    ];
    if (s.fx.date) parts.push(`FX: ${s.fx.source || 'stored rates'}`);
    document.getElementById('status-line').textContent = parts.join(' · ');
    const alert = document.getElementById('tab-containers-alert');
    alert.hidden = !s.pending_changes;
    alert.textContent = s.pending_changes ? String(s.pending_changes) : '';
    updateBell(s.alerts_unseen || 0);
    this.renderBasis();
  },

  /** The "Values from" switch: all markets, or only markets whose money you can withdraw. */
  renderBasis() {
    const on = cashOnly();
    document.querySelectorAll('#basis-switch button').forEach((b) => {
      const chosen = (b.dataset.cash === '1') === on;
      b.classList.toggle('active', chosen);
      b.setAttribute('aria-pressed', String(chosen));
    });
    document.getElementById('basis-note').textContent = on
      ? 'Steam (and anything else you can\u2019t withdraw from) is shown greyed and listed last, and left out of values.'
      : 'Steam counts too: it pays into your Steam Wallet, which can\u2019t be withdrawn.';
  },

  async setBasis(on) {
    if (on === cashOnly() || this.switchingBasis) return;   // (one switch at a time: they'd race)
    this.switchingBasis = true;
    const buttons = document.querySelectorAll('#basis-switch button');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await api('/settings', { method: 'PUT', body: { cash_only: on } });
      state.status.settings.cash_only = on;
      this.renderBasis();
      this.summaries = {};      // (the old figures don't match the new labels: show … until they arrive)
      this.renderStats();
      toast(on ? 'Values now count only markets you can withdraw money from.' : 'Values now count every market, Steam included.', 'ok', 4000);
      await this.refreshAll();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
      this.switchingBasis = false;
    }
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
    document.getElementById('keys-btn').addEventListener('click', () => openShortcuts());
    document.getElementById('basis-switch').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-cash]');
      if (button) this.setBasis(button.dataset.cash === '1');
    });
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
    // Each tab remembers how far down you'd scrolled (kept across reloads too).
    try { window.history.scrollRestoration = 'manual'; } catch { /* ignore */ }
    this.scrolls = store.get('scroll', {}) || {};
    this.holdScroll = true;   // not recorded until the tab is back where it was
    let saveTimer = null;
    window.addEventListener('scroll', () => {
      if (this.holdScroll) return;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        this.scrolls[this.activeTab] = Math.round(window.scrollY);
        store.set('scroll', this.scrolls);
      }, 150);
    }, { passive: true });
    let ready = false;   // (the first tab shown on load doesn't save the one before it)
    const activate = (name, focus = false) => {
      if (ready && this.activeTab !== name) {
        clearTimeout(saveTimer);
        this.scrolls[this.activeTab] = Math.round(window.scrollY);
        store.set('scroll', this.scrolls);
      }
      const target = this.scrolls[name] || 0;
      if (this.activeTab !== name) this.holdScroll = true;
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
      let loading = null;
      if (name === 'markets' && !this.views.markets.loaded) loading = this.views.markets.reload();
      if (name === 'planner' && !this.views.planner.loaded) loading = this.views.planner.reload();
      if (name === 'pnl' && !this.views.pnl.loaded) loading = this.views.pnl.reload();
      // Charts drawn while hidden have no size - redraw on show.
      const view = this.views[name];
      if (view && view.chart) view.chart.resize();
      if (view && ready) Promise.resolve(loading).then(() => this.restoreScroll(name, target));
      if (!ready) this.firstLoad = loading;   // init() restores the scroll once this is in too
    };
    this.activateTab = activate;
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => activate(tab.dataset.tab));
      tab.addEventListener('keydown', (event) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        activate(tabs[(i + step + tabs.length) % tabs.length].dataset.tab, true);
      });
    });
    // The [ and ] at either end of the tabs: previous / next tab, like the keys.
    document.querySelectorAll('.tabs .tab-step').forEach((step) => step.addEventListener('click', () => {
      const i = TAB_ORDER.indexOf(this.activeTab);
      activate(TAB_ORDER[(i + Number(step.dataset.step) + TAB_ORDER.length) % TAB_ORDER.length]);
    }));
    const fromHash = window.location.hash.slice(1);
    const saved = tabs.some((t) => t.dataset.tab === fromHash) ? fromHash : store.get('tab', 'public');
    activate(tabs.some((t) => t.dataset.tab === saved) ? saved : 'public');
    ready = true;
    window.addEventListener('hashchange', () => {
      const name = window.location.hash.slice(1);
      if (tabs.some((t) => t.dataset.tab === name)) activate(name);
    });
  },

  /** Back to where you'd scrolled on this tab (once its content is there). */
  restoreScroll(name, top = this.scrolls?.[name] || 0) {
    if (name !== this.activeTab) return;
    requestAnimationFrame(() => {
      window.scrollTo({ top, behavior: 'instant' });
      this.holdScroll = false;
    });
  },

  bindShortcuts() {
    const panel = () => document.getElementById(`panel-${this.activeTab}`);
    const visibleSearch = (root) => [...root.querySelectorAll('input[type="search"]')].find((i) => i.offsetParent !== null);
    const focusSearch = (root) => {
      const input = root && visibleSearch(root);
      if (!input) return false;
      input.focus();
      input.select();
      return true;
    };
    initShortcuts({
      tab: (n) => {
        const name = TAB_ORDER[n - 1];
        if (!name) return false;
        this.activateTab(name, true);
        return true;
      },
      step: (by) => {
        const i = TAB_ORDER.indexOf(this.activeTab);
        this.activateTab(TAB_ORDER[(i + by + TAB_ORDER.length) % TAB_ORDER.length], true);
      },
      search: () => {
        if (!focusSearch(panel())) toast('This tab has nothing to search.', 'info', 2500);
      },
      searchIn: (dialog) => focusSearch(dialog),
      refresh: (force) => {
        if (document.getElementById('refresh-btn').disabled) {
          toast('Prices are already being refreshed - see the progress at the top.', 'info', 4000);
          return;
        }
        this.startJob('/prices/refresh', { force });
      },
      sync: () => document.getElementById('sync-form').requestSubmit(),
      add: () => dialogs.editItem(null),
      newContainer: () => containers.newContainer(),
      export: () => {
        const link = ['public', 'total'].includes(this.activeTab) ? this.views[this.activeTab].refs.export
          : this.activeTab === 'pnl' ? document.getElementById('pnl-export') : null;
        if (link) link.click();
        else toast('Export works on Public Inventory, Total Portfolio and Profit & Loss.', 'info', 4000);
      },
      alerts: () => openAlerts(),
      settings: () => dialogs.openSettings(),
      undo: () => undoLast(),
      help: () => openShortcuts(),
      basis: () => this.setBasis(!cashOnly()),
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
    const only = cashOnly();
    const basis = only ? 'cash-out markets only' : 'after fees';
    const afterFees = (summary) => `What you'd receive selling everything where it pays most${only ? ' (only on markets you can withdraw money from)' : ''}, after each market's fees. `
      + `At buying prices (market value) it would be ${money(summary.market_value, cur)}.`;
    const steamOnly = (summary) => (only && summary.wallet_only_items ? ` · ${number(summary.wallet_only_items)} Steam-only left out` : '');
    const tiles = [];
    tiles.push(tile('Public inventory', pub ? money(pub.total_value, cur) : '…',
      pub ? `${deltaHtml(pub.change_24h)} <span>24h · ${number(pub.item_count)} items · ${basis}${steamOnly(pub)}</span>` : '',
      pub ? afterFees(pub) : ''));
    tiles.push(tile('Total portfolio', total ? money(total.total_value, cur) : '…',
      total ? `${deltaHtml(total.change_24h)} <span>24h · ${basis} · incl. manual &amp; containers${steamOnly(total)}</span>` : '',
      total ? afterFees(total) : ''));
    if (only) {
      // Total portfolio already counts only cash-out markets: show the figure with Steam Wallet sales too.
      tiles.push(tile('Incl. Steam Wallet', total ? money(total.all_markets_value, cur) : '…',
        '<span>if Steam Wallet sales counted too (that money can\u2019t be withdrawn)</span>',
        'Like Total portfolio, but every market counts, Steam included. Switch "Values from" to "All markets" to see everything this way.'));
    } else {
      tiles.push(tile('Cash-out value', total ? money(total.cash_out_value, cur) : '…',
        total ? `<span>money you could withdraw · Steam Wallet sales left out${total.cash_out_missing ? ` · ${number(total.cash_out_missing)} Steam-only` : ''}</span>` : '',
        'Like Total portfolio, but each item is sold on the best market whose money you can withdraw. Steam pays into your Steam Wallet, and some markets (such as BUFF163) only pay out to certain accounts, so those are left out here. Switch "Values from" to "Cash-out markets only" to see everything this way.'));
    }
    tiles.push(tile('7-week change', total ? deltaHtml(total.change_7w) : '…',
      '<span>total portfolio vs. 49 days ago</span>'));
    tiles.push(tile('Items tracked', total ? number(total.item_count) : '…',
      total ? `<span>${number(total.unique_items)} unique${total.unpriced_items ? ` · ${number(total.unpriced_items)} unpriced` : ''}</span>` : ''));
    tiles.push(tile('In containers', total ? money(total.value_in_containers, cur) : '…',
      total ? `<span>${number(total.items_in_containers)} items in ${number(total.storage_containers)} container${total.storage_containers === 1 ? '' : 's'} · ${basis}</span>` : ''));
    if (total && total.purchase_value) {
      const pct = (total.profit / total.purchase_value) * 100;
      tiles.push(tile('Profit / loss', money(total.profit, cur), `${deltaHtml(pct)} <span>if sold now, ${basis} · items with a purchase price</span>`));
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
    if (this.views.planner.loaded) work.push(this.views.planner.reload());
    if (this.views.pnl.loaded) work.push(this.views.pnl.reload());
    await Promise.all(work);
  },

  async startJob(path, body) {
    try {
      const { job } = await api(path, { method: 'POST', body });
      this.watchJob(job);
    } catch (error) {
      if (error.status !== 409) { toast(error.message, 'error', 9000); return; }
      // Something is already running: show its progress instead.
      try {
        const { job } = await api('/jobs/current');
        if (job && job.status === 'running') {
          this.watchJob(job);
          toast(`${job.label} is already running - its progress is shown at the top. You can cancel it there.`, 'info', 9000);
          return;
        }
      } catch { /* fall through */ }
      toast(error.message, 'warn', 9000);
    }
  },

  showJob(job) {
    const banner = document.getElementById('job-banner');
    banner.hidden = false;
    document.getElementById('job-label').textContent = `${job.label}:`;
    const steps = job.steps || [];
    document.getElementById('job-message').textContent = steps.length ? '' : (job.message || '');
    const list = document.getElementById('job-steps');
    list.hidden = !steps.length;
    list.innerHTML = steps.map((step) => {
      const count = step.total ? `${number(step.done)} of ${number(step.total)}` : '';
      const left = step.seconds_left ? ` · about ${minutesText(step.seconds_left)} left` : '';
      const note = step.note ? ` <span class="muted">· ${esc(step.note)}</span>` : '';
      return `<li${step.finished ? ' class="finished"' : ''}><strong>${esc(step.label)}</strong> ${count}${left}${note}</li>`;
    }).join('');
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
      this.pollTimer = null;
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
        if (r.changes) {
          toast(`${r.changes} change${r.changes === 1 ? '' : 's'} to review on the Containers tab: items that went into or came out of a storage unit, or were sold.`, 'info', 12000);
        }
      } else {
        toast(`${job.label} finished.`, 'ok');
      }
      announceFired(r.alerts || r.prices?.alerts);
    }
    for (const warning of job.warnings || []) toast(warning, 'warn', 12000);
    await this.refreshAll();
  },
};

function minutesText(seconds) {
  if (seconds < 90) return 'a minute';
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} minutes`;
  return `${(minutes / 60).toFixed(1)} hours`;
}

window.addEventListener('DOMContentLoaded', () => {
  if (DEMO_SITE) showDemoBanner();
  app.init();
});
