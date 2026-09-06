/* Renders one /analyze/<TICKER>/ page from dashboard/analysis/<TICKER>.json.
 *
 * The record is the only input. If a number is not in the JSON with a source
 * attached, it does not reach the screen, so there is one place to look when
 * something is wrong.
 *
 * Section order is an argument, not a layout:
 *   1  the standing call, with its falsifier set at the same weight
 *   2  what changed since the last report, because that is what is new
 *   3  what the business does, for the reader who has not met it
 *   4  the numbers over four fiscal years
 *   5  valuation
 *   6  what would break it
 *   7  the score and where it comes from
 *   8  sources, and what is not known
 */
(function () {
  const D = window.Desk;
  const { esc, has, num, pct, pts, mult, money, bn, cap, byUnit } = D;

  const TICKER = (window.DESK_TICKER || "").toUpperCase();

  function sectionHead(title, sub) {
    return `<h2>${esc(title)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</h2><div class="rule"></div>`;
  }

  /* -- 1. the standing call ---------------------------------------------- */
  function callBlock(r) {
    const th = r.thesis, ri = r.risks, v = r.valuation;
    if (!th.available) {
      return `<div class="callout none reveal" style="--i:2">
        <div class="kicker">standing call</div>
        <p class="line">No view has been formed on this name. It reached the quality screen but
        nobody has read a filing for it, so there is no thesis, no size and nothing that would
        prove the idea wrong.</p>
        <div class="meta"><span>${esc(th.why || "")}</span></div></div>`;
    }
    const logged = th.logged || {};
    const trigger = ri.price_trigger;
    const price = r.identity.price;
    const conv = th.conviction || logged.conviction;

    // The rail. Its span runs from the price that would prove the idea wrong to the
    // 52-week high, because those are the two levels this desk's own rules name.
    // Where no trigger is set the rail is not drawn at all rather than invented:
    // a range with an arbitrary bottom would be a picture of nothing.
    let zoneRail = "";
    const hi52 = v.available ? v.drawdown.high_52w : null;
    if (has(price) && has(trigger) && has(hi52) && hi52 > trigger) {
      const pad = (hi52 - trigger) * 0.08;
      zoneRail = D.rail(trigger - pad, hi52 + pad, [
        { value: trigger, kind: "kill", label: "wrong at" },
        { value: price, kind: "now", label: "today" },
        { value: hi52, kind: "", label: "52-week high" },
      ], null,
        `Today is ${pct(price / trigger - 1, 0, true)} above the level that would say the idea is `
        + `wrong, and ${pct(price / hi52 - 1, 0, true)} from the high it made this year.`,
        (x) => money(x, 0));
    } else if (has(price) && !has(trigger)) {
      zoneRail = `<div class="note warn">No price trigger is set for this name, so there is no
        level to draw the call against. The rail is left out rather than given an invented bottom.</div>`;
    }

    return `<div class="callout reveal" style="--i:2">
      <div class="kicker">standing call, logged ${esc(logged.date || r.identity.as_of)}</div>
      <p class="line"><span class="lab">the call</span>${esc(th.action || "no action stated")}</p>
      <p class="line"><span class="lab">wrong if</span>${esc(
        (logged.wrong_if || (ri.killers && ri.killers[0]) || "no falsifier recorded"))}</p>
      <div class="meta">
        ${logged.target_size ? `<span>size <b>${esc(logged.target_size)}</b></span>` : ""}
        ${conv ? `<span>conviction <b>${conv} of 5</b></span>
          <span class="conv">${[1,2,3,4,5].map(i => `<i class="${i <= conv ? "on" : ""}"></i>`).join("")}</span>` : ""}
        ${has(logged.price_at_call) ? `<span>price at call <b>${money(logged.price_at_call)}</b></span>` : ""}
        ${logged.bucket ? `<span>bucket <b>${esc(logged.bucket)}</b></span>` : ""}
      </div>
      ${zoneRail}
      ${!has(trigger) && ri.price_trigger_note ? `<div class="note">${esc(ri.price_trigger_note)}</div>` : ""}
    </div>`;
  }

  /* -- 2. what changed ---------------------------------------------------- */
  function markFor(direction) {
    return `<span class="mk ${direction === "up" ? "up" : direction === "down" ? "down" : "flat"}"></span>`;
  }

  function changedBlock(r) {
    const wc = r.what_changed;
    let rows = wc.mechanical.map((m) => `<div class="row">
      <div class="k">${markFor(m.direction)}${esc(m.label)}</div>
      <div class="v">${esc(m.detail)}</div>
      <div class="m">${esc(m.source.split(",")[0])}</div></div>`).join("");

    const read = wc.read || {};
    if (read.available === false) {
      rows += `<div class="row gap">
        <div class="k"><span class="mk open"></span>guidance and tone</div>
        <div class="v">${esc(read.why)}</div>
        <div class="m">not available</div></div>`;
    } else {
      if (read.headline) {
        rows = `<div class="row"><div class="k">the headline</div>
          <div class="v"><b>${esc(read.headline)}</b></div>
          <div class="m">${esc(read.quarter_label || "")}</div></div>` + rows;
      }
      (read.guidance || []).forEach((g) => {
        const dir = g.change === "raised" ? "up" : g.change === "cut" ? "down" : "flat";
        rows += `<div class="row"><div class="k">${markFor(dir)}guidance, ${esc(g.what)}</div>
          <div class="v">${esc(g.detail)}${g.quote ? `<div class="m" style="margin-top:5px">&ldquo;${esc(g.quote)}&rdquo;</div>` : ""}</div>
          <div class="m">${esc(g.change)}</div></div>`;
      });
      if (read.tone) {
        rows += `<div class="row"><div class="k"><span class="mk flat"></span>tone</div>
          <div class="v">${esc(read.tone.assessment)}${read.tone.evidence ? `<div class="m" style="margin-top:5px">&ldquo;${esc(read.tone.evidence)}&rdquo;</div>` : ""}</div>
          <div class="m">confidence ${esc(read.tone.confidence || "")}</div></div>`;
      }
      (read.new_risks || []).forEach((x) => {
        rows += `<div class="row"><div class="k"><span class="mk down"></span>new risk</div>
          <div class="v">${esc(x.risk)}${x.quote ? `<div class="m" style="margin-top:5px">&ldquo;${esc(x.quote)}&rdquo;</div>` : ""}</div>
          <div class="m">${x.already_in_thesis ? "already in the thesis" : "new"}</div></div>`;
      });
      (read.dodged || []).forEach((x) => {
        rows += `<div class="row"><div class="k"><span class="mk open"></span>not addressed</div>
          <div class="v">${esc(x.topic)}<div class="m" style="margin-top:5px">${esc(x.reasoning)}</div></div>
          <div class="m">confidence ${esc(x.confidence || "")}</div></div>`;
      });
      if (!(read.dodged || []).length) {
        rows += `<div class="row gap"><div class="k"><span class="mk open"></span>not addressed</div>
          <div class="v">Nothing defensible. A research note is already a summary, so its silence is
          usually the author compressing rather than management evading.</div>
          <div class="m">checked</div></div>`;
      }
      (read.not_determinable || []).forEach((x) => {
        rows += `<div class="row gap"><div class="k"><span class="mk open"></span>not in the sources</div>
          <div class="v">${esc(x)}</div><div class="m">gap</div></div>`;
      });
    }

    const earn = wc.earnings_text;
    const text = earn && earn.text
      ? `<div class="prose" style="margin-top:22px">${paragraphs(earn.text)}</div>
         <div class="note">Source: ${esc(earn.source)}.</div>` : "";
    return `<div class="ledger">${rows}</div>${text}`;
  }

  function paragraphs(text) {
    return String(text || "").split(/\n{2,}/).map((p) =>
      `<p>${esc(p.trim()).replace(/\n/g, " ")}</p>`).join("");
  }

  /* -- 4. the numbers ----------------------------------------------------- */
  function trendCards(r) {
    if (!r.trends.length) {
      return `<div class="empty">No statement table for this name. Four-year figures exist in the
      screen output below, but nobody has transcribed the annual statements into a table.</div>`;
    }
    const cards = r.trends.map((t) => {
      const obs = t.values.filter((v) => has(v));
      if (obs.length < 2) {
        return `<div class="card empty"><div class="k">${esc(t.label)}</div>
          <div class="v">not reported</div>
          <div class="d">This company does not report the line.</div></div>`;
      }
      const isRate = t.unit === "percent";
      const delta = isRate ? pts(t.change) : pct(t.change_pct, 1, true);
      const good = t.reads_well;
      const cls = good === true ? "up" : good === false ? "down" : "muted";
      const rate = isRate
        ? `${pts(t.change)} over ${t.periods} intervals`
        : (has(t.cagr) ? `${pct(t.cagr, 1, true)} a year, ${t.periods} intervals` : `${delta} over ${t.periods} intervals`);
      return `<div class="card">
        <div class="k">${esc(t.label)}</div>
        <div class="v">${esc(byUnit(t.last, t.unit))}</div>
        <div class="d"><span class="${cls}">${esc(rate)}</span></div>
        ${D.spark(t.values, t.periods_labels, { zero: t.unit === "currency_bn" })}
        ${D.sparkValues(t.values, t.periods_labels, t.unit)}
        <div class="d muted" style="font-size:10.5px;margin-top:5px">${
          t.unit === "currency_bn" ? "bars from zero" : "bars over the range, not from zero"}</div>
      </div>`;
    }).join("");
    return `<div class="grid">${cards}</div>
      <div class="note">${esc(r.fundamentals.note)} Source: ${esc(r.trends[0].source)}.</div>`;
  }

  function fundamentalsTable(r) {
    const rows = r.fundamentals.rows.map((row) => {
      const val = row.value === null || row.value === undefined
        ? `<span class="muted">n/a${row.absent_means ? " &mdash; " + esc(row.absent_means) : ""}</span>`
        : esc(byUnit(row.value, row.unit));
      return `<tr><td>${esc(row.label)}</td><td class="n">${val}</td>
        <td class="muted" style="font-size:12px">${row.higher_is_better ? "higher is better" : "lower is better"}</td></tr>`;
    }).join("");
    return `<div class="scroll"><table><thead><tr><th>screen measure</th><th class="n">value</th><th>direction</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="note">Window ${esc(r.fundamentals.window)}. Source: ${esc(r.fundamentals.source)}.</div>`;
  }

  /* -- 5. valuation ------------------------------------------------------- */
  function valuationBlock(r) {
    const v = r.valuation;
    if (!v.available) return `<div class="empty">${esc(v.why)}</div>`;

    const mrows = v.multiples.map((m) => `<tr>
      <td>${esc(m.label)}</td>
      <td class="n">${esc(mult(m.value))}</td>
      <td class="n">${has(m.own_median) ? esc(mult(m.own_median)) : '<span class="muted">n/a</span>'}</td>
      <td class="n">${has(m.vs_median) ? esc(pct(m.vs_median, 0, true)) : '<span class="muted">n/a</span>'}</td>
    </tr>`).join("");

    // The multiple against its own four-year range. Never coloured: cheaper is not
    // better, and a green number would settle that question before the reader has.
    const pe = v.multiples[0];
    let peRail = "";
    if (has(pe.value) && has(pe.own_median)) {
      const lo = Math.min(pe.value, pe.own_median) * 0.75;
      const hi = Math.max(pe.value, pe.own_median) * 1.15;
      peRail = D.rail(lo, hi, [
        { value: pe.value, kind: "now", label: "now " + mult(pe.value) },
        { value: pe.own_median, kind: "median", label: "own median " + mult(pe.own_median) },
      ], null, "Forward earnings multiple against its own median at four fiscal year ends. "
             + "Not coloured: a lower multiple is not automatically better.", (x) => mult(x, 0));
    }

    // Where it sits among peers, and whether the price matches the quality. This is
    // the answer to the own-history caveat directly above it: a stock at half its old
    // multiple is cheap only if the old multiple was deserved.
    let peerBlock = "";
    if (v.peers) {
      const p = v.peers;
      const pr = p.price_rank, qr = p.quality_rank;
      const rows = Object.entries(p.price_percentiles).map(([k, x]) => `<div class="row">
        <div class="k">${esc(k)}, among peers</div>
        <div class="v">${has(x) ? `cheaper than <b>${esc(num((1 - x) * 100, 0))}%</b> of them` : "not comparable"}</div>
        <div class="m">price</div></div>`).join("")
        + Object.entries(p.quality_percentiles).map(([k, x]) => `<div class="row">
        <div class="k">${esc(k)}, among peers</div>
        <div class="v">${has(x) ? `better than <b>${esc(num(x * 100, 0))}%</b> of them` : "not comparable"}</div>
        <div class="m">quality</div></div>`).join("");
      let gapRail = "";
      if (has(pr) && has(qr)) {
        gapRail = D.rail(0, 1, [
          { value: pr, kind: "now", label: "priced at" },
          { value: qr, kind: "median", label: "quality at" },
        ], null,
          "Both are percentiles inside the same peer set, so the distance between them is the "
          + "observation. Left is cheaper and worse; right is dearer and better.",
          (x) => num(x * 100, 0) + "th");
      }
      peerBlock = `<h2 style="font-size:13px;margin-top:34px">Against its peers
          <span class="sub">${esc(p.peer_set.n - 1)} others, by ${esc(p.peer_set.basis)}</span></h2>
        <div class="rule soft"></div>
        <div class="note">${esc(p.why)}</div>
        <div class="callout" style="margin-top:18px">
          <div class="kicker">${esc(p.peer_set.label)}</div>
          <p class="line">${esc(p.verdict)}</p>
          <div class="meta"><span>${esc(p.reasoning)}</span></div>
        </div>
        ${gapRail}
        <div class="ledger" style="margin-top:22px">${rows}
          <div class="row gap"><div class="k"><span class="mk open"></span>who the peers are</div>
            <div class="v">${p.peer_set.tickers.filter((t) => t !== p.ticker).slice(0, 24)
              .map((t) => `<a href="../${esc(t)}/">${esc(t)}</a>`).join(", ")}${
              p.peer_set.tickers.length > 25 ? ` and ${p.peer_set.tickers.length - 25} more` : ""}</div>
            <div class="m">inspectable</div></div>
          <div class="row gap"><div class="k"><span class="mk open"></span>how comparable they are</div>
            <div class="v">${esc(p.peer_set.caveat)}</div><div class="m">method</div></div>
        </div>`;
    }

    const dd = v.drawdown;
    const est = v.estimates;
    const written = v.written_view
      ? `<div class="prose" style="margin-top:22px">${paragraphs(v.written_view.text)}</div>
         <div class="note">Source: ${esc(v.written_view.source)}.</div>` : "";

    return `<div class="scroll"><table>
      <thead><tr><th>multiple</th><th class="n">now</th><th class="n">own median</th><th class="n">vs median</th></tr></thead>
      <tbody>${mrows}</tbody></table></div>
      ${peRail}
      <div class="note${v.own_history.usable ? "" : " warn"}">${esc(v.own_history.caveat)}</div>
      ${peerBlock}
      <div class="ledger" style="margin-top:24px">
        <div class="row"><div class="k">off the 52-week high</div>
          <div class="v">${esc(pct(dd.from_52w_high, 1, true))} from ${esc(money(dd.high_52w))}</div>
          <div class="m">intraday high</div></div>
        <div class="row"><div class="k">off the highest close</div>
          <div class="v">${esc(pct(dd.from_all_time_high, 1, true))} from ${esc(money(dd.all_time_high))}</div>
          <div class="m">closing basis</div></div>
        <div class="row gap"><div class="k"><span class="mk open"></span>the two are not comparable</div>
          <div class="v">${esc(dd.caveat)}</div><div class="m">method</div></div>
        <div class="row"><div class="k">next-year EPS estimate</div>
          <div class="v">${esc(num(est.eps_fy1, 2))}, moved ${esc(pct(est.fy1_change_90d, 1, true))} in 90 days
            and ${esc(pct(est.fy1_change_30d, 1, true))} in 30</div>
          <div class="m">consensus</div></div>
        <div class="row"><div class="k">analysts moving, 30 days</div>
          <div class="v">${esc(num(est.analysts_up_30d, 0))} up, ${esc(num(est.analysts_down_30d, 0))} down</div>
          <div class="m">breadth</div></div>
        <div class="row"><div class="k">short interest</div>
          <div class="v">${has(v.short_pct_float) ? esc(pct(v.short_pct_float, 1)) + " of float" : "not reported"}</div>
          <div class="m">screen</div></div>
      </div>
      ${written}`;
  }

  /* -- 6. risks ----------------------------------------------------------- */
  function risksBlock(r) {
    const ri = r.risks;
    if (!ri.available) return `<div class="empty">${esc(ri.why)}</div>`;
    const killers = (ri.killers || []).map((k) => `<div class="row">
      <div class="k"><span class="mk down"></span>thesis killer</div>
      <div class="v">${esc(k)}</div><div class="m">named in the note</div></div>`).join("");
    const trig = has(ri.price_trigger)
      ? `<div class="row"><div class="k"><span class="mk down"></span>price trigger</div>
         <div class="v">${esc(money(ri.price_trigger, 0))}. Today ${esc(money(r.identity.price))}, which is
         ${esc(pct(r.identity.price / ri.price_trigger - 1, 0, true))} above it.</div>
         <div class="m">falsifier</div></div>`
      : `<div class="row gap"><div class="k"><span class="mk open"></span>price trigger</div>
         <div class="v">${esc(ri.price_trigger_note || "not set")}</div><div class="m">gap</div></div>`;
    return `<div class="ledger">${killers}${trig}</div>
      <div class="prose" style="margin-top:24px">${paragraphs(ri.bear_case)}</div>
      <div class="note">The bear case above is written to talk you out of the position. Source: ${esc(ri.source)}.</div>`;
  }

  /* -- 7. score ----------------------------------------------------------- */
  function scoreBlock(r) {
    const s = r.score;
    if (!s.available) return `<div class="empty">Not scored.</div>`;
    const qv = s.variants.quality_value;
    const contribs = Object.entries(qv.contributions).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    const maxAbs = Math.max(...contribs.map(([, v]) => Math.abs(v)), 0.01);
    const bars = contribs.map(([k, val]) => {
      const w = (Math.abs(val) / maxAbs) * 50;
      const style = val >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
      return `<div class="bar"><div class="k">${esc(k.replace(/_/g, " "))}</div>
        <div class="t"><span class="zero" style="left:50%"></span>
        <i class="${val < 0 ? "neg" : ""}" style="${style}"></i></div>
        <div class="v">${val >= 0 ? "+" : ""}${val.toFixed(3)}</div></div>`;
    }).join("");
    const variantRows = Object.entries(s.variants).map(([k, x]) => `<tr>
      <td>${esc(k.replace(/_/g, " "))}</td><td class="n">${esc(num(x.percentile, 1))}</td>
      <td class="n">${esc(pct(x.coverage, 0))}</td>
      <td class="muted" style="font-size:12px">${x.thinly_evidenced ? "thinly evidenced" : ""}</td></tr>`).join("");
    return `<div class="ledger">
        <div class="row"><div class="k">percentile, base score</div>
          <div class="v"><b>${esc(num(qv.percentile, 1))}</b> against ${esc(s.universe)}</div>
          <div class="m">quality_value</div></div>
        <div class="row"><div class="k">evidence coverage</div>
          <div class="v">${esc(pct(qv.coverage, 0))} of the score's weight is backed by a real observation${
            qv.missing.length ? ". Missing: " + esc(qv.missing.join(", ")) : "."}</div>
          <div class="m">${qv.thinly_evidenced ? "thin" : "full"}</div></div>
        <div class="row gap"><div class="k"><span class="mk open"></span>never backtested</div>
          <div class="v">${esc(s.caveat)}</div><div class="m">gap</div></div>
      </div>
      <div class="bars" style="margin-top:24px">${bars}</div>
      <div class="note">Each bar is one component's contribution to the raw score: its weight times
      its standardised value. Positive is to the right of the centre line.</div>
      <div class="scroll" style="margin-top:20px"><table>
        <thead><tr><th>variant</th><th class="n">percentile</th><th class="n">coverage</th><th></th></tr></thead>
        <tbody>${variantRows}</tbody></table></div>
      <div class="note">The three variants differ only in how they treat a drawdown. See the
        <a href="../../positioning/">positioning page</a> for why that argument is unresolved.</div>`;
  }

  /* -- 8. sources and gaps ------------------------------------------------ */
  function sourcesBlock(r) {
    const srcs = r.sources.map((s) => `<li>${s.url
      ? `<a href="${esc(s.url)}" rel="noopener noreferrer" target="_blank">${esc(s.title)}</a>`
      : esc(s.title)}${s.read_date ? `<span class="d">read ${esc(s.read_date)}</span>` : ""}</li>`).join("");
    const gaps = r.gaps.map((g) => `<li>${esc(g)}</li>`).join("");
    return `<div class="cols" style="display:grid;grid-template-columns:1fr 1fr;gap:44px">
      <div><h2 style="font-size:13px">What is not known<span class="sub">${r.gaps.length}</span></h2>
        <div class="rule soft"></div><ul class="gaps">${gaps}</ul></div>
      <div><h2 style="font-size:13px">Sources<span class="sub">${r.sources.length}</span></h2>
        <div class="rule soft"></div><ul class="srcs">${srcs}</ul></div>
    </div>`;
  }

  /* -- assembly ----------------------------------------------------------- */
  function render(r) {
    const id = r.identity;
    document.title = `${id.ticker} · Desk`;
    const depthLabel = { deep: "research note", screen: "screen only", universe: "universe" }[r.depth];
    const buckets = (r.valuation.available && r.valuation.buckets) || [];

    const chips = [
      ...buckets.map((b) => `<span class="chip on">${esc(b)}</span>`),
      r.score.available ? `<span class="chip">score ${esc(num(r.score.variants.quality_value.percentile, 0))}</span>` : "",
      `<span class="chip${r.depth === "deep" ? "" : " warn"}">${esc(depthLabel)}</span>`,
    ].filter(Boolean).join("");

    const html = `
${D.chrome("Analyse", "../../")}
<div class="plate reveal" style="--i:1">
  <div>
    <div class="tk">${esc(id.ticker)}</div>
    <div class="co">${esc(id.company)}</div>
    <div class="facts">
      <span>${esc(id.sector || "?")}</span><span>${esc(id.industry || "")}</span>
      <span>market cap <b>${esc(cap(id.market_cap_usd))}</b></span>
      ${r.valuation.available && r.valuation.next_earnings
        ? `<span>next report <b>${esc(r.valuation.next_earnings)}</b></span>` : ""}
    </div>
    <div class="chips" style="margin-top:12px">${chips}</div>
  </div>
  <div class="price">
    <div class="n"><small>$</small>${esc(num(id.price, 2))}</div>
    <div class="when">close ${esc(id.as_of)} &middot; ${esc(id.currency || "USD")}</div>
  </div>
</div>

${callBlock(r)}

${r.business.available ? `<section class="reveal" style="--i:3">
  ${sectionHead("What the business does", r.business.source)}
  <div class="lede">${paragraphs(r.business.text)}</div>
</section>` : `<section class="reveal" style="--i:3">
  ${sectionHead("What the business does", "not available")}
  <div class="empty">${esc(r.business.why)}${r.business.industry ? ` The screen classifies it as ${esc(r.business.industry)}.` : ""}</div>
</section>`}

<section class="reveal" style="--i:4">
  ${sectionHead("What changed since the last report", "as of " + esc(r.identity.as_of))}
  ${changedBlock(r)}
</section>

<section class="reveal" style="--i:5">
  ${sectionHead("The numbers, four fiscal years", r.trends.length ? r.trends[0].source : "screen")}
  ${trendCards(r)}
</section>

<section class="reveal" style="--i:6">
  ${sectionHead("Screen measures", r.fundamentals.source)}
  ${fundamentalsTable(r)}
</section>

<section class="reveal" style="--i:7">
  ${sectionHead("What it is worth", "valuation")}
  ${valuationBlock(r)}
</section>

<section class="reveal" style="--i:8">
  ${sectionHead("What would break it", "risks and thesis killers")}
  ${risksBlock(r)}
</section>

${r.thesis.available ? `<section class="reveal" style="--i:9">
  ${sectionHead("Why it is on the list", r.thesis.source)}
  <div class="prose">${paragraphs(r.thesis.why_on_the_list)}</div>
  <h2 style="font-size:13px;margin-top:28px">Business quality</h2><div class="rule soft"></div>
  <div class="prose">${paragraphs(r.thesis.business_quality)}</div>
</section>` : ""}

<section class="reveal" style="--i:10">
  ${sectionHead("Where the score comes from", "quality_value, 150 names")}
  ${scoreBlock(r)}
</section>

<section class="reveal" style="--i:11">
  ${sectionHead("Provenance", "built " + esc(r.built_at))}
  ${sourcesBlock(r)}
  <div class="toolbar" style="margin-top:26px">${D.themeBar()}</div>
</section>

<footer>${esc(r.disclaimer)}</footer>`;

    document.body.innerHTML = `<div class="page">${html}</div>`;
    const st = document.getElementById("status");
    if (st) st.innerHTML = `snapshot ${esc(r.identity.as_of)}`;
    D.wireTheme(document);
  }

  function boot() {
    if (!TICKER) return D.fail("No ticker", "This page was generated without a ticker.");
    fetch(`../../analysis/${TICKER}.json`, { cache: "no-store" })
      .then((res) => { if (!res.ok) throw new Error(res.status + " " + res.statusText); return res.json(); })
      .then(render)
      .catch((e) => D.fail(`No analysis for ${TICKER}`,
        `Could not load analysis/${TICKER}.json (${e.message}). Run "python scripts/build_analysis.py" and reload.`));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
