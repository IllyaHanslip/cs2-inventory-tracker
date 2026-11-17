// Click-to-sort for every table (and the container cards).
//
// Click a header: ascending ▲ (A→Z, low→high). Click again: descending ▼.
// Click a third time: back to the list's normal order. Choices are remembered per table.
//
// Tables opt in with <table data-sort-id="unique-id">; sortable headers carry
// data-sort="num|text" and data-key="column-id". Cells may give an exact sort value in
// data-v (numbers, ISO dates); otherwise their text is used. Rows need data-i (their
// position in the normal order) so the third click can restore it.

import { store } from './util.js';

const states = store.get('sort-states', {});

function save() {
  store.set('sort-states', states);
}

export function getSort(id) {
  return states[id] || null;
}

/** Advance a column through ascending -> descending -> unsorted. Returns the new state. */
export function cycleSort(id, key) {
  const current = states[id];
  if (!current || current.key !== key) states[id] = { key, dir: 'asc' };
  else if (current.dir === 'asc') states[id] = { key, dir: 'desc' };
  else delete states[id];
  save();
  return states[id] || null;
}

function parse(value, type) {
  if (value === undefined || value === null || value === '') return null;
  if (type === 'num') {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return String(value);
}

/** Compare two raw values; empty values always go last, whichever the direction. */
export function compareValues(a, b, type, dir) {
  const x = parse(a, type);
  const y = parse(b, type);
  if (x === null && y === null) return 0;
  if (x === null) return 1;
  if (y === null) return -1;
  const result = type === 'num' ? x - y : x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' });
  return dir === 'desc' ? -result : result;
}

/** Sort an array of objects with the same rules (used for card lists). */
export function sortItems(items, id, getters) {
  const state = getSort(id);
  if (!state || !getters[state.key]) return items;
  const { get, type } = getters[state.key];
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => compareValues(get(a.item), get(b.item), type, state.dir) || a.index - b.index)
    .map((entry) => entry.item);
}

function cellValue(row, index) {
  const cell = row.cells[index];
  if (!cell) return null;
  return cell.dataset.v !== undefined ? cell.dataset.v : cell.textContent.trim();
}

/** Re-apply a table's current sort to its rows (call after every re-render). */
export function applySort(table) {
  if (!table || !table.tBodies[0]) return;
  const id = table.dataset.sortId;
  const state = getSort(id);
  const headers = [...table.querySelectorAll('thead th')];
  let index = -1;
  let type = 'text';
  headers.forEach((th, i) => {
    if (!th.dataset.sort) return;
    labelHeader(th);
    th.classList.add('sortable');
    th.tabIndex = 0;
    th.title = 'Click to sort - click again to reverse, a third time to clear';
    const active = state && th.dataset.key === state.key;
    if (active) {
      index = i;
      type = th.dataset.sort;
      th.setAttribute('aria-sort', state.dir === 'asc' ? 'ascending' : 'descending');
    } else {
      th.removeAttribute('aria-sort');
    }
  });
  // Each <tbody> sorts on its own (grouped tables use one per group), keeping its group header on top.
  for (const body of table.tBodies) {
    const rows = [...body.rows].filter((row) => !row.classList.contains('group-row'));
    rows.sort((a, b) => {
      const base = Number(a.dataset.i ?? 0) - Number(b.dataset.i ?? 0);
      if (index < 0) return base;
      return compareValues(cellValue(a, index), cellValue(b, index), type, state.dir) || base;
    });
    rows.forEach((row) => body.appendChild(row));
  }
}

/** The header's text in its own box, so the sort arrow can sit a fixed gap from the text (not from
 *  the cell's edge, where padding varies) without ever changing the column's width. */
function labelHeader(th) {
  if (th.querySelector(':scope > .th-label')) return;
  const label = document.createElement('span');
  label.className = 'th-label';
  label.append(...th.childNodes);
  th.append(label);
}

// ---------------------------------------------------------------------------
// Hovering (or tabbing to) a sortable header highlights its whole column.
// ---------------------------------------------------------------------------
let lit = [];

function clearColumn() {
  lit.forEach((cell) => cell.classList.remove('col-hover'));
  lit = [];
}

/** The real table and column for a header - including the floating copy of a header row. */
function columnOf(th) {
  const float = th.closest('table[data-float-for]');
  const table = float
    ? document.querySelector(`table[data-sort-id="${CSS.escape(float.dataset.floatFor)}"]`)
    : th.closest('table');
  const head = float && table ? table.querySelector(`thead th[data-key="${CSS.escape(th.dataset.key)}"]`) : th;
  return table && head ? { table, head } : null;
}

function lightColumn(th) {
  clearColumn();
  const found = columnOf(th);
  if (!found) return;
  const { table, head } = found;
  // Column position, counting any header cells that span several columns.
  let column = 0;
  for (const cell of head.parentElement.cells) {
    if (cell === head) break;
    column += cell.colSpan || 1;
  }
  lit.push(head);
  if (th !== head) lit.push(th);
  for (const body of table.tBodies) {
    for (const row of body.rows) {
      let at = 0;
      for (const cell of row.cells) {
        if (at === column && (cell.colSpan || 1) === 1) lit.push(cell);
        at += cell.colSpan || 1;
        if (at > column) break;
      }
    }
  }
  lit.forEach((cell) => cell.classList.add('col-hover'));
}

const SORTABLE_HEAD = 'table.data th[data-sort]';
document.addEventListener('pointerover', (event) => {
  const th = event.target.closest && event.target.closest(SORTABLE_HEAD);
  if (th && !lit.includes(th)) lightColumn(th);
  else if (!th && lit.length && !event.target.closest?.('th')) clearColumn();
});
document.addEventListener('pointerout', (event) => {
  const th = event.target.closest && event.target.closest(SORTABLE_HEAD);
  if (th && !(event.relatedTarget && th.contains(event.relatedTarget))) clearColumn();
});
document.addEventListener('focusin', (event) => {
  const th = event.target.closest && event.target.closest(SORTABLE_HEAD);
  if (th) lightColumn(th);
});
document.addEventListener('focusout', (event) => {
  if (event.target.closest && event.target.closest(SORTABLE_HEAD)) clearColumn();
});

function activate(th) {
  const table = th.closest('table[data-sort-id]');
  if (!table) return;
  cycleSort(table.dataset.sortId, th.dataset.key);
  applySort(table);
  table.dispatchEvent(new CustomEvent('sorted', { bubbles: true }));
}

document.addEventListener('click', (event) => {
  const th = event.target.closest('table[data-sort-id] th[data-sort]');
  if (th) activate(th);
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const th = event.target.closest('table[data-sort-id] th[data-sort]');
  if (!th) return;
  event.preventDefault();
  activate(th);
});

/** Apply sorting to every opted-in table inside an element (after rendering HTML). */
export function applySortWithin(root) {
  root.querySelectorAll('table[data-sort-id]').forEach(applySort);
}
