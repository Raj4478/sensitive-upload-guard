/*
 * Sensitive Upload Guard - content script (runs on chatgpt.com and perplexity.ai).
 *
 * The extension is only a sensor. It holds an outgoing prompt or file at the last
 * moment the page can still be stopped, asks the desktop app (via background.js
 * and Chrome native messaging), then either replays the original action or drops
 * it. All file reading, OCR and the confirmation dialog live in the desktop app.
 *
 * Hooks are registered on `window` in the CAPTURE phase at document_start, so they
 * run before any listener the site adds. When nothing sensitive is found the
 * event is left completely untouched: normal use is never slowed or changed.
 *
 *   prompts : Enter in the prompt box, click on the send button, form submit
 *   files   : <input type=file> selection, drag-and-drop, pasting files
 *   colour  : responses.css + a CSS variable set from the extension settings
 */
(() => {
  'use strict';

  const SITE = /perplexity\.ai$/.test(location.hostname) ? 'Perplexity' : 'ChatGPT';

  // ---------------------------------------------------------------- marker matching
  // Same rules as upload_guard/detector.py: upper-case, map OCR look-alikes,
  // drop everything that is not a letter, then look for WALLEDSENSITIVE.
  const LOOKALIKE = { '0': 'O', '1': 'I', '|': 'I', '!': 'I', '5': 'S', '$': 'S', '€': 'E' };
  const FOLDED_MARKER = /WA[LI][LI]EDSENSITIVE/;
  const fold = (text) => text.toUpperCase().replace(/[01|!5$€]/g, (c) => LOOKALIKE[c]).replace(/[^A-Z]+/g, '');
  const hasMarker = (text) => !!text && FOLDED_MARKER.test(fold(text));

  // ---------------------------------------------------------------- settings
  const DEFAULTS = { responseColor: '#0ea5e9', colorEnabled: true, failMode: 'block' };
  let settings = { ...DEFAULTS };

  function applySettings() {
    const root = document.documentElement;
    if (!root) return;
    root.style.setProperty('--upload-guard-response-color', settings.responseColor);
    root.classList.toggle('upload-guard-color', !!settings.colorEnabled);
  }
  applySettings();
  try {
    chrome.storage.sync.get(DEFAULTS, (stored) => { settings = { ...DEFAULTS, ...stored }; applySettings(); });
    chrome.storage.onChanged.addListener((changes) => {
      for (const [key, { newValue }] of Object.entries(changes)) settings[key] = newValue;
      applySettings();
    });
  } catch (_) { /* storage unavailable: keep defaults */ }

  // ---------------------------------------------------------------- desktop app bridge
  function ask(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (reply) => {
          const err = chrome.runtime.lastError;
          resolve(err ? { error: err.message } : (reply || { error: 'no reply' }));
        });
      } catch (e) {
        resolve({ error: String(e && e.message || e) });   // e.g. extension was reloaded
      }
    });
  }

  /** "allow" | "continue" -> go ahead; anything else -> stop. */
  function verdict(reply, what) {
    if (!reply || reply.error) {
      const allow = settings.failMode === 'allow';
      if (allow) toast('info', 'Desktop app not reachable', `The ${what} was allowed (fail-open setting).`);
      else toast('blocked', 'Desktop app not reachable', `The ${what} was blocked for safety. Run: python -m upload_guard install`);
      console.warn('[Upload Guard]', reply && reply.error);
      return allow;
    }
    return reply.decision === 'allow' || reply.decision === 'continue';
  }

  // Today's activity, shown in the popup.
  function bump(changes) {
    try {
      const day = new Date().toISOString().slice(0, 10);
      chrome.storage.local.get({ stats: null }, ({ stats }) => {
        const s = stats && stats.day === day ? stats : { day, checked: 0, flagged: 0, blocked: 0 };
        for (const [key, n] of Object.entries(changes)) s[key] += n;
        chrome.storage.local.set({ stats: s });
      });
    } catch (_) { /* extension reloaded */ }
  }

  const replayed = new WeakSet();   // events we dispatch ourselves; let them through
  const stop = (e) => { e.preventDefault(); e.stopImmediatePropagation(); };

  // ================================================================= prompts
  const COMPOSER = [
    'textarea[name="prompt"]', '#prompt-textarea', '.ProseMirror[contenteditable="true"]',   // ChatGPT
    '[data-lexical-editor="true"]', '#ask-input', '[contenteditable="true"][role="textbox"]',  // Perplexity
    'textarea',
  ].join(',');
  const SEND = [
    'button[aria-label="Send message"]', 'button[aria-label="Send prompt"]',
    'button[data-testid="send-button"]', '#composer-submit-button',                          // ChatGPT
    'button[aria-label="Submit"]', 'button[data-testid="submit-button"]',                    // Perplexity
  ].join(',');

  const composerFrom = (el) => (el && el.closest ? el.closest(COMPOSER) : null);
  function findComposer(near) {
    const scope = (near && near.closest && near.closest('form')) || document;
    return composerFrom(document.activeElement) || scope.querySelector(COMPOSER) || document.querySelector(COMPOSER);
  }
  const composerText = (c) => (!c ? '' : c.tagName === 'TEXTAREA' ? c.value : c.innerText || '');
  function sendButtonFrom(el) {
    const button = el && el.closest ? el.closest('button') : null;
    if (!button) return null;
    if (button.matches(SEND)) return button;
    if (button.type === 'submit' && button.form && button.form.querySelector(COMPOSER)) return button;
    return null;
  }

  let promptPending = false;
  let resending = false;

  function guardPrompt(event, composer, resend) {
    if (resending) return;
    if (promptPending) { stop(event); return; }   // a dialog is already open
    const text = composerText(composer);
    if (!text.trim()) return;
    if (!hasMarker(text)) { bump({ checked: 1 }); return; }   // clean: the site handles it as usual
    stop(event);
    promptPending = true;
    bump({ checked: 1, flagged: 1 });
    toast('pending', 'Waiting for your decision', 'WALLED_SENSITIVE was found in your prompt. Answer the Upload Guard dialog.');
    ask({ type: 'inspect_prompt', site: SITE, text }).then((reply) => {
      if (verdict(reply, 'prompt')) {
        resend();
        toast('success', 'Prompt sent', 'You chose Continue. It was sent unchanged.');
      } else {
        bump({ blocked: 1 });
        if (!reply.error) toast('blocked', 'Prompt not sent', 'Your text is still in the box, ready to edit.');
        composer.focus();
      }
    }).finally(() => { promptPending = false; });
  }

  // Replay the send exactly as the user did it, with our hooks switched off.
  function resend(composer, button, form) {
    resending = true;
    try {
      const target = (button && button.isConnected) ? button : document.querySelector(SEND);
      if (target && !target.disabled) {
        target.click();
      } else if (form && form.requestSubmit) {
        form.requestSubmit();
      } else {
        composer.focus();
        composer.dispatchEvent(new KeyboardEvent('keydown',
          { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
      }
    } finally {
      resending = false;
    }
  }

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.altKey || e.isComposing || e.keyCode === 229) return;
    const composer = composerFrom(e.target);
    if (composer) guardPrompt(e, composer, () => resend(composer, null, composer.closest('form')));
  }, true);

  window.addEventListener('click', (e) => {
    const button = sendButtonFrom(e.target);
    if (!button) return;
    const composer = findComposer(button);
    guardPrompt(e, composer, () => resend(composer, button, button.form));
  }, true);

  window.addEventListener('submit', (e) => {
    const composer = e.target && e.target.querySelector ? e.target.querySelector(COMPOSER) : null;
    if (composer) guardPrompt(e, composer, () => resend(composer, null, e.target));
  }, true);

  // ================================================================= files
  const MAX_BYTES = 50 * 1024 * 1024;

  function readBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  /** Resolve to { ok, flagged } for one file. */
  async function checkFile(file) {
    const name = file.name || 'pasted file';
    const base = { type: 'inspect_file', site: SITE, name, mime: file.type, size: file.size };
    let reply;
    if (file.size > MAX_BYTES) {
      reply = await ask({ ...base, too_large: true });
    } else {
      try { reply = await ask({ ...base, data: await readBase64(file) }); } catch (e) { reply = { error: `could not read ${name}` }; }
    }
    const flagged = !!(reply && reply.finding && (reply.finding.detected || !reply.finding.inspected));
    return { ok: verdict(reply, `file "${name}"`), flagged };
  }

  /** Inspect every file; resolve to the files that may be uploaded. */
  async function allowedFiles(files) {
    const label = files.length > 1 ? `${files.length} files` : files[0].name;
    const slow = setTimeout(() => toast('pending', 'Checking before upload', `Upload Guard is inspecting ${label}…`), 400);
    try {
      const results = await Promise.all(files.map(checkFile));
      clearTimeout(slow);
      const allowed = files.filter((_, i) => results[i].ok);
      const flagged = results.filter((r) => r.flagged).length;
      const blocked = files.length - allowed.length;
      bump({ checked: files.length, flagged, blocked });
      if (blocked && allowed.length) toast('blocked', 'Some files removed', `${blocked} of ${files.length} files were not uploaded.`);
      else if (blocked) toast('blocked', 'Upload cancelled', files.length > 1 ? 'None of the files were uploaded.' : `${files[0].name} was not uploaded.`);
      else if (flagged) toast('success', 'Upload allowed', 'You chose Continue. The file was uploaded unchanged.');
      else hideToast();
      return allowed;
    } finally {
      clearTimeout(slow);
    }
  }

  function fileList(files) {
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    return dt;
  }

  // <input type="file">: the browser fires "input" then "change"; hold both, replay both.
  const inFlight = new WeakSet();
  function onFileInput(e) {
    const input = e.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file' || replayed.has(e)) return;
    const files = Array.from(input.files || []);
    if (!files.length) return;
    stop(e);
    if (inFlight.has(input)) return;              // the second event of the same selection
    inFlight.add(input);
    allowedFiles(files).then((allowed) => {
      if (!allowed.length) { input.value = ''; return; }     // Cancel: nothing is uploaded
      if (allowed.length < files.length) input.files = fileList(allowed).files;
      for (const type of ['input', 'change']) {
        const ev = new Event(type, { bubbles: true });
        replayed.add(ev);
        input.dispatchEvent(ev);
      }
    }).finally(() => inFlight.delete(input));
  }
  window.addEventListener('input', onFileInput, true);
  window.addEventListener('change', onFileInput, true);

  // Drag and drop.
  window.addEventListener('drop', (e) => {
    if (replayed.has(e)) return;
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    if (!files.length) return;
    stop(e);
    const target = e.target;
    const init = { bubbles: true, cancelable: true, composed: true, clientX: e.clientX, clientY: e.clientY,
                   screenX: e.screenX, screenY: e.screenY };
    allowedFiles(files).then((allowed) => {
      const where = target.isConnected ? target : (document.elementFromPoint(e.clientX, e.clientY) || document.body);
      const ev = allowed.length
        ? new DragEvent('drop', { ...init, dataTransfer: fileList(allowed) })
        : new DragEvent('dragleave', { ...init, dataTransfer: new DataTransfer() });   // clear the drop overlay
      replayed.add(ev);
      where.dispatchEvent(ev);
    });
  }, true);

  // Pasting files (e.g. a screenshot) into the prompt box.
  window.addEventListener('paste', (e) => {
    if (replayed.has(e)) return;
    const clip = e.clipboardData;
    const files = Array.from((clip && clip.files) || []);
    if (!files.length) return;
    stop(e);
    const target = e.target;
    const texts = {};
    for (const type of clip.types) if (type !== 'Files') texts[type] = clip.getData(type);
    allowedFiles(files).then((allowed) => {
      if (!allowed.length) return;
      const dt = fileList(allowed);
      for (const [type, value] of Object.entries(texts)) dt.setData(type, value);
      const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true, composed: true });
      replayed.add(ev);
      (target.isConnected ? target : document.activeElement || document.body).dispatchEvent(ev);
    });
  }, true);

  // ================================================================= status notice
  // A small card at the top of the page, inside a closed shadow root so the
  // site's CSS cannot touch it (and ours cannot leak into the site).
  const TOAST_CSS = `
    :host { all: initial; }
    .card { position: fixed; z-index: 2147483647; top: 18px; left: 50%; transform: translate(-50%, -12px);
      display: flex; gap: 12px; align-items: flex-start; width: min(440px, calc(100vw - 32px));
      padding: 12px 14px 12px 16px; border-radius: 14px; background: #ffffff; color: #0f172a;
      border: 1px solid rgba(15, 23, 42, .08); border-left: 4px solid var(--tone);
      box-shadow: 0 12px 32px rgba(15, 23, 42, .18), 0 2px 6px rgba(15, 23, 42, .08);
      font: 13px/1.45 "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
      opacity: 0; transition: opacity .18s ease, transform .18s ease;
      pointer-events: none; /* information only: clicks pass through to the page */ }
    .card.show { opacity: 1; transform: translate(-50%, 0); }
    .icon { flex: none; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
      background: color-mix(in srgb, var(--tone) 14%, transparent); color: var(--tone); }
    .icon svg { width: 16px; height: 16px; }
    .title { font-weight: 600; font-size: 13.5px; margin: 1px 0 2px; }
    .msg { color: #475569; overflow-wrap: anywhere; }
    .brand { margin-left: auto; flex: none; font-size: 10.5px; letter-spacing: .06em; text-transform: uppercase;
      color: #94a3b8; padding-top: 3px; }
    .spin { animation: spin 0.9s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (prefers-color-scheme: dark) {
      .card { background: #111827; color: #e5e7eb; border-color: rgba(255,255,255,.08); border-left-color: var(--tone);
        box-shadow: 0 12px 32px rgba(0,0,0,.45); }
      .msg { color: #9ca3af; } .brand { color: #6b7280; }
    }`;
  const SHIELD = '<path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5l-8-3Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>';
  const KINDS = {
    pending: { tone: '#f59e0b', svg: '<circle class="spin" cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2.5" stroke-dasharray="36 14" stroke-linecap="round" style="transform-origin:12px 12px"/>', ms: 0 },
    success: { tone: '#10b981', svg: SHIELD + '<path d="m8.5 12 2.5 2.5 4.5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>', ms: 3500 },
    blocked: { tone: '#f43f5e', svg: SHIELD + '<path d="M9.5 9.5l5 5m0-5-5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>', ms: 6000 },
    info: { tone: '#0ea5e9', svg: SHIELD + '<path d="M12 11v5m0-8v.01" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>', ms: 5000 },
  };
  let toastRoot = null;
  let toastHost = null;
  let toastTimer = null;

  function toast(kind, title, message) {
    if (!document.body) return;
    if (!toastHost || !toastHost.isConnected) {
      toastHost = document.createElement('upload-guard-notice');
      toastRoot = toastHost.attachShadow({ mode: 'closed' });
      toastRoot.innerHTML = `<style>${TOAST_CSS}</style><div class="card" role="status" aria-live="polite">
        <div class="icon"><svg viewBox="0 0 24 24"></svg></div>
        <div><div class="title"></div><div class="msg"></div></div><div class="brand">Upload Guard</div></div>`;
      document.body.appendChild(toastHost);
    }
    const spec = KINDS[kind] || KINDS.info;
    const card = toastRoot.querySelector('.card');
    card.style.setProperty('--tone', spec.tone);
    toastRoot.querySelector('svg').innerHTML = spec.svg;
    toastRoot.querySelector('.title').textContent = title;
    toastRoot.querySelector('.msg').textContent = message;
    requestAnimationFrame(() => card.classList.add('show'));
    clearTimeout(toastTimer);
    if (spec.ms) toastTimer = setTimeout(hideToast, spec.ms);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    if (toastRoot) toastRoot.querySelector('.card').classList.remove('show');
  }
})();
