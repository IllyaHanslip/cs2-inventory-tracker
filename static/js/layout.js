// Sticky layout: the tab bar stays just under the top bar, and table column headers stay just
// under the tab bar once you scroll past them. The CSS does the sticking; this keeps the
// offsets matched to the real heights (the top bar wraps on narrow screens, and the progress
// banner comes and goes).

const root = document.documentElement;
const watched = new WeakSet();

export function updateStickyOffsets() {
  const topbar = document.querySelector('.topbar');
  const banner = document.getElementById('job-banner');
  const tabs = document.querySelector('.tabs');
  if (!topbar || !tabs) return;
  const topH = getComputedStyle(topbar).position === 'sticky' ? topbar.offsetHeight : 0;
  const bannerH = banner && !banner.hidden ? banner.offsetHeight : 0;
  root.style.setProperty('--topbar-h', `${topH}px`);
  root.style.setProperty('--tabs-top', `${topH + bannerH}px`);
  root.style.setProperty('--head-top', `${topH + bannerH + tabs.offsetHeight}px`);
  markStuck();
}

/** A table only scrolls sideways when it's genuinely too wide - otherwise its headers can stick. */
function checkTable(wrap) {
  const table = wrap.querySelector(':scope > table');
  if (!table) return;
  const before = wrap.classList.contains('is-scrollable');
  wrap.classList.toggle('is-scrollable', table.scrollWidth > wrap.clientWidth + 1);
  if (before !== wrap.classList.contains('is-scrollable')) syncFloat(wrap);
}

const sizes = new ResizeObserver((entries) => {
  let layout = false;
  for (const entry of entries) {
    const el = entry.target;
    if (el.matches('.topbar, #job-banner, .tabs')) layout = true;
    else checkTable(el.closest('.table-scroll') || el);
  }
  if (layout) updateStickyOffsets();
});

// ---------------------------------------------------------------------------
// Floating header for tables that have to scroll sideways (sticky can't work there):
// a copy of the header row pinned under the tabs, moving with the table's sideways scroll.
// Clicking it sorts the real table.
// ---------------------------------------------------------------------------
const floats = new Map();

function headTop() {
  return parseFloat(getComputedStyle(root).getPropertyValue('--head-top')) || 0;
}

function positionFloat(wrap) {
  const float = floats.get(wrap);
  const table = wrap.querySelector(':scope > table');
  if (!float || !table || !table.tHead) return;
  const top = headTop();
  const area = wrap.getBoundingClientRect();
  const head = table.tHead.getBoundingClientRect();
  const show = !wrap.closest('[hidden]') && head.top < top && area.bottom > top + head.height;
  float.style.display = show ? 'block' : 'none';
  if (!show) return;
  float.style.top = `${top}px`;
  float.style.left = `${area.left}px`;
  float.style.width = `${wrap.clientWidth}px`;
  float.firstElementChild.style.transform = `translateX(${-wrap.scrollLeft}px)`;
}

function syncFloat(wrap) {
  const table = wrap.querySelector(':scope > table');
  const needed = wrap.isConnected && wrap.classList.contains('is-scrollable') && table && table.tHead
    && !wrap.closest('dialog, .data-table-wrap');
  let float = floats.get(wrap);
  if (!needed) {
    if (float) { float.remove(); floats.delete(wrap); }
    return;
  }
  if (!float) {
    float = document.createElement('div');
    float.className = 'float-head';
    float.setAttribute('aria-hidden', 'true');
    float.addEventListener('click', (event) => {
      const th = event.target.closest('th[data-key]');
      const real = th && wrap.querySelector(`:scope > table thead th[data-key="${CSS.escape(th.dataset.key)}"]`);
      if (real) real.click();
    });
    wrap.addEventListener('scroll', () => positionFloat(wrap), { passive: true });
    document.body.append(float);
    floats.set(wrap, float);
  }
  float.innerHTML = `<table class="${table.className}" style="width:${table.offsetWidth}px"><thead>${table.tHead.innerHTML}</thead></table>`;
  const source = table.tHead.rows[0].cells;
  [...float.querySelector('thead tr').cells].forEach((cell, i) => {
    const width = `${source[i].getBoundingClientRect().width}px`;
    Object.assign(cell.style, { width, minWidth: width, maxWidth: width });
  });
  positionFloat(wrap);
}

function positionFloats() {
  for (const [wrap, float] of floats) {
    if (!wrap.isConnected) { float.remove(); floats.delete(wrap); } else positionFloat(wrap);
  }
}

function watchTables() {
  document.querySelectorAll('.table-scroll').forEach((wrap) => {
    const table = wrap.querySelector(':scope > table');
    for (const el of [wrap, table]) {
      if (el && !watched.has(el)) {
        watched.add(el);
        sizes.observe(el);
      }
    }
    checkTable(wrap);
    syncFloat(wrap);
  });
  positionFloats();
}

function markStuck() {
  const tabs = document.querySelector('.tabs');
  if (!tabs) return;
  const top = parseFloat(getComputedStyle(root).getPropertyValue('--tabs-top')) || 0;
  tabs.classList.toggle('stuck', window.scrollY > 0 && tabs.getBoundingClientRect().top <= top + 0.5);
}

export function initLayout() {
  ['.topbar', '#job-banner', '.tabs'].forEach((selector) => {
    const el = document.querySelector(selector);
    if (el) sizes.observe(el);
  });
  let pending = false;
  const insideFloat = (record) => {
    const el = record.target.nodeType === 1 ? record.target : record.target.parentElement;
    return Boolean(el && el.closest('.float-head'));
  };
  new MutationObserver((records) => {
    // Ignore our own floating-header rebuilds, or they'd trigger themselves forever.
    if (pending || records.every(insideFloat)) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; watchTables(); });
  }).observe(document.body, { childList: true, subtree: true });
  window.addEventListener('resize', () => { updateStickyOffsets(); document.querySelectorAll('.table-scroll').forEach(syncFloat); });
  window.addEventListener('scroll', () => { markStuck(); positionFloats(); }, { passive: true });
  // A sort changes the arrows in the header, so refresh any floating copy.
  document.addEventListener('sorted', (event) => {
    const wrap = event.target.closest && event.target.closest('.table-scroll');
    if (wrap) syncFloat(wrap);
  });
  updateStickyOffsets();
  watchTables();
}
