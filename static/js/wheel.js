// Scrolling lists (Biggest movers, the Containers tab's changes, the Profit & Loss list, the data
// tables and lists inside windows): spin the mouse wheel over one and it scrolls to its end, pauses
// there for a moment, then - if you're still turning - whatever is around it (the page, or the window
// it's in) carries on. No need to stop, or to move the pointer, first.
//
// Browsers tie a run of wheel turns to the box it started in and send the rest of the run there even
// once that box can't go any further, so on their own they'd sit stuck at the end until you paused or
// nudged the mouse. Here, turns over these lists are handled by hand instead, with a short glide like
// the browser's own scrolling. A window's lists never hand on to the page behind the window.

const BOXES = '.movers-card .mover-list ol, .change-list, .pnl-scroll, .data-table-wrap, .dialog .table-scroll, .set-list';
const GAP = 400;     // ms without a turn: the next turn starts a new run
const DRIFT = 10;    // px the pointer may move during a run
const HOLD = 250;    // ms a list rests at its end (you still turning) before what's around it moves on
const LINE = 40;     // px per line, when the browser counts in lines
const EASE = 0.22;   // share of the way left covered each frame (60 a second)

let run = { at: -Infinity, x: 0, y: 0, box: null, onward: false, scrolled: false, endSince: 0 };
const glides = new Map();
const calm = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
const pageBox = () => document.scrollingElement || document.documentElement;

/** Can this box scroll up and down? */
function scrollsY(el) {
  if (el.scrollHeight <= el.clientHeight + 1) return false;
  return /(auto|scroll)/.test(getComputedStyle(el).overflowY);
}

/** The scrolling list at a point (the innermost one that has anywhere to go), if any. */
function boxAt(el) {
  let box = el && el.closest ? el.closest(BOXES) : null;
  while (box && !scrollsY(box)) box = box.parentElement ? box.parentElement.closest(BOXES) : null;
  return box;
}

/** What a list hands on to: the box around it that scrolls, else the page - but never past a window. */
function around(el) {
  for (let up = el.parentElement; up; up = up.parentElement) {
    if (up.tagName === 'DIALOG') return null;
    if (up === document.body || up === document.documentElement) break;
    if (scrollsY(up)) return up;
  }
  return pageBox();
}

const most = (el) => Math.max(0, el.scrollHeight - el.clientHeight);

/** Where a box is heading (where it is, unless it's mid-glide). */
function aimOf(el) {
  const g = glides.get(el);
  return g && g.raf && Math.abs(el.scrollTop - g.at) <= 2 ? g.aim : el.scrollTop;
}

/** Scroll a box by `dy`, gliding there; more turns while it glides add on to where it's heading. */
function glide(el, dy) {
  let g = glides.get(el);
  if (!g || !g.raf || Math.abs(el.scrollTop - g.at) > 2) {   // (not gliding, or moved by something else since)
    if (g && g.raf) cancelAnimationFrame(g.raf);
    g = { aim: el.scrollTop, pos: el.scrollTop, at: el.scrollTop, raf: 0, last: 0 };
    glides.set(el, g);
  }
  g.aim = Math.min(most(el), Math.max(0, g.aim + dy));
  if (calm.matches) {
    el.scrollTop = g.aim;
    glides.delete(el);
    return;
  }
  if (g.raf) return;
  const step = (now) => {
    if (Math.abs(el.scrollTop - g.at) > 2) {   // scrolled some other way meanwhile: let that win
      glides.delete(el);
      return;
    }
    const frames = Math.min(4, (now - g.last) / (1000 / 60));
    g.last = now;
    g.aim = Math.min(g.aim, most(el));   // (in case it got shorter meanwhile)
    const left = g.aim - g.pos;
    g.pos = Math.abs(left) < 0.5 ? g.aim : g.pos + left * (1 - (1 - EASE) ** frames);
    el.scrollTop = g.pos;
    g.at = el.scrollTop;
    if (g.pos !== g.aim) {
      g.raf = requestAnimationFrame(step);
    } else {
      g.raf = 0;
      glides.delete(el);
    }
  };
  g.last = performance.now();
  g.raf = requestAnimationFrame(step);
}

function pixels(event, el) {
  if (event.deltaMode === 1) return event.deltaY * LINE;
  if (event.deltaMode === 2) return event.deltaY * el.clientHeight * 0.9;
  return event.deltaY;
}

function onWheel(event) {
  if (event.defaultPrevented || !event.cancelable || event.ctrlKey || event.shiftKey) return;
  if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;   // (sideways: the browser's own)
  const here = event.currentTarget;
  const box = run.box && run.box.isConnected ? run.box : null;
  if (!box && !scrollsY(here)) return;   // (a list with nowhere to go: the browser passes it on itself)
  const dy = pixels(event, box || here);
  if (!dy) return;
  event.preventDefault();
  if (box && !run.onward) {
    const aim = aimOf(box);
    const take = dy > 0 ? Math.min(dy, most(box) - aim) : Math.max(dy, -aim);
    if (Math.abs(take) >= 0.5) {   // (room left: the list takes it - never any spill-over beyond its end)
      glide(box, take);
      run.scrolled = true;
      run.endSince = 0;
      return;
    }
    // At its end (or gliding there). Nothing else moves until it has really arrived; and if this run
    // has been scrolling the list, it then rests there a moment - only turning on through that moves
    // what's around it. (A fresh scroll with the list already at its end moves on straight away.)
    if (Math.abs(box.scrollTop - (dy > 0 ? most(box) : 0)) > 1.5) return;
    if (run.scrolled) {
      const now = performance.now();
      if (!run.endSince) run.endSince = now;
      if (now - run.endSince < HOLD) return;
    }
    run.onward = true;   // (from here the rest of this run goes on to what's around it)
  }
  // Either the list is done, or this run began outside any list and the list has just slid under the
  // pointer - the run carries on with what's around it, rather than the list grabbing it.
  const next = around(box || here);
  if (next) glide(next, dy);
}

/** Watch for the lists (they come and go as things are redrawn) and keep track of each wheel run. */
export function initWheel() {
  window.addEventListener('wheel', (event) => {
    const now = performance.now();
    if (now - run.at > GAP || Math.abs(event.clientX - run.x) > DRIFT || Math.abs(event.clientY - run.y) > DRIFT) {
      const under = document.elementFromPoint(event.clientX, event.clientY) || event.target;
      run = { box: boxAt(under), x: event.clientX, y: event.clientY, onward: false, scrolled: false, endSince: 0 };
    }
    run.at = now;
  }, { capture: true, passive: true });

  let queued = false;
  const scan = () => {
    queued = false;
    for (const el of document.querySelectorAll(BOXES)) {
      if (el.dataset.wheel) continue;
      el.dataset.wheel = '1';
      el.addEventListener('wheel', onWheel, { passive: false });
    }
  };
  scan();
  new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(scan);
    }
  }).observe(document.body, { childList: true, subtree: true });
}
