// In-page probes for navigation and motion (injected by capture.mjs). Defines window.__susanooMotion.
// Read-only: it measures computed styles and positions; it never submits forms or follows links.
(() => {
  const cs = (el) => getComputedStyle(el);
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = (el) => {
    const cls = typeof el.className === 'string' ? el.className.split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${cls ? '.' + cls : ''}`.slice(0, 70);
  };
  const style = (el) => { const s = cs(el); return { position: s.position, bg: s.backgroundColor, blur: s.backdropFilter || s.webkitBackdropFilter || 'none',
    shadow: s.boxShadow === 'none' ? 'none' : s.boxShadow.slice(0, 60), border: s.borderBottomWidth !== '0px' ? `${s.borderBottomWidth} ${s.borderBottomColor}` : 'none',
    opacity: s.opacity, transform: s.transform, color: s.color }; };

  // The element that actually scrolls: the document, or a full-height scroll container (some sites).
  function scroller() {
    const doc = document.scrollingElement || document.documentElement;
    if (doc.scrollHeight > innerHeight * 1.3) return doc;
    let best = null;
    for (const el of document.querySelectorAll('body *')) {
      const s = cs(el);
      if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight * 1.3 && el.clientHeight > innerHeight * 0.6)
        if (!best || el.scrollHeight > best.scrollHeight) best = el;
    }
    return best || doc;
  }
  function scrollTo(y) { const s = scroller(); s.scrollTo(0, y); return { y: s.scrollTop, max: s.scrollHeight - s.clientHeight, container: s === (document.scrollingElement || document.documentElement) ? 'document' : label(s) }; }

  function findNav() {
    const cands = [...document.querySelectorAll('header, nav, [role="banner"], [class*="nav" i], [class*="header" i]')].filter((el) => {
      const r = el.getBoundingClientRect(); return vis(el) && r.top < 140 && r.width > innerWidth * 0.5 && r.height < 220 && r.height > 24;
    });
    cands.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width || a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    // Prefer the outermost bar (header over the nav inside it).
    return cands.find((el) => !cands.some((o) => o !== el && o.contains(el))) || cands[0] || null;
  }
  function nav() {
    const el = window.__susanooNav && document.contains(window.__susanooNav) ? window.__susanooNav : (window.__susanooNav = findNav());
    if (!el) return { found: false };
    const r = el.getBoundingClientRect();
    const links = [...el.querySelectorAll('a, button')].filter(vis);
    const first = links.find((a) => a.textContent.trim().length > 1);
    const ls = first ? cs(first) : null;
    return { found: true, el: label(el), top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width), ...style(el),
      links: links.length, linkStyle: ls ? { font: ls.fontFamily.split(',')[0].replace(/["']/g, ''), size: ls.fontSize, weight: ls.fontWeight, tt: ls.textTransform, ls: ls.letterSpacing } : null,
      cta: links.filter((a) => { const s = cs(a); return s.backgroundColor !== 'rgba(0, 0, 0, 0)' && a.getBoundingClientRect().width < 260; }).map((a) => a.textContent.trim().slice(0, 30)).slice(0, 3),
      centered: Math.abs((r.left + r.right) / 2 - innerWidth / 2) < 20 && r.width < innerWidth * 0.9 };
  }

  // Scroll motion: tag a sample of elements, then compare their screen positions and styles between steps.
  function tag() {
    let n = 0;
    for (const el of document.querySelectorAll('section, h1, h2, h3, img, video, canvas, [class*="card" i], main > div > div')) {
      if (n >= 220) break;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 20) continue;
      el.dataset.susanooProbe = String(n++);
    }
    return n;
  }
  function snapshot() {
    const out = {};
    for (const el of document.querySelectorAll('[data-susanoo-probe]')) {
      const r = el.getBoundingClientRect(); const s = cs(el);
      out[el.dataset.susanooProbe] = { top: r.top, h: r.height, op: +s.opacity, tf: s.transform, f: s.filter, cp: s.clipPath, l: label(el) };
    }
    return out;
  }
  function page() {
    const html = cs(document.documentElement), body = cs(document.body);
    let snap = html.scrollSnapType !== 'none' ? 'html ' + html.scrollSnapType : '';
    if (!snap) for (const el of document.querySelectorAll('body *')) { const t = cs(el).scrollSnapType; if (t && t !== 'none') { snap = label(el) + ' ' + t; break; } }
    const durations = {}; let withTransition = 0;
    for (const el of [...document.querySelectorAll('a, button, [class*="card" i], img, nav *, header *')].slice(0, 600)) {
      const s = cs(el); const d = s.transitionDuration.split(',')[0];
      if (d && d !== '0s') { withTransition++; const key = `${d} ${s.transitionTimingFunction.split(',')[0]}`; durations[key] = (durations[key] || 0) + 1; }
    }
    return { scrollBehavior: html.scrollBehavior, smoothScrollClass: [...document.documentElement.classList, ...document.body.classList].filter((c) => /lenis|locomotive|smooth/i.test(c)),
      scrollSnap: snap || 'none', overscroll: body.overscrollBehavior, transitions: { elements: withTransition, common: Object.entries(durations).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k, v]) => `${k} x${v}`) },
      cursor: cs(document.body).cursor, customCursor: !!document.querySelector('[class*="cursor" i]') };
  }

  // Hover targets: a few nav links and the first prominent buttons, as viewport centers.
  function hoverTargets() {
    const navEl = window.__susanooNav || findNav();
    const navLinks = navEl ? [...navEl.querySelectorAll('a')].filter((a) => vis(a) && a.textContent.trim().length > 1).slice(0, 2) : [];
    const buttons = [...document.querySelectorAll('a, button')].filter((b) => { const r = b.getBoundingClientRect(); const s = cs(b);
      return vis(b) && r.top > 80 && r.bottom < innerHeight && s.backgroundColor !== 'rgba(0, 0, 0, 0)' && r.width < 360 && r.height < 90; }).slice(0, 2);
    return [...navLinks, ...buttons].map((el, i) => { el.dataset.susanooHover = String(i); const r = el.getBoundingClientRect();
      return { i, l: label(el), text: el.textContent.trim().slice(0, 30), x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; });
  }
  function hoverState(i) {
    const el = document.querySelector(`[data-susanoo-hover="${i}"]`); if (!el) return null; const s = cs(el);
    return { color: s.color, bg: s.backgroundColor, tf: s.transform, op: s.opacity, shadow: s.boxShadow.slice(0, 50), deco: s.textDecorationLine, border: s.borderColor, transition: `${s.transitionDuration.split(',')[0]} ${s.transitionTimingFunction.split(',')[0]}` };
  }

  // Mobile menu toggle near the top: aria-expanded, or a label/class that says menu.
  function menuToggle() {
    const cands = [...document.querySelectorAll('button, [role="button"], a, [aria-expanded]')].filter((el) => {
      const r = el.getBoundingClientRect(); if (!vis(el) || r.top > 140 || r.width > 120) return false;
      // Never a real link: clicking it would navigate away. Only buttons and in-page (#) toggles.
      const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
      if (href && !href.startsWith('#') && !href.startsWith('javascript:')) return false;
      if (el.closest('form') && el.type === 'submit') return false;
      const text = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''} ${typeof el.className === 'string' ? el.className : ''} ${el.textContent}`;
      return el.hasAttribute('aria-expanded') || /menu|burger|hamburger|nav|toggle|drawer/i.test(text);
    });
    const el = cands[0]; if (!el) return null; const r = el.getBoundingClientRect();
    return { l: label(el), x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  }
  function openedOverlay() {
    let best = null;
    for (const el of document.querySelectorAll('body *')) {
      const s = cs(el); if (!(s.position === 'fixed' || s.position === 'absolute') || !vis(el)) continue;
      const r = el.getBoundingClientRect(); const area = (r.width * r.height) / (innerWidth * innerHeight);
      if (area > 0.35 && (!best || area > best.area)) best = { l: label(el), area: +area.toFixed(2), width: Math.round(r.width), bg: s.backgroundColor, links: el.querySelectorAll('a').length };
    }
    return best;
  }

  // How the motion is built, read from the libraries' own runtime state (not guessed from file names).
  function libs() {
    const out = {};
    const g = window.gsap; const st = window.ScrollTrigger || (g && g.core && g.core.globals && g.core.globals().ScrollTrigger);
    if (g) out.gsap = g.version || true;
    if (st && typeof st.getAll === 'function') {
      out.scrollTriggers = st.getAll().slice(0, 25).map((t) => {
        const v = t.vars || {}; const trg = t.trigger instanceof Element ? label(t.trigger) : String(v.trigger || '').slice(0, 60);
        return { trigger: trg, pin: !!v.pin, scrub: v.scrub ?? false, start: String(v.start ?? '').slice(0, 30), end: String(v.end ?? '').slice(0, 30), toggleActions: v.toggleActions || null, snap: !!v.snap };
      });
    }
    const lenis = window.lenis || window.__lenis || (window.Lenis && window.Lenis.instance);
    if (lenis) out.lenis = { lerp: lenis.options?.lerp ?? null, duration: lenis.options?.duration ?? null, smoothWheel: lenis.options?.smoothWheel ?? null };
    else if (document.documentElement.classList.contains('lenis')) out.lenis = { detected: 'class only' };
    if (window.LocomotiveScroll || document.querySelector('[data-scroll-container]')) out.locomotive = true;
    if (window.Webflow) { try { const ix = window.Webflow.require && window.Webflow.require('ix2'); out.webflowInteractions = !!ix; } catch { out.webflowInteractions = 'present'; } }
    const framer = document.querySelectorAll('[data-framer-appear-id]').length; if (framer) out.framerAppear = framer;
    const aos = document.querySelectorAll('[data-aos]').length; if (aos) out.aos = aos;
    const split = document.querySelectorAll('[class*="char"], [class*="split" i], .word').length; if (split > 30) out.splitText = split;
    if (window.THREE || document.querySelector('canvas')) out.canvas = document.querySelectorAll('canvas').length;
    if (window.lottie || window.bodymovin || document.querySelector('lottie-player, dotlottie-player, [data-animation-type="lottie"]')) out.lottie = true;
    if (document.querySelector('video[autoplay]')) out.autoplayVideo = document.querySelectorAll('video[autoplay]').length;
    return out;
  }

  window.__susanooMotion = { scrollTo, nav, tag, snapshot, page, hoverTargets, hoverState, menuToggle, openedOverlay, libs };
  return true;
})()
