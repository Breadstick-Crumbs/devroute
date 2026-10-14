// Screenshot a page at the brief's visual-check widths with a headless Chromium browser (Node 22+ built-ins only).
//   SUSANOO_BROWSER=<chrome-or-edge-path> node shoot.mjs <url-or-html-path> <out-dir>
// Writes 1280-top.jpg, 1280-mid.jpg, 768-top.jpg, 375-top.jpg and console.json (page errors).
// Exits non-zero when any screenshot could not be taken. Run through `susanoo taste shoot`.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';

if (+process.versions.node.split('.')[0] < 22) { console.error('Node.js 22 or newer is needed'); process.exit(4); }
const [target, outArg] = process.argv.slice(2);
if (!target || !outArg) { console.error('usage: node shoot.mjs <url-or-html-path> <out-dir>'); process.exit(2); }
const BROWSER = process.env.SUSANOO_BROWSER;
if (!BROWSER || !existsSync(BROWSER)) { console.error('no browser: set SUSANOO_BROWSER (susanoo taste shoot does this)'); process.exit(3); }
const url = /^https?:/.test(target) ? target : pathToFileURL(resolve(target)).href;
const out = resolve(outArg); mkdirSync(out, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), 'susanoo-shoot-'));
// Port 0: the OS picks a free port, written to DevToolsActivePort in the private profile folder.
const browser = spawn(BROWSER, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--hide-scrollbars', '--no-first-run', 'about:blank'], { stdio: 'ignore' });
browser.on('error', (e) => { console.error(`could not start the browser: ${e.message}`); process.exitCode = 3; cleanup(); });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const watchdog = setTimeout(() => { console.error('timed out'); process.exitCode = 5; cleanup(); }, 180000);

async function connect() {
  let port;
  for (let i = 0; i < 80 && !port; i++) {
    try { port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0].trim(); } catch { await sleep(250); }
  }
  if (!port) throw new Error('browser did not start');
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('could not connect to the browser')); });
  let id = 0; const wait = new Map(); const events = [];
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id) { wait.get(d.id)?.(d); wait.delete(d.id); } else events.push(d); };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  return { send, events };
}

let cleaning = false;
function cleanup() {
  if (cleaning) return; cleaning = true;
  clearTimeout(watchdog);
  browser.kill();
  setTimeout(() => { try { rmSync(profile, { recursive: true, force: true }); } catch {} process.exit(process.exitCode ?? 0); }, 1000);
}

(async () => {
  const c = await connect();
  await c.send('Page.enable'); await c.send('Runtime.enable');
  const written = [];
  for (const [w, h, mobile, shots] of [[1280, 800, false, [['top', 0], ['mid', 0.45]]], [768, 1024, true, [['top', 0]]], [375, 812, true, [['top', 0]]]]) {
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile });
    await c.send('Page.navigate', { url }); await sleep(3500);
    for (const [label, frac] of shots) {
      await c.send('Runtime.evaluate', { expression: `window.scrollTo(0, document.documentElement.scrollHeight * ${frac})` }); await sleep(1200);
      const s = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 70 });
      if (!s.result?.data) throw new Error(`screenshot ${w}-${label} failed: ${JSON.stringify(s.error ?? s).slice(0, 200)}`);
      writeFileSync(join(out, `${w}-${label}.jpg`), Buffer.from(s.result.data, 'base64'));
      written.push(`${w}-${label}.jpg`);
    }
  }
  const errors = c.events.filter((e) => e.method === 'Runtime.exceptionThrown' || (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error'))
    .map((e) => JSON.stringify(e.params).slice(0, 300));
  writeFileSync(join(out, 'console.json'), JSON.stringify({ url, errors }, null, 2));
  console.log(JSON.stringify({ out, widths: [375, 768, 1280], shots: written, errors: errors.length }));
})().catch((e) => { console.error(String(e)); process.exitCode = 1; }).finally(cleanup);
