/* Renders /analyze/compare/ : up to four analysed names side by side.
 *
 * The selection lives in the query string (?t=KLAC,BKNG) so a comparison is a
 * link somebody can send. Each column is one analysis/<TICKER>.json, the same
 * record the deep page renders, so a number here and a number there cannot
 * disagree: they are the same field.
 *
 * Two rules carried over from the deep page. Valuation multiples and drawdowns
 * are never coloured and never marked "best": a lower multiple is cheaper, not
 * better, and a bigger drawdown is the reason one bucket exists. And the call and
 * its falsifier sit on adjacent rows with the same class, so the thing that would
 * prove an idea wrong is never set smaller than the idea.
 *
 * The row-building half is pure and exported as window.DeskCompare so a test can
 * run it in node over the real records without a browser.
 */
(function () {
  const D = window.Desk;
  const { esc, has, num, pct, mult, money, cap, byUnit } = D;
  const MAX = 4;

  const PLACEHOLDER = new Set(["n/a", "na", "none", "-", "--", "tbd", "?", ""]);
  function firstReal(candidates) {
    for (const c of candidates || []) {
      if (typeof c !== "string") continue;
      const v = c.trim();
      if (v && !PLACEHOLDER.has(v.toLowerCase().replace(/[.\s]+$/, ""))) return v;
    }
    return null;
  }

  /* ?t=klac,bkng -> ["KLAC", "BKNG"]; unknown names are reported, not dropped silently. */
  function parseSelection(search, known) {
    const m = /[?&]t=([^&]*)/.exec(search || "");
    const raw = m ? decodeURIComponent(m[1].replace(/\+/g, " ")) : "";
    const seen = new Set();
    const tickers = [], rejected = [], overflow = [];
    raw.split(/[\s,;|]+/).map((s) => s.trim().toUpperCase()).filter(Boolean).forEach((t) => {
      if (seen.has(t)) return;
      seen.add(t);
      if (known && !known.has(t)) { rejected.push(t); return; }
      if (tickers.length >= MAX) { overflow.push(t); return; }
      tickers.push(t);
    });
    return { tickers, rejected, overflow };
  }

  /* -- cells ----------------------------------------------------------------- */
  const NA = { text: "n/a", html: '<span class="muted">n/a</span>', value: null };

  function cell(text, html, value) {
    return { text: String(text), html: html == null ? esc(text) : html, value: has(value) ? Number(value) : null };
  }

  /* Mark the best cell in a row: only where the row says which way is better,
   * only with two or more real values, and never when they all tie. */
  function markBest(cells, higherIsBetter) {
    if (higherIsBetter == null) return cells;
    const vals = cells.map((c) => c.value).filter((v) => v !== null);
    if (vals.length < 2) return cells;
    const target = higherIsBetter ? Math.max(...vals) : Math.min(...vals);
    if (vals.every((v) => v === target)) return cells;
    return cells.map((c) => (c.value === target ? { ...c, best: true } : c));
  }

  function row(label, cells, opts) {
    const o = opts || {};
    return { label, cells: markBest(cells, o.higherIsBetter), cls: o.cls || "", note: o.note || "" };
  }

  /* -- sections ----------------------------------------------------------------- */
  function identitySection(rs) {
    const depthLabel = { deep: "research note", screen: "screen only", universe: "universe" };
    return {
      title: "The names",
      sub: "",
      rows: [
        row("company", rs.map((r) => cell(r.identity.company || r.identity.ticker))),
        row("sector", rs.map((r) => cell(`${r.identity.sector || "?"} · ${r.identity.industry || ""}`.replace(/ · $/, ""),
          `<span class="muted">${esc(r.identity.sector || "?")}</span><br>${esc(r.identity.industry || "")}`))),
        row("depth", rs.map((r) => cell(depthLabel[r.depth] || r.depth,
          `<span class="chip${r.depth === "deep" ? " on" : " warn"}">${esc(depthLabel[r.depth] || r.depth)}</span>`))),
        row("price", rs.map((r) => cell(money(r.identity.price), null, r.identity.price))),
        row("market cap", rs.map((r) => cell(cap(r.identity.market_cap_usd), null, r.identity.market_cap_usd))),
        row("bucket", rs.map((r) => {
          const b = (r.valuation.available && r.valuation.buckets) || [];
          return b.length ? cell(b.join(", "), b.map((x) => `<span class="chip on">${esc(x)}</span>`).join("")) : cell("none", '<span class="muted">none</span>');
        })),
      ],
    };
  }

  function callSection(rs) {
    const callOf = (r) => r.thesis.available ? (r.thesis.action || "no action stated") : "no view formed: nobody has read a filing";
    const wrongOf = (r) => {
      if (!r.thesis.available) return null;
      const lg = r.thesis.logged || {};
      return firstReal([lg.wrong_if, r.risks.killers && r.risks.killers[0],
        r.risks.price_trigger ? `A close under ${money(r.risks.price_trigger, 0)}.` : null]);
    };
    return {
      title: "The standing call, and what would prove it wrong",
      sub: "same weight, on purpose",
      rows: [
        row("the call", rs.map((r) => cell(callOf(r), r.thesis.available ? esc(callOf(r)) : `<span class="muted">${esc(callOf(r))}</span>`)), { cls: "cmp-call" }),
        row("wrong if", rs.map((r) => {
          const w = wrongOf(r);
          return w ? cell(w) : cell("no falsifier recorded", '<span class="muted">no falsifier recorded</span>');
        }), { cls: "cmp-call" }),
        row("conviction", rs.map((r) => {
          const c = r.thesis.available ? (r.thesis.conviction || (r.thesis.logged || {}).conviction) : null;
          return has(c) ? cell(`${c} of 5`, `${c} of 5 <span class="conv">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= c ? "on" : ""}"></i>`).join("")}</span>`, c) : NA;
        })),
        row("wrong at (price)", rs.map((r) => {
          const t = r.risks.price_trigger, p = r.identity.price;
          if (!has(t)) return cell("not set", '<span class="muted">not set</span>');
          const d = has(p) && t > 0 ? pct(p / t - 1, 0, true) : "";
          return cell(`${money(t, 0)}${d ? ` (today ${d} above)` : ""}`, `${esc(money(t, 0))}${d ? `<br><span class="muted">today ${esc(d)} above</span>` : ""}`);
        })),
        row("logged", rs.map((r) => {
          const lg = r.thesis.available ? (r.thesis.logged || {}) : {};
          return lg.date ? cell(`${lg.date}${has(lg.price_at_call) ? ` at ${money(lg.price_at_call)}` : ""}`) : NA;
        })),
      ],
    };
  }

  function trendsSection(rs) {
    const order = [];
    const seen = new Set();
    rs.forEach((r) => (r.trends || []).forEach((t) => { if (!seen.has(t.key)) { seen.add(t.key); order.push({ key: t.key, label: t.label, unit: t.unit, hib: t.higher_is_better }); } }));
    const rows = order.map((o) => {
      const cells = rs.map((r) => {
        const t = (r.trends || []).find((x) => x.key === o.key);
        if (!t || !t.values || !t.values.some(has)) return NA;
        const growth = has(t.cagr) ? t.cagr : t.change_pct;
        const growthLabel = has(t.cagr) ? "CAGR" : "change";
        const zero = o.unit === "currency_bn";
        const spark = D.spark(t.values, t.periods_labels, { zero, width: 120, height: 22 });
        const colour = t.higher_is_better == null || !has(growth) || t.crosses_zero ? ""
          : ((growth > 0) === t.higher_is_better ? "up" : "down");
        const gtxt = has(growth) && !t.crosses_zero ? `${growthLabel} ${pct(growth, 1, true)}` : (t.crosses_zero ? "crosses zero" : "");
        const text = `${byUnit(t.last, o.unit)} (${t.span_label || ""}${gtxt ? "; " + gtxt : ""})`;
        const html = `${spark}<div class="cmp-tv"><b>${esc(byUnit(t.last, o.unit))}</b>${
          gtxt ? ` <span class="${colour}">${esc(gtxt)}</span>` : ""}</div><div class="muted cmp-ts">${esc(t.span_label || "")}</div>`;
        return cell(text, html, t.crosses_zero ? null : growth);
      });
      return row(o.label, cells, { higherIsBetter: o.hib });
    });
    return {
      title: "Four fiscal years",
      sub: "best marks the strongest move over the window, in the direction the row prefers",
      rows,
    };
  }

  function screenSection(rs) {
    const order = [];
    const seen = new Set();
    rs.forEach((r) => ((r.fundamentals || {}).rows || []).forEach((x) => { if (!seen.has(x.key)) { seen.add(x.key); order.push(x); } }));
    const rows = order.map((o) => row(o.label, rs.map((r) => {
      const x = ((r.fundamentals || {}).rows || []).find((y) => y.key === o.key);
      return x && has(x.value) ? cell(byUnit(x.value, x.unit), null, x.value) : NA;
    }), { higherIsBetter: o.higher_is_better }));
    return { title: "Screen measures", sub: "from the quality screen's four fiscal years", rows };
  }

  function valuationSection(rs) {
    const v = (r) => (r.valuation && r.valuation.available ? r.valuation : null);
    const multiple = (r, key) => {
      const val = v(r);
      const m = val && (val.multiples || []).find((x) => x.key === key);
      if (!m || !has(m.value)) return NA;
      const usable = val.own_history && val.own_history.usable;
      const vs = usable && has(m.vs_median) ? `vs own median ${pct(m.vs_median, 0, true)}` : "no usable own history";
      return cell(`${mult(m.value)} (${vs})`, `<b>${esc(mult(m.value))}</b><br><span class="muted">${esc(vs)}</span>`);
    };
    const est = (r, key, dec) => { const val = v(r); return val && has(val.estimates[key]) ? cell(pct(val.estimates[key], dec == null ? 1 : dec, true)) : NA; };
    return {
      title: "Valuation",
      sub: "never coloured, never marked best: cheaper is not better",
      rows: [
        row("forward P/E", rs.map((r) => multiple(r, "forward_pe")), { note: "forward against a trailing median: reads cheap by construction" }),
        row("EV / EBITDA", rs.map((r) => multiple(r, "ev_ebitda"))),
        row("off 52-week high", rs.map((r) => { const val = v(r); return val && has(val.drawdown.from_52w_high) ? cell(pct(val.drawdown.from_52w_high, 0, true)) : NA; })),
        row("off all-time high", rs.map((r) => { const val = v(r); return val && has(val.drawdown.from_all_time_high) ? cell(pct(val.drawdown.from_all_time_high, 0, true)) : NA; }),
          { note: "closing-price high against an intraday 52-week high; understated" }),
        row("next-year EPS, 90 days", rs.map((r) => est(r, "fy1_change_90d"))),
        row("next-year EPS, 30 days", rs.map((r) => est(r, "fy1_change_30d"))),
        row("analysts up / down, 30 days", rs.map((r) => {
          const val = v(r);
          if (!val || !has(val.estimates.analysts_up_30d)) return NA;
          return cell(`${num(val.estimates.analysts_up_30d, 0)} / ${num(val.estimates.analysts_down_30d, 0)}`);
        })),
        row("short interest", rs.map((r) => { const val = v(r); return val && has(val.short_pct_float) ? cell(pct(val.short_pct_float, 1) + " of float") : NA; })),
        row("among peers, price", rs.map((r) => {
          const val = v(r); const p = val && val.peers;
          return p && has(p.price_rank) ? cell(`${pct(p.price_rank, 0)} dearer than ${p.peer_set.label}`, `${esc(pct(p.price_rank, 0))}<br><span class="muted">dearer than ${esc(p.peer_set.label)} (${p.peer_set.n})</span>`) : NA;
        })),
        row("among peers, quality", rs.map((r) => {
          const val = v(r); const p = val && val.peers;
          return p && has(p.quality_rank) ? cell(`${pct(p.quality_rank, 0)} better than peers`, `${esc(pct(p.quality_rank, 0))}<br><span class="muted">better than peers</span>`) : NA;
        })),
        row("peer verdict", rs.map((r) => { const val = v(r); const p = val && val.peers; return p && p.verdict ? cell(p.verdict) : NA; })),
        row("next report", rs.map((r) => { const val = v(r); return val && val.next_earnings ? cell(val.next_earnings) : NA; })),
      ],
    };
  }

  function scoreSection(rs) {
    const variants = [["quality_value", "quality + value"], ["with_momentum", "with momentum"], ["with_reversal", "with reversal"]];
    const rows = variants.map(([k, l]) => row(`${l}, percentile`, rs.map((r) => {
      const s = r.score && r.score.available && r.score.variants[k];
      if (!s) return NA;
      const thin = s.thinly_evidenced ? ' <span class="chip warn">thin</span>' : "";
      return cell(`${num(s.percentile, 0)}${s.thinly_evidenced ? " (thin)" : ""}`, `${esc(num(s.percentile, 0))}${thin}`, s.percentile);
    }), { higherIsBetter: true }));
    rows.push(row("evidence coverage", rs.map((r) => {
      const s = r.score && r.score.available && r.score.variants.quality_value;
      return s ? cell(pct(s.coverage, 0), `<span class="${s.coverage < 0.6 ? "down" : ""}">${esc(pct(s.coverage, 0))}</span>`, s.coverage) : NA;
    }), { higherIsBetter: true }));
    rows.push(row("largest contribution", rs.map((r) => {
      const s = r.score && r.score.available && r.score.variants.quality_value;
      const top = s && s.top_contributions && s.top_contributions[0];
      return top ? cell(`${top.key} ${top.value > 0 ? "+" : ""}${num(top.value, 2)}`) : NA;
    })));
    return { title: "The score", sub: "never backtested; a percentile among the 150", rows };
  }

  function provenanceSection(rs) {
    return {
      title: "Provenance",
      sub: "",
      rows: [
        row("screen data as of", rs.map((r) => cell(r.identity.as_of || "?"))),
        row("what is not known", rs.map((r) => cell(`${(r.gaps || []).length} gaps listed`,
          `<a href="../${esc(r.identity.ticker)}/">${(r.gaps || []).length} gaps listed &rarr;</a>`, (r.gaps || []).length))),
        row("full page", rs.map((r) => cell(`/analyze/${r.identity.ticker}/`, `<a href="../${esc(r.identity.ticker)}/">${esc(r.identity.ticker)} &rarr;</a>`))),
      ],
    };
  }

  function model(records) {
    const rs = records.filter(Boolean);
    return {
      tickers: rs.map((r) => r.identity.ticker),
      sections: rs.length ? [identitySection(rs), callSection(rs), trendsSection(rs), screenSection(rs),
                             valuationSection(rs), scoreSection(rs), provenanceSection(rs)] : [],
    };
  }

  window.DeskCompare = { parseSelection, model, MAX };
  if (!window.DESK_COMPARE) return;

  /* -- the page ---------------------------------------------------------------- */
  const S = { index: null, known: new Set(), tickers: [], rejected: [], overflow: [], records: {}, q: "" };

  function sectionHtml(sec, n) {
    const head = `<thead><tr><th></th>${sec.rows.length ? S.tickers.map((t) => `<th><a href="../${esc(t)}/"><span class="tk">${esc(t)}</span></a></th>`).join("") : ""}</tr></thead>`;
    const body = sec.rows.map((r) => `<tr class="${r.cls}"><th scope="row">${esc(r.label)}${
      r.note ? `<div class="muted cmp-note">${esc(r.note)}</div>` : ""}</th>${
      r.cells.map((c) => `<td class="${c.best ? "best" : ""}">${c.html}</td>`).join("")}</tr>`).join("");
    return `<section class="reveal" style="--i:${n}">
      <h2>${esc(sec.title)}${sec.sub ? `<span class="sub">${esc(sec.sub)}</span>` : ""}</h2><div class="rule"></div>
      <div class="scroll"><table class="cmp">${head}<tbody>${body}</tbody></table></div></section>`;
  }

  function hits() {
    const q = S.q.trim().toLowerCase();
    if (!q || !S.index) return [];
    return S.index.tickers
      .filter((x) => !S.tickers.includes(x.ticker))
      .filter((x) => (x.ticker + " " + (x.company || "") + " " + (x.sector || "")).toLowerCase().includes(q))
      .slice(0, 8);
  }

  function pickerHtml() {
    const full = S.tickers.length >= MAX;
    const chips = S.tickers.map((t) => `<span class="chip on">${esc(t)} <button data-remove="${esc(t)}" aria-label="remove ${esc(t)}">&times;</button></span>`).join("");
    const list = hits().map((x) => `<button data-add="${esc(x.ticker)}"${full ? " disabled" : ""}><span class="tk">${esc(x.ticker)}</span> ${esc(x.company || "")}</button>`).join("");
    return `<div class="cmp-pick">
      <div class="chips">${chips || '<span class="muted">nothing selected</span>'}</div>
      <input type="search" id="q" placeholder="${full ? `four is the limit; remove one to add another` : "add a ticker or company"}" value="${esc(S.q)}"${full ? " disabled" : ""}>
      <div class="cmp-hits" id="hits">${list}</div>
    </div>`;
  }

  function warnings() {
    const out = [];
    if (S.rejected.length) out.push(`No analysis page for ${S.rejected.join(", ")}: not in the quality top 150, so there is nothing to compare.`);
    if (S.overflow.length) out.push(`Four is the limit. ${S.overflow.join(", ")} left out.`);
    return out.map((w) => `<div class="note warn">${esc(w)}</div>`).join("");
  }

  function render() {
    const m = model(S.tickers.map((t) => S.records[t]));
    document.title = (S.tickers.length ? S.tickers.join(" vs ") + " · " : "") + "Compare · Desk";
    const empty = !S.tickers.length ? `<section class="reveal" style="--i:2"><div class="empty">
      Pick up to four names from the quality top 150 and they are set side by side: the standing call
      and what would prove it wrong, four fiscal years of the business numbers with the strongest move
      marked, the screen measures, valuation against own history and against peers, and the score.
      Every cell comes from the same record the deep page renders, so nothing here can disagree with
      a page. The selection is in the address, so a comparison is a link you can send.
      Start by typing a ticker above, or open <a href="?t=KLAC,BKNG,AAPL,NVDA">KLAC, BKNG, AAPL and NVDA</a>.
      </div></section>` : "";
    document.body.innerHTML = `<div class="page">
${D.chrome("Analyse", "../../")}
<div class="plate reveal" style="--i:0">
  <div>
    <div class="tk">Compare</div>
    <div class="co">${S.tickers.length ? esc(S.tickers.join("  ·  ")) : "up to four analysed names, side by side"}</div>
    <div class="facts"><span>snapshot <b>${esc((S.index || {}).snapshot_date || "?")}</b></span>
      <span><a href="../">back to the index</a></span></div>
  </div>
</div>
<section class="reveal" style="--i:1"><div class="toolbar">${pickerHtml()}</div>${warnings()}</section>
${empty}
${m.sections.map((s, i) => sectionHtml(s, i + 2)).join("")}
<section class="reveal" style="--i:9"><div class="toolbar">${D.themeBar()}</div></section>
<footer>Research and analysis from public data, not personalised financial advice.</footer>
</div>`;
    const st = document.getElementById("status");
    if (st) st.innerHTML = `${S.tickers.length} of ${MAX}`;
    D.wireTheme(document);
    wire();
  }

  function setUrl() {
    try { history.replaceState(null, "", S.tickers.length ? "?t=" + S.tickers.join(",") : location.pathname); } catch (e) {}
  }

  function wire() {
    const q = document.getElementById("q");
    if (q) q.addEventListener("input", () => {
      S.q = q.value;
      const h = document.getElementById("hits");
      if (h) { h.innerHTML = hits().map((x) => `<button data-add="${esc(x.ticker)}"><span class="tk">${esc(x.ticker)}</span> ${esc(x.company || "")}</button>`).join(""); wireHits(); }
    });
    document.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
      S.tickers = S.tickers.filter((t) => t !== b.dataset.remove);
      setUrl();
      render();
    }));
    wireHits();
  }

  function wireHits() {
    document.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
      if (S.tickers.length >= MAX) return;
      S.q = "";
      add(b.dataset.add).then(() => { setUrl(); render(); const q = document.getElementById("q"); if (q) q.focus(); });
    }));
  }

  function add(t) {
    if (S.records[t]) { if (!S.tickers.includes(t)) S.tickers.push(t); return Promise.resolve(); }
    return fetch(`../../analysis/${t}.json`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(r.status + " " + r.statusText); return r.json(); })
      .then((rec) => { S.records[t] = rec; if (!S.tickers.includes(t)) S.tickers.push(t); })
      .catch(() => { S.rejected.push(t); });
  }

  fetch("../../analysis/index.json", { cache: "no-store" })
    .then((r) => r.json())
    .then((idx) => {
      S.index = idx;
      S.known = new Set(idx.tickers.map((x) => x.ticker));
      const sel = parseSelection(location.search, S.known);
      S.rejected = sel.rejected;
      S.overflow = sel.overflow;
      return Promise.all(sel.tickers.map((t) => add(t)))
        // add() appends in completion order; the address decides the column order.
        .then(() => { S.tickers = sel.tickers.filter((t) => S.records[t]); });
    })
    .then(() => { setUrl(); render(); })
    .catch((e) => D.fail("Index not built", `Run "python scripts/build_analysis.py" and reload. (${e.message})`));
})();
