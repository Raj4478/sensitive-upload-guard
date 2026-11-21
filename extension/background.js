/*
 * Service worker: relays requests from the content script to the desktop app.
 *
 * chrome.runtime.sendNativeMessage starts the registered native host
 * (python -m upload_guard.host), sends one JSON message and waits for the reply.
 * Only extension pages may talk to native hosts, so content scripts go through here.
 */
const HOST = 'com.walled.upload_guard';
const TYPES = new Set(['ping', 'inspect_prompt', 'inspect_file']);
// The two supported sites, by host name (any subdomain), over HTTPS.
const SITE_HOSTS = [/(^|\.)perplexity\.ai$/, /(^|\.)chatgpt\.com$/, /(^|\.)chat\.openai\.com$/];

function senderHost(sender) {
  // sender.origin is the most precise, but can be missing or "null" in some
  // documents; fall back to the frame URL, then the tab URL.
  for (const candidate of [sender.origin, sender.url, sender.tab && sender.tab.url]) {
    try {
      const url = new URL(candidate);
      if (url.protocol === 'https:') return url.hostname;
    } catch (_) { /* not a URL: try the next one */ }
  }
  return '';
}

function allowed(sender) {
  if (sender.id !== chrome.runtime.id) return false;
  if ((sender.url || '').startsWith(chrome.runtime.getURL(''))) return true;   // our popup
  const host = senderHost(sender);
  return SITE_HOSTS.some((re) => re.test(host));
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !TYPES.has(message.type)) return false;
  if (!allowed(sender)) {
    const detail = `sender not allowed: ${sender.origin || sender.url || 'unknown'}`;
    console.warn('[Upload Guard]', detail);
    sendResponse({ error: detail });
    return false;
  }
  chrome.runtime.sendNativeMessage(HOST, message, (reply) => {
    const err = chrome.runtime.lastError;
    if (err) console.warn('[Upload Guard] native messaging:', err.message);
    sendResponse(err ? { error: `native messaging: ${err.message}` } : (reply || { error: 'empty reply from the desktop app' }));
  });
  return true;   // keep the channel open for the async reply
});
