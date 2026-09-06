# PLAN — deep analysis pages + scorecard/positioning

Working branch: `claude/stock-analysis-scoring-xhuyl9`. Written before any code, 2026-09-06.

## Ground truth found before planning (read this first)

Three things differ from the brief. They change the plan, so they are stated up front rather than
discovered at 4am.

1. **This repo is not a Next.js app.** There is no `/app`, no `package.json`, no Vercel config.
   `idontreallyknow-20/xoxo` (and its private twin `idontreallyknow-20/investing`) is a Python
   pipeline plus a single static page, `dashboard/index.html`, served by `scripts/serve.py`
   (`SimpleHTTPRequestHandler` rooted at `dashboard/`). Tabs are Overview / Picks / Rankings /
   Holdings / Charts / Settings, switched by hash, not Watchlist / Journal / Portfolio / Compare.
2. **Finnhub is not wired through a server route.** There is no `FINNHUB_KEY` env var anywhere.
   The key is pasted by the user into Settings and lives in `localStorage`, and the browser calls
   `finnhub.io/api/v1/quote` directly (`dashboard/index.html:558`). So "never expose the key client
   side" is currently *violated by the existing page*. New code will not repeat that: every new
   Finnhub call is server-side/offline in Python, key from env only.
3. **This container has no egress to any market-data host.** `finnhub.io`, `data.sec.gov` and
   `www.sec.gov` are all refused by the org egress proxy (403 on CONNECT), through curl *and*
   through WebFetch. `pypi`, `npm` and `github` are allowed; `WebSearch` works.
   **Therefore no live data can be pulled tonight.** Every fetcher gets written and unit-tested
   against committed fixtures, and runs for real on Joseph's machine in the morning.

Consequences that shape everything below:
- Routes are delivered as real directories (`dashboard/analyze/<TICKER>/index.html`,
  `dashboard/positioning/index.html`) so the URLs are literally `/analyze/KLAC` and `/positioning`
  on the existing static server, on `python -m http.server`, and on Vercel, with **zero changes to
  `serve.py` and zero changes to existing pages beyond two nav links**.
- What can be built for real tonight is built from committed local data: 1,505 scored names
  (`universe/quality_scores_latest.csv`), 150 with valuation/revisions/drawdown
  (`universe/price_screen_latest.csv`), 16 hand-written deep dives (`research/*.md`), the decision
  log (`journal.md`), and the 11 current picks.
- **There is exactly one snapshot date (2026-09-04) in this repo and no price history.** A
  cross-sectional score cannot be backtested from one snapshot. Task group E deals with this
  honestly instead of inventing numbers.

## Conventions for every task

- Verify with the exact command listed. If it fails three times, stop, log the reason in NOTES.md,
  leave the box unchecked, move on.
- Commit only after the verification passes. One task per commit where practical.
- New Python lives in `scripts/an/` so nothing existing is touched. New page assets live in
  `dashboard/assets/`. Generated data lives in `dashboard/analysis/`.
- No task may modify `dashboard/index.html` except task P5 (two nav links).
- No secret ever reaches a generated file. Task Q3 enforces this.

---

## A. Foundation

- [x] **A1** `pytest` available, `tests/` package, `scripts/an/__init__.py`, plan/notes/summary files.
      Verify: `python -m pytest -q tests/ && test -f NOTES.md -a -f SUMMARY.md`
- [x] **A2** `scripts/an/paths.py` — new paths only, imports nothing from `config.py` at module import
      time so tests run without side effects.
      Verify: `python -c "import sys;sys.path.insert(0,'scripts');import an.paths as p;print(p.ANALYSIS_DIR)"`
- [x] **A3** `scripts/an/store.py` — atomic JSON cache with TTL + `OFFLINE` mode that raises a typed
      error instead of hanging when egress is blocked.
      Verify: `python -m pytest -q tests/test_store.py`

## B. Data adapters (all fetchers behind an injectable transport, so they test offline)

- [x] **B1** `scripts/an/local.py` — load `quality_scores_latest.csv` + `price_screen_latest.csv` into a
      normalized per-ticker record; typed, NaN-safe, unit-aware.
      Verify: `python -m pytest -q tests/test_local.py` (asserts 1,505 scored rows, 150 priced rows,
      and spot-checks EXEL: rank 1, quality 88.6, forward P/E 13.88, dd_ath -1.99%)
- [x] **B2** `scripts/an/research_md.py` — parse `research/*.md` into sections (what it does, quality,
      financial table, valuation, earnings, bear case, risks, verdict, sources).
      Verify: `python -m pytest -q tests/test_research_md.py` (all 16 files parse, every required
      section non-empty, financial tables produce >=3 fiscal-year rows)
- [x] **B3** `scripts/an/journal.py` — parse `journal.md` into entries (date, ticker, thesis, wrong-if,
      size, conviction, bucket) without importing `build_dashboard.py`.
      Verify: `python -m pytest -q tests/test_journal.py`
- [x] **B4** `scripts/an/edgar.py` — CIK map, `submissions`, `companyfacts`, `companyconcept`, filing
      index -> document URL, 8-K -> Exhibit 99.1/99.2 resolution. Correct `User-Agent`, 10 req/s cap.
      Verify: `python -m pytest -q tests/test_edgar.py` (fixture-driven) and
      `python scripts/fetch_edgar.py --dry-run AAPL` prints the exact URLs it would call
- [ ] **B5** `scripts/an/finnhub.py` — free-tier endpoints only, key strictly from `FINNHUB_KEY` env,
      typed errors on 401/403/429, never logs the key.
      Verify: `python -m pytest -q tests/test_finnhub.py` and
      `python scripts/fetch_finnhub.py --dry-run KLAC` shows `token=***REDACTED***`
- [ ] **B6** `scripts/an/prices.py` — daily adjusted closes via yfinance with an on-disk cache and a
      deterministic synthetic generator for offline tests.
      Verify: `python -m pytest -q tests/test_prices.py`

## C. The analysis record (one object per ticker, the page renders only this)

- [x] **C1** `scripts/an/model.py` — the `TickerAnalysis` dataclass + JSON schema. Every field carries
      `source` and `as_of`. Nothing renders without provenance.
      Verify: `python -m pytest -q tests/test_model.py`
- [x] **C2** `scripts/an/metrics.py` — trend series (revenue, margins, FCF, share count, ROIC),
      CAGR, YoY, and a `MetricTrend` with direction + magnitude classification.
      Verify: `python -m pytest -q tests/test_metrics.py` (hand-computed KLAC revenue CAGR from the
      committed research table matches to 0.1pp)
- [x] **C3** `scripts/an/changes.py` — "what changed this quarter vs last": guidance deltas, estimate
      revisions (30d/90d, up/down counts), drawdown moves, new risks, and an explicit
      `management_dodged` list sourced from the research notes rather than invented.
      Verify: `python -m pytest -q tests/test_changes.py`
- [x] **C4** `scripts/an/build_analysis.py` — writes `dashboard/analysis/<TICKER>.json` +
      `dashboard/analysis/index.json`.
      Verify: `python scripts/build_analysis.py && python -m pytest -q tests/test_build_analysis.py`
      (every JSON validates against the C1 schema; 16 rich + rest thin)

## D. Scoring model

- [x] **D1** `scripts/an/score.py` — documented cross-sectional score. Components, weights and the
      reason for each written in the module docstring, not just in my head. Pure function, no I/O.
      Verify: `python -m pytest -q tests/test_score.py` (determinism, monotonicity per component,
      missing-data neutrality, no NaN escapes, weights sum to 1)
- [x] **D2** Component z-scoring / winsorisation done *within sector* where the metric is
      sector-dependent (margins, ROIC) and cross-sectionally where it is not (revisions, drawdown).
      Verify: test asserts a high-margin software name does not automatically outrank a good
      industrial on the margin component
- [x] **D3** Score the 150 and write `dashboard/scorecard.json` with per-component contributions so the
      page can show *why*, not just the number.
      Verify: `python scripts/build_scorecard.py && python -m pytest -q tests/test_scorecard.py`

## E. Backtest, honestly

- [x] **E1** `scripts/an/backtest.py` — engine: rank-IC per rebalance, decile/quintile forward returns,
      hit rate, bootstrap CIs, and **required** `limitations` output. Engine refuses to emit a result
      object with an empty limitations list.
      Verify: `python -m pytest -q tests/test_backtest.py`
- [x] **E2** Engine validated on synthetic panels: (a) planted alpha is recovered with the right sign
      and roughly the right magnitude; (b) pure noise is reported as *no edge*, with the CI straddling
      zero; (c) a look-ahead-contaminated panel is flagged.
      Verify: `python -m pytest -q tests/test_backtest_synthetic.py`
- [x] **E3** `scripts/backtest_run.py` — real run on watchlist + portfolio tickers using yfinance
      prices and EDGAR-dated fundamentals, with `--synthetic` and `--dry-run` for offline. Writes
      `dashboard/backtest.json`. Survivorship bias, single-snapshot fundamentals and rebalance count
      are recorded as first-class fields, not footnotes.
      Verify: `python scripts/backtest_run.py --synthetic && python -m pytest -q tests/test_backtest_run.py`
- [x] **E4** Where the score fails is computed, not asserted: per-sector IC, per-bucket IC, worst
      deciles, and the named tickers where the score was most wrong.
      Verify: test asserts `failure_modes` is non-empty for the synthetic run

## F. Positioning memo

- [x] **F1** `scripts/an/positioning.py` — given holdings (may be empty), cash, and the scorecard,
      produce a ranked candidate list with per-name reasoning, size band, buy zone, what would prove
      it wrong, and concentration/sector checks against `criteria.md` rules.
      Verify: `python -m pytest -q tests/test_positioning.py` (respects 12% position cap, 30% sector
      cap, 30% cyclical cap, 20-30% cash, $25k/month deployment cap)
- [x] **F2** Language guard: no "guaranteed", "will", "certain", "risk-free", no price predictions
      stated as fact. Every claim carries a confidence and a falsifier.
      Verify: `python -m pytest -q tests/test_language_guard.py` runs over every generated string
- [x] **F3** Personal holdings never committed to this public repo. `portfolio/` stays gitignored;
      the memo reads it at build time and the committed JSON contains only non-personal fields
      unless the user opts in.
      Verify: `python -m pytest -q tests/test_privacy.py` + `git check-ignore -q portfolio`

## P. Pages

- [x] **P1** `dashboard/assets/desk.css` — the six existing themes' tokens copied verbatim, plus new
      component styles. `index.html` untouched.
      Verify: `python -m pytest -q tests/test_css_tokens.py` (every `--var` used by new pages is
      defined in all six themes, and the token values match `index.html` exactly)
- [x] **P2** `dashboard/assets/analyze.js` + the analyze shell. Renders only from
      `analysis/<TICKER>.json`. Theme, motion and currency settings shared with the main page via the
      same `desk-*` localStorage keys.
      Verify: headless Chromium loads `/analyze/KLAC`, zero console errors, `document.title` contains
      KLAC, and >= 8 sections rendered
- [x] **P3** `dashboard/analyze/index.html` — ticker index with search.
      Verify: headless load, all analysed tickers present as links
- [x] **P4** `dashboard/positioning/index.html` + `assets/positioning.js` — scorecard table, backtest
      results with sample sizes and CIs, where-it-fails section, ranked memo.
      Verify: headless load, zero console errors, sample-size and CI text present
- [x] **P5** Two nav links in `dashboard/index.html` (`Analyze`, `Positioning`) and nothing else.
      Verify: `git diff --stat dashboard/index.html` shows a single-digit line change, and the six
      existing views still render in headless Chromium
- [x] **P6** Charts: metric trend sparklines/bars in the existing SVG idiom (no chart library, no CDN).
      Verify: headless screenshot is non-blank and contains `<svg`

## Q. Quality gates

- [x] **Q1** Headless Chromium screenshot of every new page in `night` and `paper` themes.
      Verify: `python scripts/shoot.py` writes 6 PNGs, each > 20 KB
- [x] **Q2** Contrast check of new components against WCAG AA for body text.
      Verify: `python -m pytest -q tests/test_contrast.py`
- [x] **Q3** Secret scan: no API key, token or personal holding in anything git tracks.
      Verify: `python -m pytest -q tests/test_no_secrets.py`
- [x] **Q4** Full suite + a clean rebuild from scratch reproduces byte-identical JSON.
      Verify: `python -m pytest -q && python scripts/build_all.py && git diff --exit-code dashboard/`
- [ ] **Q5** NOTES.md records every data source found, what it costs, what it actually returns, and
      every task skipped and why. SUMMARY.md is the morning read.
      Verify: both files exist, NOTES.md has a source table, SUMMARY.md has a "review this" list

## Extension backlog (start here once A-Q are done, highest value first)

- [x] **X1** Point-in-time snapshot archiving so a *real* forward test becomes possible from the next
      pipeline run onward (`universe/snapshots/<date>/`). This is the single highest-value item:
      it is the only thing that turns E into a genuine backtest over time.
- [ ] **X2** EDGAR XBRL `companyfacts` -> 10+ years of as-reported fundamentals with filed dates,
      replacing the 4-year yfinance limitation called out in README and criteria.md.
- [ ] **X3** 8-K Exhibit 99.1 diffing: guidance language this quarter vs last, mechanically extracted.
- [ ] **X4** Peer-relative valuation using the 1,505-name panel rather than the company's own history.
- [ ] **X5** Compare view across up to 4 analysed tickers.
- [ ] **X6** Score-vs-outcome tracker wired to `journal.md` so every past call is graded automatically.
