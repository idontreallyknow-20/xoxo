# HANDOFF

For whoever picks this up next, human or model. `SUMMARY.md` is written for Joseph; this one is
written for the next person to touch the code.

Branch `claude/stock-analysis-scoring-xhuyl9`, PR
[#1](https://github.com/idontreallyknow-20/xoxo/pull/1). No CI is configured on this repo, so the
gate is local: see **Before you commit** below.

---

## The one thing that will confuse you

**This repo is not a Next.js app,** whatever the original brief said. There is no `package.json`, no
`/app`, no `FINNHUB_KEY`. It is a Python pipeline plus one static page, `dashboard/index.html`,
served by `scripts/serve.py` (`http.server.SimpleHTTPRequestHandler` rooted at `dashboard/`).

`/analyze/<TICKER>` and `/positioning` work because they are real directories with an `index.html`
inside them. That is the whole routing story. No server change was needed and none was made.

**The build environment has no network access to any market-data host.** `finnhub.io`,
`data.sec.gov` and `www.sec.gov` are refused at the egress proxy. `scripts/an/edgar.py`,
`finnhub.py` and `prices.py` have therefore **never made a real request**. They are written to
documented API shapes and tested against fixtures. Section 5b of `NOTES.md` lists what each one's
author flagged as most likely to be wrong on the first live run. Do not treat them as verified.

---

## Layout

```
scripts/an/          the analysis layer. Everything new lives here so the original
                     pipeline is untouched.
  paths.py           paths only; deliberately does not import config.py, which
                     creates directories on import
  store.py           atomic JSON cache with a TTL and an offline mode
  http.py            Transport protocol + Http/Fixture/DryRun/Recording transports
  local.py           the two universe CSVs -> typed, NaN-free dataclasses
  research_md.py     research/*.md -> structure. Strict: a malformed note raises
  journal.py         journal.md -> entries, one per ticker even in a shared heading
  edgar.py           SEC EDGAR. as_known_on() is the point-in-time primitive
  finnhub.py         free-tier endpoints only, key from FINNHUB_KEY env only
  prices.py          daily closes, with the four ways a price panel silently cheats
  stats.py           robust (median/MAD) standardisation, block bootstrap
  score.py           the 13-component score and three variants
  metrics.py         Trend: elapsed-year periods, no percentage across zero
  peers.py           valuation against peers, and against what quality justifies
  diagnostics.py     what one cross section can honestly say about the score
  backtest.py        the engine. A result cannot exist without limitations
  synthetic.py       panels with a known answer, for calibrating the engine
  power.py           how long until a backtest here could detect anything
  snapshots.py       dated archives, the thing that makes a real backtest possible
  positioning.py     the memo: three lists, sized from criteria.md
  analysis.py        assembles the record each page renders
  pages.py           writes the static page shells

scripts/build_*.py   the generators. scripts/build_all.py runs all three
scripts/fetch_*.py   the live pulls. Both have --dry-run
scripts/snapshot.py  run this MONTHLY (see power.py for why)
scripts/shoot.py     headless Chromium: console errors, overflow, screenshots

dashboard/assets/    desk.css (theme tokens copied verbatim from index.html),
                     desk-common.js, analyze.js, analyze-index.js, positioning.js
dashboard/analysis/  generated per-ticker JSON + _narrative/ (quote-verified)
```

---

## Conventions that are load-bearing

**Missing is missing.** `None`, never `0.0`, never `NaN`. A NaN that reaches a ranking silently
mis-sorts it, and a zero that means "unknown" reads as "measured and neutral". Both have bitten this
codebase; both have regression tests.

**Provenance travels with the number.** Nothing renders unless it is in the analysis record with a
`source` and an `as_of`. There is exactly one place to look when something is wrong.

**Say the sample size.** Every statistic carries the n it was computed on. `spearman` returns
`(rho, n)` as a tuple specifically so the count cannot be dropped.

**A result cannot exist without limitations.** `BacktestResult.__post_init__` raises on an empty
list. Do not remove that.

**Two rules live in the stylesheet, not in a comment.** The call and the falsifier share
`.callout .line` with no modifier, so the falsifier can never be set smaller than the call. And
valuation multiples and drawdowns are never coloured, because a 44% fall is the reason the
cyclical-turn bucket exists.

**Do not modify anything that existed before this branch.** `tests/test_nothing_existing_was_touched.py`
enforces it. `dashboard/index.html` may only gain lines, and only lines that are links.

---

## Before you commit

```bash
python -m pytest -q                                  # 677 tests
python scripts/build_all.py                          # regenerate everything
python scripts/shoot.py --mobile --themes night paper  # 24 page/theme/width combinations
```

`shoot.py` fails on console errors and on horizontal overflow. The overflow check exists because a
full-page screenshot cannot show you overflow: the image simply gets wider.

Generated files under `dashboard/` change on every build because `built_at` moves. That is
intentional. `tests/test_build_determinism.py` compares everything else.

---

## What is genuinely unfinished

Ordered by value, and `PLAN.md` carries the same list with verification commands.

1. **X7, measure the survivorship hole.** Alpha Vantage's `LISTING_STATUS` returns every delisted US
   ticker with its date for *two requests total*, affordable on a 25-a-day free key, and it is the
   only free source of that list found. It cannot repair a backtest but it can size the bias: run the
   universe filter as of a past date and count how many selected names no longer exist.
2. **X8, SEC DERA Financial Statement Data Sets** as the point-in-time fundamentals source. Quarterly
   bulk zips, genuinely as-reported, free, lagging quarter end by two weeks to two months.
3. **X2b, the 10-year EDGAR pull.** `edgar.py` is written for it and `as_known_on` is tested against
   a simulated restatement. It needs one live run.
4. **X5, a compare view** across up to four analysed tickers.
5. **X6, a score-vs-outcome tracker** wired to `journal.md`, so every past call is graded. Needs
   price history.

---

## Two things to be careful about

**Do not retune the score to make a diagnostic look tidy.** The three revision components correlate
0.54 to 0.77 and carry a quarter of the weight on largely one idea; `revisions_90d` is 26% of
realised variance on an 11% weight. That is reported, not fixed, on purpose. The one weight change
that *was* made (swapping `value_ev` above `value_pe`) corrected a documented basis mismatch, which
is a different thing.

**The front page's Journal tab is wrong, and fixing it is a scope decision, not a code one.**
`scripts/build_dashboard.py` parses journal headings with `(\S+)` for the ticker, so a heading
naming five names produces one entry under the first and folds the other four into the title. The
front page shows thirteen names; `/positioning/` shows sixteen. The fix is one line, giving that
file the grammar in `scripts/an/journal.py`. It was not made because the brief said not to modify
existing routes, and both `build_dashboard.py` and the `dashboard/data.js` it writes are on the
untouchable list. Two tests in `test_nothing_existing_was_touched.py` pin the divergence and will
fail the day someone fixes it; delete them and the note in `NOTES.md` section 5d when that happens.

**Do not touch `scripts/price_screen.py`** without deciding to. Two real defects live there and both
are flagged rather than fixed, because that file drives the bucket assignments the whole system
rests on: `dd_ath` is a maximum closing price while `high_52w` is an intraday high, and `price_ps` is
a second price produced by a merge-suffix collision on line 116. `NOTES.md` section 3 has both.

---

## Where the honest limits are written down

- `dashboard/backtest.json` — `status: NOT RUN`, and five paragraphs on why.
- `NOTES.md` section 4 — every free data source, adversarially re-checked. The short version:
  EDGAR is the only unconditionally free one, and there is no free legal automatable source of full
  earnings transcripts.
- `NOTES.md` section 5c — the first adversarial review, 38 confirmed findings, all fixed.
- `NOTES.md` section 5d — the second pass over the rendered pages and the fixes themselves,
  eleven findings. Ten fixed; the eleventh is the front-page journal divergence above.
- `scripts/an/power.py` — how long until a backtest here could say anything. Quarterly archiving:
  first verdict in 3 years. Monthly: 1 year. Archive monthly.
- `scripts/an/backtest.py` — the engine's own false-positive rate, measured, including the
  configuration where it is three times nominal.

Research and analysis from public data, not personalised financial advice.
