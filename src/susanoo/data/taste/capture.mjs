// Capture reference sites for a taste profile with a headless Chromium browser over CDP (Node 22+ built-ins only).
//   SUSANOO_BROWSER=<chrome-or-edge-path> node capture.mjs <sites-dir> <slug>=<url> [<slug>=<url> ...]
// For each site: <sites-dir>/<slug>/tokens.json (computed design tokens from extract.js) and
// top.jpg / mid.jpg / low.jpg (1440x900 at 0%, 30% and 60% scroll). Consent overlays are hidden, never accepted.
// Run through `susanoo taste capture`, which finds the browser.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

if (+process.versions.node.split('.')[0] < 22) { console.error('Node.js 22 or newer is needed'); process.exit(4); }
const HERE = dirname(fileURLToPath(import.meta.url));
const [outArg, ...pairs] = process.argv.slice(2);
const SITES = pairs.map((p) => { const i = p.indexOf('='); return [p.slice(0, i), p.slice(i + 1)]; });
if (!outArg || !SITES.length || SITES.some(([s, u]) => !/^[\w-]+$/.test(s) || !/^https?:\/\//.test(u))) {
  console.error('usage: node capture.mjs <sites-dir> <slug>=<https-url> ...'); process.exit(2);
}
const BROWSER = process.env.SUSANOO_BROWSER;
if (!BROWSER || !existsSync(BROWSER)) { console.error('no browser: set SUSANOO_BROWSER'); process.exit(3); }
const OUT = resolve(outArg);
const EXTRACT = readFileSync(join(HERE, 'extract.js'), 'utf8');
const HIDE_CONSENT = `(() => { const vw = innerWidth * innerHeight; let n = 0;
  document.querySelectorAll('body *').forEach((el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (el.closest('nav, header, footer, main') || el.querySelector('main, nav, header')) return;
    const text = el.textContent.slice(0, 800);
    const hasChoice = [...el.querySelectorAll('button, a, [role=button]')].some((b) => /\b(accept|agree|reject|decline|allow|deny|got it|ok)\b/i.test(b.textContent));
    if ((s.position === 'fixed' || s.position === 'sticky' || +s.zIndex > 999) && /cookie|consent|gdpr/i.test(text) && hasChoice && r.width * r.height > vw * 0.03) { el.style.setProperty('display', 'none', 'important'); n++; } });
  document.documentElement.style.overflow = 'auto'; document.body.style.overflow = 'auto'; return n; })()`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), 'susanoo-capture-'));
let PORT;
const browser = spawn(BROWSER, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--window-size=1440,900', '--hide-scrollbars', '--mute-audio', '--no-first-run', 'about:blank'], { stdio: 'ignore' });
browser.on('error', (e) => { console.error(`could not start the browser: ${e.message}`); process.exitCode = 3; finish(); });
// Stops well before the caller's timeout so the browser is always closed and the profile removed.
const watchdog = setTimeout(() => { console.error('timed out'); process.exitCode = 5; finish(); }, Math.min(1700, 30 + 25 * SITES.length) * 1000);
let finishing = false;
function finish() {
  if (finishing) return; finishing = true; clearTimeout(watchdog);
  browser.kill(); setTimeout(() => { try { rmSync(profile, { recursive: true, force: true }); } catch {} process.exit(process.exitCode ?? 0); }, 1500);
}

async function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('could not connect to the browser')); });
  let id = 0; const waiting = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; waiting.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  return { send, close: () => ws.close() };
}

async function main() {
  for (let i = 0; i < 80 && !PORT; i++) {
    try { PORT = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0].trim(); } catch { await sleep(250); }
  }
  if (!PORT) throw new Error('browser did not start');
  let failed = 0;
  for (const [slug, url] of SITES) {
    const out = join(OUT, slug); mkdirSync(out, { recursive: true });
    const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
    const c = await cdp(target.webSocketDebuggerUrl);
    try {
      await c.send('Page.enable'); await c.send('Runtime.enable');
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
      await c.send('Page.navigate', { url });
      await sleep(9000);
      await c.send('Runtime.evaluate', { expression: HIDE_CONSENT });
      for (const f of [0.15, 0.3, 0.45, 0]) { await c.send('Runtime.evaluate', { expression: `window.scrollTo(0, document.documentElement.scrollHeight * ${f})` }); await sleep(700); }
      const ev = await c.send('Runtime.evaluate', { expression: EXTRACT, returnByValue: true, awaitPromise: true });
      const tokens = ev.result?.result?.value ?? { error: JSON.stringify(ev.result?.exceptionDetails ?? ev).slice(0, 500) };
      writeFileSync(join(out, 'tokens.json'), JSON.stringify(tokens, null, 2));
      if (tokens.error) failed++;
      for (const [name, frac] of [['top', 0], ['mid', 0.3], ['low', 0.6]]) {
        await c.send('Runtime.evaluate', { expression: `window.scrollTo(0, document.documentElement.scrollHeight * ${frac})` });
        await sleep(2500);
        const shot = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 55 });
        if (shot.result?.data) writeFileSync(join(out, `${name}.jpg`), Buffer.from(shot.result.data, 'base64'));
      }
      console.log(JSON.stringify({ slug, ok: !tokens.error, title: tokens.title ?? null }));
    } catch (e) { failed++; console.log(JSON.stringify({ slug, ok: false, error: String(e).slice(0, 200) })); }
    finally { c.close(); await fetch(`http://127.0.0.1:${PORT}/json/close/${target.id}`).catch(() => {}); }
  }
  if (failed) process.exitCode = 1;
}
main().catch((e) => { console.error(String(e)); process.exitCode = 1; })
  .finally(finish);
