/*
 * Service worker: relays requests from the content script to the desktop app.
 *
 * chrome.runtime.sendNativeMessage starts the registered native host
 * (python -m upload_guard.host), sends one JSON message and waits for the reply.
 * Only extension pages may talk to native hosts, so content scripts go through here.
 */
const HOST = 'com.walled.upload_guard';
const TYPES = new Set(['ping', 'inspect_prompt', 'inspect_file']);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !TYPES.has(message.type)) return false;
  // Only our own content scripts on the two supported sites (or our popup) may ask.
  const origin = sender.origin || (sender.url && new URL(sender.url).origin) || '';
  const ownPage = sender.id === chrome.runtime.id && (sender.url || '').startsWith(chrome.runtime.getURL(''));
  if (!ownPage && !/^https:\/\/((www\.)?perplexity\.ai|chatgpt\.com|chat\.openai\.com)$/.test(origin)) {
    sendResponse({ error: 'sender not allowed' });
    return false;
  }
  chrome.runtime.sendNativeMessage(HOST, message, (reply) => {
    const err = chrome.runtime.lastError;
    sendResponse(err ? { error: err.message } : (reply || { error: 'empty reply from the desktop app' }));
  });
  return true;   // keep the channel open for the async reply
});
