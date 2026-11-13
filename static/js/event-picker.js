// "From an event…" in a container: every sticker, slab, capsule and souvenir package from one
// tournament, each with a quantity box. Nothing is saved from here - what you fill in goes onto
// the container's ordinary list (Steam's exact names), where you check it before adding.

import { api } from './api.js';
import { protect } from './guard.js';
import { esc, number, store, thumbHtml, toast } from './util.js';

const $ = (selector) => document.querySelector(selector);
const picker = { sets: null, event: null, items: [], qty: new Map(), kind: '', variant: '', q: '', onAdd: null, ready: false };
const PLURAL = { Sticker: 'Stickers', 'Sticker slab': 'Sticker slabs', Capsule: 'Capsules', 'Souvenir package': 'Souvenir packages',
  Patch: 'Patches', Charm: 'Charms', Graffiti: 'Graffiti', Collectible: 'Coins & pins' };

/** Open the picker; ``onAdd`` gets [{name, qty}] when you press "Add to the list". */
export async function openEventPicker(onAdd) {
  const dialog = $('#set-dialog');
  picker.onAdd = onAdd;
  picker.qty = new Map();
  if (!picker.ready) setup(dialog);
  $('#set-body').innerHTML = '<p class="muted">Loading events…</p>';
  updateSummary();
  dialog.showModal();
  try {
    if (!picker.sets || !picker.sets.length) picker.sets = (await api('/sets')).sets;
  } catch (error) {
    toast(error.message, 'error');
    dialog.close();
    return;
  }
  if (!picker.sets.length) {
    $('#set-body').innerHTML = `<div class="empty"><strong>No event items yet</strong>The list of every CS2 item downloads
      in the background (about once a week). Type an item name in the container's list to start it, then try again in a minute.</div>`;
    return;
  }
  const saved = store.get('set:event', null);
  picker.event = picker.sets.some((s) => s.event === picker.event) ? picker.event
    : picker.sets.some((s) => s.event === saved) ? saved : picker.sets[0].event;
  $('#set-body').innerHTML = `
    <div class="set-controls">
      <label class="set-event">Event
        <select class="input" id="set-event">${picker.sets.map((s) => `<option value="${esc(s.event)}"${s.event === picker.event ? ' selected' : ''}>${esc(s.event)} · ${number(s.count)} items</option>`).join('')}</select>
      </label>
      <input type="search" class="input" id="set-search" placeholder="Filter by team or player…" aria-label="Filter by team or player" value="${esc(picker.q)}">
    </div>
    <div class="chips" id="set-kinds" role="group" aria-label="Kind of item"></div>
    <div class="chips" id="set-variants" role="group" aria-label="Version"></div>
    <p class="hint">Type how many you have of each and leave the rest empty (Enter or ↓ moves to the next box). You can switch
      events and keep going: everything you fill in is put on the container's list, by its exact name, for you to check.</p>
    <div class="set-list" id="set-list"></div>`;
  await loadEvent(picker.event);
}

function setup(dialog) {
  picker.ready = true;
  protect(dialog, () => [...picker.qty.values()].some((q) => q > 0),
    'You have quantities filled in - press Add to the list, or Cancel to discard them.');
  dialog.addEventListener('change', (event) => {
    if (event.target.id === 'set-event') {
      picker.event = event.target.value;
      store.set('set:event', picker.event);
      loadEvent(picker.event);
    }
  });
  dialog.addEventListener('input', (event) => {
    if (event.target.id === 'set-search') {
      picker.q = event.target.value.trim().toLowerCase();
      renderList();
    } else if (event.target.matches('.set-qty')) {
      const value = Math.max(0, Math.min(1000, Math.floor(Number(event.target.value) || 0)));
      if (value) picker.qty.set(event.target.dataset.name, value);
      else picker.qty.delete(event.target.dataset.name);
      event.target.closest('.set-item').classList.toggle('picked', value > 0);
      updateSummary();
    }
  });
  dialog.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-kind], [data-variant]');
    if (!chip) return;
    if ('kind' in chip.dataset) { picker.kind = chip.dataset.kind; picker.variant = ''; }
    else picker.variant = chip.dataset.variant;
    renderChips();
    renderList();
  });
  dialog.addEventListener('keydown', (event) => {
    if (!event.target.matches('.set-qty') || !['Enter', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    const boxes = [...document.querySelectorAll('#set-list .set-qty')];
    const next = boxes[boxes.indexOf(event.target) + (event.key === 'ArrowUp' ? -1 : 1)];
    if (next) { next.focus(); next.select(); }
  });
  $('#set-add').addEventListener('click', () => {
    const order = new Map(picker.items.map((item, i) => [item.name, i]));
    const picked = [...picker.qty.entries()].filter(([, q]) => q > 0)
      .sort((a, b) => (order.get(a[0]) ?? 1e9) - (order.get(b[0]) ?? 1e9))
      .map(([name, qty]) => ({ name, qty }));
    if (!picked.length) return;
    picker.qty = new Map();          // nothing unsaved left, so closing isn't blocked
    dialog.close();
    picker.onAdd(picked);
  });
}

async function loadEvent(event) {
  const list = $('#set-list');
  if (!list) return;
  list.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api('/sets/items', { params: { event } });
    if (event !== picker.event) return;          // switched again meanwhile
    picker.items = data.items;
  } catch (error) {
    list.innerHTML = '';
    toast(error.message, 'error');
    return;
  }
  const kinds = new Set(picker.items.map((i) => i.kind));
  if (!kinds.has(picker.kind)) picker.kind = '';
  renderChips();
  renderList();
}

function shown() {
  return picker.items.filter((item) => (!picker.kind || item.kind === picker.kind)
    && (!picker.variant || (item.variant || 'Other') === picker.variant)
    && (!picker.q || item.name.toLowerCase().includes(picker.q)));
}

function renderChips() {
  const kinds = [...new Set(picker.items.map((i) => i.kind))];
  const count = (pred) => number(picker.items.filter(pred).length);
  $('#set-kinds').innerHTML = `<span class="filter-label">Show</span>
    <button type="button" class="chip" data-kind="" aria-pressed="${!picker.kind}">All ${count(() => true)}</button>
    ${kinds.map((k) => `<button type="button" class="chip" data-kind="${esc(k)}" aria-pressed="${picker.kind === k}">${esc(PLURAL[k] || k)} ${count((i) => i.kind === k)}</button>`).join('')}`;
  const inKind = picker.items.filter((i) => !picker.kind || i.kind === picker.kind);
  const variants = [...new Set(inKind.map((i) => i.variant || 'Other'))];
  if (!variants.includes(picker.variant)) picker.variant = '';
  $('#set-variants').hidden = variants.length < 2;
  $('#set-variants').innerHTML = `<span class="filter-label">Version</span>
    <button type="button" class="chip" data-variant="" aria-pressed="${!picker.variant}">Any</button>
    ${variants.map((v) => `<button type="button" class="chip" data-variant="${esc(v)}" aria-pressed="${picker.variant === v}">${esc(v)}</button>`).join('')}`;
}

function renderList() {
  const items = shown();
  const list = $('#set-list');
  if (!list) return;
  list.innerHTML = items.length ? items.map((item) => {
    const qty = picker.qty.get(item.name) || '';
    return `<label class="set-item${qty ? ' picked' : ''}">
      ${thumbHtml({ icon: item.icon, category: 'sticker' }, 'thumb-sm')}
      <span class="set-name">${esc(item.name)}</span>
      <input class="input set-qty" type="number" min="0" max="1000" step="1" inputmode="numeric" value="${qty}"
        data-name="${esc(item.name)}" aria-label="How many ${esc(item.name)}">
    </label>`;
  }).join('') : '<p class="muted">Nothing matches - try another filter.</p>';
  updateSummary();
}

function updateSummary() {
  const picked = [...picker.qty.values()].filter((q) => q > 0);
  const total = picked.reduce((sum, q) => sum + q, 0);
  const visible = new Set(shown().map((i) => i.name));
  const hidden = [...picker.qty.keys()].filter((name) => !visible.has(name)).length;
  $('#set-summary').textContent = picked.length
    ? `${number(picked.length)} item${picked.length === 1 ? '' : 's'} · ${number(total)} in all${hidden ? ` (${number(hidden)} not shown by the filters)` : ''}`
    : 'Type how many you have next to each item.';
  $('#set-add').disabled = !picked.length;
}
