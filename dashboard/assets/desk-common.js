/* Shared with dashboard/index.html through the same localStorage keys, so a theme
 * chosen on the main page is the theme these pages open in. index.html is not
 * modified; it already writes desk-theme and desk-motion. */
window.Desk = (function () {
  const store = {
    get(k, d) { try { const v = localStorage.getItem("desk-" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("desk-" + k, JSON.stringify(v)); } catch (e) {} },
  };

  const THEMES = [["night", "Night"], ["paper", "Paper"], ["slate", "Slate"],
                  ["forest", "Forest"], ["bone", "Bone"], ["amber", "Amber"]];

  function applyChrome() {
    document.documentElement.dataset.theme = store.get("theme", "night");
    document.documentElement.dataset.motion = store.get("motion", "on");
  }
  applyChrome();

  const esc = (s) => String(s == null ? "" : s)
    .replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const has = (v) => v !== null && v !== undefined && !(typeof v === "number" && !isFinite(v));

  function num(v, dec = 2) {
    if (!has(v)) return "n/a";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function pct(v, dec = 1, sign = false) {
    if (!has(v)) return "n/a";
    return ((sign && v > 0) ? "+" : "") + (v * 100).toFixed(dec) + "%";
  }
  function pts(v, dec = 1, sign = true) {
    if (!has(v)) return "n/a";
    return ((sign && v > 0) ? "+" : "") + (v * 100).toFixed(dec) + " pts";
  }
  function mult(v, dec = 1) { return has(v) ? Number(v).toFixed(dec) + "x" : "n/a"; }
  function money(v, dec = 2) { return has(v) ? "$" + num(v, dec) : "n/a"; }
  function bn(v, dec = 2) { return has(v) ? (v < 0 ? "-$" : "$") + num(Math.abs(v), dec) + "bn" : "n/a"; }
  function cap(v) {
    if (!has(v)) return "n/a";
    if (v >= 1e12) return "$" + (v / 1e12).toFixed(2) + "T";
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(0) + "M";
    return "$" + num(v, 0);
  }
  function count(v, dec = 0) { return has(v) ? num(v, dec) : "n/a"; }

  /* Format by the unit a Trend declares, so a rate never gets a dollar sign and a
   * share count never gets a percent. */
  function byUnit(v, unit, dec) {
    if (!has(v)) return "n/a";
    switch (unit) {
      case "percent": return pct(v, dec == null ? 1 : dec);
      case "currency_bn": return bn(v, dec == null ? 2 : dec);
      case "count_m": return count(v, dec == null ? 0 : dec) + "m";
      case "x": return mult(v, dec == null ? 1 : dec);
      default: return num(v, dec == null ? 2 : dec);
    }
  }

  /* `depth` is the relative path back to the dashboard root: "../" from
   * /analyze/ and /positioning/, "../../" from /analyze/TICKER/. Every link has
   * to be built from it. Six of these were hard-coded to "../../" while the
   * brand and the two new tabs used `depth`, so on /analyze/ and /positioning/
   * the front-page anchors pointed one directory above the site root and 404'd. */
  function chrome(active, depth) {
    const home = depth + "index.html#";
    const nav = [[home + "overview", "Overview"], [home + "picks", "Picks"],
                 [home + "rankings", "Rankings"], [home + "holdings", "Holdings"],
                 [home + "charts", "Charts"], [depth + "analyze/", "Analyse"],
                 [depth + "positioning/", "Positioning"], [home + "settings", "Settings"]];
    return `<header class="reveal" style="--i:0">
      <a class="brand" href="${depth}index.html">Desk</a>
      <nav>${nav.map(([h, l]) => `<a href="${h}"${l === active ? ' class="on"' : ""}>${l}</a>`).join("")}</nav>
      <div class="status" id="status"></div>
    </header>`;
  }

  function themeBar() {
    const cur = store.get("theme", "night");
    return `<div class="seg-btns">${THEMES.map(([k, l]) =>
      `<button data-theme-set="${k}"${k === cur ? ' class="on"' : ""}>${l}</button>`).join("")}</div>`;
  }

  function wireTheme(root) {
    (root || document).querySelectorAll("[data-theme-set]").forEach((b) => {
      b.addEventListener("click", () => {
        store.set("theme", b.dataset.themeSet);
        applyChrome();
        (root || document).querySelectorAll("[data-theme-set]").forEach((x) =>
          x.classList.toggle("on", x === b));
      });
    });
  }

  /* A rail: one number placed inside the range that gives it meaning.
   * marks: [{value, kind: "now"|"kill"|"median"|"", label}]
   * band:  {lo, hi} drawn as the zone behind the marks.
   * Labels sit in a legend beneath rather than floating over the track: two marks
   * a few percent apart would otherwise overprint each other, and on this page the
   * two closest marks are usually today's price and the price that says you were
   * wrong, which is the pair you least want illegible. */
  function rail(lo, hi, marks, band, caption, fmt) {
    if (!has(lo) || !has(hi) || hi <= lo) return "";
    const f = fmt || ((v) => num(v, 2));
    const at = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    const live = (marks || []).filter((m) => has(m.value));
    let inner = "";
    if (band && has(band.lo) && has(band.hi)) {
      inner += `<div class="band" style="left:${at(band.lo)}%;right:${100 - at(band.hi)}%"></div>`;
    }
    live.forEach((m) => {
      inner += `<div class="tick ${m.kind || ""}" style="left:${at(m.value)}%"></div>`;
    });
    const legend = live.map((m) =>
      `<span class="key ${m.kind || ""}"><i></i>${esc(m.label || "")} <b>${esc(f(m.value))}</b></span>`
    ).join("");
    return `<div class="rail"><div class="track">${inner}</div>
      <div class="ends"><span>${esc(f(lo))}</span><span>${esc(f(hi))}</span></div>
      ${legend ? `<div class="keys">${legend}</div>` : ""}
      ${caption ? `<div class="cap">${esc(caption)}</div>` : ""}</div>`;
  }

  /* A sparkline over four fiscal-year points. Bars, not a line: a line implies
   * continuity between annual statements that is not there.
   *
   * Currency series are anchored at zero, because the magnitude is the story and a
   * floating baseline would make a 3% move look like a doubling. Counts and rates
   * are drawn against a padded min-to-max range instead, because a share count
   * going 1,402 to 1,320 is a 6% fall that is invisible on a zero-anchored bar and
   * is the entire point of showing it. The chosen baseline is printed, so nobody
   * has to guess which one they are looking at. */
  function spark(values, labels, opts) {
    const o = opts || {};
    const vals = values.map((v) => (has(v) ? Number(v) : null));
    const real = vals.filter((v) => v !== null);
    if (real.length < 2) return "";
    const w = o.width || 190, h = o.height || 30;
    const zeroAnchored = o.zero !== false;
    let lo, hi;
    if (zeroAnchored) {
      lo = Math.min(0, ...real);
      hi = Math.max(0, ...real);
    } else {
      const mn = Math.min(...real), mx = Math.max(...real);
      const pad = (mx - mn) * 0.35 || Math.abs(mx || 1) * 0.05;
      lo = mn - pad; hi = mx + pad;
    }
    const span = hi - lo || 1;
    const bw = w / vals.length;
    const zeroY = h - ((zeroAnchored ? 0 : lo) - lo) / span * h;
    let bars = "";
    vals.forEach((v, i) => {
      if (v === null) return;
      const y = h - ((v - lo) / span) * h;
      const top = Math.min(y, zeroY), bh = Math.max(1.5, Math.abs(zeroY - y));
      bars += `<rect class="barfill${i === vals.length - 1 ? " last" : ""}" x="${(i * bw + bw * 0.18).toFixed(1)}" y="${top.toFixed(1)}" width="${(bw * 0.64).toFixed(1)}" height="${bh.toFixed(1)}"></rect>`;
    });
    const base = (!zeroAnchored || lo < 0)
      ? `<line class="axis" x1="0" y1="${zeroY.toFixed(1)}" x2="${w}" y2="${zeroY.toFixed(1)}"></line>` : "";
    const alt = (labels || []).map((l, i) => l + " " + (vals[i] == null ? "n/a" : vals[i])).join(", ");
    return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(alt)}"
      preserveAspectRatio="none">${base}${bars}</svg>`;
  }

  /* The four values as text under the bars. A four-point chart cannot be read
   * precisely, so the numbers go next to it rather than into a tooltip nobody
   * on a phone will ever open. */
  function sparkValues(values, labels, unit) {
    return `<div class="vals">${values.map((v, i) =>
      `<span><em>${esc((labels[i] || "").replace(/^FY/, "'").replace(/^'(\d\d)(\d\d)$/, "'$2"))}</em>${esc(byUnit(v, unit))}</span>`
    ).join("")}</div>`;
  }

  function fail(message, detail) {
    document.body.innerHTML = `<div class="page"><section><h2>${esc(message)}</h2><div class="rule"></div>
      <div class="empty">${esc(detail || "")}</div></section></div>`;
  }

  return { store, esc, has, num, pct, pts, mult, money, bn, cap, count, byUnit,
           chrome, themeBar, wireTheme, rail, spark, sparkValues, fail, THEMES, applyChrome };
})();
