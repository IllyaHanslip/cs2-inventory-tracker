// Shared helpers: escaping, formatting, storage, toasts.

import { collapseOut } from './motion.js';

export const state = {
  status: null,      // /api/status payload
  currency: 'USD',   // base currency
};

/** "Values from: Cash-out markets only" is on: markets whose money can't be withdrawn (Steam
 *  Wallet) are still shown, greyed and listed last, but don't set any price or value. */
export function cashOnly() {
  return Boolean(state.status?.settings?.cash_only);
}

/** Why a market doesn't count right now (for tooltips). */
export const NOT_COUNTED_WHY = `Its money goes to your Steam Wallet (or can't be withdrawn), and "Values from" is set to `
  + `"Cash-out markets only", so it's listed last and doesn't count towards any value.`;

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** Escape any value for safe use inside HTML text or a quoted attribute. */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** Only allow http(s) and steam:// links (item names and name tags are user-controlled). */
export function safeUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return ['https:', 'http:', 'steam:'].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

export function safeColor(color) {
  return /^#[0-9a-f]{6}$/i.test(color || '') ? color : null;
}

const formatters = new Map();
function formatter(currency, compact, maxDigits = 2) {
  const key = `${currency}|${compact}|${maxDigits}`;
  if (!formatters.has(key)) {
    try {
      formatters.set(key, new Intl.NumberFormat(undefined, compact
        ? { style: 'currency', currency, notation: 'compact', maximumFractionDigits: 1 }
        : { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: maxDigits }));
    } catch {
      formatters.set(key, new Intl.NumberFormat(undefined, { maximumFractionDigits: maxDigits }));
    }
  }
  return formatters.get(key);
}

export function money(value, currency = state.currency, { compact = false } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (compact) return formatter(currency, true).format(value);
  // Pennies matter on cheap items (531 capsules at £0.0504 each), so amounts under £1 get more
  // decimals: 3 from 10p, 4 below that. Larger amounts keep the usual 2.
  const size = Math.abs(value);
  if (value > 0 && size < 0.0001) return `< ${formatter(currency, false, 4).format(0.0001)}`;
  const digits = size === 0 || size >= 1 ? 2 : size >= 0.1 ? 3 : 4;
  return formatter(currency, false, digits).format(value);
}

export function number(value, digits = 0) {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

export function floatText(value) {
  if (value === null || value === undefined) return null;
  return Number(value).toFixed(value < 0.01 ? 8 : 6).replace(/0+$/, '').replace(/\.$/, '.0');
}

/** A coloured, arrowed percentage change. Colour is never the only cue (arrow + sign). */
export function deltaHtml(value, { suffix = '' } = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '<span class="muted" title="Not enough price history yet">—</span>';
  }
  const rounded = Math.abs(value) < 0.005 ? 0 : value;
  const cls = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat';
  const arrow = rounded > 0 ? '▲' : rounded < 0 ? '▼' : '•';
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '';
  return `<span class="delta ${cls}">${arrow} ${sign}${Math.abs(rounded).toFixed(2)}%${esc(suffix)}</span>`;
}

export function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function longDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function relTime(iso) {
  if (!iso) return 'never';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(seconds)) return 'unknown';
  if (seconds < 60) return 'just now';
  const units = [[86400, 'day'], [3600, 'hour'], [60, 'minute']];
  for (const [size, name] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      return `${n} ${name}${n === 1 ? '' : 's'} ago`;
    }
  }
  return 'just now';
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

// localStorage can be unavailable (private mode, blocked storage) - never let it break the page.
export const store = {
  get(key, fallback) {
    try {
      const raw = window.localStorage.getItem(`cs2t:${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(`cs2t:${key}`, JSON.stringify(value));
    } catch { /* ignore */ }
  },
  remove(key) {
    try {
      window.localStorage.removeItem(`cs2t:${key}`);
    } catch { /* ignore */ }
  },
};

/** Where messages go: the page's corner, or - while a window is open, when nothing outside it can
 *  be clicked - that window's corner, so a message's button (Undo) still works. */
function toastArea() {
  const modal = [...document.querySelectorAll('dialog[open]')].filter((d) => d.matches(':modal')).pop();
  if (!modal) return document.getElementById('toasts');
  let area = modal.querySelector(':scope > .toasts');
  if (!area) {
    area = document.createElement('div');
    area.className = 'toasts in-dialog';
    area.setAttribute('aria-live', 'polite');
    modal.append(area);
  }
  return area;
}

// How long a message stays (ms): a bar along its bottom runs down to when it goes.
const TOAST_LIFE = { ok: 15000, info: 15000, warn: 20000, error: 20000 };
const TOAST_MAX = 5;            // more than this on screen at once: the oldest goes early
const toastTimers = new WeakMap();
let pageHidden = typeof document !== 'undefined' && document.hidden;
// Leaving the page (reloading, closing the tab): the requests it was still making get cut off, which
// would read as "Cannot reach the local server" - those aren't real problems, so they aren't shown
// or kept in Recent messages.
let pageLeaving = false;
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { pageLeaving = true; });
  window.addEventListener('pageshow', () => { pageLeaving = false; });   // (back from the browser's cache)
}

/** A message in the corner (``kind``: ok, info, warn or error). It stays 15 seconds (20 for warnings
 *  and problems, or ``timeout`` if that's longer), with a bar along its bottom running down to when
 *  it goes. Pointing at it, tabbing into it, or leaving the browser tab pauses it; × closes it. The
 *  same message again starts its time over rather than showing twice. ``action`` adds a button
 *  ({ label, key, run }). Every message is kept in Recent messages too (messages.js). Returns the
 *  message element (close it early with dismissToast), or null while the page is being left. */
export function toast(message, kind = 'info', timeout = 0, { action = null } = {}) {
  if (pageLeaving) return null;
  const id = recordMessage(kind, message);
  const box = toastArea();
  const key = `${kind}|${message}`;
  if (!action) {
    const same = [...box.querySelectorAll('.toast:not(.collapsing)')].find((el) => el.dataset.key === key);
    if (same && toastTimers.has(same)) {
      same.dataset.message = id;
      toastTimers.get(same).restart();
      return same;
    }
  }
  const life = Math.max(TOAST_LIFE[kind] || TOAST_LIFE.info, timeout || 0);
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.dataset.key = key;
  el.dataset.message = id;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = message;
  el.append(text);
  if (action) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'toast-action';
    button.innerHTML = `${esc(action.label)}${action.key ? ` <kbd class="kbd">${esc(action.key)}</kbd>` : ''}`;
    if (action.key) button.setAttribute('aria-keyshortcuts', action.key.replace('Ctrl+', 'Control+').replace('⌘', 'Meta+'));
    button.addEventListener('click', () => { dismissToast(el); action.run(); });
    el.append(button);
  }
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', 'Close this message');
  close.title = 'Close';
  close.textContent = '×';
  close.addEventListener('click', () => { markMessageSeen(el.dataset.message); dismissToast(el); });
  const bar = document.createElement('span');
  bar.className = 'toast-timer';
  bar.setAttribute('aria-hidden', 'true');
  el.append(close, bar);
  box.append(el);
  toastTimers.set(el, toastTimer(el, life));
  const showing = [...box.querySelectorAll('.toast:not(.collapsing)')];
  showing.slice(0, Math.max(0, showing.length - TOAST_MAX)).forEach((old) => dismissToast(old));
  return el;
}

/** Runs a message's time down, paused while it's pointed at, has the focus, or the page is hidden. */
function toastTimer(el, life) {
  let remaining = life;
  let started = 0;
  let timer = null;
  let hover = false;
  let focus = false;
  const bar = el.querySelector('.toast-timer');
  const held = () => hover || focus || pageHidden;
  const stop = () => {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    remaining = Math.max(0, remaining - (performance.now() - started));
  };
  const sync = () => {
    if (held()) {
      stop();
      el.classList.add('paused');
    } else if (timer === null) {
      started = performance.now();
      timer = setTimeout(() => dismissToast(el), remaining);
      el.classList.remove('paused');
    }
  };
  bar.style.setProperty('--life', `${life}ms`);
  el.addEventListener('pointerenter', () => { hover = true; sync(); });
  el.addEventListener('pointerleave', () => { hover = false; sync(); });
  el.addEventListener('focusin', () => { focus = true; sync(); });
  el.addEventListener('focusout', (event) => { focus = el.contains(event.relatedTarget); sync(); });
  sync();
  return {
    sync,
    stop,
    restart() {
      stop();
      remaining = life;
      bar.style.animation = 'none';            // run the bar from full again
      void bar.offsetWidth;
      bar.style.animation = '';
      sync();
    },
  };
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    pageHidden = document.hidden;
    document.querySelectorAll('.toast').forEach((el) => toastTimers.get(el)?.sync());
  });
}

/** Close a message: it slides away and folds up, and the ones above close the gap. */
export function dismissToast(el) {
  if (!el || !el.isConnected || el.classList.contains('collapsing')) return;
  toastTimers.get(el)?.stop();
  collapseOut(el).then(() => el.remove());
}

// ---------------------------------------------------------------------------
// Recent messages: every message from the corner, kept in this browser for a day (messages.js
// shows them). The same message again moves to the top and counts how many times it came.
// ---------------------------------------------------------------------------
export const MESSAGE_HOURS = 24;
const MESSAGE_MAX = 50;

export function messageHistory() {
  const since = Date.now() - MESSAGE_HOURS * 3600 * 1000;
  const list = store.get('messages', []);
  return Array.isArray(list) ? list.filter((m) => m && typeof m.text === 'string' && m.at >= since) : [];
}

function saveMessages(list) {
  store.set('messages', list.slice(0, MESSAGE_MAX));
  if (typeof document !== 'undefined') document.dispatchEvent(new CustomEvent('cs2t:messages'));
}

function recordMessage(kind, text) {
  const list = messageHistory();
  const at = list.findIndex((m) => m.kind === kind && m.text === text);
  const count = at >= 0 ? (list[at].count || 1) + 1 : 1;
  if (at >= 0) list.splice(at, 1);
  const id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  list.unshift({ id, kind, text, at: Date.now(), count, seen: false });
  saveMessages(list);
  return id;
}

/** A message closed with × has clearly been read: it doesn't count as missed. */
export function markMessageSeen(id) {
  const list = messageHistory();
  const entry = list.find((m) => m.id === id);
  if (entry && !entry.seen) {
    entry.seen = true;
    saveMessages(list);
  }
}

export function markAllMessagesSeen() {
  const list = messageHistory();
  if (list.some((m) => !m.seen)) saveMessages(list.map((m) => ({ ...m, seen: true })));
}

export function clearMessages() {
  saveMessages([]);
}

const CATEGORY_ICONS = {
  weapon_skin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="7"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/></svg>',
  sticker: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 4h10l4 4v12H5z"/><path d="M15 4v4h4"/></svg>',
  sticker_slab: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="3" width="12" height="18" rx="2"/><rect x="9" y="7" width="6" height="6" rx="1"/></svg>',
  container: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8l9-4 9 4v9l-9 4-9-4z"/><path d="M3 8l9 4 9-4M12 12v9"/></svg>',
  other: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 12l8-8h8v8l-8 8z"/><circle cx="16" cy="8" r="1.5"/></svg>',
};

/** Thumbnail with Steam image when available, else a category glyph. */
export function thumbHtml(item, size = '') {
  const color = safeColor(item.rarity_color);
  const style = color ? ` style="--rarity:${color}"` : '';
  const src = safeUrl(item.icon);
  const inner = src
    ? `<img src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : (CATEGORY_ICONS[item.category] || CATEGORY_ICONS.other);
  return `<div class="thumb ${size}"${style}>${inner}</div>`;
}

/** A storage unit, or anything holding items. Cases, capsules and souvenir packages share the
 *  "container" category but can't be opened, so this, not is_container, decides "Open". */
export function isStorage(item) {
  return Boolean(item.is_storage || item.contents_count > 0);
}

/** The group an item falls in for "Group by" (and the Sell Planner's "Filter by"): its event,
 *  collection, type, weapon, rarity, wear ("exterior") or container. */
export function groupOf(item, by) {
  if (by === 'exterior') return item.exterior || 'No wear';
  if (by === 'container') return item.container_name || (isStorage(item) ? 'Containers' : 'Not in a container');
  const value = item.tags?.[by];
  return value || { event: 'No event', collection: 'No collection', weapon: 'Not a weapon', rarity: 'Unknown rarity' }[by] || 'Other';
}

export const CATEGORY_FILTERS = [
  ['weapon_skin', 'Weapon Skins'],
  ['sticker', 'Stickers'],
  ['sticker_slab', 'Sticker Slabs'],
  ['container', 'Cases & Capsules'],
  ['storage', 'Storage Units'],
  ['in_container', 'Items in Containers'],
  ['other', 'Other'],
];
