// End-to-end test: real Chrome + the extension + the real desktop app + the real
// native dialog (clicked by answer_dialog.py through Win32).
//
//   node run.mjs            local test page (prompts, file input, drop, paste)
//   node run.mjs --live     also ChatGPT and Perplexity (prompts + response colour)
import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SAMPLES = path.join(ROOT, 'samples');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const LIVE = process.argv.includes('--live');
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
}

// Start the dialog watcher BEFORE triggering the action; resolves with what it saw.
function answerDialog(answer, timeout = 25) {
  return new Promise((resolve) => {
    const p = spawn('python', [path.join(HERE, 'answer_dialog.py'), answer, String(timeout)]);
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.on('close', () => { try { resolve(JSON.parse(out)); } catch { resolve({ seen: false, text: out }); } });
  });
}

// A copy of the extension that also runs on the local test page.
function testExtension() {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'ug-ext-'));
  cpSync(path.join(ROOT, 'extension'), dir, { recursive: true });
  const manifest = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  for (const cs of manifest.content_scripts) cs.matches.push('http://localhost:8123/*');
  manifest.host_permissions.push('http://localhost:8123/*');
  writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
  const bg = path.join(dir, 'background.js');
  writeFileSync(bg, readFileSync(bg, 'utf8').replace('chatgpt\\.com|chat\\.openai\\.com)$/', 'chatgpt\\.com|chat\\.openai\\.com)$|^http:\\/\\/localhost:8123$/'));
  return dir;
}

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html' });
  res.end(readFileSync(path.join(HERE, 'mock.html')));
}).listen(8123);

const profile = mkdtempSync(path.join(os.tmpdir(), 'ug-profile-'));
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: false, pipe: true, enableExtensions: true, userDataDir: profile,
  defaultViewport: { width: 1280, height: 860 }, args: ['--no-first-run', '--no-default-browser-check'],
});
await browser.installExtension(testExtension());

try {
  const page = await browser.newPage();
  await page.goto('http://localhost:8123/', { waitUntil: 'load' });
  await sleep(500);
  const box = 'textarea[name="prompt"]';
  const sent = () => page.evaluate(() => window.sent.slice());
  const received = () => page.evaluate(() => window.received.slice());

  // ---------------------------------------------------------------- prompts
  await page.type(box, 'hello there');
  let watcher = answerDialog('cancel', 3);
  await page.keyboard.press('Enter');
  let seen = await watcher;
  check('clean prompt sends with no dialog', !seen.seen && (await sent()).includes('hello there'));

  await page.type(box, 'secret WALLED_SENSITIVE plan');
  watcher = answerDialog('cancel');
  await page.keyboard.press('Enter');
  seen = await watcher; await sleep(400);
  check('marker prompt shows the native dialog (prompt)', seen.seen && /text prompt/.test(seen.text), seen.text.split('\n')[0]);
  check('Cancel: prompt not sent, text kept in the box',
    !(await sent()).includes('secret WALLED_SENSITIVE plan') && (await page.$eval(box, (b) => b.value)) === 'secret WALLED_SENSITIVE plan');

  watcher = answerDialog('continue');
  await page.click('#send');
  seen = await watcher; await sleep(600);
  check('Continue (send button): original prompt sent unchanged', seen.seen && (await sent()).includes('secret WALLED_SENSITIVE plan'));

  // ---------------------------------------------------------------- file input
  const input = await page.$('#files');
  watcher = answerDialog('cancel', 4);
  await input.uploadFile(path.join(SAMPLES, 'clean.txt'));
  seen = await watcher;
  check('clean file uploads with no dialog', !seen.seen && (await received()).includes('input:clean.txt'));

  for (const [file, answer] of [['sensitive.txt', 'cancel'], ['sensitive.pdf', 'cancel'], ['sensitive.png', 'continue'], ['sensitive-scanned.pdf', 'continue']]) {
    watcher = answerDialog(answer);
    await input.uploadFile(path.join(SAMPLES, file));
    seen = await watcher; await sleep(800);
    const got = (await received()).includes('input:' + file);
    check(`${file}: dialog names the file, ${answer} -> ${answer === 'continue' ? 'uploaded' : 'blocked'}`,
      seen.seen && seen.text.includes(file) && got === (answer === 'continue'), seen.text.split('\n')[0]);
    if (answer === 'cancel') {
      const left = await page.$eval('#files', (i) => i.files.length);
      check(`${file}: Cancel empties the file input so the user can pick again`, left === 0, `${left} file(s) left`);
    }
  }

  // Two files at once: Cancel removes only the flagged one.
  watcher = answerDialog('cancel');
  await page.evaluate(() => { window.received.length = 0; });
  await input.uploadFile(path.join(SAMPLES, 'clean.pdf'), path.join(SAMPLES, 'sensitive.txt'));
  seen = await watcher; await sleep(800);
  const both = await received();
  check('multi-file: Cancel drops only the flagged file', seen.seen && both.includes('input:clean.pdf') && !both.includes('input:sensitive.txt'), both.join(','));

  // ---------------------------------------------------------------- drag and drop, paste
  const fire = (kind) => page.evaluate((kind) => {
    const dt = new DataTransfer();
    dt.items.add(new File(['note: WALLED_SENSITIVE inside'], `${kind}-note.txt`, { type: 'text/plain' }));
    const target = kind === 'drop' ? document.getElementById('drop') : document.querySelector('textarea');
    const ev = kind === 'drop'
      ? new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true })
      : new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    target.dispatchEvent(ev);
  }, kind);
  watcher = answerDialog('continue'); await fire('drop'); seen = await watcher; await sleep(800);
  check('drag-and-drop: dialog, Continue -> dropped file reaches the page', seen.seen && (await received()).includes('drop:drop-note.txt'));
  watcher = answerDialog('cancel'); await fire('paste'); seen = await watcher; await sleep(800);
  check('paste: dialog, Cancel -> pasted file never reaches the page', seen.seen && !(await received()).includes('paste:paste-note.txt'));

  // ---------------------------------------------------------------- live sites
  if (LIVE) {
    for (const site of [
      { name: 'ChatGPT', url: 'https://chatgpt.com/', box: 'textarea[name="prompt"], #prompt-textarea',
        answer: '[data-message-role="assistant"] [data-assistant-markdown-root] p, [data-message-author-role="assistant"] .markdown p' },
      { name: 'Perplexity', url: 'https://www.perplexity.ai/', box: '[data-lexical-editor="true"], [contenteditable="true"][role="textbox"]',
        answer: '[data-workflow-final-text] [data-renderer="lm"] p, [id^="markdown-content-"] p' },
    ]) {
      const p = await browser.newPage();
      await p.goto(site.url, { waitUntil: 'domcontentloaded' });
      try { await p.waitForSelector(site.box, { timeout: 20000 }); } catch {
        check(`${site.name}: page loaded`, false, 'prompt box not found (sign-in wall or bot check?)'); continue;
      }
      await sleep(1500);
      await p.click(site.box);
      await p.keyboard.type('Reply with one word: WALLED_SENSITIVE test');
      watcher = answerDialog('cancel');
      await p.keyboard.press('Enter');
      if (site.name === 'ChatGPT') { await sleep(1200); await p.screenshot({ path: path.join(ROOT, 'docs', 'notice.png') }); }
      seen = await watcher; await sleep(1000);
      const kept = await p.$eval(site.box, (b) => (b.value ?? b.innerText));
      check(`${site.name}: marker prompt -> native dialog; Cancel keeps text`, seen.seen && kept.includes('WALLED_SENSITIVE'), seen.text.split('\n')[1]);
      watcher = answerDialog('continue');
      await p.keyboard.press('Enter');
      seen = await watcher;
      try {
        await p.waitForSelector(site.answer, { timeout: 45000 });
        await sleep(4000);
        const color = await p.$eval(site.answer, (e) => getComputedStyle(e).color);
        check(`${site.name}: Continue sends; AI response recoloured`, seen.seen && color === 'rgb(14, 165, 233)', color);
        const mine = await p.evaluate(() => {
          const el = document.querySelector('[data-message-bubble] p, [data-message-role="user"] p, [data-message-author-role="user"] div');
          return el ? getComputedStyle(el).color : 'not found';
        });
        check(`${site.name}: the user's own question keeps its colour`, mine !== 'rgb(14, 165, 233)', mine);
      } catch (e) {
        check(`${site.name}: Continue sends; AI response recoloured`, false, 'no response appeared: ' + e.message);
      }
      await p.screenshot({ path: path.join(HERE, `live-${site.name}.png`) });

      // Real upload through the site's own file input (Perplexity allows it logged out).
      const fileInput = await p.$('input[type=file]');
      if (site.name === 'Perplexity' && fileInput) {
        // Logged out, Perplexity reacts to any file it receives with a "Sign in to upload
        // files" wall; logged in, it shows the file as an attachment. Either proves the
        // site got the file; neither appearing proves it did not.
        const siteGotFile = (name) => p.evaluate((n) =>
          document.body.innerText.includes(n) || /Sign in to upload files/i.test(document.body.innerText), name);
        watcher = answerDialog('cancel');
        await fileInput.uploadFile(path.join(SAMPLES, 'sensitive.pdf'));
        seen = await watcher; await sleep(2500);
        check('Perplexity live upload: flagged PDF -> dialog; Cancel -> site never receives it',
          seen.seen && /sensitive\.pdf/.test(seen.text) && !(await siteGotFile('sensitive.pdf')));
        watcher = answerDialog('cancel', 5);
        await fileInput.uploadFile(path.join(SAMPLES, 'clean.pdf'));
        seen = await watcher; await sleep(3500);
        check('Perplexity live upload: clean PDF -> no dialog, site receives it', !seen.seen && (await siteGotFile('clean.pdf')));
        await p.screenshot({ path: path.join(HERE, 'live-Perplexity-upload.png') });
      }
    }
  }
  // Popup screenshot (real extension page, so its status check runs for real).
  const pop = await browser.newPage();
  await pop.setViewport({ width: 340, height: 720, deviceScaleFactor: 2 });
  await pop.goto('chrome-extension://aioadbeodagpdhphhhodimcmfghmadoj/popup.html');
  await sleep(1500);
  const popupStatus = await pop.$eval('#statusText', (e) => e.textContent);
  check('popup reports the desktop app as connected', /connected/.test(popupStatus) && !/not/.test(popupStatus), popupStatus);
  const height = await pop.evaluate(() => document.body.scrollHeight);
  await pop.screenshot({ path: path.join(ROOT, 'docs', 'popup.png'), clip: { x: 0, y: 0, width: 340, height } });
} finally {
  await browser.close();
  server.close();
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exitCode = failed ? 1 : 0;
}
