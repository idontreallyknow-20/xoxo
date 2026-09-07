# Prompt for Fable 5.1

Copy everything below the line. Fill in the four bracketed decisions in section 5 first, because
whoever picks this up cannot make them for you and will stall or guess if they are blank.

---

## 1. Who I am and what this repo is

I'm Joseph, Grade 11 in Richmond Hill, Ontario. This is my personal equity research repo,
`github.com/idontreallyknow-20/xoxo`. Casual and direct, no em dashes, Canadian spelling, CAD and
metric where it comes up. Push back on facts, not on what I've decided to do.

**Read these before writing code, in order: `HANDOFF.md`, `SUMMARY.md`, `PLAN.md`, then `NOTES.md`
sections 3, 4, 7 and 8.** Do not skip this. Three sessions have started here and the ones that read
first went faster.

**This repo is not a Next.js app.** No `package.json`, no `/app`, no framework, no CDN. It is a
Python pipeline plus a static dashboard served by `scripts/serve.py`
(`SimpleHTTPRequestHandler` rooted at `dashboard/`). `/analyze/<TICKER>/` and `/positioning/` work
because they are real directories with an `index.html` inside. Every earlier brief that said
otherwise was wrong and cost a session's first hour.

Current state: 862 passing tests, ~13,000 lines of Python under `scripts/an/`, 150 generated
analysis pages, and a documented 13-component score.

## 2. What I want you to build

### a) Daily email

Every day, an email to me with the state of my portfolio and anything that moved. There is already
`scripts/email_picks.py`, which renders `dashboard/email_picks.html` as a self-contained HTML email
with table layout and inline styles. **It renders but does not send.** There is no SMTP anywhere in
this repo and no mail credential.

Build the sending half. It needs to run on a schedule on my Windows machine, alongside the monthly
job already registered in `setup.ps1` (see `-InstallTask` and `-Monthly` in that file for the pattern
to copy).

### b) Scanning, daily or hourly

I want the system watching things, not just sitting there between quarterly runs. Prices, estimate
revisions, filings, news, social sentiment. Tell me what changed and what it means for names I hold
or watch.

I said "scan tweets, scan everything." Read section 4 before you plan this, because some of it is
paid and some of it is against a site's terms, and this repo has a standard for both.

### c) Complete visual redesign

Rip up the current look. I want **minimalist and simple in layout, maximalist in motion**: real
animation, 3D effects, depth, transitions that feel expensive. The current dashboard is flat,
static, six colour themes, hand-rolled inline SVG, no chart library and no CDN.

Constraints you need to know before you design: the pages are static HTML served off a plain file
server, so anything you add ships as a file in the repo. `three.js` or similar means either a CDN
tag or a vendored copy, and the existing pages deliberately have neither. The tests check WCAG AA
contrast on body text (`tests/test_contrast.py`) and check that no page scrolls horizontally at
390px (`scripts/shoot.py`). Motion needs a `prefers-reduced-motion` path.

### d) The goal

I want to beat the market. That is the point of the whole thing. Benchmarks are already set in
`scripts/config.py`: SPY, QQQ and VFV.TO, inception 2026-09-04.

**A fact you need, not an opinion:** this repo currently holds exactly one dated snapshot
(2026-09-04) and no price history, so nothing in it can yet measure whether anything beats anything.
`dashboard/backtest.json` says `NOT RUN` and `scripts/an/power.py` computes how long that takes to
change: about one year of monthly snapshots before the engine will return a verdict above "weak".
Do not write code or copy that claims performance the data cannot support. Every generated file in
here states its own status honestly and the tests enforce it.

## 3. Hard rules, all enforced by tests

Run `pip install pytest` first, it is not in `requirements.txt`. Then `python -m pytest` must pass
before any commit. 862 pass right now. Keep that number honest.

- **Never expose an API key client-side and never write one into a generated file.**
  `tests/test_privacy.py` (six tests: no key-shaped string in any tracked file, no key embedded in a
  generated page, the Finnhub key comes from the environment only, and `portfolio/` stays untracked).
  `PLAN.md` item Q3 calls this file `test_no_secrets.py`; that name is stale, the file is
  `test_privacy.py`. Note that the *existing* front page already
  violates this: `dashboard/index.html:558` has the browser call Finnhub with a key pasted into
  Settings and stored in `localStorage`. New code must not repeat it.
- **`tests/test_nothing_existing_was_touched.py` is the definition of what you may not modify.**
  See section 5a, I may be lifting part of it.
- **Never present anything as a guaranteed prediction.** `tests/test_language_guard.py` walks every
  generated string and fails on the vocabulary of certainty.
- **Missing is missing.** `None`, never `0.0`, never `NaN`. A zero that means "unknown" reads as
  "measured and neutral" and has bitten this codebase twice.
- **A backtest result cannot exist without a limitations list.** Enforced in
  `BacktestResult.__post_init__`. Do not remove it.
- **Every fetcher goes behind the injectable transport in `scripts/an/http.py`**, with a fixture, a
  `--dry-run`, and a line that says whether it has ever made a real request. Follow that pattern
  exactly for anything new. Never fake data to make a page look finished; write the honest NOT RUN
  state, as `dashboard/backtest.json` and `dashboard/tracker.json` already do.

## 4. What the scanning actually costs, checked already

`NOTES.md` section 4 has the full table with every "it's free" claim re-checked adversarially. The
short version for what I'm asking for:

- **X/Twitter.** The free API tier is effectively write-only and gives no useful read access. Reading
  tweets at any scale is a paid product, roughly $100/month at the entry tier and far more above it.
  Scraping it without the API is against their terms. This repo has already refused one source on
  exactly those grounds: Motley Fool publishes full earnings transcripts free to read, and
  `NOTES.md` records that a pipeline fetching them would breach their terms, so the project does not.
  Apply the same standard here and tell me what it rules out.
- **Free alternatives worth pricing out instead:** Reddit's API has a real free tier with OAuth,
  StockTwits has a rate-limited API, SEC EDGAR full-text search is free and unlimited-ish, and most
  financial news sites publish RSS. Evaluate these and tell me which are usable rather than assuming.
- **Prices hourly.** `scripts/config.py` sets `WORKERS = 2` with the comment "Yahoo rate limits
  aggressively." yfinance is unofficial and undocumented. Hourly across 150 names is a real
  engineering problem, not a config change. Say what it would take.
- **SEC EDGAR** is the one unconditionally free source here. No key, no tier, no rate card. Filings
  and 8-K press releases are already wired: `scripts/an/edgar.py`, `dera.py`, `guidance.py`.
- **Email sending** needs a credential. Gmail needs an app password. It goes in an environment
  variable, never in a file, and the secret tests will catch it if it lands in one.

## 5. Decisions only I can make. I have filled these in.

**a) The untouchable list.** "Redo everything" collides directly with
`tests/test_nothing_existing_was_touched.py`, which currently freezes `scripts/serve.py`,
`price_screen.py`, `quality_screen.py`, `build_dashboard.py`, `dashboard/data.js`, `README.md`,
`criteria.md`, `journal.md` and the universe CSVs, and allows `dashboard/index.html` to gain nav
links only.

> **My answer: [ lift it entirely / lift it for `dashboard/` only / keep it, redesign only the new
> pages ]**

If I lift it, delete the tests that pin it and say so in `NOTES.md`. Do not quietly work around them.

**b) Money.** Every session so far has run under "never spend money, no paid data source, no paid
API tier." Scanning social sentiment properly breaks that.

> **My answer: [ still zero / I'll pay up to $___ a month, for ___ ]**

**c) Two known defects I have been sitting on.** Both are documented in `NOTES.md` section 3 and
both are deliberately unfixed because they are mine to call:

- `scripts/price_screen.py`: `dd_ath` is a max *closing* price while `high_52w` is an *intraday*
  high, so the cyclical-turn bucket filter is stricter than `criteria.md` describes. And `price_ps`
  is a second price from a merge-suffix collision on line 116, not a price-to-sales ratio.
- `scripts/build_dashboard.py` parses journal tickers with `(\S+)`, so the front page Journal tab
  shows 13 names where `/positioning/` shows 16.

> **My answer: [ fix both / fix the journal one only / leave them ]**

**d) Cadence, and what an email is allowed to say.** My `README.md` currently says weekly check-ins
with "no trade recommendations unless something material happened," quarterly re-underwrites, and
"do not run research more often than this," on a 2 to 5 year holding period. A daily email changes
that.

> **My answer: [ daily email that only alerts when one of my written triggers fires / daily email
> with a full readout every day / daily email that recommends trades ]**

If the answer changes the cadence, update `README.md` and `criteria.md` to match, so the repo does
not contradict itself.

## 6. What has never run, and needs my machine

Every container this project has been built in was blocked from `sec.gov`, `alphavantage.co`,
`finnhub.io` and Yahoo. So `edgar.py`, `dera.py`, `dera_fundamentals.py`, `guidance.py`,
`prices.py`, `listing_status.py` and `tracker.py` are all fixture-tested and **have never made a
real request**. `setup.ps1` automates the whole live sequence on Windows but has itself never been
executed, because no container had a PowerShell runtime.

If you have network access, run the live pulls early and report what broke. `NOTES.md` sections 5b,
7a, 7b and 8b list, per module, what its author flagged as most likely to be wrong on first contact
with real data.

## 7. How to work

- Extend `PLAN.md` as you go and keep `SUMMARY.md` current, because `SUMMARY.md` is what I actually
  read. Every claim in it has to be something you ran.
- Verify with a real command before claiming anything works.
- If a task fails verification three times, stop, write down why in `NOTES.md`, leave the box
  unchecked, move to the next one.
- Commit only when `python -m pytest` passes.
- `python scripts/build_all.py` then `python scripts/shoot.py --mobile --themes night paper`
  renders every page at two widths in two themes and fails on console errors and horizontal
  overflow. Run it before you call a redesign done.
- Open a PR when the suite is green.
