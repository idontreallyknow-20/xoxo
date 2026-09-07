# HANDOFF

For whoever picks this up next, human or model. `SUMMARY.md` is written for Joseph; this one is
written for the next person to touch the code.

Fifth session on branch `claude/equity-research-setup-dtxlbl`, restarted from `main` after PR #5
merged (the first three sessions were `claude/stock-analysis-scoring-xhuyl9`, PRs #1 to #3). No CI
is configured on this repo, so the gate is local: see **Before you commit** below.

**What changed in the seventh session, in one paragraph.** The desk runs itself. Quotes arrive through
git: a GitHub Actions workflow pulls closes twice a weekday and commits them to the `quotes` branch,
`an/quotes.py` reads them, and `DESK_QUOTES=data/cache/quotes` makes every `--live` script read the
file through `prices.default_downloader()`. `book.md` is Claude's own paper book in the journal grammar,
derived into `dashboard/book.json` by `an/book.py` under criteria.md's caps. The digest gained a `close`
edition and three sections (My book, The memo sized for $100,000, Tests). Two Routines (Desk morning
11:00 UTC, Desk close 22:00 UTC, weekdays) run a fresh session each that tests, marks, decides, emails
`josephislockedin@gmail.com` through the Gmail connector, and commits to the `desk` branch. `NOTES.md`
section 12 has the routes, the rules the routine works under, and what could not be verified here.

**What changed in the sixth session, in one paragraph.** Real daily prices are on disk for the first
time: `an/history.py` and `scripts/fetch_history.py` pull five years of S&P 500 closes, SPY and QQQ,
a cross-check and index membership from public GitHub repositories (the one host the proxy allows),
audit them, and write a manifest; `pip install pandas numpy pytest` first, nothing else is needed.
`an/paper.py` replays the `swing.md` rules on that panel with fake money, `an/ab.py` runs them against
twenty matched random-entry controls with a fixed train/held-out split and a pre-registration guard,
`scripts/paper_trade.py` is the CLI, `lab/LAB.md` and `lab/results/` are the append-only record,
`dashboard/paper.json` and `/paper/` show the result. `NOTES.md` section 11 has the data audit, the
mechanics and the verdict. In one line: no edge found at these horizons on this data. The pullback rule as written lost 27 bp a trade to SPY on the held-out window (2016-07 to 2018-02, n=10,375, interval [-39, -12] bp); the in-sample favourite, the filtered pullback at 20 sessions, lost 26 bp with an interval straddling zero and sat inside its random control; the gap-on-volume proxy lost 55 bp. 36 hypotheses were counted. No arm beat a matched random entry.

**What changed in the fifth session, in one paragraph.** Three email editions a day (07:00 brief,
12:00 check, an event email whenever a written rule fires and has not been sent), driven by a
narrow intraday module (`an/intraday.py`, fifteen-minute bars for the watched names only) and
alert de-duplication in the scan state. A swing layer: `swing.md` holds the rules, `an/setups.py`
applies four mechanical setups to the liquid universe and writes `setups.json`, the journal grammar
gained `Horizon:` and `Stop:`, and the tracker grades swing calls at 5, 10 and 20 sessions.
`NOTES.md` section 10 has the framing (there is no measured edge at that horizon; the layer exists
to survive while one is measured), what is most likely to break live, and the first-run order.

**What changed in the fourth session, in one paragraph.** The freeze on `dashboard/` is lifted
(`data.js` excepted; everything outside `dashboard/` is still frozen), the front page is rewritten on
the shared stylesheet with a hand-written motion layer and no client-side API key, and three new
modules exist: `an/mail.py` sends email, `an/watch.py` scans prices, filings and headlines against the
journal's own rules, `an/digest.py` turns the scan into the daily email. `setup.ps1 -InstallTask`
schedules the scan and the email on Windows. `NOTES.md` section 9 has the decisions, the source
evaluation and what has still never run (everything that touches a network, still).

---

## The one thing that will confuse you

**This repo is not a Next.js app,** whatever the original brief said. There is no `package.json`, no
`/app`, no `FINNHUB_KEY`. It is a Python pipeline plus one static page, `dashboard/index.html`,
served by `scripts/serve.py` (`http.server.SimpleHTTPRequestHandler` rooted at `dashboard/`).

`/analyze/<TICKER>` and `/positioning` work because they are real directories with an `index.html`
inside them. That is the whole routing story. No server change was needed and none was made.

**The build environment has no network access to any market-data host.** `finnhub.io`,
`data.sec.gov`, `www.sec.gov`, `www.alphavantage.co` and Yahoo are refused at the egress proxy, in
both sessions so far. `scripts/an/edgar.py`, `finnhub.py`, `prices.py`, `listing_status.py`,
`dera.py` and `tracker.py` have therefore **never made a real request**. They are written to
documented API shapes and tested against fixtures. Sections 5b and 7 of `NOTES.md` list what each
one's author flagged as most likely to be wrong on the first live run. Do not treat them as verified.

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
  listing_status.py  Alpha Vantage delisting list -> the size of the survivorship hole (X7),
                     and measured_attrition(), which the backtest limitations quote (X13)
  dera.py            SEC DERA quarterly data sets -> point-in-time fundamentals (X8)
  dera_fundamentals.py  those facts -> the screen's aggregates, reconciled with the CSV and
                     merged into the record; universe_with_basis() is what every builder calls (X12)
  guidance.py        8-K Exhibit 99.1 guidance figures, diffed against last quarter's (X3)
  tracker.py         every journal call graded against prices, "so far" (X6)
  watch.py           the daily scan: prices vs the journal's falsifiers, new filings, headlines (X16);
                     intraday reads and alert keys (X22)
  intraday.py        15-minute bars for the watched names, one batched call, cached ten minutes (X22)
  setups.py          four mechanical swing setups from swing.md, closes only (X23)
  quotes.py          daily closes through the quotes branch; ticker_universe, age_sessions (X32)
  book.py            Claude's own paper book, derived from book.md under the caps (X33)
  history.py         real daily closes from public GitHub mirrors, the split audit, the cross-checks,
                     index membership, the committed fixture (X25)
  paper.py           the swing rules replayed with fake money: features, masks, the event study, the
                     ledger replay under swing.md's caps (X26)
  ab.py              named arms against twenty matched random controls, the fixed split, the
                     pre-registration guard, dashboard/paper.json (X27)
  digest.py          the daily email as data, then as HTML; three editions (X17, X22)
  mail.py            SMTP, credential from the environment only, dry-run and provenance (X15)
  outcomes.py        the grades read back: score at call vs outcome, refusing thin samples (X14)
  positioning.py     the memo: three lists, sized from criteria.md
  analysis.py        assembles the record each page renders
  pages.py           writes the static page shells

scripts/build_*.py   the generators. scripts/build_all.py runs all four (track_calls.py is the fourth)
scripts/fetch_*.py   the live pulls, plus listing_status.py, guidance_diff.py and track_calls.py.
                     All have --dry-run; fetch_dera.py --basis-report and guidance_diff.py
                     --fixture print their shape on committed miniatures
scripts/snapshot.py  run this MONTHLY (see power.py for why)
scripts/scan.py      the daily scan -> dashboard/watch.json; --intraday -> watch_intraday.json
scripts/setups.py    the swing setups -> dashboard/setups.json (--live, --dry-run, --fixture)
scripts/fetch_quotes.py  the daily closes pull (runs on the Actions runner or on Joseph's machine)
scripts/build_book.py  book.md -> dashboard/book.json (--live, --quotes DIR, --journal, --as-of)
scripts/run_tests.py  the suite, with its result written for the email
book.md              Claude's own calls, append-only; the routine appends, never edits
.github/workflows/quotes.yml  the twice-a-weekday quotes pull into the quotes branch
scripts/fetch_history.py  the history pull (--dry-run, --audit, --make-fixture); needs raw.githubusercontent.com
scripts/paper_trade.py  the experiments (--live --split train|test, --fixture, --dry-run) and, with no
                     flags, the render of dashboard/paper.json from lab/results/ (offline, in build_all)
lab/LAB.md           the append-only lab notebook: pre-registration block, one entry per iteration
lab/results/         one committed JSON summary per --live run; never pruned (a test checks)
scripts/daily_email.py  the email (--edition morning|midday|event, --preview, --dry-run, --send,
                     --only-if-alerts, --only-new-alerts)
setup.ps1            -InstallTask registers the three weekday tasks and the monthly one;
                     -Daily -Edition <e> and -Monthly are what they run
swing.md             the swing rules, read by the scanner and quoted by the email
scripts/shoot.py     headless Chromium: console errors, overflow, screenshots

dashboard/assets/    desk.css (the one definition of the theme tokens; index.html loads it too),
                     motion.js (depth field, tilt, lifts; no library), home.js (the front page),
                     desk-common.js, analyze.js, analyze-index.js, compare.js, positioning.js
dashboard/analyze/compare/   /analyze/compare/?t=A,B,C,D, one shell, selection in the address
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

**Do not modify anything that existed before this work outside `dashboard/`.**
`tests/test_nothing_existing_was_touched.py` enforces it. Since the fourth session `dashboard/` may
change freely except `dashboard/data.js`, which is written by the frozen `build_dashboard.py`.

**No secret anywhere but the environment.** The mail password, the Finnhub key, the Alpha Vantage key.
`tests/test_privacy.py` scans every tracked file. The mailer redacts the password out of server replies
before raising, because SMTP servers echo the AUTH string.

---

## Before you commit

```bash
pip install pytest playwright                        # neither is in requirements.txt
python -m pytest -q                                  # ~1000 tests, ~5 min
python scripts/build_all.py                          # regenerate everything
python scripts/shoot.py --mobile --themes night paper  # 32 page/theme/width combinations
```

`shoot.py` fails on console errors and on horizontal overflow. The overflow check exists because a
full-page screenshot cannot show you overflow: the image simply gets wider.

Generated files under `dashboard/` change on every build because `built_at` moves. That is
intentional. `tests/test_build_determinism.py` compares everything else.

---

## What is genuinely unfinished

The fifth session's additions, in the order to run them: `NOTES.md` 10g. Then 9e, then the list
below, which is unchanged because none of it could run here either.

The second session (2026-09-06, `NOTES.md` section 7) built X7, X8, X5 and X6 from the backlog.
The third (2026-09-07, section 8) wired the modules nothing consumed: DERA into the score (X12),
the measured attrition into the backtest limitations (X13), the 8-K guidance diff (X3) and the
tracker's read path (X14). Everything that touches a remote source is fixture-tested and has never
made a real request, so the list is now **live runs that need a machine with egress**, each a
single command that prints its own provenance. In order of value:

1. **X12 live, the one that changes the score's inputs.** `export SEC_USER_AGENT="Name email"`,
   `python scripts/fetch_edgar.py --dry-run AAPL` (caches the ticker map), `python
   scripts/fetch_dera.py --since 2016q1` (about forty zips), `python scripts/fetch_dera.py
   --basis-report`, read the disagreement counts, then `python scripts/build_all.py`. Section 8b
   lists what is most likely to be wrong on the first file.
2. **X2b, the 10-year EDGAR pull.** `python scripts/fetch_edgar.py --facts --exhibits KLAC`. Read
   the output against the filing.
3. **X3 live:** `python scripts/guidance_diff.py KLAC BKNG`, then `python scripts/build_analysis.py`
   and look at "What changed since the last report" on those two pages.
4. **X6 live:** `python scripts/track_calls.py --live`, then look at `/positioning/` under
   "Every call, graded". The file says NOT GRADED until this has run, and the read path under it
   says INSUFFICIENT until there are twenty graded calls on four dates.
5. **X7 and X13 live:** `export ALPHAVANTAGE_KEY=...` then `python scripts/listing_status.py
   --fetch`. Two requests. Rebuild and the backtest limitation quotes the measured rate.
6. Once X12 and X7 have run for real: `backtest_run.py --live`, with
   `dera_fundamentals.fundamentals_for(..., on_date=rebalance_date)` as the point-in-time
   fundamentals, which is the first version of the backtest that could be believed.

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
