// Undo for the last change: the server answers an undoable change (removing, moving, deleting,
// confirming a change from a Steam sync...) with a token, and the message saying it's done gets an
// Undo button. Ctrl+Z does the same until something else changes (the server then refuses).

import { api } from './api.js';
import { toast } from './util.js';

export const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
export const UNDO_KEY = IS_MAC ? '⌘Z' : 'Ctrl+Z';

let last = null;              // { token, el }
let refresh = async () => {};

/** What to reload after an undo (set once by the app). */
export function onUndone(fn) {
  refresh = fn;
}

/** "Done" message, with an Undo button when the server said the change can be undone. */
export function doneToast(message, token, timeout = 10000) {
  if (!token) {
    toast(message, 'ok');
    return;
  }
  last?.el?.remove();         // only the newest change can be undone
  const el = toast(message, 'ok', timeout, { action: { label: 'Undo', key: UNDO_KEY, run: () => undoLast() } });
  last = { token, el };
}

export function canUndo() {
  return Boolean(last);
}

export async function undoLast() {
  if (!last) {
    toast('Nothing to undo.', 'info', 3000);
    return false;
  }
  const { token, el } = last;
  last = null;
  el?.remove();
  try {
    await api(`/undo/${token}`, { method: 'POST' });
    toast('Undone - everything is back as it was.', 'ok', 5000);
    await refresh();
    return true;
  } catch (error) {
    toast(error.message, 'warn', 9000);
    return false;
  }
}
