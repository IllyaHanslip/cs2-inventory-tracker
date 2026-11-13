// Shared helpers: escaping, formatting, storage, toasts.

export const state = {
  status: null,      // /api/status payload
  currency: 'USD',   // base currency
};

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

export function toast(message, kind = 'info', timeout = 6000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  el.textContent = message;
  box.append(el);
  setTimeout(() => el.remove(), timeout);
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

export const CATEGORY_FILTERS = [
  ['weapon_skin', 'Weapon Skins'],
  ['sticker', 'Stickers'],
  ['sticker_slab', 'Sticker Slabs'],
  ['container', 'Containers'],
  ['in_container', 'Items in Containers'],
  ['other', 'Other'],
];
