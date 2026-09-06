/* Renders /positioning/ from positioning.json, scorecard.json and backtest.json.
 *
 * The page has one job: let someone decide what to look at next, while making it
 * impossible to mistake the ranking for a tested edge. So the first thing on it,
 * above the memo and above the table, is the status of the score, and it says
 * plainly that it has never been validated.
 *
 * Order:
 *   1  what the score's status actually is
 *   2  the memo: names with a written falsifier, ranked, sized by the rules
 *   3  the research queue: names the score likes that nobody has read
 *   4  where the score does not work
 *   5  the scorecard, all 150, searchable
 *   6  the portfolio rules, checked mechanically
 */
(function () {
  const D = window.Desk;
  const { esc, has, num, pct, mult, money, cap } = D;

  const S = { memo: null, card: null, back: null, variant: "quality_value", q: "", sort: "score" };

  function head(title, sub) {
    return `<h2>${esc(title)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</h2><div class="rule"></div>`;
  }

  /* -- 1. status ---------------------------------------------------------- */
  function statusBlock() {
    const st = S.memo.status_of_the_score;
    const cal = (S.back && S.back.engine_calibration) || null;
    return `<div class="callout reveal" style="--i:1">
      <div class="kicker">status of the score, ${esc(S.memo.snapshot_date)}</div>
      <p class="line"><span class="lab">has it been validated</span>No. ${esc(st.statement)}</p>
      <p class="line"><span class="lab">what that means for this page</span>The ordering below is a
      view about which companies look better on measurable characteristics today. It is not evidence
      that the ordering predicts anything, and it should not be treated as one.</p>
      ${cal ? `<div class="meta">
        <span>engine calibrated on <b>${cal.cases.length}</b> synthetic cases</span>
        <span>its own false-positive rate <b>${esc(pct(cal.measured_false_positive_rate, 1))}</b> against a nominal 5%</span>
      </div>` : ""}
    </div>`;
  }

  /* -- 2. the memo -------------------------------------------------------- */
  function candidate(c) {
    const band = c.suggested_band_usd;
    const chips = [
      ...(c.buckets || []).map((b) => `<span class="chip on">${esc(b)}</span>`),
      `<span class="chip">${esc(num(c.percentile, 0))}th pctile</span>`,
      c.thinly_evidenced ? `<span class="chip warn">thin evidence</span>` : "",
      c.depth === "screen" ? `<span class="chip warn">no filing read</span>` : "",
      c.peer_verdict && c.peer_verdict !== "in the middle"
        ? `<span class="chip">${esc(c.peer_verdict)}</span>` : "",
    ].filter(Boolean).join("");

    const why = c.reasoning.map((r) => `<p>${esc(r)}</p>`).join("");
    const wrong = c.what_would_be_wrong.map((w) => typeof w === "string"
      ? `<li>${esc(w)}</li>`
      : `<li>${esc(w.text)}<span class="src">${esc(w.source)}${
          w.date ? ", " + esc(w.date) : ""}</span></li>`).join("");
    const logged = c.already_logged;

    return `<article class="cand">
      <div class="ch">
        <div class="cn"><span class="r mono">${String(c.rank).padStart(2, "0")}</span>
          <a href="../analyze/${esc(c.ticker)}/"><span class="tk">${esc(c.ticker)}</span></a>
          <span class="co">${esc(c.company || "")}</span></div>
        <div class="chips">${chips}</div>
      </div>
      <div class="cb">
        <div class="prose">${why}</div>
        <div class="cs">
          <div class="kv"><span>price</span><b>${esc(money(c.price))}</b></div>
          <div class="kv"><span>forward P/E</span><b>${esc(mult(c.forward_pe))}</b></div>
          <div class="kv"><span>vs own median</span><b>${has(c.pe_vs_median) ? esc(pct(c.pe_vs_median, 0, true)) : "n/a"}</b></div>
          <div class="kv"><span>off 52w high</span><b>${esc(pct(c.dd_52w, 0, true))}</b></div>
          <div class="kv"><span>next-year estimate, 90d</span><b>${esc(pct(c.revisions_90d, 1, true))}</b></div>
          <div class="kv"><span>next report</span><b>${esc(c.next_earnings || "n/a")}</b></div>
          <div class="kv"><span>evidence coverage</span><b>${esc(pct(c.coverage, 0))}</b></div>
          ${has(c.peer_gap) ? `<div class="kv"><span>peer gap, quality less price</span><b>${
            c.peer_gap >= 0 ? "+" : ""}${esc(num(c.peer_gap * 100, 0))} pts</b></div>` : ""}
        </div>
      </div>
      <div class="cf">
        <div class="cfx">
          <div class="lab">what would say this is wrong</div>
          <ul class="kill">${wrong}</ul>
        </div>
        <div class="cfz">
          <div class="lab">size, from the rules not from conviction</div>
          <div class="amt mono">${band ? `${esc(money(band[0], 0))} to ${esc(money(band[1], 0))}` : "not sized"}</div>
          ${(c.constraint_notes || []).map((n) => `<div class="m">${esc(n)}</div>`).join("")}
          ${logged ? `<div class="m">Already logged ${esc(logged.date)}: ${esc(logged.action || "")}${
            has(logged.target_usd) ? `, ${esc(money(logged.target_usd, 0))}` : ""}.</div>` : ""}
          <div class="m">Confidence: ${esc(c.confidence)}.</div>
        </div>
      </div>
    </article>`;
  }

  /* -- 3. research queue -------------------------------------------------- */
  function queueTable() {
    const rows = S.memo.research_queue.map((c) => `<tr>
      <td class="n mono">${String(c.rank).padStart(2, "0")}</td>
      <td><a href="../analyze/${esc(c.ticker)}/"><span class="tk">${esc(c.ticker)}</span></a></td>
      <td>${esc(c.company || "")}</td>
      <td class="muted" style="font-size:12px">${esc(c.sector || "")}</td>
      <td class="n">${esc(num(c.percentile, 1))}</td>
      <td class="n">${esc(mult(c.forward_pe))}</td>
      <td class="n">${esc(pct(c.revisions_90d, 1, true))}</td>
      <td class="n">${esc(pct(c.dd_52w, 0, true))}</td>
      <td class="muted" style="font-size:11px">${esc(c.peer_verdict || "")}</td>
    </tr>`).join("");
    return `<div class="note warn">${esc(S.memo.research_queue_note)}</div>
      <div class="scroll"><table><thead><tr>
        <th class="n">#</th><th>ticker</th><th>company</th><th>sector</th>
        <th class="n">pctile</th><th class="n">fwd P/E</th><th class="n">est 90d</th><th class="n">off high</th>
        <th>against peers</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  /* -- 3b. read and declined ----------------------------------------------
   * The disagreement between the score and the reading is the most informative
   * thing on this page, so it gets its own section instead of being filtered out
   * silently. Applied Materials ranks in the top quartile and the note says Pass. */
  function declinedTable() {
    const rows = (S.memo.reviewed_and_declined || []);
    if (!rows.length) return `<div class="empty">Every researched name concluded to buy.</div>`;
    const body = rows.map((c) => `<tr>
      <td><a href="../analyze/${esc(c.ticker)}/"><span class="tk">${esc(c.ticker)}</span></a></td>
      <td>${esc(c.company || "")}</td>
      <td><span class="chip">${esc(c.stance)}</span></td>
      <td>${esc(c.verdict_text || "")}</td>
      <td class="n">${esc(num(c.percentile, 1))}</td>
      <td class="n">${esc(mult(c.forward_pe))}</td>
      <td class="n">${esc(pct(c.revisions_90d, 1, true))}</td>
      <td class="muted" style="font-size:11px">${esc(c.peer_verdict || "")}</td>
    </tr>`).join("");
    const gaps = rows.filter((c) => c.percentile >= 60).map((c) =>
      `${esc(c.ticker)} ranks at the ${esc(num(c.percentile, 0))}th percentile and the note says "${esc(c.verdict_text)}"`);
    return `<div class="note warn">${esc(S.memo.reviewed_and_declined_note)}</div>
      <div class="scroll"><table><thead><tr>
        <th>ticker</th><th>company</th><th>stance</th><th>the note's verdict</th>
        <th class="n">pctile</th><th class="n">fwd P/E</th><th class="n">est 90d</th><th>against peers</th>
      </tr></thead><tbody>${body}</tbody></table></div>
      ${gaps.length ? `<div class="note">Where the score and the reading disagree most: ${
        esc(gaps.join("; "))}. Not sized either way; the disagreement is the finding.</div>` : ""}`;
  }

  /* -- 4. where it does not work ------------------------------------------ */
  function failsBlock() {
    if (!S.back) return `<div class="empty">backtest.json not loaded.</div>`;
    const st = S.back.score_structure && S.back.score_structure[S.variant];
    const cal = S.back.engine_calibration;
    const why = (S.back.why_not_run || []).map((x) => `<li>${esc(x)}</li>`).join("");

    const redundant = st ? (st.redundant_pairs || []).map((p) => `<div class="row">
      <div class="k">${esc(p.a)} and ${esc(p.b)}</div>
      <div class="v">correlate ${esc(num(p.rho, 2))}, carrying ${esc(pct(p.combined_weight, 0))} of the
        weight between them on what is largely one idea</div>
      <div class="m">redundant</div></div>`).join("") : "";

    const notes = st ? (st.notes || []).map((n) => `<div class="row gap">
      <div class="k"><span class="mk open"></span>structure</div>
      <div class="v">${esc(n)}</div><div class="m">measured</div></div>`).join("") : "";

    const calRows = cal ? cal.cases.map((c) => `<tr>
      <td>${esc(c.case)}</td>
      <td class="n">${has(c.planted_alpha) ? esc(num(c.planted_alpha, 3)) : "n/a"}</td>
      <td class="n">${has(c.expected_rank_ic) ? esc(num(c.expected_rank_ic, 3)) : "n/a"}</td>
      <td class="n">${has(c.measured_rank_ic) ? esc(num(c.measured_rank_ic, 3)) : "n/a"}</td>
      <td>${esc(c.verdict)}</td>
      <td class="muted" style="font-size:12px">${esc((c.flags || [])[0] ? "flagged" : "")}</td>
    </tr>`).join("") : "";

    const va = S.memo.variant_agreement;
    const overlap = Object.entries(va.overlap || {}).map(([k, v]) => `<div class="row">
      <div class="k">${esc(k.replace(/_/g, " "))}</div>
      <div class="v">share <b>${v}</b> of their top ${va.top_n}</div>
      <div class="m">overlap</div></div>`).join("");

    // How long until this could say anything. The most useful thing on the page: it
    // turns "no backtest yet" from an apology into a schedule.
    const pw = S.back.how_long_until_this_can_say_anything;
    let powerBlock = "";
    if (pw) {
      const rows = pw.smallest_detectable_rank_ic.map((r) => `<tr>
        <td class="n">${r.quarters}</td>
        <td class="n">${esc(num(r.years, 1))}</td>
        <td class="n">${esc(num(r.quarters / 12, 1))}</td>
        <td class="n">${esc(num(r.detectable_ic, 3))}</td>
        <td>${r.detects["a typical published signal"] ? "yes" : '<span class="muted">no</span>'}</td>
        <td>${r.detects["a strong published cross-sectional signal"] ? "yes" : '<span class="muted">no</span>'}</td>
      </tr>`).join("");
      const meas = Object.entries(pw.measured_detection_rate).map(([k, v]) => `<tr>
        <td class="n">${esc(k)}</td>
        <td class="n">${esc(pct(v["0.04"], 0))}</td>
        <td class="n">${esc(pct(v["0.06"], 0))}</td></tr>`).join("");
      const c = pw.cadence;
      powerBlock = `
        <h2 style="font-size:13px;margin-top:34px">How long until this could say anything
          <span class="sub">answered, not guessed</span></h2>
        <div class="rule soft"></div>
        <div class="note">${esc(pw.question)}</div>
        <div class="callout" style="margin-top:18px">
          <div class="kicker">what to do about it today</div>
          <p class="line">${esc(c.recommendation)}</p>
          <div class="meta">
            <span>quarterly: first verdict in <b>${esc(c.quarterly.years_to_first_verdict)} years</b></span>
            <span>monthly: first verdict in <b>${esc(c.monthly.years_to_first_verdict)} years</b></span>
          </div>
        </div>
        <div class="scroll" style="margin-top:20px"><table><thead><tr>
          <th class="n">rebalances</th><th class="n">years, quarterly</th><th class="n">years, monthly</th>
          <th class="n">smallest detectable IC</th><th>sees a typical signal</th><th>sees a strong one</th>
        </tr></thead><tbody>${rows}</tbody></table></div>
        <div class="scroll" style="margin-top:20px"><table><thead><tr>
          <th class="n">rebalances</th><th class="n">detected, typical signal</th>
          <th class="n">detected, strong signal</th></tr></thead><tbody>${meas}</tbody></table></div>
        <div class="note">${esc(pw.measured_how)} ${esc(pw.why_zero_below_the_floor)}</div>
        <div class="note warn">${esc(c.caveat)}</div>`;
    }

    return `
      <h2 style="font-size:13px">There is no backtest, and here is why<span class="sub">status ${esc(S.back.status)}</span></h2>
      <div class="rule soft"></div>
      <ul class="gaps">${why}</ul>
      ${powerBlock}

      <h2 style="font-size:13px;margin-top:34px">What the score is made of, measured on one cross section<span class="sub">${esc(S.variant)}</span></h2>
      <div class="rule soft"></div>
      <div class="ledger">${redundant}${notes}</div>

      <h2 style="font-size:13px;margin-top:34px">The two price variants disagree on purpose<span class="sub">and barely change the answer</span></h2>
      <div class="rule soft"></div>
      <div class="ledger">${overlap}</div>
      <div class="note">${esc(va.note)}</div>

      <h2 style="font-size:13px;margin-top:34px">The engine that would run the test, calibrated<span class="sub">synthetic panels, known answers</span></h2>
      <div class="rule soft"></div>
      <div class="scroll"><table><thead><tr>
        <th>case</th><th class="n">planted alpha</th><th class="n">expected IC</th>
        <th class="n">measured IC</th><th>verdict</th><th></th></tr></thead>
        <tbody>${calRows}</tbody></table></div>
      <div class="note">${esc(cal ? cal.note : "")} Its own false-positive rate against panels with no
        signal at all is ${esc(pct(cal ? cal.measured_false_positive_rate : null, 1))}, against a
        nominal 5%: roughly one finding in thirteen at the suggestive level is nothing.</div>`;
  }

  /* -- 5. scorecard ------------------------------------------------------- */
  function scorecard() {
    const v = S.card.variants[S.variant];
    let rows = v.rows;
    if (S.q) {
      const q = S.q.toLowerCase();
      rows = rows.filter((r) => (r.ticker + " " + (r.company || "") + " " + (r.sector || "")).toLowerCase().includes(q));
    }
    const body = rows.map((r, i) => `<tr>
      <td class="n mono muted">${i + 1}</td>
      <td><a href="../analyze/${esc(r.ticker)}/"><span class="tk">${esc(r.ticker)}</span></a></td>
      <td>${esc(r.company || "")}</td>
      <td class="muted" style="font-size:12px">${esc(r.sector || "")}</td>
      <td class="n">${esc(num(r.percentile, 1))}</td>
      <td class="n ${r.thinly_evidenced ? "down" : "muted"}">${esc(pct(r.coverage, 0))}</td>
      <td class="n">${esc(num(r.quality_screen_score, 1))}</td>
      <td class="n">${esc(mult(r.forward_pe))}</td>
      <td class="n">${has(r.pe_vs_median) ? esc(pct(r.pe_vs_median, 0, true)) : '<span class="muted">n/a</span>'}</td>
      <td class="n">${esc(pct(r.revisions_90d, 1, true))}</td>
      <td class="n">${esc(pct(r.dd_52w, 0, true))}</td>
      <td class="muted" style="font-size:11px">${esc((r.buckets || []).join(", "))}</td>
    </tr>`).join("");
    return `<div class="toolbar">
        <div class="seg-btns" id="variants">${Object.keys(S.card.variants).map((k) =>
          `<button data-variant="${k}"${k === S.variant ? ' class="on"' : ""}>${esc(k.replace(/_/g, " "))}</button>`).join("")}</div>
        <input type="search" id="q" placeholder="find a ticker, company or sector" value="${esc(S.q)}">
        <span class="right muted mono" style="font-size:12px">${rows.length} of ${v.rows.length}</span>
      </div>
      <div class="scroll"><table><thead><tr>
        <th class="n">#</th><th>ticker</th><th>company</th><th>sector</th><th class="n">pctile</th>
        <th class="n">coverage</th><th class="n">quality screen</th><th class="n">fwd P/E</th>
        <th class="n">vs median</th><th class="n">est 90d</th><th class="n">off high</th><th>bucket</th>
      </tr></thead><tbody>${body}</tbody></table></div>
      <div class="note">${esc(S.card.caveat)}</div>`;
  }

  /* -- 6. rules ----------------------------------------------------------- */
  function rulesBlock() {
    const p = S.memo.portfolio;
    const rows = S.memo.constraints.map((c) => `<div class="row">
      <div class="k"><span class="mk ${c.status === "ok" ? "up" : "open"}"></span>${esc(c.rule)}</div>
      <div class="v">${esc(c.detail)}</div>
      <div class="m">${esc(c.status)}</div></div>`).join("");
    const calls = (p.logged_calls || []).map((c) => `<tr>
      <td class="mono muted" style="font-size:12px">${esc(c.date)}</td>
      <td><a href="../analyze/${esc(c.ticker)}/"><span class="tk">${esc(c.ticker)}</span></a></td>
      <td>${esc(c.action || "")}</td>
      <td class="n">${has(c.target_usd) ? esc(money(c.target_usd, 0)) : "n/a"}</td>
      <td class="n">${esc(num(c.conviction, 0))}</td>
      <td class="muted" style="font-size:12px">${esc(c.bucket || "")}</td>
      <td style="font-size:13px">${esc(c.wrong_if || "")}</td>
    </tr>`).join("");
    return `<div class="ledger">
        <div class="row"><div class="k">holdings source</div><div class="v">${esc(p.source)}</div><div class="m">input</div></div>
        <div class="row"><div class="k">cash</div><div class="v">${esc(money(p.cash_usd, 0))} of ${esc(money(p.total_usd, 0))}</div><div class="m">ledger</div></div>
        ${rows}
      </div>
      <div class="note">Rules from ${esc(S.memo.rules.source)}. ${esc(p.note)}</div>
      ${calls ? `<h2 style="font-size:13px;margin-top:34px">Calls already logged<span class="sub">journal.md</span></h2>
        <div class="rule soft"></div>
        <div class="scroll"><table><thead><tr><th>date</th><th>ticker</th><th>call</th>
          <th class="n">size</th><th class="n">conv</th><th>bucket</th><th>wrong if</th></tr></thead>
          <tbody>${calls}</tbody></table></div>` : ""}`;
  }

  /* -- assembly ----------------------------------------------------------- */
  function render() {
    const m = S.memo;
    document.title = "Positioning · Desk";
    const how = m.how_to_read_this.map((x) => `<li>${esc(x)}</li>`).join("");

    document.body.innerHTML = `<div class="page">
${D.chrome("Positioning", "../")}

<div class="plate reveal" style="--i:0">
  <div>
    <div class="tk">Positioning</div>
    <div class="co">A scoring model, an honest account of what validating it would take, and a ranked memo.</div>
    <div class="facts"><span>snapshot <b>${esc(m.snapshot_date)}</b></span>
      <span>universe <b>${esc(S.card.n_names)} names</b></span>
      <span>built <b>${esc(m.built_at)}</b></span></div>
  </div>
</div>

${statusBlock()}

<section class="reveal" style="--i:2">
  ${head("How to read this", "before the list")}
  <ul class="howto">${how}</ul>
</section>

<section class="reveal" style="--i:3">
  ${head("Candidates with a written falsifier", `${m.candidates.length} names, ranked by ${esc(m.variant).replace(/_/g, " ")}`)}
  <div class="cands">${m.candidates.map(candidate).join("")}</div>
</section>

<section class="reveal" style="--i:4">
  ${head("Read, and declined", `${(m.reviewed_and_declined || []).length} names the note says not to buy`)}
  ${declinedTable()}
</section>

<section class="reveal" style="--i:5">
  ${head("The research queue", `${m.research_queue.length} names the score likes that nobody has read`)}
  ${queueTable()}
</section>

<section class="reveal" style="--i:6">
  ${head("Where the score does not work", "and what would be needed to know")}
  ${failsBlock()}
</section>

<section class="reveal" style="--i:7">
  ${head("The scorecard", `all ${S.card.n_names} names`)}
  <div id="card">${scorecard()}</div>
</section>

<section class="reveal" style="--i:8">
  ${head("The rules, checked", esc(m.rules.source))}
  ${rulesBlock()}
  <div class="toolbar" style="margin-top:26px">${D.themeBar()}</div>
</section>

<footer>${esc(m.disclaimer)}</footer>
</div>`;

    const st = document.getElementById("status");
    if (st) st.innerHTML = `snapshot ${esc(m.snapshot_date)}`;
    D.wireTheme(document);
    wireCard();
  }

  function wireCard() {
    const box = document.getElementById("card");
    if (!box) return;
    box.querySelectorAll("[data-variant]").forEach((b) => b.addEventListener("click", () => {
      S.variant = b.dataset.variant;
      box.innerHTML = scorecard();
      wireCard();
    }));
    const q = document.getElementById("q");
    if (q) {
      q.addEventListener("input", () => {
        S.q = q.value;
        box.innerHTML = scorecard();
        wireCard();
        const nq = document.getElementById("q");
        if (nq) { nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); }
      });
    }
  }

  function boot() {
    Promise.all([
      fetch("../positioning.json", { cache: "no-store" }).then((r) => r.json()),
      fetch("../scorecard.json", { cache: "no-store" }).then((r) => r.json()),
      fetch("../backtest.json", { cache: "no-store" }).then((r) => r.json()).catch(() => null),
    ]).then(([memo, card, back]) => {
      S.memo = memo; S.card = card; S.back = back;
      render();
    }).catch((e) => D.fail("Positioning data not built",
      `Run "python scripts/build_positioning.py" and reload. (${e.message})`));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
