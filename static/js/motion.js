// Small touches of motion: a highlight that slides between a toggle's options, an underline that
// slides between tabs, and boxes that keep their height while you flip toggles (so the page doesn't
// jump). Everything is quick, and switched off when the system asks for reduced motion (style.css).

/** Every toggle (.segmented) gets a highlight that slides to the chosen option - including toggles
 *  added later (the item detail's chart range, for example). */
export function initMotion() {
  let queued = false;
  const scan = () => {
    queued = false;
    document.querySelectorAll('.segmented:not([data-thumb])').forEach(setupToggle);
  };
  scan();
  new MutationObserver(() => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(scan);
    }
  }).observe(document.body, { childList: true, subtree: true });
  setupTabs(document.querySelector('.tabs'));
  window.addEventListener('resize', () => {
    for (const el of document.querySelectorAll('[data-tallest]')) {
      delete el.dataset.tallest;
      el.style.minHeight = '';
    }
  });
}

function setupToggle(group) {
  group.dataset.thumb = '1';
  const thumb = document.createElement('span');
  thumb.className = 'seg-thumb';
  thumb.setAttribute('aria-hidden', 'true');
  group.prepend(thumb);
  const place = () => {
    const active = group.querySelector('button.active, button[aria-pressed="true"]');
    if (!active || !active.offsetWidth) {
      thumb.style.opacity = '0';
      return;
    }
    thumb.style.opacity = '1';
    thumb.style.width = `${active.offsetWidth}px`;
    thumb.style.transform = `translateX(${active.offsetLeft}px)`;
    // The first placement (and any made while hidden) doesn't slide in from nowhere.
    if (!thumb.classList.contains('ready')) requestAnimationFrame(() => thumb.classList.add('ready'));
  };
  new MutationObserver(place).observe(group, { subtree: true, attributes: true, attributeFilter: ['class', 'aria-pressed'] });
  new ResizeObserver(place).observe(group);
  place();
}

/** The main tabs: one underline that slides to the selected tab. */
function setupTabs(tabs) {
  if (!tabs) return;
  const ink = document.createElement('span');
  ink.className = 'tab-ink';
  ink.setAttribute('aria-hidden', 'true');
  tabs.append(ink);
  tabs.classList.add('has-ink');
  const place = () => {
    const tab = tabs.querySelector('[aria-selected="true"]');
    if (!tab) return;
    ink.style.width = `${tab.offsetWidth}px`;
    ink.style.transform = `translateX(${tab.offsetLeft}px)`;
    if (!ink.classList.contains('ready')) requestAnimationFrame(() => ink.classList.add('ready'));
  };
  new MutationObserver(place).observe(tabs, { subtree: true, attributes: true, attributeFilter: ['aria-selected'] });
  new ResizeObserver(place).observe(tabs);
  place();
}

/** Keep a box at the tallest it has been, so switching a toggle or filter that makes it shorter
 *  doesn't pull everything below it up (and back down again on the next click). Reset when the
 *  window is resized. Call after re-rendering the box. */
export function steady(el) {
  if (!el) return;
  el.style.minHeight = '';
  const height = Math.ceil(el.getBoundingClientRect().height);   // whole pixels, so it never wobbles by one
  if (!height) return;                      // hidden: measure next time
  const tallest = Math.max(Number(el.dataset.tallest || 0), height);
  el.dataset.tallest = String(tallest);
  el.style.minHeight = `${tallest}px`;
}
