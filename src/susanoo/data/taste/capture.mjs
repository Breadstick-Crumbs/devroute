// Capture reference sites for a taste profile with a headless Chromium browser over CDP (Node 22+ built-ins only).
//   SUSANOO_BROWSER=<chrome-or-edge-path> node capture.mjs <sites-dir> <slug>=<url> [<slug>=<url> ...]
// For each site: <sites-dir>/<slug>/tokens.json (computed design tokens from extract.js) and
// top.jpg / mid.jpg / low.jpg (1440x900 at 0%, 30% and 60% scroll), plus navigation and motion (motion.json:
// navbar behaviour across a scroll down and up, pinned/parallax/animated elements while scrolling, hover changes,
// the mobile menu), frames/ and strip.jpg (8 scroll frames), mobile-top.jpg and mobile-menu.jpg.
// SUSANOO_CAPTURE_NO_MOTION=1 skips the motion part. Consent overlays are hidden, never accepted.
// Run through `susanoo taste capture`, which finds the browser.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
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
const watchdog = setTimeout(() => { console.error('timed out'); process.exitCode = 5; finish(); }, Math.min(3500, 30 + 75 * SITES.length) * 1000);
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

// ---- navigation and motion (motion-probe.js runs in the page; nothing is submitted or followed) ----
const PROBE = readFileSync(join(HERE, 'motion-probe.js'), 'utf8');
const NO_MOTION = process.env.SUSANOO_CAPTURE_NO_MOTION === '1';

async function evalIn(c, expression) {
  const r = await c.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  const ex = r.result?.exceptionDetails;
  if (ex) throw new Error(`in-page error: ${ex.exception?.description ?? ex.text}`.slice(0, 300));
  return r.result?.result?.value;
}
const M = (call) => `window.__susanooMotion.${call}`;

function summarizeNav(samples) {
  const at = Object.fromEntries(samples.map((s) => [s.label, s]));
  const top = at.top, down = at['down-1200'], far = at['down-2000'], up = at['up-1500'];
  if (!top?.found) return ['no navbar found near the top'];
  const visible = (s) => s?.found && s.top > -s.height * 0.5 && s.top < 200;
  const out = [];
  if (!visible(down)) out.push(top.position === 'fixed' || top.position === 'sticky' ? 'hides when you scroll down' : 'scrolls away with the page');
  else out.push(`stays visible while scrolling (${down.position})`);
  if (!visible(far) && visible(up)) out.push('comes back when you scroll up');
  if (down?.found && top.bg !== down.bg) out.push(`background ${top.bg} -> ${down.bg} after scrolling`);
  if (down?.found && top.blur !== down.blur && down.blur !== 'none') out.push(`backdrop blur appears (${down.blur})`);
  if (down?.found && top.shadow === 'none' && down.shadow !== 'none') out.push('a shadow appears after scrolling');
  if (down?.found && top.border === 'none' && down.border !== 'none') out.push('a bottom border appears after scrolling');
  if (down?.found && Math.abs(top.height - down.height) > 8) out.push(`height ${top.height}px -> ${down.height}px (shrinks or grows)`);
  if (top.centered) out.push(`floating, centred bar (${top.width}px wide)`);
  out.push(`${top.links} links/buttons${top.cta.length ? `; CTA: ${top.cta.join(', ')}` : ''}`);
  return out;
}

function summarizeScroll(steps) {
  const pinned = new Map(), parallax = new Map(), animated = new Map();
  for (let i = 1; i < steps.length; i++) {
    const a = steps[i - 1], b = steps[i]; const dy = b.y - a.y; if (dy < 50) continue;
    for (const [id, p] of Object.entries(a.els)) {
      const q = b.els[id]; if (!q) continue;
      const inView = (e) => e.top < 900 && e.top + e.h > 0;
      if (!inView(p) || !inView(q)) continue;
      const ratio = (p.top - q.top) / dy;
      if (Math.abs(ratio) < 0.15 && p.h < 2000) pinned.set(id, p.l);
      else if ((ratio > 0.2 && ratio < 0.85) || (ratio > 1.15 && ratio < 2.5)) parallax.set(id, `${p.l} (${ratio.toFixed(2)}x)`);
      if (Math.abs(p.op - q.op) > 0.3 || p.tf !== q.tf || p.f !== q.f || p.cp !== q.cp) animated.set(id, p.l);
    }
  }
  const pick = (m) => [...m.values()].slice(0, 5);
  return { pinned: { count: pinned.size, examples: pick(pinned) }, parallax: { count: parallax.size, examples: pick(parallax) },
    animatedOnScroll: { count: animated.size, examples: pick(animated) } };
}

async function probeMotion(c, out) {
  await evalIn(c, PROBE);
  const motion = { page: await evalIn(c, M('page()')), libs: await evalIn(c, M('libs()')) };
  // 1. Navbar across a scroll down and back up.
  const navSamples = [];
  for (const [y, label] of [[0, 'top'], [400, 'down-400'], [1200, 'down-1200'], [2000, 'down-2000'], [1500, 'up-1500']]) {
    const s = await evalIn(c, M(`scrollTo(${y})`)); await sleep(900);
    navSamples.push({ label, scroll: s, ...(await evalIn(c, M('nav()'))) });
  }
  motion.nav = { summary: summarizeNav(navSamples), samples: navSamples };
  // 2. Scroll: positions and styles of tagged elements at 8 steps, plus a frame per step.
  await evalIn(c, M('scrollTo(0)')); await sleep(800);
  motion.tagged = await evalIn(c, M('tag()'));
  const frames = join(out, 'frames'); mkdirSync(frames, { recursive: true });
  const steps = [];
  for (let i = 0; i < 8; i++) {
    const s = await evalIn(c, M(`scrollTo(${i * 540})`)); await sleep(750);
    steps.push({ y: s.y, els: await evalIn(c, M('snapshot()')) });
    const shot = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 50, clip: { x: 0, y: 0, width: 1440, height: 900, scale: 0.25 } });
    if (shot.result?.data) writeFileSync(join(frames, `${String(i).padStart(2, '0')}.jpg`), Buffer.from(shot.result.data, 'base64'));
    if (s.y >= s.max) break;
  }
  motion.scroll = summarizeScroll(steps);
  motion.scroll.container = steps.length ? (await evalIn(c, M('scrollTo(0)'))).container : 'document';
  // 3. Hover: nav links and the first buttons, before and after the pointer moves over them.
  await sleep(800);
  const targets = await evalIn(c, M('hoverTargets()'));
  motion.hover = [];
  for (const t of targets) {
    const before = await evalIn(c, M(`hoverState(${t.i})`));
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: t.x, y: t.y }); await sleep(500);
    const after = await evalIn(c, M(`hoverState(${t.i})`));
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 2, y: 450 }); await sleep(200);
    const changed = before && after ? Object.keys(after).filter((k) => k !== 'transition' && after[k] !== before[k]) : [];
    motion.hover.push({ target: t.l, text: t.text, changed: changed.map((k) => `${k}: ${before[k]} -> ${after[k]}`), transition: after?.transition });
  }
  // 4. Mobile: the top at 390 px and the menu opened by its toggle (a click on the menu button only).
  await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await c.send('Page.reload', {}); await sleep(7000);
  await c.send('Runtime.evaluate', { expression: HIDE_CONSENT }); await evalIn(c, PROBE);
  const mtop = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 55 });
  if (mtop.result?.data) writeFileSync(join(out, 'mobile-top.jpg'), Buffer.from(mtop.result.data, 'base64'));
  const toggle = await evalIn(c, M('menuToggle()'));
  motion.mobileMenu = { toggle };
  if (toggle) {
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: toggle.x, y: toggle.y, button: 'left', clickCount: 1 });
    await sleep(1200);
    await evalIn(c, PROBE); // re-inject in case the page re-rendered
    motion.mobileMenu.opened = await evalIn(c, M('openedOverlay()'));
    motion.mobileMenu.urlAfter = await evalIn(c, 'location.href');
    const mm = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 55 });
    if (mm.result?.data) writeFileSync(join(out, 'mobile-menu.jpg'), Buffer.from(mm.result.data, 'base64'));
  }
  // 5. One image with all scroll frames, for a quick look.
  const imgs = steps.map((_, i) => `<figure><img src="frames/${String(i).padStart(2, '0')}.jpg"><figcaption>${i + 1} · ${steps[i].y}px</figcaption></figure>`).join('');
  writeFileSync(join(out, 'strip.html'), `<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#111;display:grid;grid-template-columns:repeat(4,360px);gap:4px;padding:4px;font:12px system-ui;color:#ccc}figure{margin:0}img{display:block;width:360px}</style>${imgs}`);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1456, height: 8 + Math.ceil(steps.length / 4) * 250, deviceScaleFactor: 1, mobile: false });
  await c.send('Page.navigate', { url: pathToFileURL(join(out, 'strip.html')).href }); await sleep(1200);
  const strip = await c.send('Page.captureScreenshot', { format: 'jpeg', quality: 70 });
  if (strip.result?.data) writeFileSync(join(out, 'strip.jpg'), Buffer.from(strip.result.data, 'base64'));
  return motion;
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
      let motionOk = true;
      if (!NO_MOTION) {
        try { const m = await probeMotion(c, out); writeFileSync(join(out, 'motion.json'), JSON.stringify(m, null, 2)); }
        catch (e) { motionOk = false; writeFileSync(join(out, 'motion.json'), JSON.stringify({ error: String(e).slice(0, 300) }, null, 2)); }
      }
      console.log(JSON.stringify({ slug, ok: !tokens.error, motion: NO_MOTION ? 'skipped' : motionOk, title: tokens.title ?? null }));
    } catch (e) { failed++; console.log(JSON.stringify({ slug, ok: false, error: String(e).slice(0, 200) })); }
    finally { c.close(); await fetch(`http://127.0.0.1:${PORT}/json/close/${target.id}`).catch(() => {}); }
  }
  if (failed) process.exitCode = 1;
}
main().catch((e) => { console.error(String(e)); process.exitCode = 1; })
  .finally(finish);
