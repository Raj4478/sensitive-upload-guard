const DEFAULTS = { responseColor: '#0ea5e9', colorEnabled: true, failMode: 'block' };

chrome.storage.sync.get(DEFAULTS, (s) => {
  document.getElementById('colorEnabled').checked = s.colorEnabled;
  document.getElementById('responseColor').value = s.responseColor;
  document.getElementById('failMode').value = s.failMode;
});

for (const id of ['colorEnabled', 'responseColor', 'failMode']) {
  document.getElementById(id).addEventListener('input', (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    chrome.storage.sync.set({ [id]: value });
  });
}

chrome.runtime.sendMessage({ type: 'ping' }, (reply) => {
  const box = document.getElementById('status');
  const err = chrome.runtime.lastError || (reply && reply.error && { message: reply.error });
  if (err || !reply || !reply.ok) {
    box.className = 'status bad';
    box.textContent = 'Desktop app not connected. Run "python -m upload_guard install", then reload this page. ('
      + ((err && err.message) || 'no reply') + ')';
  } else {
    box.className = 'status ok';
    box.textContent = `Desktop app connected (v${reply.version}, ${reply.platform}).`;
  }
});
