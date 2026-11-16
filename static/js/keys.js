// Keyboard shortcuts. Every control with a shortcut shows its key (a small badge, see markKeys),
// and ? lists them all. Shortcuts never fire while you're typing in a box, and only Esc, / and
// Ctrl+Z work while a window is open.

import { UNDO_KEY } from './undo.js';
import { esc } from './util.js';

/** The list shown by "?" (and the README). Keys are shown exactly as written here. */
export const SHORTCUTS = [
  ['Moving around', [
    [['1', '2', '3', '4', '5', '6'], 'Go to a tab: 1 Public Inventory, 2 Total Portfolio, 3 Containers, 4 Market Comparison, 5 Sell Planner, 6 Profit & Loss'],
    [['['], 'Previous tab'],
    [[']'], 'Next tab'],
    [['/'], 'Search the list on this tab (or in the container you have open)'],
    [['Esc'], 'Close a window, leave a search box, or clear the items ticked in a container'],
    [['Enter'], 'Open the details of the row you\'re on (move between rows with Tab)'],
  ]],
  ['Actions', [
    [['R'], 'Refresh prices'],
    [['Shift', 'R'], 'Refresh every price, even ones fetched recently'],
    [['S'], 'Sync your Steam inventory'],
    [['A'], 'Add an item'],
    [['N'], 'New container'],
    [['E'], 'Export this tab as a spreadsheet (inventory tabs and Profit & Loss)'],
    [['B'], 'Price alerts'],
    [[','], 'Settings'],
    [[UNDO_KEY], 'Undo the last change: removing, moving, deleting, or confirming a change from a Steam sync'],
    [['?'], 'Show these shortcuts'],
  ]],
];

export function kbd(key) {
  return `<kbd class="kbd">${esc(key)}</kbd>`;
}

/** Spoken / tooltip form of a key ("Shift+R", "Control+Z"). */
function ariaKey(key) {
  return key.replace('Ctrl+', 'Control+').replace('⌘', 'Meta+').replace('Esc', 'Escape');
}

/** Show each control's shortcut on it: a small key badge, the key in its tooltip, and
 *  aria-keyshortcuts for screen readers. Controls opt in with data-shortcut="R". */
export function markKeys(root = document) {
  for (const el of root.querySelectorAll('[data-shortcut]:not([data-shortcut-shown])')) {
    const key = el.dataset.shortcut;
    el.dataset.shortcutShown = '1';
    el.setAttribute('aria-keyshortcuts', ariaKey(key));
    const hint = `Shortcut: ${key}`;
    el.title = el.title ? `${el.title} (${hint})` : hint;
    const badge = document.createElement('kbd');
    badge.className = 'kbd key-hint';
    badge.setAttribute('aria-hidden', 'true');
    badge.textContent = key;
    if (el.matches('input')) {
      // A search box: the key sits inside its right edge until you start typing.
      const wrap = document.createElement('span');
      wrap.className = `search-wrap${el.classList.contains('search') ? ' search' : ''}`;
      el.classList.remove('search');
      el.replaceWith(wrap);
      wrap.append(el, badge);
    } else if (el.matches('[role="tab"]')) {
      el.prepend(badge);   // before the name, so it isn't mistaken for the tab's counts
    } else {
      el.append(badge);
    }
  }
}

/** Listen for the shortcuts. ``on`` has one function per action (see app.js). */
export function initShortcuts(on) {
  document.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.isComposing) return;
    const target = event.target instanceof Element ? event.target : document.body;
    const typing = target.closest('input, textarea, select, [contenteditable="true"]');
    const key = event.key;
    const mod = event.ctrlKey || event.metaKey;

    if (mod && !event.altKey && !event.shiftKey && key.toLowerCase() === 'z') {
      if (typing) return;                            // in a box it undoes your typing, as usual
      event.preventDefault();
      on.undo();
      return;
    }
    if (mod || event.altKey) return;
    if (typing) {
      // Esc in an empty search box (outside a window) leaves it, so the other keys work again.
      if (key === 'Escape' && target.matches('input[type="search"]') && !target.value && !target.closest('dialog')) {
        target.blur();
      }
      return;
    }
    const dialog = document.querySelector('dialog[open]');
    if (dialog) {
      if (key === '/' && on.searchIn(dialog)) event.preventDefault();
      return;
    }
    const actions = {
      '[': () => on.step(-1),
      ']': () => on.step(1),
      '/': on.search,
      '?': on.help,
      r: () => on.refresh(false),
      R: () => on.refresh(true),
      s: on.sync,
      a: on.add,
      n: on.newContainer,
      e: on.export,
      b: on.alerts,
      ',': on.settings,
    };
    if (/^[1-9]$/.test(key)) {
      if (on.tab(Number(key))) event.preventDefault();
      return;
    }
    const action = actions[key];
    if (!action) return;
    event.preventDefault();
    action();
  });
}

/** The "?" window: every shortcut, grouped. */
export function openShortcuts() {
  const dialog = document.getElementById('keys-dialog');
  document.getElementById('keys-body').innerHTML = SHORTCUTS.map(([group, rows]) => `
    <h4>${esc(group)}</h4>
    <dl class="keys-list">${rows.map(([keys, what]) => `<div class="keys-row"><dt>${
      keys.length === 6 ? `${kbd('1')}<span class="muted"> to </span>${kbd('6')}` : keys.map(kbd).join('<span class="muted">+</span>')
    }</dt><dd>${esc(what)}</dd></div>`).join('')}</dl>`).join('')
    + `<p class="hint">Shortcuts don't fire while you're typing in a box. Each button with a shortcut shows its key.</p>`;
  dialog.showModal();
}
