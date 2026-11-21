// End-to-end test of the standalone desktop app's proxy.
//
// Real Chrome (throwaway profile) -> Upload Guard proxy (python -m upload_guard proxy)
// -> a local test site and, with --live, the real ChatGPT and Perplexity.
// The native Windows dialog is clicked by answer_dialog.py. Chrome trusts the proxy
// only through --ignore-certificate-errors-spki-list: nothing is installed on the PC
// and no system proxy setting is changed.
//
//   node run.mjs            local test site
//   node run.mjs --live     also ChatGPT and Perplexity
import puppeteer from 'puppeteer-core';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const SAMPLES = path.join(ROOT, 'samples');
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const LIVE = process.argv.includes('--live');
const PROXY_PORT = 18899, PAC_PORT = 18898, SITE_PORT = 18123;
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
}

function answerDialog(answer, timeout = 25) {
  return new Promise((resolve) => {
    const p = spawn('python', [path.join(HERE, 'answer_dialog.py'), answer, String(timeout)]);
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.on('close', () => { try { resolve(JSON.parse(out)); } catch { resolve({ seen: false, text: out }); } });
  });
}

// ------------------------------------------------------------------ local test site
const received = { prompts: [], files: [], beacons: [] };
const site = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    const json = (obj, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (req.url === '/') { res.writeHead(200, { 'content-type': 'text/html' }); return res.end(readFileSync(path.join(HERE, 'mock.html'))); }
    if (req.url === '/api/ask') { received.prompts.push(JSON.parse(body).query_str); return json({ ok: true }); }
    if (req.url === '/api/files') { return json({ upload_url: `http://upload-guard.test:${SITE_PORT}/blob/7f3a9c` }); }
    if (req.url.startsWith('/blob/')) { received.files.push({ how: 'raw', size: body.length }); return json({ ok: true }, 201); }
    if (req.url === '/s3-upload') {
      const name = (body.toString('latin1').match(/filename="([^"]+)"/) || [])[1];
      received.files.push({ how: 'multipart', name }); return json({ ok: true }, 204 === 0 ? 0 : 201);
    }
    if (req.url === '/rest/event/analytics') { received.beacons.push(body.toString()); return json({ ok: true }); }
    json({ error: 'not found' }, 404);
  });
}).listen(SITE_PORT);

// ------------------------------------------------------------------ Upload Guard proxy
const home = mkdtempSync(path.join(os.tmpdir(), 'ug-home-'));
const proxy = spawn('python', ['-m', 'upload_guard', 'proxy', '--port', String(PROXY_PORT), '--pac-port', String(PAC_PORT)], {
  cwd: ROOT, env: { ...process.env, PYTHONUNBUFFERED: '1', UPLOAD_GUARD_HOME: home, UPLOAD_GUARD_TEST_HOSTS: 'upload-guard.test', UPLOAD_GUARD_TEST_UPSTREAM: '127.0.0.1' },
});
let proxyOut = '';
proxy.stdout.on('data', (d) => { proxyOut += d; });
proxy.stderr.on('data', (d) => { proxyOut += d; });
for (let i = 0; i < 100 && !/CA public-key hash: (\S+)/.test(proxyOut); i++) await sleep(200);
const spki = (proxyOut.match(/CA public-key hash: (\S+)/) || [])[1];
if (!spki) { console.log(proxyOut); throw new Error('proxy did not start'); }

const profile = mkdtempSync(path.join(os.tmpdir(), 'ug-profile-'));
const browser = await puppeteer.launch({
  executablePath: CHROME, headless: false, userDataDir: profile, defaultViewport: { width: 1280, height: 860 },
  args: [`--proxy-pac-url=http://127.0.0.1:${PAC_PORT}/upload-guard.pac`, `--ignore-certificate-errors-spki-list=${spki}`,
         '--no-first-run', '--no-default-browser-check'],
});

try {
  const page = await browser.newPage();
  await page.goto(`http://upload-guard.test:${SITE_PORT}/`, { waitUntil: 'load' });
  const call = (fn, arg) => page.evaluate((f, a) => window[f](a), fn, arg);
  const pick = async (file) => (await page.$('#file')).uploadFile(path.join(SAMPLES, file));

  // ---------------------------------------------------------------- prompts
  let watcher = answerDialog('cancel', 3);
  let status = await call('sendPrompt', 'hello there');
  let seen = await watcher;
  check('clean prompt is sent, no dialog', !seen.seen && received.prompts.includes('hello there'), status);

  watcher = answerDialog('cancel');
  status = await call('sendPrompt', 'secret WALLED_SENSITIVE plan');
  seen = await watcher;
  check('marker prompt -> native dialog; Cancel -> never reaches the site',
    seen.seen && /text prompt/.test(seen.text) && !received.prompts.includes('secret WALLED_SENSITIVE plan') && /403/.test(status),
    `${seen.text.split('\n')[0]} / ${status}`);

  watcher = answerDialog('cancel', 4);
  status = await call('sendBeacon', 'secret WALLED_SENSITIVE plan');
  seen = await watcher;
  check('tracking beacon with unapproved marker text: blocked, no dialog', !seen.seen && received.beacons.length === 0, status);

  watcher = answerDialog('continue');
  status = await call('sendPrompt', 'secret WALLED_SENSITIVE plan');
  seen = await watcher;
  check('Continue -> the original prompt reaches the site unchanged', seen.seen && received.prompts.includes('secret WALLED_SENSITIVE plan'), status);

  watcher = answerDialog('cancel', 4);
  status = await call('sendBeacon', 'secret WALLED_SENSITIVE plan');
  seen = await watcher;
  check('tracking beacon repeating the approved text: allowed, no dialog', !seen.seen && received.beacons.length === 1, status);

  // ---------------------------------------------------------------- files
  for (const [file, how, answer] of [
    ['clean.txt', 'uploadMultipart', null], ['clean.pdf', 'uploadRaw', null], ['clean.png', 'uploadMultipart', null],
    ['sensitive.txt', 'uploadMultipart', 'cancel'], ['sensitive.pdf', 'uploadRaw', 'cancel'],
    ['sensitive.png', 'uploadMultipart', 'continue'], ['sensitive-scanned.pdf', 'uploadRaw', 'continue'],
  ]) {
    const before = received.files.length;
    await pick(file);
    watcher = answerDialog(answer || 'cancel', answer ? 25 : 5);
    status = await call(how);
    seen = await watcher;
    const arrived = received.files.length > before;
    if (!answer) check(`${file} (${how}): clean -> uploaded, no dialog`, !seen.seen && arrived, status);
    else check(`${file} (${how}): dialog names the file; ${answer} -> ${answer === 'continue' ? 'uploaded' : 'blocked'}`,
      seen.seen && seen.text.includes(file) && arrived === (answer === 'continue'), seen.text.split('\n')[0]);
  }

  // ---------------------------------------------------------------- live sites
  if (LIVE) {
    for (const s of [
      { name: 'ChatGPT', url: 'https://chatgpt.com/', box: 'textarea[name="prompt"], #prompt-textarea',
        answer: '[data-message-role="assistant"] [data-assistant-markdown-root] p, [data-message-author-role="assistant"] .markdown p',
        mine: '[data-message-role="user"] p, [data-message-author-role="user"] div' },
      { name: 'Perplexity', url: 'https://www.perplexity.ai/', box: '[data-lexical-editor="true"], [contenteditable="true"][role="textbox"]',
        answer: '[data-workflow-final-text] [data-renderer="lm"] p, [id^="markdown-content-"] p', mine: '[data-message-bubble] p' },
    ]) {
      const p = await browser.newPage();
      await p.goto(s.url, { waitUntil: 'domcontentloaded' });
      try { await p.waitForSelector(s.box, { timeout: 30000 }); } catch { check(`${s.name}: page loaded through the proxy`, false); continue; }
      await sleep(1500);
      await p.click(s.box);
      await p.keyboard.type('Reply with one word: WALLED_SENSITIVE test');
      watcher = answerDialog('cancel');
      await p.keyboard.press('Enter');
      seen = await watcher; await sleep(3000);
      const kept = await p.$eval(s.box, (b) => (b.value ?? b.innerText)).catch(() => '');
      const answered = await p.$(s.answer);
      check(`${s.name}: marker prompt -> native dialog; Cancel -> not sent, text kept`,
        seen.seen && kept.includes('WALLED_SENSITIVE') && !answered, seen.text.split('\n')[0]);
      watcher = answerDialog('continue');
      await p.click(s.box);
      await p.keyboard.press('Enter');
      seen = await watcher;
      try {
        await p.waitForSelector(s.answer, { timeout: 45000 }); await sleep(4000);
        const color = await p.$eval(s.answer, (e) => getComputedStyle(e).color);
        check(`${s.name}: Continue -> sent; AI response recoloured`, seen.seen && color === 'rgb(14, 165, 233)', color);
        const mine = await p.$eval(s.mine, (e) => getComputedStyle(e).color).catch(() => 'not found');
        check(`${s.name}: the user's own message keeps its colour`, mine !== 'rgb(14, 165, 233)', mine);
      } catch (e) {
        check(`${s.name}: Continue -> sent; AI response recoloured`, false, e.message);
      }
      await p.screenshot({ path: path.join(HERE, `live-${s.name}.png`) });
    }
  }
} finally {
  await browser.close();
  proxy.kill();
  site.close();
  for (const dir of [profile, home]) { try { rmSync(dir, { recursive: true, force: true }); } catch {} }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  if (failed) console.log('--- proxy output ---\n' + proxyOut.slice(-3000));
  process.exitCode = failed ? 1 : 0;
}
