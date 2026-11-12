// Closing pop-ups safely.
//
// Every pop-up closes when you click its dark backdrop - but only for a real click outside it:
// selecting text in a box and letting go of the mouse outside no longer counts.
// Pop-ups that hold typed input register a "dirty" check with protect(). While there's unsaved
// input, a click outside (or Esc) doesn't close them; the pop-up nudges, its buttons flash and a
// note says how to close it. Pop-ups that only show information (item details) close as before.

const dirtyChecks = new Map();   // dialog -> () => boolean
const cleanStates = new WeakMap();  // dialog -> form snapshot when it was opened
const DEFAULT_HINT = 'You have unsaved changes - save them, or press Cancel to discard them.';

/** Snapshot of every field in a pop-up (search boxes excluded: they only filter what's shown). */
function snapshot(root) {
  return JSON.stringify([...root.querySelectorAll('input, select, textarea')]
    .filter((el) => el.type !== 'search')
    .map((el) => (el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.value)));
}

/** Protect a pop-up: while isDirty() is true, a click outside or Esc won't close it. */
export function protect(dialog, isDirty, hint = DEFAULT_HINT) {
  dirtyChecks.set(dialog, isDirty);
  dialog.dataset.nudgeHint = hint;
}

/** Protect a form pop-up: it counts as changed once any field differs from when markClean() ran. */
export function protectForm(dialog, hint) {
  protect(dialog, () => cleanStates.has(dialog) && cleanStates.get(dialog) !== snapshot(dialog), hint);
}

/** Call once a form pop-up has been filled in, just before the user starts editing. */
export function markClean(dialog) {
  cleanStates.set(dialog, snapshot(dialog));
}

export function hasUnsavedInput(dialog) {
  const check = dirtyChecks.get(dialog);
  return Boolean(check && check());
}

function isOutside(dialog, event) {
  const box = dialog.getBoundingClientRect();
  return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
}

function restart(el, className) {
  el.classList.remove(className);
  void el.offsetWidth;  // restart the animation
  el.classList.add(className);
}

/** Show that the pop-up stays open: a small bump, flashing buttons and a short note. */
export function nudge(dialog) {
  restart(dialog, 'nudge');
  const buttons = dialog.querySelectorAll(
    '.dialog-actions .btn-primary, .dialog-actions [data-close], [data-act="close"], [data-act="add-submit"]',
  );
  for (const button of buttons) {
    restart(button, 'flash');
    clearTimeout(button.flashTimer);
    button.flashTimer = setTimeout(() => button.classList.remove('flash'), 1800);
  }
  const actions = dialog.querySelector('.dialog-actions');
  if (!actions) return;
  let note = actions.querySelector('.nudge-hint');
  if (!note) {
    note = document.createElement('span');
    note.className = 'nudge-hint';
    note.setAttribute('role', 'status');
    actions.prepend(note);
  }
  note.textContent = dialog.dataset.nudgeHint || DEFAULT_HINT;
  restart(note, 'show');
  clearTimeout(note.hideTimer);
  clearTimeout(note.removeTimer);
  note.hideTimer = setTimeout(() => {
    note.classList.remove('show');
    note.removeTimer = setTimeout(() => note.remove(), 350);   // after the fade, so the row closes up
  }, 5000);
}

export function initDialogs() {
  for (const dialog of document.querySelectorAll('dialog')) {
    let pressedOutside = false;
    dialog.addEventListener('pointerdown', (event) => {
      pressedOutside = event.target === dialog && isOutside(dialog, event);
    });
    dialog.addEventListener('click', (event) => {
      const fromBackdrop = pressedOutside && event.target === dialog && isOutside(dialog, event);
      pressedOutside = false;
      if (!fromBackdrop) return;
      if (hasUnsavedInput(dialog)) nudge(dialog);
      else dialog.close();
    });
    dialog.addEventListener('cancel', (event) => {  // Esc
      if (hasUnsavedInput(dialog)) {
        event.preventDefault();
        nudge(dialog);
      }
    });
    dialog.addEventListener('close', () => dialog.querySelector('.nudge-hint')?.remove());
  }
}
