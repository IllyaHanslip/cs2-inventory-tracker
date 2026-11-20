// Recent messages: the button in the corner opens every message that popped up there in the last day
// (util.js keeps them in this browser), newest first, with how long ago each came - for when one went
// before you'd read it. The count on the button: warnings and problems you haven't seen yet (opening
// the list, or closing a message with ×, counts as seeing it).

import { setBadge } from './motion.js';
import { MESSAGE_HOURS, clearMessages, esc, markAllMessagesSeen, messageHistory } from './util.js';

const $ = (id) => document.getElementById(id);
let ticker = null;
let fresh = new Set();          // shown in bold: the ones that were new when the list was opened

/** "just now", "25 seconds ago", "a minute ago", "3 hours ago", "a day ago". */
export function ago(at, now = Date.now()) {
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes === 1 ? 'a minute ago' : `${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours === 1 ? 'an hour ago' : `${hours} hours ago`;
  return 'a day ago';
}

function clock(at) {
  return new Date(at).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function isOpen() {
  const panel = $('messages-panel');
  return !panel.hidden && !panel.classList.contains('closing');
}

function render() {
  const list = messageHistory();
  const open = isOpen();
  const missed = open ? 0 : list.filter((m) => !m.seen && (m.kind === 'warn' || m.kind === 'error')).length;
  setBadge($('messages-count'), missed);
  $('messages-btn').setAttribute('aria-label', missed ? `Recent messages (${missed} you may have missed)` : 'Recent messages');
  if (!open) return;
  $('messages-clear').hidden = !list.length;
  $('messages-list').innerHTML = list.length
    ? list.map((m) => `<li class="message ${esc(m.kind)}${fresh.has(m.id) ? ' unseen' : ''}">
        <span class="message-dot" aria-hidden="true"></span>
        <div><p class="message-text">${esc(m.text)}</p>
          <time class="message-when" datetime="${new Date(m.at).toISOString()}" title="${esc(clock(m.at))}">${ago(m.at)}${
            m.count > 1 ? ` · ${m.count} times` : ''}</time></div>
      </li>`).join('')
    : `<li class="messages-empty">No messages in the last ${MESSAGE_HOURS} hours.</li>`;
}

export function openMessages() {
  const panel = $('messages-panel');
  fresh = new Set(messageHistory().filter((m) => !m.seen).map((m) => m.id));
  panel.classList.remove('closing');
  panel.hidden = false;
  $('messages-btn').setAttribute('aria-expanded', 'true');
  document.body.classList.add('messages-open');
  markAllMessagesSeen();
  render();
  $('messages-list').scrollTop = 0;
  $('messages-title').focus();
  clearInterval(ticker);
  ticker = setInterval(render, 10000);      // keep "how long ago" current
}

export function closeMessages({ focusButton = false } = {}) {
  const panel = $('messages-panel');
  if (!isOpen()) return;
  clearInterval(ticker);
  $('messages-btn').setAttribute('aria-expanded', 'false');
  document.body.classList.remove('messages-open');
  if (focusButton || panel.contains(document.activeElement)) $('messages-btn').focus();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    panel.hidden = true;
    render();
    return;
  }
  panel.classList.add('closing');
  setTimeout(() => {
    if (!panel.classList.contains('closing')) return;   // opened again meanwhile
    panel.classList.remove('closing');
    panel.hidden = true;
  }, 160);
  render();
}

export function toggleMessages() {
  if (isOpen()) closeMessages({ focusButton: true });
  else openMessages();
}

export function initMessages() {
  $('messages-btn').addEventListener('click', toggleMessages);
  $('messages-close').addEventListener('click', () => closeMessages({ focusButton: true }));
  $('messages-clear').addEventListener('click', () => {
    clearMessages();
    $('messages-title').focus();
  });
  document.addEventListener('cs2t:messages', render);
  // The app open in another tab of this browser: its messages appear here too.
  window.addEventListener('storage', (event) => { if (event.key === 'cs2t:messages') render(); });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !isOpen() || document.querySelector('dialog[open]')) return;
    event.preventDefault();
    closeMessages({ focusButton: true });
  });
  // A click anywhere else closes it.
  document.addEventListener('pointerdown', (event) => {
    if (isOpen() && !event.target.closest('#messages-panel, #messages-btn')) closeMessages();
  });
  render();
}
