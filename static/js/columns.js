// "Columns": choose which columns a table shows. Remembered per table (Public Inventory, Total
// Portfolio and container contents each keep their own). Hidden columns are hidden with one style
// rule per table, so re-drawing a table never brings them back.

import { esc, store } from './util.js';

const hidden = new Map();     // table id -> Set of hidden column keys
const registry = new Map();   // table id -> [{ key, label, index }]
let sheet = null;

// An open menu closes when you click anywhere else.
document.addEventListener('click', (event) => {
  for (const menu of document.querySelectorAll('details.col-menu[open]')) {
    if (!menu.contains(event.target)) menu.open = false;
  }
});

function writeStyles() {
  if (!sheet) {
    sheet = document.createElement('style');
    sheet.id = 'column-choices';
    document.head.append(sheet);
  }
  const rules = [];
  for (const [id, columns] of registry) {
    const off = hidden.get(id) || new Set();
    for (const column of columns) {
      if (!off.has(column.key)) continue;
      const sel = `table[data-cols="${id}"]`;
      rules.push(`${sel} > thead > tr > th:nth-child(${column.index}), ${sel} > tbody > tr:not(.group-row) > td:nth-child(${column.index}) { display: none; }`);
    }
  }
  sheet.textContent = rules.join('\n');
}

/** Register a table's optional columns ({key, label, index (1-based)}) and draw the "Columns"
 *  menu into ``slot``. The table needs data-cols="<id>". */
export function columnChooser(slot, id, columns) {
  registry.set(id, columns);
  const saved = store.get(`columns:${id}`, []);
  hidden.set(id, new Set(Array.isArray(saved) ? saved.filter((k) => columns.some((c) => c.key === k)) : []));
  writeStyles();
  if (!slot) return;
  slot.innerHTML = `<details class="col-menu">
      <summary class="btn btn-small" title="Choose which columns to show">Columns</summary>
      <div class="col-menu-list" role="group" aria-label="Columns to show">
        ${columns.map((c) => `<label class="check"><input type="checkbox" data-col="${esc(c.key)}"${hidden.get(id).has(c.key) ? '' : ' checked'}> ${esc(c.label)}</label>`).join('')}
        <button type="button" class="link-btn small" data-col-reset>Show all</button>
      </div>
    </details>`;
  const menu = slot.querySelector('details');
  menu.addEventListener('change', (event) => {
    const box = event.target.closest('input[data-col]');
    if (!box) return;
    const off = hidden.get(id);
    if (box.checked) off.delete(box.dataset.col); else off.add(box.dataset.col);
    store.set(`columns:${id}`, [...off]);
    writeStyles();
  });
  menu.addEventListener('click', (event) => {
    if (!event.target.closest('[data-col-reset]')) return;
    hidden.get(id).clear();
    store.set(`columns:${id}`, []);
    menu.querySelectorAll('input[data-col]').forEach((box) => { box.checked = true; });
    writeStyles();
  });
  menu.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && menu.open) {
      event.preventDefault();
      event.stopPropagation();
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
}
