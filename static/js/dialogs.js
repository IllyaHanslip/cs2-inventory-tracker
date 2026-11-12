// Modal dialogs: item details, add/edit, manual quotes, settings, confirm.
// (Container management lives in containers.js.)

import { api } from './api.js';
import { chartsAvailable, dataTableHtml, renderLineChart } from './charts.js';
import * as containers from './containers.js';
import { initDialogs, markClean, protectForm } from './guard.js';
import { MarketFilter, cheapest } from './market-filter.js';
import { applySortWithin } from './sortable.js';
import {
  CATEGORY_FILTERS, debounce, deltaHtml, esc, floatText, longDate, money, number, relTime, safeUrl, state,
  thumbHtml, toast,
} from './util.js';

let app;
let itemChart = null;
let itemHistory = null;
let itemRange = '90d';
let editing = null;

const $ = (selector) => document.querySelector(selector);
const STRATEGY_LABELS = {
  median: 'median of all markets',
  lowest: 'lowest market price',
  highest: 'highest market price',
  mean: 'average of all markets',
  steam: 'Steam Market price',
  skinport: 'Skinport price',
  csfloat: 'CSFloat price',
  marketcsgo: 'Market.CSGO price',
  waxpeer: 'Waxpeer price',
  evidence: 'same skin with the same stickers',
};
const STEAM_EDITABLE = ['float_value', 'paint_seed', 'custom_price', 'custom_price_currency', 'purchase_price', 'purchase_currency', 'notes'];
const ALL_FIELDS = ['market_hash_name', 'category', 'exterior', 'quantity', 'name_tag', 'container_id', ...STEAM_EDITABLE];

export function init(appRef) {
  app = appRef;
  initDialogs();
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
  }
  // Forms you type into don't close from a stray click outside (see guard.js).
  protectForm($('#edit-dialog'), 'You have unsaved changes - press Save to keep them, or Cancel to discard them.');
  protectForm($('#quote-dialog'), 'You have unsaved changes - press Save quote to keep them, or Cancel to discard them.');
  protectForm($('#settings-dialog'), 'You have unsaved changes - press Save settings to keep them, or Cancel to discard them.');
  $('#item-dialog').addEventListener('close', () => {
    if (itemChart) itemChart.destroy();
    itemChart = null;
  });
  setupEditForm();
  setupQuoteForm();
  setupSettingsForm();
}

/** Fill every currency <select> once the list of available rates is known. */
export function populateCurrencies() {
  const currencies = state.status.currencies.length ? state.status.currencies : ['USD'];
  for (const select of document.querySelectorAll('.currency-select')) {
    const current = select.value;
    select.innerHTML = currencies.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
    select.value = currencies.includes(current) ? current : state.currency;
  }
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = !message;
}

/** Fill a <datalist> with matching item names (stickers: pass kind to strip the "Sticker | " prefix). */
export const suggestNames = debounce(async (input, listId, kind) => {
  const q = input.value.trim();
  if (q.length < 2) return;
  try {
    const { names } = await api('/names', { params: { q, kind } });
    const prefix = kind ? new RegExp(`^${kind}\\s*\\|\\s*`, 'i') : null;
    $(`#${listId}`).innerHTML = names.map((n) => `<option value="${esc(prefix ? n.replace(prefix, '') : n)}"></option>`).join('');
  } catch { /* suggestions are optional */ }
}, 250);

// ---------------------------------------------------------------------------
// Confirm
// ---------------------------------------------------------------------------
/**
 * Ask the user to confirm. Resolves with the chosen button's value (null on cancel/close).
 * With `quantity` ({max, value, label}) and/or `select` ({label, options, value}) fields,
 * resolves with {value, quantity, selected} instead.
 */
export function confirmDialog({ title, message, buttons, quantity = null, select = null }) {
  const dialog = $('#confirm-dialog');
  $('#confirm-title').textContent = title;
  $('#confirm-message').textContent = message;
  const fields = $('#confirm-fields');
  const error = $('#confirm-error');
  showError(error, '');
  fields.innerHTML = [
    select ? `<label>${esc(select.label)}<select class="input" id="confirm-select">${select.options.map((o) => `<option value="${esc(o.value)}"${o.value === select.value ? ' selected' : ''}${o.disabled ? ' disabled' : ''}>${esc(o.label)}${o.disabled ? ' - full' : ''}</option>`).join('')}</select></label>` : '',
    quantity ? `<label>${esc(quantity.label || 'Quantity')}<span class="qty-row">
      <input class="input" id="confirm-qty" type="number" min="1" max="${quantity.max}" step="1" value="${quantity.value ?? quantity.max}">
      <span class="muted">of ${number(quantity.max)}</span>
      <button type="button" class="btn btn-small" id="confirm-all">All</button></span></label>` : '',
  ].join('');
  fields.hidden = !select && !quantity;
  $('#confirm-all')?.addEventListener('click', () => { $('#confirm-qty').value = quantity.max; });
  const actions = $('#confirm-actions');
  actions.innerHTML = buttons.map((b, i) => {
    const cls = b.kind === 'danger' ? 'btn-danger' : b.kind === 'primary' ? 'btn-primary' : '';
    return `<button type="button" class="btn ${cls}" data-i="${i}">${esc(b.label)}</button>`;
  }).join('');
  const withFields = Boolean(select || quantity);
  return new Promise((resolve) => {
    let result = null;
    const onClick = (event) => {
      const button = event.target.closest('button[data-i]');
      if (!button) return;
      const value = buttons[Number(button.dataset.i)].value;
      if (withFields) {
        let qty = null;
        if (quantity && value !== null) {
          const raw = $('#confirm-qty').value.trim();
          qty = Number(raw);
          if (!/^\d+$/.test(raw) || qty < 1 || qty > quantity.max) {
            showError(error, `Enter a whole number between 1 and ${number(quantity.max)}.`);
            return;
          }
        }
        result = { value, quantity: qty, selected: select ? $('#confirm-select').value : null };
      } else {
        result = value;
      }
      dialog.close();
    };
    actions.addEventListener('click', onClick);
    dialog.addEventListener('close', () => {
      actions.removeEventListener('click', onClick);
      resolve(result);
    }, { once: true });
    dialog.showModal();
    (withFields ? fields.querySelector('input, select') : actions.querySelector('.btn'))?.focus();
  });
}

// ---------------------------------------------------------------------------
// Item details
// ---------------------------------------------------------------------------
export async function openItem(item) {
  const dialog = $('#item-dialog');
  if (itemChart) { itemChart.destroy(); itemChart = null; }
  $('#item-title').textContent = item.name;
  $('#item-body').innerHTML = itemBodyHtml(item);
  renderItemActions(item);
  if (!dialog.open) dialog.showModal();

  $('#item-ranges').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-range]');
    if (!button) return;
    itemRange = button.dataset.range;
    loadItemHistory(item);
  });
  $('#item-table-details').addEventListener('toggle', renderItemTable);
  $('#item-body').onclick = (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    if (button.dataset.act === 'compare') loadComparables(item, false);
    if (button.dataset.act === 'compare-refresh') loadComparables(item, true);
    if (button.dataset.act === 'use-suggestion') useSuggestion(item, Number(button.dataset.value));
  };
  await Promise.all([loadItemQuotes(item), loadItemHistory(item)]);
}

// ---------------------------------------------------------------------------
// CSFloat comparables (same skin, same valuable stickers)
// ---------------------------------------------------------------------------
async function loadComparables(item, refresh) {
  const box = $('#comparables');
  const button = $('#item-body [data-act="compare"]');
  button.disabled = true;
  box.innerHTML = '<p class="muted small">Searching CSFloat listings and recent sales… (a few seconds)</p>';
  try {
    const data = await api(`/items/${item.id}/comparables`, { params: refresh ? { refresh: 1 } : {} });
    if (box.isConnected) { box.innerHTML = comparablesHtml(data); applySortWithin(box); }
    if (data.applied && data.suggestion) {
      toast(`${item.name} is now valued at ${money(data.suggestion.value)} from these CSFloat results.`, 'ok', 8000);
      app.refreshAll();
    }
  } catch (error) {
    box.innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

function comparablesHtml(data) {
  const cur = data.currency;
  const s = data.suggestion;
  const suggestion = s
    ? `<div class="suggestion">
        <div><div class="stat-label">Suggested value</div><div class="stat-value">${money(s.value, cur)}</div>
          <div class="small muted">Based on ${esc(s.basis)}.</div>
          ${s.notes.map((n) => `<p class="hint">${esc(n)}</p>`).join('')}</div>
        <button type="button" class="btn btn-primary btn-small" data-act="use-suggestion" data-value="${s.value}">Use as this item's custom price</button>
      </div>`
    : `<p class="hint warn-text">No clean comparables right now. Anything with other valuable stickers is set aside because
        they'd inflate the price. Try again later, or set a custom price yourself.</p>`;
  const groups = data.groups.filter((g) => g.rows.length).map((g) => {
    const rows = g.rows.slice(0, 8).map((c, i) => `<tr data-i="${i}" class="${c.clean ? '' : 'dim'}">
        <td class="num strong" data-v="${c.price}">${money(c.price, cur)}</td>
        <td class="num" data-v="${c.float ?? ''}">${c.float !== null && c.float !== undefined ? Number(c.float).toFixed(4) : '—'}</td>
        <td><div class="sticker-list">${c.stickers.map((st) => `<span class="sticker-chip${st.key ? ' key' : ''}" title="${esc(st.name)}">${esc(st.name)}${st.wear ? ` · ${Math.round(st.wear * 100)}% scraped` : ''}</span>`).join('')}</div>
          ${c.clean ? '' : `<div class="sub">Set aside: other stickers worth ~${money(c.extra_value, cur)}${c.unpriced_extras ? ` plus ${c.unpriced_extras} unpriced` : ''}</div>`}</td>
        <td class="muted small" data-v="${esc(c.date || '')}">${esc(longDate(c.date))}</td>
        <td>${safeUrl(c.url) ? `<a href="${esc(safeUrl(c.url))}" target="_blank" rel="noopener noreferrer">View ↗</a>` : ''}</td>
      </tr>`).join('');
    return `<h5 class="group-title">${esc(g.title)} <span class="muted">(${g.rows.length})</span></h5>
      <div class="table-scroll"><table class="data comparables-table" data-sort-id="comparables-${esc(g.id)}"><thead><tr><th class="num" data-sort="num" data-key="price">Price</th><th class="num" data-sort="num" data-key="float">Float</th>
        <th data-sort="text" data-key="stickers">Stickers <span class="muted">(yours highlighted)</span></th><th data-sort="text" data-key="date">${g.sold ? 'Sold' : 'Listed'}</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
  }).join('') || '<p class="muted small">CSFloat has no listings or recent sales of this skin with these stickers.</p>';
  return `${suggestion}${groups}
    <p class="hint">Plain skin: ${money(data.plain_price, cur)}. Matched on: ${data.key_stickers.map((k) => esc(k.name)).join(' + ')}.
      Prices from CSFloat, converted to ${esc(cur)}.${data.cached
        ? ' Cached result. <button type="button" class="link-btn" data-act="compare-refresh">Search again</button>' : ''}</p>`;
}

async function useSuggestion(item, value) {
  try {
    await api(`/items/${item.id}`, { method: 'PUT', body: { custom_price: value, custom_price_currency: state.currency } });
    $('#item-dialog').close();
    toast(`Custom price set to ${money(value)}. Change or clear it any time with Edit.`, 'ok', 8000);
    await app.refreshAll();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function kv(label, value, cls = '') {
  return `<dt class="${cls}">${esc(label)}</dt><dd class="${cls}">${value}</dd>`;
}

function itemBodyHtml(item) {
  const badges = [
    `<span class="badge ${item.source === 'steam' ? 'badge-steam' : ''}">${item.source === 'steam' ? 'Steam inventory' : 'Manual'}</span>`,
    `<span class="badge">${esc(item.category_label)}</span>`,
    item.exterior ? `<span class="badge">${esc(item.exterior)}</span>` : '',
    item.rarity ? `<span class="badge">${esc(item.rarity)}</span>` : '',
    item.source === 'steam' && !item.tradable ? '<span class="badge">Trade-locked / not tradable</span>' : '',
  ].join('');
  const stacked = item.stack_ids && item.stack_ids.length > 1
    ? `<p class="note">This row combines ${number(item.stack_ids.length)} identical entries. Edit and delete apply to the first one.</p>`
    : '';
  const sourceText = item.price_source === 'custom'
    ? '<span class="muted small">custom</span>'
    : item.price_source ? `<span class="muted small">${esc(STRATEGY_LABELS[item.price_source] || item.price_source)}</span>` : '';
  const wholeItem = item.price_source === 'custom' || item.price_source === 'evidence';
  const valuation = [
    wholeItem
      ? kv(item.price_source === 'custom' ? 'Your custom price' : 'Value from CSFloat', `${money(item.unit_price)} ${sourceText}`)
      : kv('Market price', `${money(item.unit_price)} ${sourceText}`),
    wholeItem ? kv('Plain skin (markets)', money(item.market_price)) : '',
    kv('Sticker premium', item.sticker_premium > 0 ? `+${money(item.sticker_premium)}` : (item.sticker_included ? 'included' : '—')),
    kv('Unit value', money(item.unit_value)),
    kv('Quantity', number(item.quantity)),
    item.is_container ? kv('Contents', `${money(item.contents_value)} <span class="muted small">(${number(item.contents_count)} items)</span>`) : '',
    kv('Total value', money(item.display_value), 'total'),
    kv('After fees', item.payout_value !== null
      ? `${money(item.payout_value)} <span class="muted small">selling on ${esc(item.payout_market_label)}</span>`
      : '<span class="muted small">no cash-out price (Steam Wallet only, or not priced yet)</span>'),
    item.purchase_value !== null ? kv('Paid', money(item.purchase_value)) : '',
    item.profit !== null && item.purchase_value ? kv('Profit / loss', `${money(item.profit)} ${deltaHtml((item.profit / item.purchase_value) * 100)}`) : '',
    kv('24 hours', deltaHtml(item.change_24h)),
    kv('7 weeks', deltaHtml(item.change_7w)),
  ].join('');
  const f = floatText(item.float_value);
  const details = [
    kv('Market name', esc(item.market_hash_name)),
    f ? kv('Float', esc(f)) : '',
    item.paint_seed !== null ? kv('Pattern seed', esc(item.paint_seed)) : '',
    item.name_tag ? kv('Name tag', `“${esc(item.name_tag)}”`) : '',
    item.container_name ? kv('Stored in', esc(item.container_name)) : '',
    item.item_type ? kv('Type', esc(item.item_type)) : '',
    kv('Added', esc(longDate(item.created_at))),
    item.notes ? kv('Notes', esc(item.notes)) : '',
  ].join('');
  const thin = item.stickers.some((s) => s.thin);
  const stickerSummary = item.sticker_loose_total ? `
      <dl class="sticker-summary">
        <dt>Loose value of these stickers</dt><dd>${money(item.sticker_loose_total)} <span class="muted small">(cheapest listings)</span></dd>
        <dt>Share by rate</dt><dd>${money(item.sticker_raw_premium)}</dd>
        <dt><strong>Counted in the item's value</strong></dt><dd><strong>${item.sticker_included ? (item.price_source === 'evidence' ? 'in the CSFloat-based value' : 'in your custom price') : money(item.sticker_premium)}</strong></dd>
      </dl>
      ${item.sticker_included && item.price_source === 'custom' ? '<p class="hint">You set a custom price for this item, so it is used as the whole value and the stickers aren\'t added on top.</p>' : ''}
      ${item.sticker_included && item.price_source === 'evidence' ? '<p class="hint">This item is valued from real CSFloat listings and sales with the same stickers (below), so the formula above isn\'t used.</p>' : ''}
      ${item.sticker_no_base ? '<p class="hint warn-text">Not counted yet: the skin itself has no price, so a premium can\'t be judged.</p>' : ''}
      ${item.sticker_capped ? `<p class="hint">Capped at ${money(item.sticker_cap)}: rare stickers on a cheaper skin rarely sell for anything
        close to their loose value, so the premium is limited relative to the skin's own price (change this in Settings).</p>` : ''}
      ${thin ? '<p class="hint warn-text">⚠ Some of these stickers have only one or two listings on a single market, so their prices are asking prices, not proven sales.</p>' : ''}` : '';
  const stickers = item.stickers.length ? `
    <div class="panel"><h4>Applied stickers &amp; charms</h4>
      <div class="table-scroll"><table class="data" data-sort-id="item-stickers"><thead><tr><th data-sort="text" data-key="name">Name</th><th class="num" data-sort="num" data-key="loose">Loose price</th>
      <th class="num" data-sort="num" data-key="rate">Rate</th><th class="num" data-sort="num" data-key="raw">By rate</th><th class="num" data-sort="num" data-key="counted">Counted</th></tr></thead><tbody>
      ${item.stickers.map((s, i) => `<tr data-i="${i}"><td data-v="${esc(s.name)}">${esc(s.name)} <span class="muted small">${esc(s.kind)}${s.scrape ? ` · ${Math.round(s.scrape * 100)}% scraped` : ''}${s.craft ? ' · 4× craft' : ''}</span>${s.thin ? ' <span class="warn-text small" title="Only one or two listings on a single market">⚠ few listings</span>' : ''}</td>
        <td class="num" data-v="${s.loose_price ?? ''}">${money(s.loose_price)}</td><td class="num" data-v="${s.rate ?? ''}">${s.rate !== null ? `${(s.rate * 100).toFixed(1)}%` : '—'}</td>
        <td class="num" data-v="${s.contribution || ''}">${s.contribution ? money(s.contribution) : '—'}</td>
        <td class="num strong" data-v="${s.counted || ''}">${s.counted ? `+${money(s.counted)}` : '—'}</td></tr>`).join('')}
      </tbody></table></div>
      ${stickerSummary}
      <div class="compare-box">
        <div class="compare-head">
          <button type="button" class="btn btn-small" data-act="compare"${state.status?.keys?.csfloat_api_key ? '' : ' disabled'}>Compare on CSFloat</button>
          <span class="muted small">${state.status?.keys?.csfloat_api_key
            ? 'See real listings and recent sales of this skin with the same stickers.'
            : 'Needs a CSFloat API key in .env.'}</span>
        </div>
        <div id="comparables"></div>
      </div>
    </div>` : '';
  return `
    <div class="detail-head">${thumbHtml(item, 'thumb-lg')}
      <div><h3>${esc(item.name)}</h3><div class="badges">${badges}</div></div></div>
    ${stacked}
    <div class="detail-grid">
      <div class="panel"><h4>Valuation</h4><dl class="kv">${valuation}</dl></div>
      <div class="panel"><h4>Details</h4><dl class="kv">${details}</dl></div>
    </div>
    <div class="panel"><h4>Prices across markets</h4>
      <div class="chips market-chips" id="item-market-chips" role="group" aria-label="Show these markets"></div>
      <div id="item-sticker-match"></div><div id="item-cheapest"></div><div id="item-quotes" class="muted">Loading…</div></div>
    ${stickers}
    <div class="panel">
      <div class="card-head"><h4>Price history</h4>
        <div class="segmented" id="item-ranges" role="group" aria-label="History range">
          ${['7d', '30d', '90d', '1y', 'all'].map((r) => `<button type="button" data-range="${r}">${r === 'all' ? 'All' : r.toUpperCase()}</button>`).join('')}
        </div></div>
      <div class="chart-wrap small"><canvas id="item-chart" role="img" aria-label="Price history by market"></canvas>
        <div class="chart-empty" id="item-chart-empty" hidden></div></div>
      <details class="data-details" id="item-table-details"><summary>Show data table</summary>
        <div class="data-table-wrap" id="item-chart-table"></div></details>
    </div>`;
}

function renderItemActions(item) {
  const actions = $('#item-actions');
  const market = `https://steamcommunity.com/market/listings/730/${encodeURIComponent(item.market_hash_name)}`;
  const inspect = safeUrl(item.inspect_link);
  actions.innerHTML = `
    ${item.source === 'manual' ? `<button type="button" class="btn btn-danger" data-act="remove">Remove</button>
      ${item.is_storage ? '' : '<button type="button" class="btn" data-act="move">Move…</button>'}` : ''}
    <span class="spacer"></span>
    ${inspect ? `<a class="btn" href="${esc(inspect)}">Inspect in game</a>` : ''}
    <a class="btn" href="${esc(market)}" target="_blank" rel="noopener noreferrer">Steam Market ↗</a>
    <button type="button" class="btn" data-act="quote">+ Quote</button>
    ${item.is_container && item.container_id === null ? '<button type="button" class="btn" data-act="open">Open container</button>' : ''}
    <button type="button" class="btn btn-primary" data-act="edit">Edit</button>`;
  actions.onclick = async (event) => {
    const act = event.target.closest('[data-act]')?.dataset.act;
    try {
      if (act === 'edit') { $('#item-dialog').close(); editItem(item); }
      if (act === 'open') { $('#item-dialog').close(); containers.openContainer(item.id); }
      if (act === 'quote') addQuote(item.market_hash_name);
      if (act === 'move') { $('#item-dialog').close(); await containers.moveItem(item); }
      if (act === 'remove' && await containers.removeItem(item)) $('#item-dialog').close();
    } catch (error) {
      toast(error.message, 'error');
    }
  };
}

async function loadItemQuotes(item) {
  const box = $('#item-quotes');
  try {
    const data = await api('/quotes', { params: { name: item.market_hash_name, item: item.id } });
    if (!box.isConnected) return;
    box.classList.remove('muted');
    const quotes = data.quotes.map((q) => ({ ...q, price: q.price_base, original: q.price }));
    const missing = data.missing || [];
    renderStickerMatch(data);
    let render = () => {};
    const filter = new MarketFilter($('#item-market-chips'), 'item-quotes', () => render());
    filter.setMarkets([...quotes, ...missing].map((q) => ({ key: q.market, label: q.label })));
    render = () => {
      const shown = quotes.filter((q) => filter.includes(q.market));
      const absent = missing.filter((m) => filter.includes(m.market));
      const top = cheapest(shown, null);
      const sell = shown.filter((q) => q.cash_out && q.net !== null).sort((a, b) => b.net - a.net).slice(0, 3);
      $('#item-cheapest').innerHTML = (top.length
        ? `<div class="cheapest-strip"><span class="filter-label">3 cheapest to buy</span>${top.map((q, i) => `<span class="cheap-pill${i === 0 ? ' first' : ''}">
            <span class="rank">${i + 1}</span> ${money(q.price, data.currency)} <span class="mkt">${esc(q.label)}</span></span>`).join('')}</div>`
        : '') + (sell.length
        ? `<div class="cheapest-strip"><span class="filter-label">Best to sell (cash after fees)</span>${sell.map((q, i) => `<span class="cheap-pill${i === 0 ? ' first' : ''}">
            <span class="rank">${i + 1}</span> ${money(q.net, data.currency)} <span class="mkt">${esc(q.label)}</span></span>`).join('')}</div>`
        : '');
      if (!shown.length && !absent.length) {
        box.innerHTML = '<p class="muted">No markets selected.</p>';
        return;
      }
      const priced = shown.map((q, i) => {
        const url = safeUrl(q.url);
        const name = url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(q.label)}</a>` : esc(q.label);
        const best = i === 0 && shown.length > 1 ? ' <span class="badge badge-best">Cheapest</span>' : '';
        return `<tr data-i="${i}"><td class="num" data-v="${i + 1}">${i + 1}</td><td data-v="${esc(q.label)}">${name}${best}</td>
          <td class="num" data-v="${q.price}">${money(q.price, data.currency)}</td>
          <td class="num" data-v="${q.net ?? ''}" title="${esc(q.fee_note || '')}">${money(q.net, data.currency)}${q.cash_out ? '' : '<div class="sub">Steam Wallet only</div>'}</td>
          <td class="num" data-v="${q.original}">${q.currency !== data.currency ? money(q.original, q.currency) : '<span class="muted">—</span>'}</td>
          <td class="num" data-v="${q.volume ?? ''}">${q.volume !== null ? number(q.volume) : '—'}</td>
          <td class="muted small" data-v="${esc(q.fetched_at)}">${esc(relTime(q.fetched_at))}</td></tr>`;
      });
      const unpriced = absent.map((m, j) => `<tr data-i="${shown.length + j}" class="dim">
          <td class="num" data-v=""></td><td data-v="${esc(m.label)}">${esc(m.label)}</td>
          <td colspan="5" class="muted small" data-v="">${esc(m.status)}</td></tr>`);
      box.innerHTML = `<div class="table-scroll"><table class="data" data-sort-id="item-quotes"><thead><tr>
          <th class="num" data-sort="num" data-key="rank">#</th><th data-sort="text" data-key="market">Market</th>
          <th class="num" data-sort="num" data-key="price">Price (${esc(data.currency)})</th>
          <th class="num" data-sort="num" data-key="net" title="After this site's selling and withdrawal fees - hover a value for the fee details">You'd receive</th>
          <th class="num" data-sort="num" data-key="original">Original</th>
          <th class="num" data-sort="num" data-key="volume">Listings / volume</th>
          <th data-sort="text" data-key="updated">Updated</th></tr></thead>
        <tbody>${priced.concat(unpriced).join('')}</tbody></table></div>
        <p class="hint">"You'd receive" is what selling at that price would leave you with after the site's selling and
          withdrawal fees (hover it for details; change fees in Settings). These are each market's cheapest listing of this skin. Their price lists don't include stickers,
          so ${item.stickers.length ? 'your stickers are valued separately (see the CSFloat sticker match above)' : 'stickers aren\'t reflected here'}.</p>`;
      applySortWithin(box);
    };
    render();
  } catch (error) {
    box.textContent = error.message;
  }
}

/** The CSFloat "closest sticker match" for items whose stickers are valued from market evidence. */
function renderStickerMatch(data) {
  const box = $('#item-sticker-match');
  const m = data.sticker_match;
  if (!box) return;
  if (!m) { box.innerHTML = ''; return; }
  box.innerHTML = m.value
    ? `<div class="suggestion compact"><div>
        <div class="stat-label">CSFloat · same skin, closest sticker match</div>
        <div class="stat-value">${money(m.value, data.currency)}</div>
        <div class="small muted">Based on ${esc(m.basis)} · checked ${esc(relTime(m.fetched_at))}</div>
        ${(m.notes || []).map((n) => `<p class="hint">${esc(n)}</p>`).join('')}</div></div>`
    : `<p class="hint">CSFloat sticker match: ${esc(m.basis)} (checked ${esc(relTime(m.fetched_at))}).</p>`;
}

async function loadItemHistory(item) {
  document.querySelectorAll('#item-ranges button').forEach((b) => b.classList.toggle('active', b.dataset.range === itemRange));
  const empty = $('#item-chart-empty');
  try {
    itemHistory = await api('/history/item', { params: { name: item.market_hash_name, range: itemRange } });
    const canvas = $('#item-chart');
    if (!canvas) return;
    const h = itemHistory;
    const manualKeys = h.series.map((s) => s.market).filter((k) => k.startsWith('manual:')).sort();
    itemChart = renderLineChart(itemChart, canvas, {
      labels: h.labels,
      series: h.series.map((s) => ({ key: s.market, label: s.label, values: s.values })),
      currency: h.currency,
      manualKeys,
    });
    empty.hidden = h.labels.length > 0 && chartsAvailable();
    empty.textContent = chartsAvailable()
      ? 'No price history for this item yet. History builds up each day prices are refreshed.'
      : 'Charts could not load (Chart.js is fetched from cdn.jsdelivr.net).';
    renderItemTable();
  } catch (error) {
    toast(error.message, 'error');
  }
}

function renderItemTable() {
  const details = $('#item-table-details');
  if (!details || !details.open || !itemHistory) return;
  $('#item-chart-table').innerHTML = dataTableHtml(itemHistory.labels, itemHistory.series, itemHistory.currency, 'chart-item');
  applySortWithin($('#item-chart-table'));
}

// ---------------------------------------------------------------------------
// Add / edit item
// ---------------------------------------------------------------------------
function stickerRowHtml(sticker = {}) {
  const kinds = (state.status?.applied_kinds || ['sticker', 'patch', 'charm']);
  return `<div class="sticker-row">
    <select class="input" data-f="kind" aria-label="Type">${kinds.map((k) => `<option value="${k}"${sticker.kind === k ? ' selected' : ''}>${k[0].toUpperCase()}${k.slice(1)}</option>`).join('')}</select>
    <input class="input" data-f="name" list="sticker-suggestions" maxlength="200" placeholder="e.g. Crown (Foil)" aria-label="Sticker name" value="${esc(sticker.name || '')}">
    <input class="input" data-f="scrape" type="number" min="0" max="100" step="1" placeholder="Scrape %" title="How scraped it is: 0 = pristine, 100 = fully scraped" aria-label="Scrape percent" value="${sticker.scrape ? Math.round(sticker.scrape * 100) : ''}">
    <button type="button" class="btn btn-icon btn-small" data-remove aria-label="Remove">×</button>
  </div>`;
}

function setupEditForm() {
  const form = $('#edit-form');
  const rows = $('#sticker-rows');
  $('#add-sticker').addEventListener('click', () => {
    if (rows.children.length >= 6) { toast('At most 6 stickers/charms per item', 'warn'); return; }
    rows.insertAdjacentHTML('beforeend', stickerRowHtml());
  });
  rows.addEventListener('click', (event) => {
    if (event.target.closest('[data-remove]')) event.target.closest('.sticker-row').remove();
  });

  form.elements.market_hash_name.addEventListener('input', (e) => suggestNames(e.target, 'name-suggestions'));
  rows.addEventListener('input', (event) => {
    if (event.target.matches('[data-f="name"]')) {
      const kind = event.target.closest('.sticker-row').querySelector('[data-f="kind"]').value;
      suggestNames(event.target, 'sticker-suggestions', kind);
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = $('#edit-error');
    const isSteam = editing && editing.source === 'steam';
    const fields = isSteam ? STEAM_EDITABLE : ALL_FIELDS;
    const payload = {};
    for (const name of fields) payload[name] = form.elements[name].value;
    if (!isSteam) {
      if (!payload.market_hash_name.trim()) { showError(error, 'Item name is required.'); return; }
      payload.container_id = payload.container_id || null;
      payload.stickers = [...rows.querySelectorAll('.sticker-row')].map((row) => ({
        kind: row.querySelector('[data-f="kind"]').value,
        name: row.querySelector('[data-f="name"]').value,
        scrape: row.querySelector('[data-f="scrape"]').value,
      })).filter((s) => s.name.trim());
    }
    try {
      let result;
      if (editing) result = await api(`/items/${editing.id}`, { method: 'PUT', body: payload });
      else result = await api('/items', { method: 'POST', body: payload });
      $('#edit-dialog').close();
      toast(editing ? 'Item updated' : result.stacked ? `Added - stacked onto the ${result.item.name} you already had (now ${result.item.quantity})` : 'Item added', 'ok');
      await Promise.all([app.refreshAll(), containers.reopenIfOpen()]);
    } catch (err) {
      showError(error, err.message);
    }
  });
}

export async function editItem(item, defaults = {}) {
  editing = item;
  const form = $('#edit-form');
  const isSteam = item && item.source === 'steam';
  form.reset();
  showError($('#edit-error'), '');
  populateCurrencies();

  form.elements.category.innerHTML = `<option value="">Auto-detect</option>${CATEGORY_FILTERS
    .filter(([k]) => k !== 'in_container').map(([k, label]) => `<option value="${k}">${esc(label.replace(/s$/, ''))}</option>`).join('')}`;
  form.elements.exterior.innerHTML = `<option value="">Not applicable / from float</option>${(state.status?.exteriors || [])
    .map((e) => `<option value="${esc(e)}">${esc(e)}</option>`).join('')}`;

  const isContainer = (item?.category || defaults.category) === 'container';
  $('#edit-title').textContent = item ? `Edit ${item.name}` : isContainer ? 'Add container' : 'Add item';
  $('#edit-steam-note').hidden = !isSteam;
  form.querySelectorAll('.manual-only').forEach((el) => { el.hidden = Boolean(isSteam); });
  // A container that already holds items can't itself be stored (one level deep only).
  const holdsItems = Boolean(item && item.contents_count > 0);
  $('#container-field').hidden = Boolean(isSteam) || holdsItems;

  let storageList = [];
  let capacity = 1000;
  if (!isSteam && !holdsItems) {
    try {
      ({ containers: storageList, capacity } = await api('/containers'));
    } catch { /* optional */ }
  }
  form.elements.container_id.innerHTML = `<option value="">Not inside a container</option>${storageList
    .filter((c) => !item || c.id !== item.id)
    .map((c) => `<option value="${c.id}">${esc(c.label)}${c.source === 'steam' ? ' (Steam)' : ''} · ${number(c.contents_count)}/${number(capacity)}</option>`).join('')}`;

  const values = { ...defaults, ...(item || {}) };
  for (const name of ALL_FIELDS) {
    const el = form.elements[name];
    const value = values[name];
    if (value === null || value === undefined) continue;
    if (name === 'category' && !item && !defaults.category) continue;
    el.value = String(value);
  }
  if (!values.custom_price_currency) form.elements.custom_price_currency.value = state.currency;
  if (!values.purchase_currency) form.elements.purchase_currency.value = state.currency;
  if (!item && !values.quantity) form.elements.quantity.value = '1';

  $('#sticker-rows').innerHTML = (item?.stickers || []).map((s) => stickerRowHtml(s)).join('');
  const dialog = $('#edit-dialog');
  markClean(dialog);
  if (!dialog.open) dialog.showModal();
  (isSteam ? form.elements.float_value : form.elements.market_hash_name).focus();
}

// ---------------------------------------------------------------------------
// Manual quotes
// ---------------------------------------------------------------------------
function setupQuoteForm() {
  const form = $('#quote-form');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form).entries());
    try {
      await api('/quotes', { method: 'POST', body: payload });
      $('#quote-dialog').close();
      toast(`Saved ${payload.market_label} quote`, 'ok');
      app.refreshAll();
    } catch (error) {
      showError($('#quote-error'), error.message);
    }
  });
  form.elements.market_hash_name.addEventListener('input', debounce(async (e) => {
    const q = e.target.value.trim();
    if (q.length < 2) return;
    try {
      const { names } = await api('/names', { params: { q } });
      $('#name-suggestions').innerHTML = names.map((n) => `<option value="${esc(n)}"></option>`).join('');
    } catch { /* optional */ }
  }, 250));
}

export function addQuote(name = '') {
  const form = $('#quote-form');
  form.reset();
  showError($('#quote-error'), '');
  populateCurrencies();
  form.elements.market_hash_name.value = name;
  form.elements.currency.value = state.status.currencies.includes('CNY') ? 'CNY' : state.currency;
  markClean($('#quote-dialog'));
  $('#quote-dialog').showModal();
  (name ? form.elements.market_label : form.elements.market_hash_name).focus();
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Selling & cash-out fees (inside Settings)
// ---------------------------------------------------------------------------
const pct = (rate) => +(rate * 100).toFixed(2);

function renderFees(fees) {
  $('#fees-intro').textContent = `Used for "After fees" and "You'd receive". Defaults come from public fee information `
    + `(${fees.checked}); marketplaces change their fees, so adjust them to match your account. Steam adds its fee on `
    + 'top of what you receive, and Steam Wallet money can\'t be withdrawn.';
  $('#settings-fees').innerHTML = fees.markets.map((m) => `<tr data-market="${esc(m.key)}"
      data-sale="${pct(m.sale)}" data-withdraw="${pct(m.withdraw)}" data-cash="${m.cash_out}">
      <td>${esc(m.label)}${m.custom ? ' <span class="badge">edited</span>' : ''}<div class="sub" title="${esc(m.note)}">${esc(m.note)}</div></td>
      <td class="num"><input class="input fee-input" data-f="sale" type="number" step="0.1" min="0" max="50" value="${pct(m.sale)}"
        aria-label="${esc(m.label)} selling fee">${m.tiered ? '<div class="sub">6% on €1,000+</div>' : ''}</td>
      <td class="num"><input class="input fee-input" data-f="withdraw" type="number" step="0.1" min="0" max="50"
        value="${pct(m.withdraw)}" aria-label="${esc(m.label)} withdrawal fee"></td>
      <td><input type="checkbox" data-f="cash_out"${m.cash_out ? ' checked' : ''} aria-label="Money from ${esc(m.label)} can be withdrawn"></td>
    </tr>`).join('');
}

/** Only values you actually changed become overrides, so tiered defaults (Skinport) keep working. */
function collectFees() {
  if ($('#settings-fees').dataset.reset === '1') return {};
  const overrides = { ...(state.status.settings.selling_fees || {}) };
  for (const row of document.querySelectorAll('#settings-fees tr[data-market]')) {
    const key = row.dataset.market;
    const entry = { ...(overrides[key] || {}) };
    const sale = Number(row.querySelector('[data-f="sale"]').value);
    const withdraw = Number(row.querySelector('[data-f="withdraw"]').value);
    const cash = row.querySelector('[data-f="cash_out"]').checked;
    if (sale !== Number(row.dataset.sale)) entry.sale = sale / 100;
    if (withdraw !== Number(row.dataset.withdraw)) entry.withdraw = withdraw / 100;
    if (String(cash) !== row.dataset.cash) entry.cash_out = cash;
    if (Object.keys(entry).length) overrides[key] = entry;
  }
  return overrides;
}

function setupSettingsForm() {
  const form = $('#settings-form');
  $('#fees-reset').addEventListener('click', () => {
    $('#settings-fees').dataset.reset = '1';
    toast('Fees will be reset to the defaults when you save.', 'info');
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const el = form.elements;
    const payload = {
      base_currency: el.base_currency.value,
      valuation_strategy: el.valuation_strategy.value,
      sticker_rate_min: Number(el.sticker_rate_min.value) / 100,
      sticker_rate_max: Number(el.sticker_rate_max.value) / 100,
      sticker_premium_cap: Number(el.sticker_premium_cap.value) / 100,
      sticker_evidence: el.sticker_evidence.checked,
      selling_fees: collectFees(form),
      steam_id: el.steam_id.value.trim(),
      enabled_markets: [...form.querySelectorAll('input[name="market"]:checked')].map((i) => i.value),
    };
    try {
      const result = await api('/settings', { method: 'PUT', body: payload });
      $('#settings-dialog').close();
      toast(result.job ? 'Settings saved - fetching prices for the newly enabled markets' : 'Settings saved', 'ok');
      if (result.job) app.watchJob(result.job);
      await app.refreshAll();
    } catch (error) {
      showError($('#settings-error'), error.message);
    }
  });
}

export function openSettings() {
  const form = $('#settings-form');
  const { settings, markets, keys } = state.status;
  showError($('#settings-error'), '');
  populateCurrencies();
  form.elements.base_currency.value = settings.base_currency;
  form.elements.valuation_strategy.value = settings.valuation_strategy;
  form.elements.sticker_rate_min.value = +(settings.sticker_rate_min * 100).toFixed(2);
  form.elements.sticker_rate_max.value = +(settings.sticker_rate_max * 100).toFixed(2);
  form.elements.sticker_premium_cap.value = +(settings.sticker_premium_cap * 100).toFixed(1);
  form.elements.steam_id.value = settings.steam_id || '';
  form.elements.sticker_evidence.checked = settings.sticker_evidence !== false;
  const marketState = (m) => {
    if (!m.available) return `<span class="small missing-text">${esc(m.reason)}</span>`;
    if (m.quotes) return `<span class="small ok-text">${number(m.quotes)} prices · updated ${esc(relTime(m.last_fetched))}</span>`;
    if (m.last_attempt) return `<span class="small warn-text">no prices yet · last tried ${esc(relTime(m.last_attempt))}</span>`;
    return '<span class="small missing-text">no prices yet - press Refresh prices</span>';
  };
  $('#settings-markets').innerHTML = markets.map((m) => `
    <label class="market-option check">
      <span><input type="checkbox" name="market" value="${esc(m.key)}"${m.enabled ? ' checked' : ''}> ${esc(m.label)}</span>
      ${marketState(m)}
    </label>`).join('');
  delete $('#settings-fees').dataset.reset;
  renderFees(state.status.fees);
  const line = (ok, label, help) => `<li><span class="${ok ? 'ok-text' : 'missing-text'}">${ok ? '✓' : '○'} ${esc(label)}</span> - ${help}</li>`;
  const legacy = keys.legacy_steam_api_key
    ? `<li><span class="missing-text">⚠ STEAM_API_KEY</span> - <strong>no longer used.</strong> Delete that line from
        <code>.env</code> and revoke the key at steamcommunity.com/dev/apikey so it can't be misused.</li>`
    : '';
  $('#settings-keys').innerHTML = `<strong>API keys</strong> live in the <code>.env</code> file next to <code>app.py</code>
    (never in the browser). Restart the app after editing it.
    <ul>
      ${line(keys.csfloat_api_key, 'CSFloat API key', keys.csfloat_api_key ? 'configured.' : 'not set. Get one free at csfloat.com → Profile → Developers, then set <code>CSFLOAT_API_KEY</code>.')}
      ${legacy}
      <li><span class="ok-text">✓ Steam, Skinport, Market.CSGO, Waxpeer &amp; exchange rates</span> - no key needed.</li>
      <li><span class="missing-text">BUFF163</span> - can only be read while logged in, so it is never automated.
        Add BUFF prices with <em>+ Manual quote</em> instead.</li>
    </ul>`;
  markClean($('#settings-dialog'));
  $('#settings-dialog').showModal();
}
