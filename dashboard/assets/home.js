/* The front page. Renders from data.js (window.DASH, written by the original
 * build_dashboard.py, untouched) plus the analysis layer's JSON: watch.json for
 * the daily scan and tracker.json for the journal. The journal tab reads the
 * tracker rather than data.js because build_dashboard.py folds a heading that
 * names five tickers into one entry (NOTES.md 5d); the tracker parses it right.
 *
 * No market-data call leaves this page. The old page pasted a Finnhub key into
 * localStorage and called the API from the browser; that is gone. Prices are
 * whatever the server last wrote. */
(function () {
  const Desk = window.Desk, esc = Desk.esc, has = Desk.has;
  let D = window.DASH || {};
  const $ = (id) => document.getElementById(id);
  const money = (v, dec = 0) => v == null || isNaN(v) ? "n/a" : Number(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const pct = (v, dec = 1, sign = false) => v == null || isNaN(v) ? "n/a" : ((sign && v > 0) ? "+" : "") + (v * 100).toFixed(dec) + "%";
  const cls = (v) => v == null ? "muted" : v > 0 ? "up" : v < 0 ? "down" : "";
  const bname = { "SPY": "SPY, S&P 500", "QQQ": "QQQ, Nasdaq 100", "VFV.TO": "VFV, S&P 500 in CAD" };
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let colors = {};
  const readColors = () => { colors = { portfolio: cssVar("--ink"), "SPY": cssVar("--spy"), "QQQ": cssVar("--qqq"), "VFV.TO": cssVar("--vfv") }; };
  let firstRender = true;
  const reduced = () => window.DeskMotion ? window.DeskMotion.reduced() : false;

  const store = Desk.store;
  const S = { theme: store.get("theme", "night"), ccy: store.get("ccy", "USD"), motion: store.get("motion", "on"), buys: store.get("buys", []) };
  document.documentElement.dataset.theme = S.theme;
  document.documentElement.dataset.motion = S.motion;
  const fxTo = () => S.ccy === "CAD" ? (D.usdcad || 1.37) : 1;
  const cur = (v, dec = 0) => money(v == null ? null : v * fxTo(), dec);

  // the scan's closes, when a scan has run: ticker -> {close, date}
  let W = null, T = null, SU = null;
  const scanned = () => W && W.status === "SCANNED";
  const priceOf = (tk, fallback) => {
    if (scanned()) { const r = (W.names || []).find((n) => n.ticker === tk); if (r && r.price && has(r.price.last_close)) return r.price.last_close; }
    return fallback;
  };

  function holdingsAll() {
    const base = (D.holdings || []).map((h) => ({ ...h, local: false }));
    const fx = D.usdcad || 1.37;
    S.buys.forEach((b) => {
      const p = priceOf(b.ticker, null), conv = b.currency === "CAD" ? 1 / fx : 1;
      base.push({ ticker: b.ticker, name: b.name || "", sector: b.sector || "", shares: b.shares, cost_per_share: b.price, price: p, currency: b.currency,
        value_usd: p != null ? p * b.shares * conv : null, cost_usd: b.price * b.shares * conv, pl_pct: p != null ? p / b.price - 1 : null, date_bought: b.date, local: true });
    });
    base.forEach((h) => { const p = priceOf(h.ticker, h.price); if (p != null && h.shares) { const conv = h.currency === "CAD" ? 1 / fx : 1; h.price = p; h.value_usd = p * h.shares * conv; h.pl_pct = h.cost_per_share ? p / h.cost_per_share - 1 : null; } });
    return base;
  }
  function totals() {
    const H = holdingsAll();
    const localCost = S.buys.reduce((a, b) => a + b.price * b.shares * (b.currency === "CAD" ? 1 / (D.usdcad || 1.37) : 1), 0);
    const cash = (D.cash || 0) - localCost, inv = H.reduce((a, h) => a + (h.value_usd || h.cost_usd || 0), 0);
    return { H, cash, inv, total: cash + inv };
  }

  // views
  const views = ["overview", "picks", "rankings", "holdings", "charts", "journal", "setups", "settings"];
  function show(v) {
    if (!views.includes(v)) v = "overview";
    views.forEach((k) => $("v-" + k).classList.toggle("on", k === v));
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.v === v));
    if (v === "charts") renderCharts();
    if (window.DeskMotion) window.DeskMotion.refresh();
    window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));

  function status(extra) {
    $("status").innerHTML = `${extra ? `<b>${esc(extra)}</b> &nbsp; ` : ""}snapshot ${esc(D.built_at || D.as_of || "")}`;
    $("foot-built").textContent = D.built_at ? `built ${D.built_at}` : "";
  }

  // hero
  function renderHero() {
    const t = totals(), el = $("big"), target = t.total * fxTo();
    el.dataset.count = target.toFixed(0);
    el.classList.remove("counted");
    if (reduced() || !firstRender) { el.classList.add("counted"); el.textContent = money(target); }
    else if (window.DeskMotion) window.DeskMotion.refresh();
    $("eyebrow").textContent = `${S.ccy} · since ${D.inception || "inception"}`;
    const pr = (D.series && D.series.portfolio) ? D.series.portfolio : [];
    const portRet = pr.length > 1 ? t.total / pr[0] - 1 : 0;
    $("kv").innerHTML = `<span>cash <b>${cur(t.cash)}</b></span><span>invested <b>${cur(t.inv)}</b></span><span>positions <b>${t.H.length}</b></span>`;
    let s = `<div class="head"><span>since ${esc(D.inception || "")}</span><span>index</span><span>you vs it</span></div>`;
    s += `<div class="h-stat"><span class="k">Your return</span><span class="v"></span><span class="d ${cls(portRet)}">${pct(portRet, 2, true)}</span></div>`;
    (D.benchmarks || []).forEach((b) => {
      const sc = (D.scorecard || {})[b], r = sc ? sc.return : null, diff = (sc && r != null) ? portRet - r : null;
      s += `<div class="h-stat"><span class="k"><span class="sw" style="background:${colors[b]}"></span>${esc(bname[b] || b)}</span><span class="v ${cls(r)}">${pct(r, 2, true)}</span><span class="d ${cls(diff)}">${diff == null ? "n/a" : (diff >= 0 ? "+" : "") + (diff * 100).toFixed(2) + " pts"}</span></div>`;
    });
    const days = (D.dates || []).length;
    s += `<p class="muted" style="font-size:12px;margin:10px 0 0;max-width:40ch">${days < 2 ? `Day ${days}. There is no track record yet; a verdict on the process needs a year of monthly snapshots (scripts/an/power.py).` : `${days} days tracked. Not a verdict on the process until there are far more of them.`}</p>`;
    $("stats").innerHTML = s;
  }

  // the scan panel
  function renderScan() {
    const host = $("scan"), sub = $("scan-sub");
    if (!W) { host.innerHTML = `<div class="empty">No scan file. python scripts/scan.py writes one.</div>`; sub.textContent = ""; return; }
    if (!scanned()) {
      sub.textContent = W.status;
      host.innerHTML = `<div class="scan"><div class="cell"><div class="k">status</div><div class="v">${esc(W.status)}</div><div class="d">${esc((W.why_not_run || [""])[0])}</div></div>
        <div class="cell"><div class="k">watched</div><div class="v">${W.n_watched}</div><div class="d">${esc((W.watched || []).map((n) => n.ticker).join(" "))}</div></div>
        <div class="cell"><div class="k">to run it</div><div class="v">daily</div><div class="d">setup.ps1 -InstallTask schedules the scan and the email on Windows; until then nothing here has looked at a price.</div></div></div>`;
      return;
    }
    sub.textContent = `closes as of ${W.as_of}`;
    const rules = W.n_rule_alerts || 0, lines = (W.alerts || []).length;
    const b = W.benchmarks || {};
    const bench = Object.keys(b).map((k) => `${k} <span class="${cls(b[k].chg_1d)}">${pct(b[k].chg_1d, 1, true)}</span>`).join(" &nbsp; ");
    let h = `<div class="scan">
      <div class="cell ${rules ? "hot" : ""}"><div class="k">rules fired</div><div class="v">${rules}</div><div class="d">${rules ? "a level you wrote down was crossed" : "nothing crossed a written rule"}</div></div>
      <div class="cell"><div class="k">lines</div><div class="v">${lines}</div><div class="d">moves, filings and stakes worth a glance</div></div>
      <div class="cell"><div class="k">watched</div><div class="v">${W.n_watched}</div><div class="d">journal names plus holdings</div></div>
      <div class="cell"><div class="k">benchmarks, one day</div><div class="v mono" style="font-size:20px">${bench || "n/a"}</div><div class="d">${esc(((W.sources || {}).prices || {}).detail || "")}</div></div>
    </div>`;
    if (lines) {
      h += `<div class="alerts">` + (W.alerts || []).slice(0, 12).map((a) => `<div class="alert s${a.severity}"><div class="t">${esc(a.ticker)}<small>${esc(a.kind.replace(/_/g, " "))}</small></div><div><div class="x">${esc(a.text)}</div><div class="r">${esc(a.rule)} · ${esc(a.source)} · ${esc(a.as_of || "")}</div></div></div>`).join("") + `</div>`;
    }
    host.innerHTML = h;
  }

  // generic line chart. series: {key: [values]}
  function lineChart(el, dates, series, opts = {}) {
    const keys = Object.keys(series).filter((k) => Array.isArray(series[k]) && series[k].length === dates.length && series[k].some((v) => v != null));
    if (dates.length < 2 || !keys.length) { el.innerHTML = `<div class="empty">${dates.length < 2 ? `Day ${dates.length}. Lines appear once there are two closes.` : "No data yet."}</div>`; return; }
    const fmt = opts.fmt || ((v) => money(v / 1000, 1) + "k");
    const W0 = Math.max(600, el.clientWidth || 1100), H = opts.height || 320, m = { t: 14, r: opts.endLabels === false ? 20 : 118, b: 30, l: 62 };
    const vals = keys.flatMap((k) => series[k]).filter((v) => v != null);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (opts.band) { lo = Math.min(lo, opts.band[0]); hi = Math.max(hi, opts.band[1]); }
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.02 || 1; lo -= pad; hi += pad;
    const x = (i) => m.l + (i / (dates.length - 1)) * (W0 - m.l - m.r), y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
    let g = "";
    if (opts.band) g += `<rect class="band" x="${m.l}" width="${W0 - m.l - m.r}" y="${y(opts.band[1])}" height="${y(opts.band[0]) - y(opts.band[1])}"/>`;
    for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4, yy = y(v).toFixed(1); g += `<line class="grid" x1="${m.l}" x2="${W0 - m.r}" y1="${yy}" y2="${yy}"/><text x="${m.l - 10}" y="${+yy + 4}" text-anchor="end">${fmt(v)}</text>`; }
    [...new Set([0, Math.floor((dates.length - 1) / 2), dates.length - 1])].forEach((i) => g += `<text x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"}">${dates[i]}</text>`);
    const main = opts.main, order = keys.filter((k) => k !== main).concat(keys.includes(main) ? [main] : []);
    const ly = {}, sorted = order.map((k) => ({ k, y: y(series[k][series[k].length - 1]) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < sorted.length; i++) if (sorted[i].y - sorted[i - 1].y < 14) sorted[i].y = sorted[i - 1].y + 14;
    for (let i = sorted.length - 2; i >= 0; i--) if (sorted[i + 1].y - sorted[i].y < 14) sorted[i].y = sorted[i + 1].y - 14;
    sorted.forEach((o) => ly[o.k] = o.y);
    let paths = "", labels = "", dots = "";
    order.forEach((k) => {
      const d = series[k].map((v, i) => v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean), last = series[k][series[k].length - 1];
      const col = (opts.colors || colors)[k] || colors.portfolio;
      paths += `<path class="line ${k === main ? "main" : ""}" d="${d.map((p, i) => (i ? "L" : "M") + p).join(" ")}" stroke="${col}" data-k="${k}"/>`;
      if (opts.endLabels !== false) labels += `<text class="endlab ${k === main ? "main" : ""}" x="${W0 - m.r + 8}" y="${ly[k] + 4}">${esc((opts.labels || {})[k] || k)} ${fmt(last)}</text>`;
      dots += `<circle class="dot h" r="4" stroke="${col}" data-k="${k}"/>`;
    });
    el.innerHTML = `<div class="chart-wrap"><svg width="100%" viewBox="0 0 ${W0} ${H}" preserveAspectRatio="none" style="display:block;overflow:visible">${g}<line class="axis" x1="${m.l}" x2="${W0 - m.r}" y1="${H - m.b}" y2="${H - m.b}"/>${paths}<line class="xhair" y1="${m.t}" y2="${H - m.b}"/>${dots}${labels}<rect class="hit" x="${m.l}" y="0" width="${W0 - m.l - m.r}" height="${H}" fill="transparent"/></svg><div class="tip"></div></div>`;
    const svg = el.querySelector("svg");
    if (opts.animate !== false && firstRender && !reduced()) svg.querySelectorAll("path.line").forEach((p, i) => { const L = p.getTotalLength(); p.style.strokeDasharray = L; p.style.strokeDashoffset = L; p.style.transition = `stroke-dashoffset 1200ms ${i * 90}ms cubic-bezier(0.23, 1, 0.32, 1)`; requestAnimationFrame(() => requestAnimationFrame(() => p.style.strokeDashoffset = 0)); });
    const hit = svg.querySelector(".hit"), xh = svg.querySelector(".xhair"), tip = el.querySelector(".tip"), dotEls = [...svg.querySelectorAll(".dot")];
    hit.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * (W0 / r.width);
      const i = Math.max(0, Math.min(dates.length - 1, Math.round((sx - m.l) / (W0 - m.l - m.r) * (dates.length - 1))));
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.style.opacity = 1;
      let rows = `<div class="row"><span>${dates[i]}</span></div>`;
      dotEls.forEach((dt) => { const v = series[dt.dataset.k][i]; if (v == null) { dt.style.opacity = 0; return; } dt.setAttribute("cx", x(i)); dt.setAttribute("cy", y(v)); dt.style.opacity = 1; });
      order.slice().reverse().forEach((k) => { const v = series[k][i]; if (v == null) return; rows += `<div class="row"><span><span class="sw" style="background:${(opts.colors || colors)[k] || colors.portfolio}"></span>${esc((opts.labels || {})[k] || k)}</span><span>${opts.tipFmt ? opts.tipFmt(v) : money(v)}</span></div>`; });
      tip.innerHTML = rows; tip.classList.add("on");
      const left = (x(i) / W0) * r.width; tip.style.left = (left > r.width * 0.6 ? left - tip.offsetWidth - 14 : left + 14) + "px"; tip.style.top = Math.max(0, (ev.clientY - r.top) - 20) + "px";
    });
    hit.addEventListener("mouseleave", () => { xh.style.opacity = 0; dotEls.forEach((d) => d.style.opacity = 0); tip.classList.remove("on"); });
  }

  function renderIndexChart(host, subId) {
    const dates = D.dates || [];
    $(subId).textContent = dates.length ? `${dates[0]} to ${dates[dates.length - 1]}, both start at $100,000` : "";
    const legend = ["portfolio"].concat(D.benchmarks || []).map((k) => `<span><span class="sw ${k === "portfolio" ? "line" : ""}" style="background:${colors[k]}"></span>${k === "portfolio" ? "you" : esc(bname[k] || k)}</span>`).join("");
    host.innerHTML = `<div class="legend">${legend}</div><div class="c"></div>`;
    lineChart(host.querySelector(".c"), dates, D.series || {}, { main: "portfolio", labels: { portfolio: "you", SPY: "SPY", QQQ: "QQQ", "VFV.TO": "VFV" } });
  }

  // picks
  function renderPicks() {
    const P = D.picks || [], tot = P.reduce((a, p) => a + (p.usd || 0), 0);
    $("picks-sub").textContent = P.length ? `${P.length} names, ${cur(tot)} if you took them all · logged ${P[0] && P[0].date ? P[0].date : ""}` : "";
    if (!P.length) { $("picks").innerHTML = `<div class="empty">${esc(D.picks_status || "Picks land here once the research is done.")}</div>`; return; }
    let h = "";
    P.forEach((p, i) => {
      const price = priceOf(p.ticker, p.price), inZone = price != null && price >= p.low && price <= p.high, shares = price ? Math.floor(p.usd / price) : p.shares;
      const span = Math.max(p.high * 1.3, (price || 0) * 1.12), lo0 = Math.min(p.low * 0.75, (price || p.low) * 0.88);
      const f = (v) => ((v - lo0) / (span - lo0) * 100).toFixed(1) + "%";
      const conv = `<span class="conv" title="conviction ${p.conviction || 0} of 5">${[1, 2, 3, 4, 5].map((n) => `<i class="${n <= (p.conviction || 0) ? "on" : ""}"></i>`).join("")}</span>`;
      const st = price == null ? "" : inZone ? `<span class="up">in the buy zone</span>` : price < p.low ? `<span class="down">below the zone, check the thesis</span>` : `<span class="muted">above the zone, wait</span>`;
      h += `<div class="h-pick lift" style="--i:${Math.min(i, 8)}"><div class="n">${i + 1}</div>
        <div><a href="analyze/${encodeURIComponent(p.ticker)}/" class="tk" style="text-decoration:none">${esc(p.ticker)}</a>${conv}<span class="nm">${esc(p.name || "")}</span><div class="act"><b>${esc(p.action || "")}</b>${p.bucket ? ", " + esc(p.bucket) : ""}</div></div>
        <div class="amt">${cur(p.usd)}<small>${shares ? money(shares) + " shares at about " + money(price, 2) : ""}</small><small>${st}</small></div>
        <div class="zonecol"><div class="h-track"><div class="band" style="left:${f(p.low)};width:${((p.high - p.low) / (span - lo0) * 100).toFixed(1)}%"></div>${price == null ? "" : `<div class="px ${inZone ? "in" : ""}" style="left:${f(price)}"></div>`}</div><div class="z"><span>buy ${money(p.low, 2)} to ${money(p.high, 2)}</span><span>${scanned() ? "close" : "at call"} ${money(price, 2)}</span></div></div>
        <div class="thesis">${esc(p.thesis || "")}${p.wrong_if ? ` <b>Wrong if</b> ${esc(p.wrong_if)}` : ""}</div></div>`;
    });
    $("picks").innerHTML = h;
  }

  // holdings
  function renderOwn() {
    const t = totals(), H = t.H, total = t.total;
    $("own-sub").textContent = `${cur(t.cash)} cash, ${cur(t.inv)} invested`;
    let segs = `<div class="seg cash" style="flex:${Math.max(t.cash, 0)}"></div>`, labs = `<span><span class="sw" style="background:var(--track)"></span>cash ${pct(total ? t.cash / total : 1, 0)}</span>`;
    H.forEach((h, i) => { segs += `<div class="seg pos" style="flex:${h.value_usd || h.cost_usd || 0};opacity:${1 - i * 0.07}"></div>`; labs += `<span><span class="sw" style="background:var(--ink);opacity:${1 - i * 0.07}"></span>${esc(h.ticker)} ${pct(total ? (h.value_usd || h.cost_usd || 0) / total : 0, 1)}</span>`; });
    $("alloc").innerHTML = `<div class="h-alloc">${segs}</div><div class="alloc-labels">${labs}</div>`;
    requestAnimationFrame(() => requestAnimationFrame(() => document.querySelectorAll(".h-alloc .seg").forEach((s, i) => { s.style.transitionDelay = (firstRender ? i * 60 : 0) + "ms"; s.style.transform = "scaleX(1)"; })));
    if (!H.length) $("holdings").innerHTML = `<div class="empty">No positions yet. Holdings are read from portfolio/holdings.csv on the server, which is never committed; buys logged here stay in this browser.</div>`;
    else {
      let tb = `<table style="margin-top:18px"><thead><tr><th>ticker</th><th>sector</th><th class="num">shares</th><th class="num">cost</th><th class="num">price</th><th class="num">value</th><th class="num">gain</th><th class="num">wrong if</th></tr></thead><tbody>`;
      H.forEach((h, i) => tb += `<tr class="row" style="animation-delay:${firstRender ? i * 30 : 0}ms"><td><span class="tk">${esc(h.ticker)}</span><span class="nm">${esc(h.name || "")}${h.local ? " (logged here)" : ""}</span></td><td class="muted">${esc(h.sector || "")}</td><td class="num">${money(h.shares)}</td><td class="num">${money(h.cost_per_share, 2)}</td><td class="num">${money(h.price, 2)}</td><td class="num">${cur(h.value_usd)}</td><td class="num ${cls(h.pl_pct)}">${pct(h.pl_pct, 1, true)}</td><td class="num muted">${h.wrong_if_price == null ? "" : money(h.wrong_if_price, 2)}</td></tr>`);
      $("holdings").innerHTML = tb + "</tbody></table>";
    }
    const bySec = {}; H.forEach((h) => { const k = h.sector || "unknown"; bySec[k] = (bySec[k] || 0) + (h.value_usd || h.cost_usd || 0); });
    const secs = Object.entries(bySec).sort((a, b) => b[1] - a[1]);
    $("sectors").innerHTML = secs.length ? secs.map(([k, v]) => `<div class="h-sector"><span>${esc(k)}</span><span class="h-bar w"><i data-w="${total ? v / total : 0}"></i></span><span class="num mono ${total && v / total > 0.30 ? "down" : ""}">${pct(total ? v / total : 0, 0)}</span></div>`).join("") + `<p class="muted" style="font-size:12px;margin:10px 0 0">Red means a sector is above the 30% ceiling in criteria.md.</p>` : `<div class="empty">Nothing to show until there are positions.</div>`;
    requestAnimationFrame(() => requestAnimationFrame(() => $("sectors").querySelectorAll(".h-bar i").forEach((b) => b.style.transform = `scaleX(${b.dataset.w})`)));
    $("localbuys").innerHTML = S.buys.length ? `<table style="margin-top:16px"><tbody>${S.buys.map((b, i) => `<tr class="row"><td class="mono">${esc(b.date)}</td><td><span class="tk">${esc(b.ticker)}</span></td><td class="num">${money(b.shares)} at ${money(b.price, 2)} ${esc(b.currency)}</td><td class="num"><button data-rm="${i}">remove</button></td></tr>`).join("")}</tbody></table>` : "";
    $("localbuys").querySelectorAll("button[data-rm]").forEach((b) => b.addEventListener("click", () => { S.buys.splice(+b.dataset.rm, 1); store.set("buys", S.buys); renderAll(); }));
  }
  $("buyform").addEventListener("submit", (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    const tk = String(f.get("ticker")).trim().toUpperCase(); if (!tk) return;
    S.buys.push({ ticker: tk, shares: +f.get("shares"), price: +f.get("price"), date: f.get("date"), currency: f.get("currency") });
    store.set("buys", S.buys); e.target.reset(); firstRender = false; renderAll();
  });

  // rankings
  let current = null, showAll = false, sortKey = null, sortDir = 1, query = "";
  $("q").addEventListener("input", (e) => { query = e.target.value.trim().toLowerCase(); showAll = false; renderScreen(false); });
  function renderScreen(fade) {
    const S0 = D.screen || [], comp = S0.filter((r) => r.bucket_compounder), cyc = S0.filter((r) => r.bucket_cyclical_turn);
    const tabs = [["compounders", `Compounders ${comp.length}`, comp], ["cyclical", `Cyclical turns ${cyc.length}`, cyc], ["all", `All ${S0.length}`, S0]];
    if (!current) current = comp.length ? "compounders" : "all";
    $("screen-sub").textContent = S0.length ? "quality out of 100 · click a row for the breakdown, a heading to sort" : "";
    $("tabs").innerHTML = tabs.map(([k, l]) => `<button data-k="${k}" class="${k === current ? "on" : ""}">${l}</button>`).join("");
    $("tabs").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; current = b.dataset.k; showAll = false; renderScreen(true); };
    let rows = tabs.find((t) => t[0] === current)[2];
    if (query) rows = rows.filter((r) => (r.ticker || "").toLowerCase().includes(query) || (r.name || "").toLowerCase().includes(query));
    if (sortKey) rows = rows.slice().sort((a, b) => { const va = a[sortKey], vb = b[sortKey]; if (va == null) return 1; if (vb == null) return -1; return (va > vb ? 1 : va < vb ? -1 : 0) * sortDir; });
    const shown = showAll ? rows : rows.slice(0, 40), wrap = $("screen");
    $("rank-count").textContent = rows.length ? `${shown.length} of ${rows.length}` : "";
    const cols = [["rank", "#", "num"], ["ticker", "company", ""], ["industry", "industry", ""], ["quality_score", "quality", ""], ["pe_vs_median", "price vs its usual", "num"], ["dd_ath", "off high", "num"], ["eps_fy1_chg_90d", "estimates 90d", "num"], ["short_pct_float", "short", "num"]];
    const fmtRaw = (r) => [["Return on capital", r.score_roic, r.roic_avg == null ? "n/a" : `ROIC ${pct(r.roic_avg, 0)} avg`], ["Cash generation", r.score_fcf, r.fcf_margin_avg == null ? "n/a" : `${pct(r.fcf_margin_avg, 0)} of revenue is free cash`], ["Growth", r.score_growth, r.rev_cagr == null ? "n/a" : `revenue ${pct(r.rev_cagr, 0, true)} a year`], ["Debt", r.score_balance, r.nd_to_ebitda == null ? "n/a" : r.nd_to_ebitda === 0 ? "net cash" : `${r.nd_to_ebitda.toFixed(1)}x net debt to EBITDA`], ["Share count", r.score_shares, r.share_change == null ? "n/a" : `${pct(r.share_change, 1, true)} over the window`], ["Margin steadiness", r.score_gm_stability, r.gm_avg == null ? "no gross margin line" : `gross margin ${pct(r.gm_avg, 0)}`]];
    const build = () => {
      if (!S0.length) { wrap.innerHTML = `<div class="empty">The screen has not been built. python scripts/build_dashboard.py writes data.js.</div>`; return; }
      if (!rows.length) { wrap.innerHTML = `<div class="empty">Nothing here${query ? " for that search" : ""}.</div>`; return; }
      let t = `<table><thead><tr>${cols.map(([k, l, c]) => `<th class="sort ${c} ${sortKey === k ? "on" : ""}" data-s="${k}">${l}${sortKey === k ? (sortDir > 0 ? " +" : " -") : ""}</th>`).join("")}</tr></thead><tbody>`;
      shown.forEach((r, i) => {
        const vs = r.pe_vs_median != null ? r.pe_vs_median : r.ev_vs_median, vsTxt = vs == null ? "n/a" : (vs <= 0 ? `${pct(-vs, 0)} cheaper` : `${pct(vs, 0)} dearer`), dl = firstRender ? Math.min(i, 20) * 25 : 0;
        t += `<tr class="row click" data-i="${i}" style="animation-delay:${dl}ms"><td class="mono muted">${r.rank ?? ""}</td><td><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name || "")}</span></td><td class="muted" style="font-size:13px">${esc(r.industry || r.sector || "")}</td><td><span class="h-bar"><i style="transition-delay:${dl + 150}ms" data-w="${(r.quality_score || 0) / 100}"></i></span><span class="mono">${r.quality_score == null ? "" : r.quality_score.toFixed(0)}</span></td><td class="num ${vs == null ? "muted" : ""}">${vsTxt}</td><td class="num muted">${pct(r.dd_ath, 0)}</td><td class="num ${cls(r.eps_fy1_chg_90d)}">${pct(r.eps_fy1_chg_90d, 1, true)}</td><td class="num muted">${r.short_pct_float == null ? "n/a" : pct(r.short_pct_float, 1)}</td></tr>`;
        t += `<tr class="h-detail" data-d="${i}"><td colspan="8"><div class="clip"><div class="inner"><div class="pad">${fmtRaw(r).map(([k, v, raw]) => `<div class="c"><div class="k"><span>${k}</span><b>${v == null ? "n/a" : Math.round(v)}</b></div><span class="h-bar sm"><i data-w="${(v || 0) / 100}"></i></span><span class="raw">${esc(raw)}</span></div>`).join("")}<p>${esc(r.name)} at ${money(r.price, 2)} ${esc(r.currency || "")}. Forward P/E ${r.forward_pe == null ? "n/a" : r.forward_pe.toFixed(1)} against its own median of ${r.median_pe_hist == null ? "n/a" : r.median_pe_hist.toFixed(1)}. ${r.dd_52w == null ? "" : pct(r.dd_52w, 0) + " from the 52 week high."} ${r.next_earnings ? "Next earnings " + esc(r.next_earnings) + "." : ""} ${r.bucket_compounder ? "Compounder." : ""} ${r.bucket_cyclical_turn ? "Cyclical turn, size smaller." : ""} <a href="analyze/${encodeURIComponent(r.ticker)}/">Open the analysis page.</a></p></div></div></div></td></tr>`;
      });
      t += "</tbody></table>";
      if (rows.length > 40 && !showAll) t += `<button class="more" id="more">show all ${rows.length}</button>`;
      wrap.innerHTML = t;
      requestAnimationFrame(() => requestAnimationFrame(() => wrap.querySelectorAll(".h-bar i").forEach((b) => b.style.transform = `scaleX(${b.dataset.w})`)));
      wrap.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => { const d = wrap.querySelector(`tr.h-detail[data-d="${tr.dataset.i}"]`), open = !d.classList.contains("open"); wrap.querySelectorAll(".open").forEach((x) => x.classList.remove("open")); d.classList.toggle("open", open); tr.classList.toggle("open", open); }));
      wrap.querySelectorAll("th.sort").forEach((th) => th.addEventListener("click", () => { const k = th.dataset.s; if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k === "rank" || k === "ticker" || k === "industry" ? 1 : -1; } firstRender = false; renderScreen(false); }));
      const more = wrap.querySelector("#more"); if (more) more.addEventListener("click", () => { showAll = true; firstRender = false; renderScreen(false); });
    };
    if (fade && !reduced()) { wrap.style.transition = "opacity 160ms ease"; wrap.style.opacity = 0; setTimeout(() => { build(); wrap.style.opacity = 1; }, 160); } else build();
  }

  // charts tab
  let chartTk = null, chartRange = 365;
  function renderCharts() {
    const Hh = D.history || {}, tks = Object.keys(Hh);
    if (!tks.length) { $("pricechart").innerHTML = `<div class="empty">History arrives with the next snapshot.</div>`; $("chart-tickers").innerHTML = ""; renderIndexChart($("chart2"), "chart-sub2"); return; }
    if (!chartTk || !Hh[chartTk]) chartTk = tks.find((t) => !(D.benchmarks || []).includes(t)) || tks[0];
    $("chart-tickers").innerHTML = tks.map((t) => `<button data-t="${t}" class="${t === chartTk ? "on" : ""}">${t.replace(".TO", "")}</button>`).join("");
    $("chart-tickers").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; chartTk = b.dataset.t; firstRender = false; renderCharts(); };
    $("chart-range").innerHTML = [[30, "1M"], [90, "3M"], [180, "6M"], [365, "1Y"]].map(([d, l]) => `<button data-r="${d}" class="${d === chartRange ? "on" : ""}">${l}</button>`).join("");
    $("chart-range").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; chartRange = +b.dataset.r; firstRender = false; renderCharts(); };
    const h = Hh[chartTk], n = Math.min(h.dates.length, chartRange), dates = h.dates.slice(-n), close = h.close.slice(-n);
    const pick = (D.picks || []).find((p) => p.ticker === chartTk), held = (D.holdings || []).find((x) => x.ticker === chartTk);
    const first = close[0], last = close[close.length - 1];
    $("pricechart").innerHTML = `<div class="legend"><span><span class="sw line" style="background:${colors.portfolio}"></span>${esc(chartTk)}</span><span class="mono">${money(last, 2)} &nbsp; <span class="${cls(last / first - 1)}">${pct(last / first - 1, 1, true)}</span> over ${n} days</span>${pick ? `<span class="muted">shaded band is the buy zone</span>` : ""}${held ? `<span class="muted">bought at ${money(held.cost_per_share, 2)}</span>` : ""}</div><div class="c"></div>`;
    lineChart($("pricechart").querySelector(".c"), dates, { [chartTk]: close }, { main: chartTk, fmt: (v) => money(v, 0), tipFmt: (v) => money(v, 2), endLabels: false, band: pick ? [pick.low, pick.high] : null, animate: firstRender });
    renderIndexChart($("chart2"), "chart-sub2");
  }

  // journal, from the tracker (sixteen names, one per call, however the heading was written)
  function renderJournal() {
    const host = $("journal"), sub = $("journal-sub");
    if (!T) { host.innerHTML = `<div class="empty">No tracker file. python scripts/track_calls.py writes one.</div>`; sub.textContent = ""; return; }
    const G = T.grades || [];
    sub.textContent = `${G.length} calls · ${T.status === "GRADED" ? `graded against prices as of ${T.as_of}` : "not graded against prices yet"}`;
    host.innerHTML = G.map((g) => `<div class="e"><div class="dt">${esc(g.date)}</div>
      <div><div class="h"><a href="analyze/${encodeURIComponent(g.ticker)}/" style="text-decoration:none">${esc(g.ticker)}</a><span>${esc(g.action || "")}</span></div>
        <div class="th">${g.wrong_if ? `<b>Wrong if</b> ${esc(g.wrong_if)}` : `<span class="muted">no falsifier written</span>`}</div>
        <div class="th muted" style="font-size:13px">${esc(g.verdict || "")}</div></div>
      <div class="side"><span>at call <b>${g.price_at_call == null ? "n/a" : money(g.price_at_call, 2)}</b></span><span>trigger <b>${g.trigger == null ? "none" : money(g.trigger, 2)}</b></span>${g.is_swing ? `<span>stop <b>${g.stop == null ? "none" : money(g.stop, 2)}</b></span><span>horizon <b>${g.horizon_days} sessions</b></span>` : ""}<span>score at call <b>${g.score_percentile_at_call == null ? "n/a" : "p" + Math.round(g.score_percentile_at_call)}</b></span>${g.ret == null ? "" : `<span>since <b class="${cls(g.ret)}">${pct(g.ret, 1, true)}</b></span>`}${g.is_swing && g.horizon_grades && g.horizon_grades.horizon ? `<span>at horizon <b class="${cls(g.horizon_grades.horizon.excess_vs_spy)}">${pct(g.horizon_grades.horizon.excess_vs_spy, 1, true)} vs SPY</b></span>` : ""}</div></div>`).join("") || `<div class="empty">No calls logged.</div>`;
  }

  // setups, from setups.json (swing.md). A setup is a pattern that matched, never a recommendation.
  function renderSetups() {
    const host = $("setups"), sub = $("setups-sub"), note = $("setups-note"), rules = $("setups-rules");
    if (!SU) { host.innerHTML = `<div class="empty">No setups file. python scripts/setups.py writes one.</div>`; sub.textContent = ""; note.textContent = ""; rules.innerHTML = ""; return; }
    const list = SU.setups || [];
    sub.textContent = SU.status === "SCANNED" ? `${list.length} matched as of ${SU.as_of} over ${money(SU.universe_n)} names` : SU.status;
    note.textContent = SU.status === "SCANNED"
      ? "Each row is a mechanical pattern that matched a rule in swing.md, with the entry, the stop and the horizon those rules dictate. Nothing here is a recommendation; the tracker grades what you log, at 5, 10 and 20 sessions against SPY, and swing.md allows no size change before thirty graded calls."
      : ((SU.why_not_run || [])[0] || "") + " " + ((SU.why_not_run || [])[1] || "");
    host.innerHTML = list.length ? list.map((x, i) => `<div class="h-pick lift" style="--i:${Math.min(i, 8)}"><div class="n">${i + 1}</div>
        <div><a href="analyze/${encodeURIComponent(x.ticker)}/" class="tk" style="text-decoration:none">${esc(x.ticker)}</a><span class="nm">${esc(x.name || "")}</span><div class="act"><b>${esc(x.label)}</b></div></div>
        <div class="amt">${money(x.entry, 2)}<small>stop ${money(x.stop, 2)} (${pct(x.risk_pct, 1)} risk)</small><small>${x.horizon_days} sessions</small></div>
        <div class="zonecol"><div class="z" style="margin-top:0;flex-direction:column;align-items:flex-start;gap:3px">${Object.entries(x.numbers || {}).map(([k, v]) => `<span>${esc(k)} <b style="color:var(--ink);font-weight:500">${esc(v)}</b></span>`).join("")}</div></div>
        <div class="thesis">${esc(x.rule)} <b>${esc(x.what_this_is)}.</b></div></div>`).join("")
      : (SU.status === "SCANNED" ? `<div class="empty">Nothing matched today. A quiet scan is the normal case.</div>` : "");
    rules.innerHTML = Object.entries(SU.kinds || {}).map(([k, r]) => `<div class="e"><div class="dt">${esc(k)}</div><div><div class="h">${esc(r.label)}</div><div class="th">${esc(r.rule)}</div></div><div class="side"></div></div>`).join("")
      + (SU.limitations || []).map((l) => `<div class="e"><div class="dt muted">limit</div><div class="th muted">${esc(l)}</div><div class="side"></div></div>`).join("");
  }

  // overview extras
  function renderRest() {
    const E = D.earnings || [];
    $("earnings").innerHTML = E.length ? `<table><thead><tr><th>date</th><th>company</th><th></th></tr></thead><tbody>${E.map((e, i) => `<tr class="row" style="animation-delay:${i * 30}ms"><td class="mono">${esc(e.date)}</td><td><span class="tk">${esc(e.ticker)}</span><span class="nm">${esc(e.name || "")}</span></td><td class="muted" style="font-size:13px">${e.held ? "held" : e.watch ? "pick" : "screen"}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing held or picked reports in the next two weeks.</div>`;
    const C = D.counts || {};
    $("funnel").innerHTML = [["universe", C.universe, "US or Canada, over $2B, liquid, 3+ years public"], ["scored", C.scored, "banks, insurers and Chinese ADRs set aside"], ["quality 150", C.top, "top of the quality score"], ["compounders", C.compounders, "at or below their usual price"], ["cyclical turns", C.cyclical_turns, "35%+ off highs, estimates stable"]].map(([k, v, d]) => `<div><b>${v == null ? "n/a" : money(v)}</b>${k}<br><span class="muted">${d}</span></div>`).join("");
  }

  // settings
  const swatch = { night: "#131311", paper: "#ffffff", slate: "#0f1418", forest: "#0c1410", bone: "#f3f1ea", amber: "#0a0a0a" };
  function segButtons(id, items, val, onpick) {
    $(id).innerHTML = items.map(([k, l, sw]) => `<button data-k="${k}" class="${String(k) === String(val) ? "on" : ""}">${sw ? `<span class="sw" style="background:${sw}"></span>` : ""}${l}</button>`).join("");
    $(id).onclick = (e) => { const b = e.target.closest("button"); if (!b) return; onpick(b.dataset.k); $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); };
  }
  function renderSettings() {
    segButtons("themes", Desk.THEMES.map(([k, l]) => [k, l, swatch[k]]), S.theme, (k) => { S.theme = k; store.set("theme", k); document.documentElement.dataset.theme = k; firstRender = false; renderAll(); });
    segButtons("ccy", [["USD", "USD"], ["CAD", "CAD"]], S.ccy, (k) => { S.ccy = k; store.set("ccy", k); firstRender = false; renderAll(); });
    segButtons("motion", [["on", "on"], ["off", "off"]], S.motion, (k) => { S.motion = k; store.set("motion", k); document.documentElement.dataset.motion = k; });
    const ps = W && W.sources && W.sources.prices ? W.sources.prices : null;
    $("pricesrc").textContent = W ? `${W.status}${ps ? ", " + ps.detail : ""}${W.as_of ? ", closes as of " + W.as_of : ""}` : "no scan file";
    $("snap").innerHTML = `built ${esc(D.built_at || D.as_of || "n/a")}<br>${money((D.counts || {}).universe)} names in the universe, ${(D.dates || []).length} days tracked, USDCAD ${D.usdcad ?? "n/a"}`;
  }
  $("clearlocal").addEventListener("click", () => { ["theme", "key", "interval", "ccy", "motion", "buys"].forEach((k) => { try { localStorage.removeItem("desk-" + k); } catch (e) {} }); location.reload(); });

  async function loadJson(name) {
    try { const r = await fetch(name, { cache: "no-store" }); if (!r.ok) return null; return await r.json(); } catch (e) { return null; }
  }
  async function pollSnapshot() {
    try {
      const r = await fetch("data.js?t=" + Date.now(), { cache: "no-store" }); if (!r.ok) return;
      const txt = await r.text(); const m = txt.match(/"built_at": "([^"]+)"/);
      if (m && D.built_at && m[1] !== D.built_at) { const fn = new Function(txt + "; return window.DASH;"); D = fn(); firstRender = false; renderAll(); }
    } catch (e) {}
  }

  function renderAll() { readColors(); status(scanned() ? "scan " + W.as_of : ""); renderHero(); renderScan(); renderIndexChart($("chart"), "chart-sub"); renderPicks(); renderOwn(); renderScreen(false); renderJournal(); renderSetups(); renderRest(); renderSettings(); if ($("v-charts").classList.contains("on")) renderCharts(); }

  Promise.all([loadJson("watch.json"), loadJson("tracker.json"), loadJson("setups.json")]).then(([w, t, su]) => {
    W = w; T = t; SU = su;
    renderAll();
    show(location.hash.slice(1) || "overview");
    firstRender = false;
  });
  setInterval(pollSnapshot, 60000);
  window.addEventListener("resize", () => { let t; clearTimeout(t); t = setTimeout(() => { renderIndexChart($("chart"), "chart-sub"); if ($("v-charts").classList.contains("on")) renderCharts(); }, 200); });
})();
