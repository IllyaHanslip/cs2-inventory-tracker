// Price alerts: "tell me when this sells for above / below X" (after fees, like the Price column).
// Set them from an item's details; the bell at the top lists them and counts the ones that have
// gone off since you last looked. They're checked after every price refresh.

import { api } from './api.js';
import { esc, money, relTime, state, thumbHtml, toast } from './util.js';

const $ = (selector) => document.querySelector(selector);
let openItemByName = () => {};

export function initAlerts({ openItem }) {
  openItemByName = openItem;
  $('#alerts-btn').addEventListener('click', () => openAlerts());
  $('#alerts-body').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-act]');
    if (!button) return;
    const id = button.dataset.id;
    try {
      if (button.dataset.act === 'alert-open') {
        $('#alerts-dialog').close();
        openItemByName(button.dataset.name);
      } else if (button.dataset.act === 'alert-delete') {
        await api(`/alerts/${id}`, { method: 'DELETE' });
        await openAlerts({ refreshOnly: true });
      } else if (button.dataset.act === 'alert-rearm') {
        await api(`/alerts/${id}/rearm`, { method: 'POST' });
        toast('Turned back on: it goes off again when the price is reached.', 'ok');
        await openAlerts({ refreshOnly: true });
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  });
}

/** The count on the bell: alerts that went off since you last opened the list. */
export function updateBell(count) {
  const badge = $('#alerts-count');
  badge.hidden = !count;
  badge.textContent = count ? String(count) : '';
  $('#alerts-btn').setAttribute('aria-label', count ? `Price alerts (${count} went off)` : 'Price alerts');
}

function conditionText(a, currency) {
  return `sells for ${a.direction} ${money(a.price, currency)}`;
}

function statusHtml(a, currency) {
  if (a.triggered_at) {
    return `<span class="badge badge-warn">Went off</span> <span class="muted small">${esc(relTime(a.triggered_at))}${
      a.triggered_value !== null ? `, at ${money(a.triggered_value, currency)}` : ''}</span>`;
  }
  return '<span class="muted small">Waiting</span>';
}

export async function openAlerts({ refreshOnly = false } = {}) {
  const dialog = $('#alerts-dialog');
  const body = $('#alerts-body');
  if (!refreshOnly) {
    body.innerHTML = '<p class="muted">Loading…</p>';
    dialog.showModal();
  }
  let data;
  try {
    data = await api('/alerts');
  } catch (error) {
    body.innerHTML = `<p class="muted">${esc(error.message)}</p>`;
    return;
  }
  const cur = data.currency;
  body.innerHTML = `<p class="muted small">You'll be told (here and with a Windows notification) when an item sells for above or
      below a price you choose: what you'd receive for one after fees, selling where it pays most (the Price column).
      Set one from any item's details. Prices are checked after every refresh.</p>
    ${data.alerts.length ? `<div class="table-scroll"><table class="data alerts-table"><thead><tr>
        <th class="col-icon"><span class="sr-only">Image</span></th><th>Item</th><th>Tell me when it</th>
        <th class="num">Now</th><th>Status</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${data.alerts.map((a) => `<tr class="${a.triggered_at && !a.seen ? 'fresh' : ''}">
        <td>${thumbHtml({ icon: a.icon, category: 'other' })}</td>
        <td><button type="button" class="link-btn" data-act="alert-open" data-name="${esc(a.name)}" title="Show details">${esc(a.name)}</button></td>
        <td>${esc(conditionText(a, cur))}</td>
        <td class="num">${money(a.now, cur)}</td>
        <td>${statusHtml(a, cur)}</td>
        <td><div class="row-actions-cell">
          ${a.triggered_at ? `<button type="button" class="btn btn-small" data-act="alert-rearm" data-id="${a.id}" title="Go off again the next time the price is reached">Turn on again</button>` : ''}
          <button type="button" class="icon-btn danger" data-act="alert-delete" data-id="${a.id}" aria-label="Delete the alert for ${esc(a.name)}" title="Delete">×</button>
        </div></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty"><strong>No price alerts yet</strong>Open any item\'s details and use <em>Price alert</em> to set one.</div>'}`;
  if (data.unseen) {
    try {
      await api('/alerts/seen', { method: 'POST' });
    } catch { /* the live demo can't save this */ }
  }
  updateBell(0);
}

/** The "Price alert" section of an item's details: its alerts, and a form to add one. */
export async function renderItemAlerts(box, item) {
  if (!box) return;
  let data;
  try {
    data = await api('/alerts');
  } catch {
    box.innerHTML = '';
    return;
  }
  if (!box.isConnected) return;
  const cur = data.currency || state.currency;
  const mine = data.alerts.filter((a) => a.name === item.market_hash_name);
  const now = item.sell_quotes?.[0]?.net ?? item.unit_value;
  box.innerHTML = `<h4>Price alert</h4>
    ${mine.length ? `<ul class="item-alerts">${mine.map((a) => `<li>Tell me when one ${esc(conditionText(a, cur))} · ${statusHtml(a, cur)}
        <button type="button" class="icon-btn danger" data-alert-delete="${a.id}" aria-label="Delete this alert" title="Delete">×</button></li>`).join('')}</ul>` : ''}
    <form class="alert-form" autocomplete="off">
      <span>Tell me when one sells for</span>
      <select class="input select-small" name="direction" aria-label="Above or below">
        <option value="above">above</option><option value="below">below</option></select>
      <input class="input" name="price" type="number" min="0" step="any" inputmode="decimal" required
        placeholder="${now ? esc(String(Math.round(now * 100) / 100)) : 'price'}" aria-label="Price (${esc(cur)}, after fees)">
      <span class="muted small">${esc(cur)} after fees${now ? ` (now ${money(now, cur)})` : ''}</span>
      <button type="submit" class="btn btn-small btn-primary">Set alert</button>
    </form>`;
  box.querySelector('.alert-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const price = Number(form.elements.price.value);
    if (!(price > 0)) { toast('Enter a price above zero.', 'warn'); return; }
    try {
      const result = await api('/alerts', { method: 'POST', body: { name: item.market_hash_name, direction: form.elements.direction.value, price } });
      if (result.fired?.length) {
        toast(`Alert set - and it's already ${form.elements.direction.value} ${money(price, cur)}, so it went off straight away.`, 'warn', 9000);
        refreshBell();
      } else {
        toast(`Alert set: you'll be told when ${item.name} sells for ${form.elements.direction.value} ${money(price, cur)}.`, 'ok');
      }
      renderItemAlerts(box, item);
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  box.querySelectorAll('[data-alert-delete]').forEach((button) => button.addEventListener('click', async () => {
    try {
      await api(`/alerts/${button.dataset.alertDelete}`, { method: 'DELETE' });
      renderItemAlerts(box, item);
    } catch (error) {
      toast(error.message, 'error');
    }
  }));
}

async function refreshBell() {
  try {
    const status = await api('/status');
    updateBell(status.alerts_unseen || 0);
  } catch { /* ignore */ }
}

/** After a price refresh: say which alerts went off. */
export function announceFired(fired) {
  for (const a of (fired || []).slice(0, 3)) {
    toast(`Price alert: ${a.name} now sells for ${money(a.value, a.currency)} (${a.direction} ${money(a.price, a.currency)}).`, 'warn', 15000,
      { action: { label: 'Show alerts', key: 'B', run: () => openAlerts() } });
  }
  if ((fired || []).length > 3) toast(`…and ${fired.length - 3} more price alerts went off.`, 'warn', 15000);
}
