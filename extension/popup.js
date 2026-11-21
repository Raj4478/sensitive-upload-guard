const DEFAULTS = { responseColor: '#0ea5e9', colorEnabled: true, failMode: 'block' };
const PRESETS = ['#0ea5e9', '#10b981', '#8b5cf6', '#f59e0b', '#f43f5e'];
const $ = (id) => document.getElementById(id);

function paintColor(color) {
  $('responseColor').value = color;
  $('previewText').style.color = color;
  for (const el of document.querySelectorAll('.swatch')) el.classList.toggle('on', el.dataset.color === color);
}

function paintFailMode(mode) {
  for (const b of document.querySelectorAll('#failMode button')) b.classList.toggle('on', b.dataset.value === mode);
}

// Colour swatches.
for (const color of PRESETS) {
  const b = document.createElement('button');
  b.className = 'swatch';
  b.style.background = color;
  b.dataset.color = color;
  b.title = color;
  b.addEventListener('click', () => { chrome.storage.sync.set({ responseColor: color }); paintColor(color); });
  $('swatches').appendChild(b);
}

chrome.storage.sync.get(DEFAULTS, (s) => {
  $('colorEnabled').checked = s.colorEnabled;
  paintColor(s.responseColor);
  paintFailMode(s.failMode);
});

$('colorEnabled').addEventListener('change', (e) => chrome.storage.sync.set({ colorEnabled: e.target.checked }));
$('responseColor').addEventListener('input', (e) => { chrome.storage.sync.set({ responseColor: e.target.value }); paintColor(e.target.value); });
for (const b of document.querySelectorAll('#failMode button')) {
  b.addEventListener('click', () => { chrome.storage.sync.set({ failMode: b.dataset.value }); paintFailMode(b.dataset.value); });
}

// Today's activity (written by content.js).
chrome.storage.local.get({ stats: null }, ({ stats }) => {
  const today = new Date().toISOString().slice(0, 10);
  const s = stats && stats.day === today ? stats : { checked: 0, flagged: 0, blocked: 0 };
  $('checked').textContent = s.checked;
  $('flagged').textContent = s.flagged;
  $('blocked').textContent = s.blocked;
});

$('version').textContent = 'v' + chrome.runtime.getManifest().version;

// Is the desktop app reachable?
chrome.runtime.sendMessage({ type: 'ping' }, (reply) => {
  const err = chrome.runtime.lastError || (reply && reply.error && { message: reply.error });
  const ok = !err && reply && reply.ok;
  $('status').className = 'status ' + (ok ? 'ok' : 'bad');
  $('statusText').textContent = ok ? 'Protected · desktop app connected' : 'Desktop app not connected';
  $('hint').style.display = ok ? 'none' : 'block';
});
