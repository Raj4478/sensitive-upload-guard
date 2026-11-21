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
      toast(allow
        ? `Upload Guard desktop app not reachable - ${what} allowed (fail-open).`
        : `Upload Guard desktop app not reachable - ${what} blocked. Run: python -m upload_guard install`, !allow);
      console.warn('[Upload Guard]', reply && reply.error);
      return allow;
    }
    return reply.decision === 'allow' || reply.decision === 'continue';
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
    if (!hasMarker(text)) return;                 // clean: the site handles it as usual
    stop(event);
    promptPending = true;
    toast('WALLED_SENSITIVE found in your prompt - answer the Upload Guard dialog.');
    ask({ type: 'inspect_prompt', site: SITE, text }).then((reply) => {
      if (verdict(reply, 'prompt')) {
        resend();
        toast('Prompt sent.');
      } else if (!reply.error) {
        toast('Prompt not sent. Your text is still in the box.');
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

  async function checkFile(file) {
    const base = { type: 'inspect_file', site: SITE, name: file.name || 'pasted file', mime: file.type, size: file.size };
    if (file.size > MAX_BYTES) return verdict(await ask({ ...base, too_large: true }), `"${base.name}"`);
    let data;
    try { data = await readBase64(file); } catch (e) { return verdict({ error: `could not read ${base.name}` }, 'file'); }
    return verdict(await ask({ ...base, data }), `"${base.name}"`);
  }

  /** Inspect every file; resolve to the files that may be uploaded. */
  async function allowedFiles(files) {
    const slow = setTimeout(() => toast(`Upload Guard is checking ${files.length > 1 ? files.length + ' files' : '"' + files[0].name + '"'}…`), 500);
    try {
      const results = await Promise.all(files.map(checkFile));
      const allowed = files.filter((_, i) => results[i]);
      if (allowed.length < files.length) {
        toast(allowed.length ? `${files.length - allowed.length} file(s) removed from the upload.` : 'Upload cancelled.');
      }
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

  // ================================================================= status toast
  let toastHost = null;
  let toastTimer = null;
  function toast(message, isError = false) {
    if (!document.body) return;
    if (!toastHost || !toastHost.isConnected) {
      toastHost = document.createElement('div');
      toastHost.attachShadow({ mode: 'open' }).innerHTML =
        '<style>div{position:fixed;z-index:2147483647;left:50%;bottom:96px;transform:translateX(-50%);' +
        'max-width:560px;padding:10px 16px;border-radius:10px;font:14px/1.4 system-ui,sans-serif;' +
        'color:#fff;background:#1f2937;box-shadow:0 6px 24px rgba(0,0,0,.35)}div.err{background:#b91c1c}</style><div></div>';
      document.body.appendChild(toastHost);
    }
    const box = toastHost.shadowRoot.querySelector('div');
    box.textContent = '🛡 ' + message;
    box.className = isError ? 'err' : '';
    box.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.style.display = 'none'; }, isError ? 7000 : 4000);
  }
})();
