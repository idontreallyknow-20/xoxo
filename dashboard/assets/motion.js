/* Desk motion layer. Hand-written, no library, no CDN: the pages are static
 * files off a plain file server and stay that way.
 *
 * Three things, all decorative, all off under prefers-reduced-motion and under
 * the Motion: off setting (localStorage desk-motion, shared with every page):
 *
 * 1. A depth field. One canvas behind the page holds a slow cloud of points in
 *    three dimensions, projected with a real perspective divide, drifting past
 *    the viewer and parallaxing with the pointer and the scroll. Points near
 *    each other are joined by faint lines, so the field reads as structure
 *    rather than snow. Colour is the theme's --ink at low alpha, re-read
 *    whenever the theme changes.
 * 2. Tilt. Any element matching TILT_SELECTOR rotates toward the pointer in
 *    three dimensions and carries a light that follows it. Done by setting CSS
 *    variables the stylesheet reads, so the maths is here and the look is there.
 * 3. Lift and count. Elements with .lift rise into place when they scroll into
 *    view; elements with data-count tween from zero to their number once.
 *
 * Dynamically rendered pages are covered by a MutationObserver, so a script
 * that builds its DOM after load gets the same treatment without calling in.
 */
window.DeskMotion = (function () {
  const reduced = () => {
    try {
      if (matchMedia("(prefers-reduced-motion: reduce)").matches) return true;
      const v = localStorage.getItem("desk-motion");
      if (v != null && JSON.parse(v) === "off") return true;
    } catch (e) {}
    return document.documentElement.dataset.motion === "off";
  };
  const enabled = () => !reduced();

  const TILT_SELECTOR = ".tilt, .card, .cand, .h-pick, .scan .cell, .callout";
  const MAX_TILT = 6;   // degrees

  /* ---- depth field ------------------------------------------------------- */
  let canvas = null, ctx = null, pts = [], raf = 0, W = 0, H = 0, dpr = 1, ink = "241,239,230";
  let px = 0, py = 0, tx = 0, ty = 0, scrollY = 0, t0 = 0, running = false;
  const N = 150;   // one point per name in the quality 150, as it happens

  function readInk() {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      const h = m[1];
      ink = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)).join(",");
    }
  }

  function seed() {
    pts = [];
    for (let i = 0; i < N; i++) {
      pts.push({ x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 2, z: Math.random(),
                 vz: 0.010 + Math.random() * 0.018, r: 0.6 + Math.random() * 1.4 });
    }
  }

  function size() {
    dpr = Math.min(1.5, window.devicePixelRatio || 1);
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function frame(ts) {
    if (!running) return;
    const dt = Math.min(0.05, (ts - (t0 || ts)) / 1000); t0 = ts;
    ctx.clearRect(0, 0, W, H);
    // pointer parallax eases toward the target
    px += (tx - px) * 0.04; py += (ty - py) * 0.04;
    const f = 0.9 * Math.min(W, H);      // focal length
    const cx = W / 2 + px * 40, cy = H / 2 + py * 40 - scrollY * 0.06;
    const proj = [];
    for (const p of pts) {
      p.z -= p.vz * dt;
      if (p.z <= 0.05) { p.z = 1; p.x = (Math.random() - 0.5) * 2; p.y = (Math.random() - 0.5) * 2; }
      const s = f / (p.z * f + 240);
      const X = cx + p.x * 900 * s, Y = cy + p.y * 600 * s;
      const a = (1 - p.z) * 0.55;
      proj.push({ X, Y, a, r: p.r * (0.4 + (1 - p.z) * 1.6) });
    }
    ctx.lineWidth = 1;
    for (let i = 0; i < proj.length; i++) {
      const a = proj[i];
      for (let j = i + 1; j < proj.length; j++) {
        const b = proj[j];
        const dx = a.X - b.X, dy = a.Y - b.Y, d2 = dx * dx + dy * dy;
        if (d2 < 130 * 130) {
          const k = (1 - Math.sqrt(d2) / 130) * Math.min(a.a, b.a) * 0.35;
          if (k > 0.01) { ctx.strokeStyle = `rgba(${ink},${k.toFixed(3)})`; ctx.beginPath(); ctx.moveTo(a.X, a.Y); ctx.lineTo(b.X, b.Y); ctx.stroke(); }
        }
      }
    }
    for (const p of proj) {
      if (p.X < -10 || p.X > W + 10 || p.Y < -10 || p.Y > H + 10) continue;
      ctx.fillStyle = `rgba(${ink},${p.a.toFixed(3)})`;
      ctx.beginPath(); ctx.arc(p.X, p.Y, p.r, 0, Math.PI * 2); ctx.fill();
    }
    raf = requestAnimationFrame(frame);
  }

  function startDepth() {
    if (canvas || !enabled()) return;
    canvas = document.createElement("canvas"); canvas.id = "depth"; canvas.setAttribute("aria-hidden", "true");
    document.body.prepend(canvas);
    ctx = canvas.getContext("2d");
    readInk(); seed(); size();
    running = true; t0 = 0; raf = requestAnimationFrame(frame);
    requestAnimationFrame(() => canvas.classList.add("on"));
    window.addEventListener("resize", size);
    window.addEventListener("pointermove", (e) => { tx = (e.clientX / W - 0.5) * 2; ty = (e.clientY / H - 0.5) * 2; }, { passive: true });
    window.addEventListener("scroll", () => { scrollY = window.scrollY || 0; }, { passive: true });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { running = false; cancelAnimationFrame(raf); }
      else if (canvas && enabled()) { running = true; t0 = 0; raf = requestAnimationFrame(frame); }
    });
  }

  function stopDepth() {
    running = false; cancelAnimationFrame(raf);
    if (canvas) { canvas.remove(); canvas = null; ctx = null; }
  }

  /* ---- tilt --------------------------------------------------------------- */
  function tiltMove(e) {
    if (!enabled()) return;
    const el = e.target.closest && e.target.closest(TILT_SELECTOR);
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width < 80 || r.height < 40) return;
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    el.classList.add("tilt", "live");
    el.style.setProperty("--ry", ((x - 0.5) * 2 * MAX_TILT).toFixed(2) + "deg");
    el.style.setProperty("--rx", ((0.5 - y) * 2 * MAX_TILT).toFixed(2) + "deg");
    el.style.setProperty("--px", (x * 100).toFixed(1) + "%");
    el.style.setProperty("--py", (y * 100).toFixed(1) + "%");
  }
  function tiltLeave(e) {
    const el = e.target.closest && e.target.closest(TILT_SELECTOR);
    if (!el) return;
    el.classList.remove("live");
    el.style.setProperty("--rx", "0deg"); el.style.setProperty("--ry", "0deg");
  }

  /* ---- lift and count ------------------------------------------------------ */
  let io = null;
  function watch(root) {
    (root || document).querySelectorAll(".lift:not(.in)").forEach((el) => {
      if (!enabled()) { el.classList.add("in"); return; }
      if (io) io.observe(el); else el.classList.add("in");
    });
    (root || document).querySelectorAll("[data-count]:not(.counted)").forEach(countUp);
  }
  function countUp(el) {
    el.classList.add("counted");
    const target = Number(el.dataset.count), dec = Number(el.dataset.dec || 0);
    const fmt = (v) => v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    if (!isFinite(target) || !enabled()) { el.textContent = fmt(target); return; }
    const t0 = performance.now(), dur = 1300;
    const step = (ts) => {
      const p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 5);
      el.textContent = fmt(target * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function boot() {
    if ("IntersectionObserver" in window) {
      io = new IntersectionObserver((entries) => {
        entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
    }
    watch();
    new MutationObserver((muts) => {
      let any = false;
      muts.forEach((m) => { if (m.addedNodes && m.addedNodes.length) any = true; });
      if (any) watch();
    }).observe(document.body, { childList: true, subtree: true });
    document.addEventListener("pointermove", tiltMove, { passive: true });
    document.addEventListener("pointerout", tiltLeave, { passive: true });
    startDepth();
    // theme or motion changed on the page: re-read the ink, or switch the field off
    new MutationObserver(() => {
      readInk();
      if (enabled()) startDepth(); else stopDepth();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-motion"] });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  return { enabled, reduced, refresh: () => watch(), restart: () => { stopDepth(); startDepth(); } };
})();
