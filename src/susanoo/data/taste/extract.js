(() => {
  const cs = (el) => (el ? getComputedStyle(el) : null);
  const pick = (el) => { const s = cs(el); if (!s) return null;
    return { font: s.fontFamily.split(',')[0].replace(/["']/g, '').trim(), size: s.fontSize, weight: s.fontWeight, lh: s.lineHeight, ls: s.letterSpacing, color: s.color, tt: s.textTransform }; };
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const first = (sel) => [...document.querySelectorAll(sel)].find(vis) || null;
  const texty = [...document.querySelectorAll('body *')].filter((el) => el.childElementCount === 0 && el.textContent.trim().length > 3 && vis(el));
  const biggest = texty.filter((el) => el.getBoundingClientRect().top < innerHeight).sort((a, b) => parseFloat(cs(b).fontSize) - parseFloat(cs(a).fontSize))[0];
  const h1 = first('h1') || biggest, h2 = first('h2'), p = first('main p, p') || texty.find((el) => parseFloat(cs(el).fontSize) < 20 && el.textContent.length > 40);
  const btn = first('a[class*="button"], button, a[class*="btn"], [role="button"]');
  const area = {};
  [...document.querySelectorAll('body, body *')].slice(0, 3000).forEach((el) => { const s = getComputedStyle(el); const r = el.getBoundingClientRect(); const a = Math.min(r.width * r.height, 2e6);
    if (a > 400 && s.backgroundColor && !s.backgroundColor.endsWith(', 0)') && s.backgroundColor !== 'transparent') area[s.backgroundColor] = (area[s.backgroundColor] || 0) + a; });
  const txt = {}; texty.slice(0, 800).forEach((el) => { const c = cs(el).color; txt[c] = (txt[c] || 0) + 1; });
  const fonts = new Set(); try { document.fonts.forEach((f) => f.status === 'loaded' && fonts.add(f.family.replace(/["']/g, ''))); } catch (e) {}
  const usedFonts = {}; texty.slice(0, 600).forEach((el) => { const f = cs(el).fontFamily.split(',')[0].replace(/["']/g, ''); usedFonts[f] = (usedFonts[f] || 0) + 1; });
  const sizes = {}; texty.slice(0, 600).forEach((el) => { const z = Math.round(parseFloat(cs(el).fontSize)); sizes[z] = (sizes[z] || 0) + 1; });
  const radii = {}; [...document.querySelectorAll('button, a, img, [class*="card"], [class*="Card"]')].slice(0, 400).forEach((el) => { const r = cs(el).borderRadius; if (r && r !== '0px') radii[r] = (radii[r] || 0) + 1; });
  const shadows = {}; [...document.querySelectorAll('body *')].slice(0, 2000).forEach((el) => { const sh = cs(el).boxShadow; if (sh && sh !== 'none') shadows[sh.slice(0, 60)] = (shadows[sh.slice(0, 60)] || 0) + 1; });
  const scripts = [...document.scripts].map((s) => s.src).filter(Boolean).join(' ');
  const html = document.documentElement.outerHTML.slice(0, 400000);
  const has = (re) => re.test(scripts) || re.test(html);
  const b = btn ? cs(btn) : null;
  let anims = 0; try { anims = document.getAnimations().length; } catch (e) {}
  return { url: location.href, title: document.title.slice(0, 80),
    builder: document.documentElement.dataset.wfSite ? 'webflow' : (document.querySelector('[data-framer-name],[data-framer-component-type]') ? 'framer' : (window.Shopify ? 'shopify' : (window.__NEXT_DATA__ || document.querySelector('#__next') ? 'next' : 'other'))),
    fontsLoaded: [...fonts].slice(0, 8), fontsUsed: Object.entries(usedFonts).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([f, n]) => f + ':' + n),
    sizeScale: Object.entries(sizes).sort((x, y) => y[1] - x[1]).slice(0, 8).map(([z]) => +z).sort((x, y) => x - y),
    h1: pick(h1), h2: pick(h2), body: pick(p), h1text: h1 ? h1.textContent.trim().replace(/\s+/g, ' ').slice(0, 120) : null,
    h2texts: [...document.querySelectorAll('h2')].filter(vis).slice(0, 6).map((e) => e.textContent.trim().replace(/\s+/g, ' ').slice(0, 70)),
    button: b ? { bg: b.backgroundColor, color: b.color, radius: b.borderRadius, pad: b.padding, font: b.fontFamily.split(',')[0], tt: b.textTransform, text: btn.textContent.trim().slice(0, 30) } : null,
    bgByArea: Object.entries(area).sort((x, y) => y[1] - x[1]).slice(0, 6).map(([c]) => c), textColors: Object.entries(txt).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([c]) => c),
    radii: Object.entries(radii).sort((x, y) => y[1] - x[1]).slice(0, 4).map(([r]) => r), shadows: Object.keys(shadows).slice(0, 3),
    pageHeightScreens: +(document.documentElement.scrollHeight / innerHeight).toFixed(1), sections: document.querySelectorAll('section').length,
    images: document.images.length, videos: document.querySelectorAll('video').length, canvases: document.querySelectorAll('canvas').length, svgs: document.querySelectorAll('svg').length, runningAnimations: anims,
    motion: { three: !!window.THREE || has(/three(\.module)?(\.min)?\.js|three@|THREE\./i), gsap: !!window.gsap || has(/gsap/i), scrollTrigger: !!window.ScrollTrigger || has(/ScrollTrigger/), lenis: has(/lenis/i),
      spline: !!document.querySelector('spline-viewer') || has(/spline/i), lottie: has(/lottie/i), rive: has(/rive\.?(app|wasm|canvas)/i), framerMotion: has(/framer-motion|motion\.dev/i), webgl: document.querySelectorAll('canvas').length > 0 } };
})()
