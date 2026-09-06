/* Renders /analyze/ from analysis/index.json: every name that has a page.
 *
 * The list is sorted by score by default and says, for each row, how much of the
 * page behind it is real. "research note" means somebody read a filing;
 * "screen only" means nobody has, and the page will say so too. A reader who
 * cannot see that distinction from the index will assume all 150 pages are the
 * same depth, and 134 of them are not.
 */
(function () {
  const D = window.Desk;
  const { esc, has, num, pct, mult, money } = D;

  const S = { data: null, q: "", sort: "score", dir: -1, depth: "all" };

  const COLS = [
    { key: "ticker", label: "ticker", cls: "" },
    { key: "company", label: "company", cls: "" },
    { key: "sector", label: "sector", cls: "" },
    { key: "depth", label: "depth", cls: "" },
    { key: "score", label: "pctile", cls: "n" },
    { key: "coverage", label: "coverage", cls: "n" },
    { key: "price", label: "price", cls: "n" },
    { key: "forward_pe", label: "fwd P/E", cls: "n" },
    { key: "pe_vs_median", label: "vs median", cls: "n" },
    { key: "dd_52w", label: "off high", cls: "n" },
    { key: "next_earnings", label: "next report", cls: "n" },
  ];

  function rows() {
    let r = S.data.tickers.slice();
    if (S.depth !== "all") r = r.filter((x) => x.depth === S.depth);
    if (S.q) {
      const q = S.q.toLowerCase();
      r = r.filter((x) => (x.ticker + " " + (x.company || "") + " " + (x.sector || "") + " " +
                           (x.industry || "")).toLowerCase().includes(q));
    }
    const k = S.sort;
    r.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return S.dir * av.localeCompare(bv);
      return S.dir * (av - bv);
    });
    return r;
  }

  function fmt(x, key) {
    switch (key) {
      case "ticker": return `<a href="${esc(x.ticker)}/"><span class="tk">${esc(x.ticker)}</span></a>`;
      case "company": return esc(x.company || "");
      case "sector": return `<span class="muted" style="font-size:12px">${esc(x.sector || "")}</span>`;
      case "depth": return x.depth === "deep"
        ? `<span class="chip on" style="font-size:10px">research note</span>`
        : `<span class="chip" style="font-size:10px">screen only</span>`;
      case "score": return esc(num(x.score, 1));
      case "coverage": return `<span class="${x.coverage != null && x.coverage < 0.6 ? "down" : "muted"}">${esc(pct(x.coverage, 0))}</span>`;
      case "price": return esc(money(x.price));
      case "forward_pe": return esc(mult(x.forward_pe));
      case "pe_vs_median": return has(x.pe_vs_median) ? esc(pct(x.pe_vs_median, 0, true)) : '<span class="muted">n/a</span>';
      case "dd_52w": return esc(pct(x.dd_52w, 0, true));
      case "next_earnings": return `<span class="muted" style="font-size:12px">${esc(x.next_earnings || "")}</span>`;
      default: return "";
    }
  }

  function table() {
    const r = rows();
    const head = COLS.map((c) => `<th class="${c.cls}"><button data-sort="${c.key}"
      style="border:none;padding:0;font-size:11px;color:${S.sort === c.key ? "var(--ink)" : "var(--ink-3)"}"
      >${esc(c.label)}${S.sort === c.key ? (S.dir < 0 ? " ↓" : " ↑") : ""}</button></th>`).join("");
    const body = r.map((x) => `<tr>${COLS.map((c) => `<td class="${c.cls}">${fmt(x, c.key)}</td>`).join("")}</tr>`).join("");
    return `<div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
      <div class="note">${r.length} of ${S.data.tickers.length} names. Everything is dated
      ${esc(S.data.snapshot_date)}.</div>`;
  }

  function render() {
    const d = S.data;
    const deep = d.tickers.filter((t) => t.depth === "deep").length;
    document.title = "Analyse · Desk";
    document.body.innerHTML = `<div class="page">
${D.chrome("Analyse", "../")}
<div class="plate reveal" style="--i:0">
  <div>
    <div class="tk">Analyse</div>
    <div class="co">One page per name in the quality top 150.</div>
    <div class="facts">
      <span><b>${deep}</b> with a research note behind them</span>
      <span><b>${d.tickers.length - deep}</b> from the screen only, where nobody has read a filing</span>
      <span>snapshot <b>${esc(d.snapshot_date)}</b></span>
    </div>
  </div>
</div>
<section class="reveal" style="--i:1">
  <div class="toolbar">
    <div class="seg-btns" id="depth">
      ${[["all", "all"], ["deep", "research note"], ["screen", "screen only"]].map(([k, l]) =>
        `<button data-depth="${k}"${k === S.depth ? ' class="on"' : ""}>${esc(l)}</button>`).join("")}
    </div>
    <input type="search" id="q" placeholder="find a ticker, company, sector or industry" value="${esc(S.q)}">
    <span class="right"><a class="mono" style="font-size:12px" href="../positioning/">positioning &rarr;</a></span>
  </div>
  <div id="tbl">${table()}</div>
</section>
<section class="reveal" style="--i:2">
  <div class="toolbar">${D.themeBar()}</div>
</section>
<footer>Research and analysis from public data, not personalised financial advice.</footer>
</div>`;
    const st = document.getElementById("status");
    if (st) st.innerHTML = `snapshot ${esc(d.snapshot_date)}`;
    D.wireTheme(document);
    wire();
  }

  /* Two scopes, deliberately separate. The depth buttons and the search box live
   * outside #tbl and survive a redraw; the sort headers are inside it and are
   * destroyed with it. Binding all three from one function after every sort left
   * the survivors carrying a fresh duplicate listener each time, so five sorts
   * put eighteen handlers on the search box and one keystroke redrew the table
   * six times. */
  function redraw() {
    document.getElementById("tbl").innerHTML = table();
    wireTable();
  }

  function wireChrome() {
    document.querySelectorAll("[data-depth]").forEach((b) => b.addEventListener("click", () => {
      S.depth = b.dataset.depth;
      render();
    }));
    const q = document.getElementById("q");
    if (q) q.addEventListener("input", () => { S.q = q.value; redraw(); });
  }

  function wireTable() {
    document.querySelectorAll("[data-sort]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.sort;
      if (S.sort === k) S.dir = -S.dir;
      else { S.sort = k; S.dir = (k === "ticker" || k === "company" || k === "sector") ? 1 : -1; }
      redraw();
    }));
  }

  function wire() {
    wireChrome();
    wireTable();
  }

  fetch("../analysis/index.json", { cache: "no-store" })
    .then((r) => r.json())
    .then((d) => { S.data = d; render(); })
    .catch((e) => D.fail("Index not built",
      `Run "python scripts/build_analysis.py" and reload. (${e.message})`));
})();
