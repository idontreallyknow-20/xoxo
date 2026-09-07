/* Renders /paper/ from paper.json: the swing rules replayed on real closes with fake money.
 *
 * Order, and why:
 *   1  the verdict, first, in one sentence, so nobody reads a table before the answer
 *   2  the data and its audit gate: what was replayed, what was excluded, what the basis is
 *   3  the held-out window, if it has been run: pre-registered arms against the random control
 *   4  the train window: every arm ever run, the in-sample grid, labelled as in-sample
 *   5  the limitations, verbatim, because a result cannot exist without them
 *
 * The random control column is the one that matters. An arm's own interval says whether
 * its mean is distinguishable from zero; the control says whether it is distinguishable
 * from picking names at random with the same stop and horizon. Most rules are not.
 */
(function () {
  const D = window.Desk;
  const { esc, has, num, pct } = D;

  const S = { p: null };

  function head(title, sub) {
    return `<h2>${esc(title)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</h2><div class="rule"></div>`;
  }

  function bp(v) { return has(v) ? ((v > 0 ? "+" : "") + (v * 10000).toFixed(0) + " bp") : "n/a"; }
  function money0(v) { return has(v) ? "$" + num(v, 0) : "n/a"; }

  /* -- 1. verdict ------------------------------------------------------- */
  function verdictBlock() {
    const p = S.p;
    if (p.status !== "RUN") {
      return `<div class="callout none reveal" style="--i:1">
        <div class="kicker">status</div>
        <p class="line"><span class="lab">not run</span>${esc(p.why_not_run || "No paper-trading run has been recorded.")}</p>
      </div>`;
    }
    const t = p.test;
    return `<div class="callout reveal" style="--i:1">
      <div class="kicker">verdict, ${esc(p.data_scope === "full" ? "real 2013 to 2018 closes" : "fixture only")}</div>
      <p class="line"><span class="lab">held-out window</span>${esc(p.verdict)}</p>
      <p class="line"><span class="lab">in full</span>${esc(p.verdict_sentence || "")}</p>
      <div class="meta">
        <span>hypotheses counted <b>${esc(has(t) ? t.hypotheses_tested : (p.train ? p.train.hypotheses_tested : "n/a"))}</b></span>
        <span>train runs <b>${esc(p.lab ? p.lab.train_runs : "n/a")}</b></span>
        <span>pre-registered <b>${esc(p.preregistration && p.preregistration.arm_ids.length ? p.preregistration.arm_ids.join(", ") : "nothing yet")}</b></span>
        <span>log <b>${esc(p.lab ? p.lab.log : "lab/LAB.md")}</b></span>
      </div>
    </div>`;
  }

  /* -- 2. data ------------------------------------------------------------ */
  function dataBlock() {
    const d = S.p.data;
    if (!d) return "";
    const m = d.membership || {};
    const wc = d.worst_cross_check;
    const rows = [
      ["source", d.source],
      ["window", `${d.first} to ${d.last}, ${d.sessions} sessions, ${d.tickers} names, ${has(d.eligible_cells) ? num(d.eligible_cells, 0) + " eligible name-days" : ""}`],
      ["basis", d.basis],
      ["adjustment audit", `${d.dataset_verdict}; gate ${d.quality_gate}`],
      ["worst cross-check", wc ? `${wc.ticker} ${wc.a} vs ${wc.b}: daily-return disagreement median ${num(wc.median_abs_rel_diff, 5)}, max ${num(wc.max_abs_rel_diff, 4)}` : "n/a"],
      ["membership", has(m.members_on_first_session) ? `${m.members_on_first_session} of ${m.n_tickers} were in the index on the first session, ${m.members_on_last_session} on the last; a name is eligible only while a member` : "not applied"],
      ["excluded as data artefacts", (d.excluded_as_suspect || []).length ? d.excluded_as_suspect.join(", ") : "none"],
      ["rules", S.p.rules ? `sleeve ${money0(S.p.rules.sleeve_usd)}, risk ${money0(S.p.rules.risk_usd)} per trade, position cap ${money0(S.p.rules.max_position_usd)}, ${S.p.rules.max_open} open, ${S.p.rules.max_per_sector} per sector` : "n/a"],
      ["costs", S.p.costs ? `${S.p.costs.commission_bps} bp commission + ${S.p.costs.slippage_bps} bp slippage each way` : "n/a"],
      ["split", `train ${S.p.split.train.join(" to ")}; held out ${S.p.split.test.join(" to ")}`],
    ];
    return `<section class="reveal" style="--i:2">
      ${head("What was replayed", "and the audit it passed")}
      <div class="ledger">${rows.map(([k, v]) => `<div class="row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span><span></span></div>`).join("")}</div>
    </section>`;
  }

  /* -- 3/4. arm tables ---------------------------------------------------- */
  function armTable(block, label, i) {
    if (!block || !block.arms) return "";
    const rule = block.arms.filter((a) => a.arm.kind !== "spy_hold");
    const spy = block.arms.find((a) => a.arm.kind === "spy_hold");
    const row = (a) => {
      const ci = a.excess_ci || [null, null];
      const n = a.null || {};
      const ok = a.decision_rule && a.decision_rule.survives;
      return `<tr>
        <td class="tk">${esc(a.arm.id)}${a.arm.preregistered ? ' <span class="m">pre-registered</span>' : ""}</td>
        <td class="dim">${esc(a.arm.label.split(":")[0].split(" (")[0])}${(a.arm.params && a.arm.params.filters && a.arm.params.filters.length) ? ` + ${esc(a.arm.params.filters.join(", "))}` : ""}</td>
        <td>${num(a.n_trades, 0)}</td>
        <td>${pct(a.hit_rate, 1)}</td>
        <td>${bp(a.mean_excess_vs_spy)}</td>
        <td class="dim">${has(ci[0]) ? `${bp(ci[0])} to ${bp(ci[1])}` : "n/a"}</td>
        <td>${bp(n.p95)}</td>
        <td>${has(n.percentile_of_arm) ? num(n.percentile_of_arm, 2) : "n/a"}</td>
        <td>${esc(a.verdict)}</td>
        <td>${a.ledger ? money0(a.ledger.final_equity) : "n/a"}</td>
        <td>${ok ? "survives" : ""}</td>
      </tr>`;
    };
    return `<section class="reveal" style="--i:${i}">
      ${head(label, `${rule.length} rule arms, window ${block.window.join(" to ")}${spy && spy.ledger ? `; SPY held: ${money0(spy.ledger.final_equity)}` : ""}`)}
      <p class="dim" style="font-size:14px;margin:0 0 12px">${esc(block.verdict_sentence || "")}</p>
      <div class="scroll"><table>
        <thead><tr><th>arm</th><th>what it is</th><th>trades</th><th>hit</th><th>mean excess vs SPY, per trade</th>
        <th>95% interval</th><th>random control p95</th><th>percentile in the control</th><th>verdict</th><th>sleeve after</th><th></th></tr></thead>
        <tbody>${rule.map(row).join("")}</tbody>
      </table></div>
      ${block.flags && block.flags.length ? `<p class="dim" style="font-size:13px">flags: ${esc(block.flags.join("; "))}</p>` : ""}
    </section>`;
  }

  /* -- 5. limitations ----------------------------------------------------- */
  function limitations() {
    const p = S.p;
    const lim = (p.limitations || []).map((x) => `<li>${esc(x)}</li>`).join("");
    const nt = p.arms_not_testable || {};
    return `<section class="reveal" style="--i:5">
      ${head("Limitations", "a result cannot exist without them")}
      <ul class="howto">${lim}${Object.keys(nt).map((k) => `<li>${esc(k)}: ${esc(nt[k])}</li>`).join("")}</ul>
      <p class="dim" style="font-size:13px;margin-top:18px">${esc(p.disclaimer || "")}</p>
    </section>`;
  }

  function render() {
    const p = S.p;
    document.title = "Paper · Desk";
    document.body.innerHTML = `<div class="page">
${D.chrome("Paper", "../")}

<div class="plate reveal" style="--i:0">
  <div>
    <div class="tk">Paper</div>
    <div class="co">${esc(p.what_this_is || "The swing rules replayed on real closes with fake money.")}</div>
    <div class="facts"><span>status <b>${esc(p.status)}</b></span>
      <span>real data <b>${p.is_real ? "yes" : "no"}</b></span>
      <span>built <b>${esc(p.built_at)}</b></span></div>
  </div>
</div>

${verdictBlock()}
${dataBlock()}
${armTable(p.test, "Held-out window", 3)}
${armTable(p.train, "Train window, in-sample", 4)}
${limitations()}
<div class="toolbar" style="margin-top:26px">${D.themeBar()}</div>
</div>`;
    D.wireTheme(document);
  }

  function boot() {
    fetch("../paper.json", { cache: "no-store" }).then((r) => r.json()).then((p) => { S.p = p; render(); })
      .catch((e) => D.fail("Paper data not built", `Run "python scripts/paper_trade.py" and reload. (${e.message})`));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
