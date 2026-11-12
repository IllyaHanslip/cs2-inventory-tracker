// Containers: the Containers tab, the container manager (add / move / remove items),
// and the shared remove / move / delete flows, each with a confirmation step.

import { api } from './api.js';
import { confirmDialog, editItem, openItem, suggestNames } from './dialogs.js';
import { MarketFilter, availableMarkets, cheapest, cheapestHtml } from './market-filter.js';
import { applySortWithin, cycleSort, getSort, sortItems } from './sortable.js';
import { esc, money, number, state, thumbHtml, toast } from './util.js';

let app;
const manager = { id: null, data: null, pendingRows: null, filter: null };
const $ = (selector) => document.querySelector(selector);

export const ICONS = {
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  move: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>',
};

export function crateSvg(cls = 'crate') {
  return `<svg class="${cls}" viewBox="0 0 92 80" aria-hidden="true">
    <g class="glow"><ellipse cx="46" cy="32" rx="36" ry="13" fill="#3987e5" opacity=".35"/></g>
    <path d="M10 34h72v38a4 4 0 0 1-4 4H14a4 4 0 0 1-4-4z" fill="#262b36" stroke="#3a404c" stroke-width="2"/>
    <path d="M10 50h72" stroke="#3a404c" stroke-width="2"/>
    <rect x="40" y="44" width="12" height="11" rx="2" fill="#3987e5"/>
    <g class="lid"><rect x="7" y="21" width="78" height="13" rx="3" fill="#2f3542" stroke="#3a404c" stroke-width="2"/></g>
  </svg>`;
}

export function capacityHtml(used, capacity) {
  const pct = Math.min(100, (used / capacity) * 100);
  const cls = used >= capacity ? 'full' : pct >= 90 ? 'near-full' : '';
  const note = used >= capacity ? 'Full' : pct >= 90 ? `Almost full · ${number(capacity - used)} free` : `${number(capacity - used)} free`;
  return `<div class="capacity">
    <div class="capacity-text"><span><strong>${number(used)}</strong> / ${number(capacity)} items</span><span>${note}</span></div>
    <div class="capacity-bar ${cls}" role="progressbar" aria-label="Space used" aria-valuemin="0"
      aria-valuemax="${capacity}" aria-valuenow="${used}"><div style="width:${pct.toFixed(1)}%"></div></div>
  </div>`;
}

export function init(appRef) {
  app = appRef;
  setupNewContainerForm();
  setupManagerEvents();
}

/** Re-render the container manager if it's open (e.g. after editing one of its items). */
export function reopenIfOpen() {
  if ($('#container-dialog').open && manager.id) return openContainer(manager.id, { animate: false });
  return Promise.resolve();
}

/** Refresh everything after a change, keeping an open container manager up to date. */
async function afterChange() {
  const work = [app.refreshAll()];
  if ($('#container-dialog').open && manager.id) work.push(openContainer(manager.id, { animate: false }));
  await Promise.all(work);
}

// ---------------------------------------------------------------------------
// Containers tab
// ---------------------------------------------------------------------------
const CARD_SORTS = [['name', 'Name'], ['items', 'Items'], ['value', 'Stored value'], ['free', 'Free space']];
const CARD_GETTERS = {
  name: { type: 'text', get: (c) => c.name_tag || c.name },
  items: { type: 'num', get: (c) => c.contents_count },
  value: { type: 'num', get: (c) => c.contents_value },
  free: { type: 'num', get: (c) => c.capacity - c.contents_count },
};

export class ContainersView {
  constructor(root) {
    this.root = root;
    this.data = null;
    this.loaded = false;
    root.querySelector('#ct-new').addEventListener('click', () => newContainer());
    root.querySelector('#ct-sort').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-key]');
      if (!button || !this.data) return;
      cycleSort('container-cards', button.dataset.key);
      this.render();
    });
    root.querySelector('#ct-grid').addEventListener('click', (event) => {
      const button = event.target.closest('[data-act]');
      if (!button) return;
      const card = this.data?.containers.find((c) => String(c.id) === button.dataset.id);
      const act = button.dataset.act;
      if (act === 'new') newContainer();
      else if (card && act === 'open') openContainer(card.id);
      else if (card && act === 'add') openContainer(card.id, { focusAdd: true });
      else if (card && act === 'delete') deleteContainer(card);
    });
  }

  async reload() {
    const grid = this.root.querySelector('#ct-grid');
    grid.classList.add('is-loading');
    try {
      this.data = await api('/containers');
      this.loaded = true;
      this.render();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      grid.classList.remove('is-loading');
    }
  }

  render() {
    const { containers, summary, currency } = this.data;
    $('#tab-containers-count').textContent = containers.length ? String(containers.length) : '';
    const tile = (label, value, foot) => `<div class="stat"><div class="stat-label">${esc(label)}</div>
      <div class="stat-value">${value}</div><div class="stat-foot">${foot}</div></div>`;
    this.root.querySelector('#ct-stats').innerHTML = [
      tile('Containers', number(summary.containers), '<span>storage units and other containers</span>'),
      tile('Items stored', number(summary.items_stored), '<span>across all containers</span>'),
      tile('Stored value', money(summary.value_stored, currency), '<span>included in your Total Portfolio</span>'),
      tile('Space used', summary.capacity ? `${((summary.items_stored / summary.capacity) * 100).toFixed(1)}%` : '—',
        `<span>${number(summary.items_stored)} of ${number(summary.capacity)} slots</span>`),
    ].join('');
    const sort = getSort('container-cards');
    this.root.querySelector('#ct-sort').innerHTML = `<span class="filter-label">Sort</span>${CARD_SORTS.map(([key, label]) => {
      const active = sort && sort.key === key;
      const arrow = active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '';
      return `<button type="button" class="chip" data-key="${key}" aria-pressed="${Boolean(active)}"
        title="Click to sort - again to reverse, a third time to clear">${label}${arrow}</button>`;
    }).join('')}`;
    const sorted = sortItems(containers, 'container-cards', CARD_GETTERS);
    this.root.querySelector('#ct-grid').innerHTML = sorted.map((c) => this.cardHtml(c, currency)).join('')
      + `<button type="button" class="container-card new-card" data-act="new">
          <span class="plus">+</span><strong>New container</strong>
          <span class="small">Record what's inside a Storage Unit</span></button>`;
  }

  cardHtml(c, currency) {
    const steam = c.source === 'steam';
    const preview = c.preview.length
      ? `<ul class="preview">${c.preview.map((p) => `<li><span class="qty">${number(p.quantity)}×</span> ${esc(p.name)}</li>`).join('')}
         ${c.kinds > c.preview.length ? `<li class="muted">+ ${number(c.kinds - c.preview.length)} more</li>` : ''}</ul>`
      : '<p class="muted small">Empty. Add the items you keep in it.</p>';
    let remove = `<button type="button" class="btn btn-small btn-danger" data-act="delete" data-id="${c.id}">Delete</button>`;
    if (steam) {
      remove = c.contents_count
        ? `<button type="button" class="btn btn-small btn-danger" data-act="delete" data-id="${c.id}">Empty</button>` : '';
    }
    return `<article class="container-card">
      <header>${crateSvg('crate-mini')}
        <div><h3 title="${esc(c.label)}">${esc(c.name_tag || c.name)}</h3>
          <div class="badges"><span class="badge ${steam ? 'badge-steam' : ''}">${steam ? 'Steam' : 'Manual'}</span>
            <span class="badge">${esc(c.name)}</span></div></div>
      </header>
      ${capacityHtml(c.contents_count, c.capacity)}
      <div class="card-value"><span class="value">${money(c.contents_value, currency)}</span><span class="muted small">stored value</span></div>
      ${preview}
      <footer>
        <button type="button" class="btn btn-small btn-primary" data-act="open" data-id="${c.id}">Open</button>
        <button type="button" class="btn btn-small" data-act="add" data-id="${c.id}">+ Add items</button>
        ${remove}
      </footer>
    </article>`;
  }
}

// ---------------------------------------------------------------------------
// New container
// ---------------------------------------------------------------------------
function setupNewContainerForm() {
  const form = $('#new-container-form');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const error = $('#new-container-error');
    const payload = {
      market_hash_name: form.elements.market_hash_name.value.trim() || 'Storage Unit',
      name_tag: form.elements.name_tag.value.trim(),
      category: 'container',
      is_storage: true,
    };
    try {
      const { item } = await api('/items', { method: 'POST', body: payload });
      $('#new-container-dialog').close();
      toast(`Created ${payload.name_tag || payload.market_hash_name}`, 'ok');
      app.refreshAll();
      openContainer(item.id, { focusAdd: true });
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  });
}

export function newContainer() {
  const form = $('#new-container-form');
  form.reset();
  $('#new-container-error').hidden = true;
  $('#new-container-dialog').showModal();
  form.elements.name_tag.focus();
}

// ---------------------------------------------------------------------------
// Container manager
// ---------------------------------------------------------------------------
function addRowHtml(row = {}) {
  return `<div class="add-row">
    <input class="input" data-f="name" list="name-suggestions" maxlength="200" autocomplete="off"
      placeholder="Item name, e.g. Recoil Case" aria-label="Item name" value="${esc(row.name || '')}">
    <input class="input" data-f="qty" type="number" min="1" max="1000" step="1" value="${esc(row.qty || 1)}" aria-label="Quantity">
    <button type="button" class="icon-btn danger" data-act="remove-row" aria-label="Remove this row">${ICONS.trash}</button>
  </div>`;
}

function collectRows() {
  return [...document.querySelectorAll('#add-rows .add-row')].map((row) => ({
    name: row.querySelector('[data-f="name"]').value,
    qty: row.querySelector('[data-f="qty"]').value,
  }));
}

export async function openContainer(id, { focusAdd = false, animate = true } = {}) {
  const dialog = $('#container-dialog');
  const body = $('#container-body');
  const switching = manager.id !== id;
  manager.pendingRows = dialog.open && !switching ? collectRows() : null;
  manager.id = id;
  if (!dialog.open) {
    body.innerHTML = `<div class="crate-stage">${crateSvg()}<p class="muted">Opening…</p></div>`;
    $('#container-actions').innerHTML = '';
    dialog.showModal();
  }
  let data;
  let candidates = [];
  try {
    [data, candidates] = await Promise.all([
      api(`/containers/${id}`),
      api('/items', { params: { scope: 'total' } }).then((r) => r.items.filter((i) => i.source === 'manual'
        && i.id !== id && i.container_id !== id && !i.is_storage && !(i.is_container && i.contents_count > 0))),
    ]);
  } catch (error) {
    toast(error.message, 'error');
    if (switching || !manager.data) dialog.close();
    return;
  }
  manager.data = data;
  renderManager(data, candidates, { animate: animate && switching });
  if (focusAdd) body.querySelector('#add-rows [data-f="name"]')?.focus();
}

function renderManager({ container: c, contents, currency, capacity }, candidates, { animate }) {
  const steam = c.source === 'steam';
  const used = c.contents_count;
  const room = Math.max(capacity - used, 0);
  $('#container-title').textContent = c.label;
  const rows = manager.pendingRows && manager.pendingRows.length ? manager.pendingRows : [{}];
  $('#container-body').innerHTML = `
    <div class="crate-stage manager-top">${crateSvg()}
      <div class="capacity-wrap">
        ${capacityHtml(used, capacity)}
        <div class="crate-summary" style="margin-top:12px">
          <div class="stat"><div class="stat-label">Stored value</div><div class="stat-value">${money(c.contents_value, currency)}</div></div>
          <div class="stat"><div class="stat-label">Container itself</div><div class="stat-value">${money(c.total_value, currency)}</div></div>
          <div class="stat"><div class="stat-label">Total</div><div class="stat-value">${money(c.display_value, currency)}</div></div>
        </div>
      </div>
    </div>
    <section class="panel">
      <h4>Add items</h4>
      <div id="add-rows">${rows.map(addRowHtml).join('')}</div>
      <div class="add-actions">
        <button type="button" class="btn btn-small" data-act="add-row">+ Another item</button>
        <button type="button" class="link-btn small" data-act="add-detailed">Add one with stickers, float or a name tag…</button>
        <span class="spacer"></span>
        <span class="muted small">${number(room)} spaces left</span>
        <button type="button" class="btn btn-primary" data-act="add-submit"${room ? '' : ' disabled'}>Add to container</button>
      </div>
      <p class="form-error" id="add-error" role="alert" hidden></p>
      <p class="hint">Adding an item that's already in here stacks onto it, so 7 + 21 becomes 28.</p>
      ${candidates.length ? `<div class="move-here">
        <select class="input" id="move-candidate" aria-label="Item to move into this container">
          <option value="">…or move an item you've already recorded in here</option>
          ${candidates.map((i) => `<option value="${i.id}">${esc(i.name)} ×${number(i.quantity)}${i.container_name ? ` (in ${esc(i.container_name)})` : ''}</option>`).join('')}
        </select>
        <button type="button" class="btn" data-act="move-in">Move in</button></div>` : ''}
    </section>
    <section class="panel">
      <div class="card-head"><h4>Contents · ${number(contents.length)} different item${contents.length === 1 ? '' : 's'}</h4>
        ${contents.length > 5 ? '<input type="search" class="input" id="contents-search" placeholder="Search this container…" aria-label="Search this container" style="max-width:240px">' : ''}</div>
      ${contents.length ? '<div class="chips market-chips" id="contents-markets" role="group" aria-label="Compare prices on these markets"></div>' : ''}
      <div id="contents-wrap"></div>
    </section>`;
  const chips = $('#contents-markets');
  manager.filter = null;
  if (chips) {
    manager.filter = new MarketFilter(chips, 'container-contents', () => renderContents(manager.data.contents, currency));
    manager.filter.setMarkets(availableMarkets(state.status, contents.map((i) => i.quotes)));
  }
  renderContents(contents, currency, animate);

  const removeLabel = steam ? 'Empty container' : 'Delete container';
  const canRemove = !steam || used > 0;
  $('#container-actions').innerHTML = `
    ${canRemove ? `<button type="button" class="btn btn-danger" data-act="delete-container">${removeLabel}</button>` : ''}
    <span class="spacer"></span>
    <button type="button" class="btn" data-act="close">Close</button>`;
  const stage = $('#container-body .crate-stage');
  if (animate) requestAnimationFrame(() => requestAnimationFrame(() => stage.classList.add('opened')));
  else stage.classList.add('opened');
}

/** The contents table on its own, so changing the market chips doesn't reset the add-items rows. */
function renderContents(contents, currency, animate = false) {
  const wrap = $('#contents-wrap');
  if (!wrap) return;
  wrap.innerHTML = contents.length ? `
    <div class="table-scroll"><table class="data ${animate ? 'reveal-rows' : ''}" id="contents-table" data-sort-id="container-contents">
      <thead><tr><th class="col-icon"><span class="sr-only">Image</span></th><th data-sort="text" data-key="name">Item</th>
        <th class="num" data-sort="num" data-key="qty">Qty</th><th class="num" data-sort="num" data-key="each">Each</th>
        <th data-sort="num" data-key="cheapest">3 cheapest</th>
        <th class="num" data-sort="num" data-key="value">Value</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${contents.map((item, i) => {
        const top = cheapest(item.quotes, manager.filter);
        return `<tr data-i="${i}" data-id="${item.id}" data-name="${esc(item.name.toLowerCase())}" style="--i:${Math.min(i, 20)}">
        <td>${thumbHtml(item)}</td>
        <td data-v="${esc(item.name)}"><button type="button" class="link-btn" data-act="view" data-id="${item.id}">${esc(item.name)}</button>
          ${item.stickers.length || item.float_value !== null ? `<div class="item-meta">${[
            item.float_value !== null ? `Float ${Number(item.float_value).toFixed(4)}` : '',
            item.stickers.length ? `${item.stickers.length} sticker${item.stickers.length > 1 ? 's' : ''}` : '',
          ].filter(Boolean).join(' · ')}</div>` : ''}</td>
        <td class="num strong" data-v="${item.quantity}">${number(item.quantity)}</td>
        <td class="num" data-v="${item.unit_value ?? ''}">${money(item.unit_value, currency)}</td>
        <td data-v="${top[0]?.price ?? ''}">${cheapestHtml(top, currency)}</td>
        <td class="num strong" data-v="${item.total_value ?? ''}">${money(item.total_value, currency)}</td>
        <td><div class="row-actions-cell">
          <button type="button" class="icon-btn" data-act="edit" data-id="${item.id}" aria-label="Edit ${esc(item.name)}" title="Edit">${ICONS.edit}</button>
          <button type="button" class="icon-btn" data-act="move" data-id="${item.id}" aria-label="Move ${esc(item.name)}" title="Move">${ICONS.move}</button>
          <button type="button" class="icon-btn danger" data-act="remove" data-id="${item.id}" aria-label="Remove ${esc(item.name)}" title="Remove">${ICONS.trash}</button>
        </div></td></tr>`;
      }).join('')}</tbody></table></div>`
    : '<div class="empty"><strong>This container is empty</strong>Add what you keep in it above. Stored items count towards your Total Portfolio.</div>';
  applySortWithin(wrap);
  const search = $('#contents-search');
  if (search && search.value) search.dispatchEvent(new Event('input', { bubbles: true }));

}

function setupManagerEvents() {
  const dialog = $('#container-dialog');
  dialog.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-act]');
    if (!target || !manager.data) return;
    const act = target.dataset.act;
    const item = manager.data.contents.find((i) => String(i.id) === target.dataset.id);
    try {
      if (act === 'close') dialog.close();
      else if (act === 'add-row') {
        $('#add-rows').insertAdjacentHTML('beforeend', addRowHtml());
        $('#add-rows .add-row:last-child [data-f="name"]').focus();
      } else if (act === 'remove-row') {
        const rows = document.querySelectorAll('#add-rows .add-row');
        if (rows.length > 1) target.closest('.add-row').remove();
        else rows[0].querySelectorAll('input').forEach((input) => { input.value = input.dataset.f === 'qty' ? '1' : ''; });
      } else if (act === 'add-submit') await submitAdd();
      else if (act === 'add-detailed') editItem(null, { container_id: manager.id });
      else if (act === 'move-in') {
        const id = $('#move-candidate').value;
        if (!id) return;
        await api(`/items/${id}/move`, { method: 'POST', body: { container_id: manager.id } });
        toast('Moved into the container', 'ok');
        await afterChange();
      } else if (act === 'view' && item) openItem(item);
      else if (act === 'edit' && item) editItem(item);
      else if (act === 'move' && item) await moveItem(item);
      else if (act === 'remove' && item) await removeItem(item);
      else if (act === 'delete-container') {
        if (await deleteContainer(manager.data.container) && manager.data.container.source !== 'steam') dialog.close();
      }
    } catch (error) {
      toast(error.message, 'error');
    }
  });
  dialog.addEventListener('input', (event) => {
    if (event.target.matches('#add-rows [data-f="name"]')) suggestNames(event.target, 'name-suggestions');
    if (event.target.id === 'contents-search') {
      const q = event.target.value.trim().toLowerCase();
      document.querySelectorAll('#contents-table tbody tr').forEach((tr) => { tr.hidden = !tr.dataset.name.includes(q); });
    }
  });
  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.closest('#add-rows')) {
      event.preventDefault();
      submitAdd().catch((error) => toast(error.message, 'error'));
    }
  });
  dialog.addEventListener('close', () => { manager.pendingRows = null; });
}

async function submitAdd() {
  const error = $('#add-error');
  const rows = collectRows().filter((r) => r.name.trim());
  const show = (message) => { error.textContent = message; error.hidden = !message; };
  if (!rows.length) { show('Type the name of at least one item.'); return; }
  const bad = rows.find((r) => !/^\d+$/.test(String(r.qty).trim()) || Number(r.qty) < 1);
  if (bad) { show(`Quantity for "${bad.name}" must be a whole number of at least 1.`); return; }
  const total = rows.reduce((sum, r) => sum + Number(r.qty), 0);
  const { container, capacity } = manager.data;
  const room = capacity - container.contents_count;
  if (total > room) { show(`That's ${number(total)} items, but only ${number(room)} spaces are left (limit ${number(capacity)}).`); return; }
  show('');
  const result = await api(`/containers/${manager.id}/items`, {
    method: 'POST',
    body: { items: rows.map((r) => ({ market_hash_name: r.name.trim(), quantity: Number(r.qty) })) },
  });
  const stacked = result.stacked ? ` (${result.stacked} stacked onto items already there)` : '';
  toast(`Added ${number(total)} item${total === 1 ? '' : 's'}${stacked}. ${number(result.contents_count)} / ${number(capacity)} used.`, 'ok');
  document.querySelectorAll('#add-rows .add-row').forEach((row) => row.remove());
  manager.pendingRows = null;
  $('#add-rows').insertAdjacentHTML('beforeend', addRowHtml());
  await afterChange();
  $('#add-rows [data-f="name"]')?.focus();
}

// ---------------------------------------------------------------------------
// Shared flows: remove, move, delete container
// ---------------------------------------------------------------------------
export async function removeItem(item) {
  if (item.source === 'steam') {
    toast("Steam items can't be removed here. They disappear automatically when they leave your Steam inventory.", 'warn', 9000);
    return false;
  }
  if (item.stack_ids && item.stack_ids.length > 1) {
    toast('This row combines several separate entries. Untick "Stack duplicates" to remove them one at a time.', 'warn', 9000);
    return false;
  }
  if (item.is_container && item.contents_count) return deleteContainer(item);
  const where = item.container_name ? ` from ${item.container_name}` : '';
  const many = item.quantity > 1;
  const answer = await confirmDialog({
    title: 'Remove item?',
    message: many
      ? `You have ${number(item.quantity)} × ${item.name}${where}. How many do you want to remove? This can't be undone.`
      : `Remove ${item.name}${where}? This can't be undone.`,
    quantity: many ? { max: item.quantity, value: item.quantity, label: 'Remove' } : null,
    buttons: [{ label: 'Cancel', value: null }, { label: 'Remove', value: true, kind: 'danger' }],
  });
  const confirmed = many ? answer && answer.value : answer;
  if (!confirmed) return false;
  const quantity = many ? answer.quantity : item.quantity;
  await api(`/items/${item.id}`, { method: 'DELETE', params: quantity < item.quantity ? { quantity } : {} });
  toast(quantity < item.quantity ? `Removed ${number(quantity)} × ${item.name}` : `Removed ${item.name}`, 'ok');
  await afterChange();
  return true;
}

export async function moveItem(item) {
  if (item.source === 'steam') {
    toast('Steam items stay in your Steam inventory and can\'t be put in containers here.', 'warn');
    return;
  }
  const { containers, capacity } = await api('/containers');
  const options = [{ value: '', label: 'Main inventory (not in a container)' }].concat(
    containers.filter((c) => c.id !== item.id).map((c) => ({
      value: String(c.id),
      label: `${c.label} (${number(c.contents_count)} / ${number(capacity)})`,
      disabled: c.contents_count >= capacity && c.id !== item.container_id,
    })),
  ).filter((o) => o.value !== String(item.container_id ?? ''));
  if (!options.length) { toast('Create a container first.', 'warn'); return; }
  const answer = await confirmDialog({
    title: `Move ${item.name}`,
    message: item.container_name ? `Currently in ${item.container_name}.` : 'Currently in your main inventory.',
    select: { label: 'Move to', options, value: options[0].value },
    quantity: item.quantity > 1 ? { max: item.quantity, value: item.quantity, label: 'How many' } : null,
    buttons: [{ label: 'Cancel', value: null }, { label: 'Move', value: true, kind: 'primary' }],
  });
  if (!answer || !answer.value) return;
  await api(`/items/${item.id}/move`, {
    method: 'POST',
    body: { container_id: answer.selected ? Number(answer.selected) : null, quantity: answer.quantity ?? null },
  });
  toast(`Moved ${answer.quantity && answer.quantity < item.quantity ? `${number(answer.quantity)} × ` : ''}${item.name}`, 'ok');
  await afterChange();
}

/** Empty containers go straight away; containers holding items ask what to do with them first. */
export async function deleteContainer(container) {
  const steam = container.source === 'steam';
  const label = container.label || container.name;
  const count = container.contents_count || 0;
  if (!count) {
    if (steam) return false;
    await api(`/containers/${container.id}`, { method: 'DELETE' });
    toast(`Deleted ${label}`, 'ok');
    if ($('#container-dialog').open && manager.id === container.id) $('#container-dialog').close();
    await app.refreshAll();
    return true;
  }
  const value = container.contents_value ? ` worth ${money(container.contents_value)}` : '';
  const choice = await confirmDialog({
    title: steam ? 'Empty this container?' : 'Delete this container?',
    message: steam
      ? `${label} holds ${number(count)} item${count === 1 ? '' : 's'}${value}. Steam containers can't be deleted here, `
        + 'but you can move everything back to your main inventory or delete the recorded items.'
      : `${label} holds ${number(count)} item${count === 1 ? '' : 's'}${value}. What should happen to them? This can't be undone.`,
    buttons: [
      { label: 'Cancel', value: null },
      { label: 'Keep the items (move to main inventory)', value: 'keep', kind: 'primary' },
      { label: steam ? 'Delete the items' : 'Delete container and items', value: 'delete', kind: 'danger' },
    ],
  });
  if (!choice) return false;
  await api(`/containers/${container.id}`, { method: 'DELETE', params: { contents: choice } });
  toast(steam ? `Emptied ${label}` : `Deleted ${label}${choice === 'keep' ? ' and moved its items to your main inventory' : ''}`, 'ok');
  if (!steam && $('#container-dialog').open && manager.id === container.id) $('#container-dialog').close();
  await afterChange();
  return true;
}
