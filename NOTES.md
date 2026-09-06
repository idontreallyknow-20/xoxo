# NOTES — data sources, data quality, and everything skipped

Running log. Newest findings appended per section. Written for Joseph, 2026-09-06 overnight run.

---

## 1. Environment constraints hit on this run

**No egress to any market-data host.** The container running this work goes out through an
organisation egress proxy that refuses `CONNECT` (HTTP 403) to `finnhub.io`, `data.sec.gov` and
`www.sec.gov`. Verified three ways: `curl`, Python `requests`, and the `WebFetch` tool. `pypi`,
`npm`, `github` and `WebSearch` all work.

What that means for the code: **every fetcher in `scripts/an/` was written and unit-tested against
committed fixtures, and has never made a real request.** They are written to the documented API
shapes, they have `--dry-run` modes that print the exact URL and headers they would send, and they
will need one live run on your machine before you trust them. The first thing to do in the morning
is `python scripts/fetch_edgar.py AAPL` and read what comes back.

Everything that renders from local data — the analyse pages for the 16 researched names, the
scorecard over the 150, the whole positioning memo — is real and was built and verified end to end.

**No `FINNHUB_KEY` anywhere.** Not in the environment, not in a `.env`, not in the repo. The brief
said Finnhub was wired through `/app/api` with a `FINNHUB_KEY`. It is not; see section 2.

---

## 2. What this repo actually is (differs from the brief)

The brief described a Next.js site on Vercel with `/app/api` routes, `FINNHUB_KEY`, and Watchlist /
Journal / Portfolio / Compare pages. None of that is in `idontreallyknow-20/xoxo`, and none of it is
in its private twin `idontreallyknow-20/investing` either (I attached and cloned that repo to check;
it is the same project with `portfolio/` committed instead of gitignored).

What is here: a Python pipeline (`scripts/*.py`, yfinance) plus one static page,
`dashboard/index.html`, served by `scripts/serve.py` using `http.server.SimpleHTTPRequestHandler`
rooted at `dashboard/`. Tabs are Overview / Picks / Rankings / Holdings / Charts / Settings.

**The Finnhub key is currently exposed client side.** `dashboard/index.html:558` builds
`https://finnhub.io/api/v1/quote?symbol=...&token=${S.key}` in the browser from a key the user pastes
into Settings and that is stored in `localStorage`. That is the existing behaviour and I did not
change it (the brief said not to modify existing routes). It is worth knowing: a free Finnhub key in
`localStorage` is readable by any script on the page and travels in a query string. Nothing new that
I wrote calls Finnhub from the browser — all new Finnhub access is Python, key from `FINNHUB_KEY` env
only, and `tests/test_no_secrets.py` fails the build if a key ever reaches a generated file.

Because there is no Next.js router, `/analyze/[ticker]` and `/positioning` are delivered as real
directories with `index.html` inside them. `SimpleHTTPRequestHandler` serves those as
`/analyze/KLAC/` and `/positioning/` with no server change at all, and the same layout works on
Vercel and on `python -m http.server`.

---

## 3. Data quality findings in the existing screen output

Found while writing the loader. None of these are things I changed; they are things worth knowing
before leaning on the numbers.

**`dd_ath` and `dd_52w` are measured on different bases.** `scripts/price_screen.py:40` computes
`ath` as `close.max()` over the full unadjusted history, so it is the highest *closing* price.
`high_52w` comes from the Yahoo screener field `fiftyTwoWeekHigh`, which is the highest *intraday*
price. For 75 of the 150 names, `ath < high_52w`. Consequences:

- `dd_ath` systematically understates the real drawdown from the true all-time high.
- The **cyclical-turn bucket** requires `dd_ath <= -0.35`. Because `dd_ath` is measured against a
  lower (closing-basis) peak, that filter is *stricter* than intended, and the bucket of 12 names is
  probably smaller than the rule as written in `criteria.md` describes.
- Three names (EXEL, VRTX, JNJ) happen to have `price_ps == ath` exactly. Checked: coincidence, not
  a column shift.

**`ath` is computed from `auto_adjust=False` closes.** So it is a nominal price peak, not
split/dividend adjusted. For a name that has split (KLAC did a 10-for-1 in June 2026, per its
research note), the "all-time high" is the post-split price series maximum, which is what you want,
but for older splits the raw series can be discontinuous. Worth spot-checking any name where
`dd_ath` looks implausible.

**64 of 1,505 names have no `nd_to_ebitda`.** Missing, not zero. The loader keeps them as `None` and
the new score treats missing leverage as neutral rather than as a fortress balance sheet.

**12 of the 150 have no own-history multiples** (`n_hist_years == 0`), all because statements are
reported in a different currency from the listing and the pipeline correctly refuses the comparison.
They carry a `multiples_note` saying so. The new score does not penalise them; it marks them
`valuation: unscored` and says why on the page.

**`price_ps` is a second price, not a price-to-sales ratio.** `scripts/price_screen.py:116` merges
the quality top-150 frame with the price-screen output using `suffixes=("", "_ps")`. Both frames
carry a `price` column, so the price screen's own last close lands in the CSV under `price_ps`. I
briefly rendered it as "Price to sales" and gave Adobe a multiple of 280x before catching it.

Two things follow. The column is now named `price_from_price_screen` in the loader and is not shown
as a multiple anywhere. And more importantly: **every derived figure in that row was computed from
that price, not from the `price` column the CSV surfaces.** `dd_52w`, `dd_ath` and `pe_vs_median` all
come out of `one()`, which uses the price screen's last close. The two prices are within a fraction
of a percent of each other for all 150 names, so nothing is materially wrong, but the price shown at
the top of an analysis page and the drawdown shown further down are not computed from the same
number. The pages now say so.

**One snapshot only.** Every row in `universe/` is `pulled = 2026-09-04`. There is no history. This
is the single biggest constraint on the whole backtest question — see section 5 and
`dashboard/backtest.json`.

**`fiscal_years` is four years, not five.** Already documented in README and `criteria.md`. It means
every CAGR here is a 3-year CAGR. The analyse pages label it that way rather than saying "5 year".

---

## 3b. Contrast, measured

The six themes were inherited verbatim from `dashboard/index.html` rather than chosen, so this is a
measurement of the palette, not a criticism of it. `tests/test_contrast.py` computes every pairing
the new components use, in all six themes, and records the shortfalls rather than loosening the bar.

**One real bug, fixed.** I had used `--mark` for the keyboard focus ring. In the `paper` theme
`--mark` is `#fff27a`, a pale yellow on a white page: **1.15:1**, which is an invisible focus ring
and therefore no focus ring at all. Now `--ink`, which is the one pairing guaranteed high contrast in
every theme because the whole palette is built around it.

**Three inherited shortfalls, not fixed, because fixing them means changing values that must stay
identical to `index.html`:**

| pairing | theme | measured | bar |
|---|---|---|---|
| `--ink-3` on `--paper` | bone | 2.90:1 | 3.0:1 for large or secondary text |
| `--ink-3` on `--paper` | amber | 2.81:1 | 3.0:1 |
| `--up` on `--paper` | bone | 4.47:1 | 4.5:1 (a rounding-error miss) |

`--ink-3` carries captions, units and as-of dates. The new pages never make it the only carrier of a
fact: every caption restates something that also appears in the body text or in a value. So the
shortfall degrades polish rather than access. If you want it gone, `--ink-3` in bone needs to go from
`#928e82` to about `#7d7a70`, and in amber from `#6e5520` to about `#8a6b28`, in both files at once.

## 4. Free data sources: what is actually usable, verified

A research sweep looked at every category and then a second pass tried to refute each "it's free"
claim. Everything below was checked against 2026 sources; **none of it could be tested live, because
this container reaches none of these hosts.** Confidence is stated per row.

### The verdict in one line

**SEC EDGAR is the only source here that is unconditionally free, complete, and not going to change
its mind.** It is a statutory US government disclosure system, not a freemium product: no key, no
account, no tier, no rate card, and no mechanism by which a paid tier could appear. Everything else
is either a genuine free tier with a limit that bites, or a trial wearing a "free" label.

### Sources worth using

| source | what it gives | cost | the catch |
|---|---|---|---|
| **EDGAR submissions** | filing index: form, dates, accession, 8-K item codes | free, no key | `filings.recent` is columnar arrays, not objects. Holds 1 year or 1,000 filings, whichever is more; the rest is in shard files you fetch and concatenate |
| **EDGAR companyfacts** | every XBRL fact ever tagged, with its filing date | free, no key | 15-25 MB per large filer. Always send `Accept-Encoding: gzip` |
| **EDGAR companyconcept** | one tag's history, tens of KB | free, no key | 404 means the company never used that tag, which is data, not an error. Past 3 tags, pull companyfacts once instead |
| **EDGAR frames** | one tag, all filers, one period: a peer cross-section in one call | free, no key | Period token rules are strict: `CY2024` is a duration, `CY2024Q1I` is an instant, and asking for the wrong one gives an empty frame |
| **EDGAR 8-K Item 2.02** | the earnings press release, Exhibit 99.1 | free, no key | The closest free thing to a transcript. Prepared numbers and guidance language, no analyst Q&A |
| **yfinance** | daily adjusted OHLCV, splits, dividends | free, no key | Unofficial, undocumented throttling, breaks when Yahoo changes something. Still the best free price source |
| **Tiingo** | daily EOD, raw and adjusted | free key | **50 requests an hour.** 150 tickers is a 3-hour sweep. Fine as an overnight backfill, useless for anything interactive |
| **Finnhub free** | quote, profile, ~100 pre-computed ratios, earnings, news | free key | 60 calls/min. Historical candles are premium |
| **Alpha Vantage `LISTING_STATUS`** | **the list of delisted tickers** | free key, 2 calls total | The single most valuable thing on this table for a backtest. See below |
| **SEC DERA Financial Statement Data Sets** | quarterly zips of as-reported fundamentals | free bulk download | Point-in-time by construction. Lags quarter end by two weeks to two months |

### Sources not worth using

- **Stooq** now serves a JavaScript proof-of-work challenge instead of CSV to plain HTTP clients.
  Whatever guide told you `stooq.com/q/d/l/?s=aapl.us&i=d` works is out of date.
- **Alpha Vantage for prices**: 25 requests a day. 150 tickers is a six-day refresh. The arithmetic
  kills it before any other consideration.
- **Nasdaq Data Link / Quandl WIKI**: last bar is 2018-03-27 and it will never advance.

### Earnings transcripts: the honest answer is no

This was the part of the brief with the least satisfying result, so it is worth being precise.

**There is no free, legal, automatable source of full earnings call transcripts with Q&A.** Every
candidate fails for a different reason:

- **Financial Modeling Prep** puts transcripts on its Ultimate tier, roughly $139 a month. Not the
  mid tier. There is no free allowance and no trial for that dataset.
- **Alpha Vantage** `EARNINGS_CALL_TRANSCRIPT` costs one of your 25 daily requests per
  ticker-quarter. A single quarter across 150 names is six days of your entire quota.
- **API Ninjas** appears to gate the transcript endpoint outright, and its free tier forbids
  commercial use.
- **Motley Fool** publishes genuinely good full transcripts, free to read in a browser. Their terms
  of use prohibit automated access, scripts and harvesting. Reading them yourself is fine; a
  pipeline that fetches them is not.
- **EDGAR full-text search** can find the rare company that files a verbatim transcript as an 8-K
  exhibit. It is rare enough that it cannot be a source, only a lucky find.

**What the analysis pages do instead:** they say so. Every page's gaps section names the absence
explicitly and points at the 8-K Exhibit 99.1 press release as the free substitute, which carries
the guidance language and the prepared numbers but not the questions. That is the honest position:
the thing you asked for does not exist for free, and pretending a press release is a transcript
would be worse than saying so.

One correction to a common assumption: **Exhibit 99.2 is not reliably the slide deck.** Exhibit
numbering under Item 601 is not standardised past the top-level 99, so 99.2 is a deck at some
filers, a supplemental data pack at others, and something unrelated at a few. `edgar.py` returns it
without claiming what it is.

### Delisted tickers, and why that row matters most

Every backtest in this project is survivorship biased and will stay that way until the universe
includes companies that stopped existing. Of everything surveyed, exactly one free source lists
them: **Alpha Vantage's `LISTING_STATUS` endpoint**, which returns active and delisted US listings
with their delisting dates, and costs two requests in total rather than one per ticker. That makes
it affordable even on a 25-per-day key.

It does not give prices for those names, so it cannot fully repair a backtest. What it can do is
tell you how badly biased one is: run the universe filter as of a past date, count how many of the
names it selects no longer exist, and you have measured the hole rather than guessed at it. That is
worth an hour and is now on the backlog as X7.

### Corrections this research forced in code already written

`scripts/an/edgar.py` was written from documented shapes before this sweep ran. Four things it had
wrong, now fixed, all of which would have produced quietly wrong numbers rather than errors:

1. **`fy` and `fp` describe the filing, not the fact.** A revenue figure covering Feb 2018 to Jan
   2019 carries `fy=2021` when it appears as a comparative column in the FY2021 10-K. The old
   `Fact.is_annual` read `fp == "FY"` and would have called a single quarter annual whenever it
   appeared in a 10-K. Now `covers_a_year` derives the period from `start` and `end`.
2. **`frame` is the SEC's canonical-fact marker.** The same figure recurs under many accession
   numbers as restatements and comparatives pile up. Taking the latest silently uses restated
   numbers; taking the first ignores genuine corrections. `frame` is the SEC's own answer, and
   `EdgarClient.canonical()` now uses it.
3. **`companyconcept` returns 404 when a company never used a tag.** That is the normal answer to a
   normal question, not an outage. It returned an exception; now it returns `None`.
4. **`acceptanceDateTime` is the real point-in-time stamp.** `filingDate` is only a date, and a
   filing accepted at 17:35 carries that day's date while the market did not see it until the next
   session. `Filing` now carries it.

Two more that are documented rather than fixed, because they are the caller's job:
**Q4 is almost never tagged** (the 10-K reports the full year, so Q4 is FY minus the three
quarters), and **cash-flow facts in a 10-Q are cumulative year-to-date**, so Q2 operating cash flow
covers six months and treating it as a quarter doubles it.

### Two live warnings about Finnhub

- **`/quote` has a current silent-staleness bug.** Finnhub issue #583, filed 2026-08-31, reports
  `/quote` returning Friday's close for GOOG, TSLA, NVDA and others during Monday's session. It
  returns a 200 with a plausible number, so nothing in the client can detect it. If you use Finnhub
  for live quotes, compare the `t` timestamp against the clock rather than trusting the price.
- **`/stock/financials-reported` may not be premium** even though `/stock/financials` is. Our
  premium table lists it as blocked. `python scripts/fetch_finnhub.py --probe-premium AAPL` spends
  one call per endpoint and tells you which entries the table has wrong.

---

## 5. Why there is no honest backtest yet

Deferred to the backtest section, written when the engine landed.

---

## 5b. Verify these before trusting the two fetchers

Both clients were written against documented API shapes and have never made a request. The subagents
that built them flagged their own most likely failure points, and these are worth checking on the
first live run rather than discovering later:

**Finnhub**

- `Profile.market_cap` assumes `marketCapitalization` is in **millions**. If that is wrong, every
  market cap is out by a factor of a million and nothing in the fixtures can catch it. Check one
  ticker by eye first.
- The premium-endpoint table is a 2026 snapshot and Finnhub moves endpoints between tiers without
  notice. `python scripts/fetch_finnhub.py --probe-premium AAPL` spends one call per endpoint and
  tells you which the table has wrong.
- Whether a free key gets a real HTTP 403 or a 200 carrying `{"error": "You don't have access..."}`
  on a premium endpoint is unverified. Both are mapped to `PremiumEndpoint`, so either way it
  degrades rather than crashes, but only one path has ever been exercised.

**Prices**

- `YFinanceDownloader.download` is the one unverifiable line: no test calls `yfinance.download`, so
  the keyword arguments are signature-checked but not behaviour-checked. Compare the returned column
  layout against `tests/fixtures/yf_download_panel.json` on the first run. If it differs, the fix
  goes in `closes_from_download`, which is fully unit tested.
- A ticker whose data merely stops a few rows before the panel's last row (a foreign holiday, a data
  outage) is reported as `stopped_trading`. That is literally what the data says, but it is a false
  positive for a still-listed name. If it shows up, the fix is a small staleness tolerance in
  `_forward_one`.
- The split fixture is hand-built, so it proves the adjusted-close preference logic but not that
  Yahoo's own adjustment is right.

## 5c. The adversarial review, and what it found

After the layer was built, 124 agents reviewed it across four dimensions (numerical correctness,
whether the output could mislead, what breaks, and whether it lived up to its own claims). Every
finding then had to survive three independent skeptics who were told to default to rejecting it and
had to reproduce the failure with a real command. 38 survived. All 38 are fixed.

I spot-checked the two most serious myself before acting on either, and both reproduced exactly.

**The seven that made a page state something false**, in the order they would have misled you:

1. **Net debt percentages were sign-inverted on nine of sixteen pages.** Alphabet went from $84bn of
   net cash to $16bn of net debt and the card printed "-118.8%". `cagr` already refused negative
   bases, and the page fell back to `change_pct` precisely when it did, routing around the guard.
2. **Forward P/E was compared against a trailing median.** The screen builds its historical P/E from
   the fiscal-year-end price over that year's reported EPS. The current figure is a forward
   estimate. Where earnings grow, forward is mechanically below trailing, so the column read cheap
   by construction: 85% of names negative with a median of -29%, against 51% and -0.6% on EV/EBITDA
   where both sides are trailing. The bias also tracks growth (-0.28 rank correlation with revenue
   CAGR against -0.16), so the component was partly a second helping of the growth component.
3. **The memo ranked and sized names the analyst wrote "Pass" for.** It decided the candidate list on
   whether a research note file existed and never read the verdict at the bottom of it. Four of the
   sixteen say Pass or Watch. Applied Materials ranks in the top third and its note says "Pass for
   now", with a dollar band printed next to it.
4. **A journal heading naming five tickers produced one entry.** `## 2026-09-04 META NOW ACN AMAT
   NVR` matched a single `\S+`, so NOW, ACN, AMAT and NVR were logged calls that had stopped
   existing downstream.
5. **"n/a" is truthy.** That heading writes "n/a" for its falsifier and the fallback chain stopped
   there, so Meta's standing call rendered its falsifier as the word "n/a" while three real thesis
   killers sat unused in the same record.
6. **Every page said it was built on 1 January 1970.** `--check` pinned the timestamp so files could
   be diffed, and those were the files that got committed.
7. **Eighty pages showed "0.0x" net debt to EBITDA.** The upstream screen writes 0.0 for every
   net-cash name, so the best balance sheets in the universe read as the middle of the range under a
   "lower is better" column.

**The one that changed a number I had published.** `MEASURED_FALSE_POSITIVE_RATE` said 5.5 to 7.5
percent and the engine printed it on every result. It had only ever been measured with the holding
period equal to the rebalance spacing. Measuring the realistic case needed the synthetic generator to
be able to produce it, so it gained score persistence and a multi-step horizon. With four-to-one
overlap, at the sample size where the twelve-period floor stops protecting, the rate is **15.3%,
three times nominal**. Widening the bootstrap block made it worse, not better (13.3%, 17.5%, 26.7% at
1.5x, 2x and 3x). The fix is not in the engine: use a one-step forward return so the windows do not
overlap, which is what the archiving advice already said for a different reason.

**Two citations were doing work the papers do not do.** Novy-Marx (2013) is cited for profitability
everywhere, but his result is that *gross* profits over assets beats bottom-line measures, which is
an argument against ROIC and FCF margin rather than for them. And Boehmer, Jones and Zhang measure
daily short-sale order flow; what is free here is the fortnightly short-interest level, whose own
literature finds a weaker effect. Both docstrings now say what they are actually standing on.

**A CSS grid blowout at 390px.** Grid items default to `min-width: auto`, which resolves to
min-content, so one long headline pushed a page to 586px on a 390px screen. Found by the
overflow check in `scripts/shoot.py`, which exists because a full-page screenshot cannot show you
this: the image simply gets wider.

The rest were smaller: a "four-point median" caveat hard-coded when twelve names have two or three
points, the page and the score disagreeing on what counts as history, an inconsistency penalty that
rewarded companies with negative free cash flow, "nobody revised" counted as evidence, a variance
decomposition that ignored covariance, a falsifier de-duplicator that deleted distinct thresholds,
notes keyed by their heading so a stray copy could silently overwrite one, and a short table row
padded and then indexed positionally so every later column was read as the wrong metric.

**What this says about the work.** A review this productive on code that was written carefully, with
tests, means the tests were testing what I believed rather than what was true. The fixes all came
with regression tests that assert the *wrong* behaviour is gone, not just that the right one is
present, which is a different and better thing to check.

## 5d. A second review pass, and the one thing that cannot be fixed here

Two more hunting passes ran after the fixes above landed, one on the rendered pages in a real
browser and one on the diffs themselves looking for regressions the first round had introduced.
Eleven findings, ten fixed. In rough order of how badly each would have misled you:

1. **Two pages printed a median they had just called unusable.** Raising the own-history floor to
   three points changed `has_own_history`, but `_valuation_block` still emitted `own_median` and
   `vs_median` unconditionally. Dollar Tree and Paylocity showed `own median 19.9x / vs median
   -16%` two rows above the sentence "No usable own-history multiples for this name." The numbers
   are withheld now, and the caveat says which of the two situations applies: no data at all
   (currency mismatch, twelve names) or not enough of it (two year ends, two names).
2. **Three hard-coded "four"s survived the first fix.** The gaps line on all 150 pages, the rail
   caption under the P/E chart, and the sizing sentence in the positioning memo. 24 of 150 names
   have a two-, three- or zero-point median. All three now count what is actually there.
3. **Six of eight nav links 404'd from `/analyze/` and `/positioning/`.** `chrome(active, depth)`
   built the brand and the two new tabs from `depth` and hard-coded `../../` for the front-page
   anchors, which is right only from `/analyze/<TICKER>/`. `tests/test_site_links.py` now runs the
   real function in node at each of the three depths and checks every href against the filesystem.
4. **The quarter label was clipped on all sixteen deep pages.** It runs from 28 to 148 characters
   (a short marker, then the report date, then often a parenthetical about what the note did or did
   not say) and went into a one-line column capped at 27ch. Lam Research had 82% of it hidden.
   Split now: the marker keeps the column ("Q3 fiscal 2026", 14 characters), everything after the
   first comma or bracket goes under the headline where there is room. Not a tooltip, which is
   invisible on a phone. The first attempt at this split only moved the parenthetical and left
   thirteen of the sixteen still clipped, at 252px in a 179px box; the browser measurement caught
   it, reading the source did not. `tests/test_site_links.py` now runs the real split over every
   label the build produces and checks that the marker fits and that no word is lost from either
   half.
5. **Section 6 of `/positioning` ignored the variant switch.** It reads `score_structure[variant]`
   but only `#card` was redrawn, so the component correlations stayed on `quality_value` under a
   heading naming whichever variant had just been picked.
6. **The two-column footer could not be made responsive.** `grid-template-columns: 1fr 1fr` was an
   inline style, which no media query can override, so at 390px it was two 157px columns either
   side of a 44px gutter. It is a class in `desk.css` now, and a test rejects any inline
   multi-column grid in the scripts.
7. **A one-space journal heading parsed to nothing.** The multi-ticker repair required the
   template's two-space separator, which turned a typo in an append-only hand-written file into
   `parse()` returning `[]` for the whole file. Worse than the bug it replaced. The split is a
   function now, and falls back to taking ticker-shaped tokens from the left.
8. **Listeners accumulated on the analysis index.** Sorting replaced only `#tbl` and then rebound
   every handler on the page, including the search box outside it. Five sorts, and one keystroke
   redrew the table six times.
9. **A class in the markup with no rule anywhere.** `.cfx` was styled only by a wildcard. Declared
   by name now, with a test that every class the scripts emit resolves to a rule.
10. **A stale type annotation.** `what_would_be_wrong: List[str]` had been `List[Dict]` since the
    falsifiers gained a source and a date.

All ten are verified in headless Chromium at 1440px and 390px: every nav link returns 200 from all
three page depths, no `.ledger .m` clips on any of the sixteen deep pages, the two-column footer is
two columns at 1440 and one at 390, the variant switch redraws section 6, and five sorts followed by
one keystroke produce one table redraw rather than six. The only failing request anywhere on the
site is `/favicon.ico`, which the existing `index.html` has always 404'd on and which no page here
declares either; the new pages make no failing requests of their own.

Two of these needed a second attempt, and both times the browser measurement caught what reading
the source had not: the quarter-label split moved only the parenthetical and left thirteen of the
sixteen pages still clipped, and the memo's rewritten sizing sentence started with a numeral. There
is a guard for the second now (`test_no_generated_prose_sentence_opens_with_a_numeral`), scoped to
the fields this project writes as prose rather than to the value cells beside a label, which are
legitimately numeric, or to quoted text, which belongs to whoever wrote it.

Finding 1 also exposed a paragraph that had gone stale in the same move. The forward-vs-trailing
warning on every valuation block was written by hand and said "across the 138 names that carry both
comparisons"; raising the history floor left 136, and the EV/EBITDA figures beside it had drifted
from 51% and -0.6% to 50% and 0.0%. It is computed from the panel at build time now
(`analysis._basis_warning`), with a test that checks it against a fresh count rather than against a
string. The same figures appear in `score.py`'s module docstring, which cannot recompute itself; a
second test fails if the docstring and the measurement disagree.

**The eleventh cannot be fixed under the brief.** The front page's Journal tab and the new pages
disagree about the decision log. `scripts/build_dashboard.py` parses headings with `(\S+)` for the
ticker, so `## 2026-09-04 META NOW ACN AMAT NVR  Recommendation: Watch or Pass` becomes one entry
under META whose title is `NOW ACN AMAT NVR  Recommendation: Watch or Pass`. The front page shows
thirteen names and fourteen entries; `/positioning/` shows sixteen. Both the generator and its
output (`dashboard/data.js`) are on the untouchable list, and the brief said not to modify existing
routes beyond adding links, so this is left alone deliberately.

The repair, if that constraint is ever lifted, is one line: give `build_dashboard.py` the grammar
in `scripts/an/journal.py`. Two tests in `test_nothing_existing_was_touched.py` pin the divergence
so it reads as a known decision rather than a fresh bug, and both fail the day someone fixes it,
which is how you will know to delete them and this note.

## 6. Tasks skipped, and why

Nothing skipped. Every task in PLAN.md either passed its stated verification or is still open.

One thing was **deliberately not done**: the `dd_ath` basis mismatch in section 3 is a one-line
change in `scripts/price_screen.py`, and I did not make it. That file drives the bucket assignments
the whole system rests on, and the brief said not to modify existing routes. It is flagged in
SUMMARY.md for Joseph to decide.

Similarly, the score was **not retuned** in response to the redundancy findings in section 3.
Adjusting weights until a diagnostic looks tidy is how overfitting starts, and the finding is more
useful than a quietly fixed number.

---

## 7. Second session, 2026-09-06: the backlog

Picked up from HANDOFF.md. Same egress situation as the first session: `www.alphavantage.co`,
`www.sec.gov`, `data.sec.gov`, `finnhub.io` and `query1.finance.yahoo.com` all refuse the connection,
so everything below is written to documented shapes and tested against fixtures, exactly as before.
Nothing in this section has made a real request.

### 7a. X7, the survivorship hole, measured rather than guessed

`scripts/an/listing_status.py` and `scripts/listing_status.py`. Alpha Vantage's `LISTING_STATUS`
endpoint, two requests (`state=active`, `state=delisted`), cached a week under
`data/cache/alphavantage/`. The key is `ALPHAVANTAGE_KEY` from the environment only, and every URL
that is printed, logged or cached goes through `redact()`, which already treats `apikey` as a secret.

**What it computes.** From the two files the listed market on any past date can be reconstructed: a
row existed on date D if it listed on or before D and had not been delisted by D. The report then
applies the one step-1 rule the file can answer (three years listed, from `ipoDate`), restricts to
NYSE and Nasdaq common stock, and counts how many of those names carry a delisting date today. That
is done at one, three, five and ten years back by default, and at any `--as-of` date on request.

**What it cannot compute, and says so on every run.**

- The file has no market cap and no volume, so the count is over the whole listed market, not the
  $2bn-plus slice the screen selects. Big names leave less often, so it is an upper bound on the
  screen's own attrition.
- The file gives no reason for a delisting. Acquisition is the largest one, and an acquired holder
  was usually paid up, not wiped out. The synthetic engine's assumption (four of 150 leave every
  quarter, each at a 50% loss, about 10% a year) is the pessimistic end, and the report prints that
  number next to the measured one so the two can be read together.
- No prices for the departed names. This sizes the hole; it cannot fill it.
- Symbols get reassigned. Each row is its own listing and the report counts how many symbols carry
  more than one, so the reader knows how much of that there is.

**Failure modes handled.** Alpha Vantage answers every failure with a 200 and a small JSON body, so
the parser's first job is to notice that the CSV is not a CSV. A daily-quota note becomes
`RateLimited` and is never cached (a cached quota note would look like data for a week); anything
else becomes `BadKey`, with the server's text redacted before it is quoted, because an error body
tends to echo the URL and the URL carries the key. A changed column set raises rather than
mis-parsing.

**Provenance is a first-class output.** The cache meta records the transport class and a `live`
flag that is true only for `HttpTransport`. `provenance()` reads it back, `--dry-run` prints
"ever made a real request: no, never", and the report's first line names the source. A report on the
committed fixture opens with a banner saying it measures nothing.

**The fixture.** Twenty-two hand-built rows in `tests/fixtures/av_listing_*.csv`. Active rows are
real tickers with the first-trade dates `universe_latest.csv` already carries. Delisted rows are
fictional (`FAK*`, `ZZZA`) apart from Twitter, so no false corporate history is attached to a real
company. The answers in `tests/test_listing_status.py` were computed by hand before the code ran:
as of 2016-09-06, eleven eligible and four gone; edge cases for a delisting on the as-of date
(gone), the day after (listed), a null listing date (skipped and counted), an ETF on an allowed
venue (excluded), and a reassigned symbol (two listings, counted once as reused).

**Most likely to be wrong on the first live run**, for whoever runs it:

1. Exchange spellings. The parser matches `NYSE` and `NASDAQ` exactly after upper-casing. If the
   live file says `NYSE MKT`, `NASDAQ GS` or similar for names that should count, `SCREEN_EXCHANGES`
   needs widening and the by-exchange table in the report will show it immediately.
2. The cross-check against the current universe should find nearly every US name. On the fixture it
   finds nine of 1,505, which is the fixture's size, not a bug. On live data a large "not covered"
   count means the symbol conventions differ (dots versus dashes are already tried both ways).
3. The quota note's wording. `_LIMIT_MARKERS` is matched against the JSON body's text; if Alpha
   Vantage rewords it, the note lands as `BadKey` instead of `RateLimited`. Either way it is not
   cached and the run stops.

### 7b. X8, the DERA data sets as the point-in-time fundamentals source

`scripts/an/dera.py` and `scripts/fetch_dera.py`. One zip per calendar quarter from
`www.sec.gov/files/dera/data/financial-statement-data-sets/`, 50 to 100 MB each, every numeric fact
from every filing accepted that quarter, as filed. Kept on disk under `data/cache/dera/`, one
request each, never re-fetched. User-Agent from `SEC_USER_AGENT`, same as `edgar.py`.

**Why it is the right source for a backtest and the wrong one for a screen.** `companyfacts` is
one file per company; a backtest over 1,500 names would need 1,500 pulls of 15 to 25 MB. The DERA
sets are the same facts cut the other way, every company per quarter, so twelve files are three
years of the whole market. The cost is the lag: a set is cut a few weeks after quarter end.

**Four format facts the loader handles, each of which would have produced a quietly wrong number.**

1. `qtrs` is the period length and `ddate` its end. There is no start date. The same tag at the
   same `ddate` with `qtrs` 1 and `qtrs` 3 is a quarter and a year-to-date figure, both present in
   every 10-Q. The loader keeps both and every lookup names its `qtrs`.
2. `coreg` non-blank is a subsidiary or co-registrant, not the company. Dropped unless asked for.
   In the Notes variant of the sets `dimh` marks dimensioned rows (a segment, not the total) and
   only `0x00000000` is the consolidated number.
3. A fact recurs across filings. `first_reported` is the earliest filing carrying a period;
   `as_known_on` is the latest filing on or before a date. The fixture's fictional restater shows
   1,000,000 on 2023-09-30 and 950,000 on 2023-12-31, and its 10-K/A counts from its own filing day.
4. Q4 is never filed. `derive_fourth_quarters` subtracts the nine-month figure from the year and
   stamps the result with the **10-K's** filing date, because that is when the quarter became
   knowable. A derived fact says `derived=True` and a year without a nine-month figure gets a hole,
   not a guess.

**The verification the plan asked for.** `tests/test_dera.py` loads the hand-built miniature of
`2023q4` and reconstructs Apple's FY2023 net sales, 383,285,000,000, from `num.txt` by tag priority
(`RevenueFromContractWithCustomerExcludingAssessedTax`), with the 10-K's accession and filing date
attached. Across `2023q3` and `2023q4` it also reconstructs the three-year annual series and derives
the fourth quarters of FY2023 and FY2022 as 89,498 and 90,146 million, which are the figures Apple
reported for those quarters. The second of those was not planned: the 10-Q's comparative nine months
and the 10-K's comparative year are enough to derive the prior year's Q4 too, and the code did.

**The fixture.** `tests/fixtures/dera/2023q3/` and `2023q4/`, four tab-separated tables each with the
documented 36-column `sub.txt` header and 9-column `num.txt` header. Apple's rows carry the figures
its filings report; `EXAMPLE RESTATER CORP` is fictional and exists for the restatement, the
co-registrant row and the footnote-only row.

**Most likely to be wrong on the first live file.**

1. The zip's member names. The loader looks for `sub.txt` and `num.txt` case-insensitively and
   raises naming the members it found if either is absent.
2. Encoding. Read as UTF-8 with replacement; a company name with a stray byte will show a
   replacement character, which is cosmetic. If the header itself mis-decodes the loader raises.
3. The `accepted` timestamp format is kept as a string and not parsed, on purpose, so a format
   change cannot break a load. `filed` is the point-in-time stamp and is a plain `YYYYMMDD`.
4. Memory. A full quarter streams through `num.txt` once and keeps only the CIKs and tags asked
   for. Loading a quarter with no filter keeps every row, which for a real set is millions of
   dataclasses; filter by `ciks` for anything real.

### 7c. X5, the compare view

`dashboard/analyze/compare/index.html` and `dashboard/assets/compare.js`. The URL is
`/analyze/compare/?t=KLAC,BKNG,AAPL,NVDA`: a real directory with one shell, the selection in the
query string, read by the page rather than by a server, so the existing static server and Vercel
both serve it unchanged. The index page and every deep page link to it.

**Design.** A table, one column per name, rows grouped in the deep page's order: the names, the
standing call with its falsifier on the row beneath it and the same class, four fiscal years with the
sparkline and the move over the window, the screen measures, valuation, the score, provenance. Three
rules from the deep page carry over. Valuation multiples and drawdowns are never coloured and never
marked best. The best cell in a row is marked only where the row declares which way is better,
only with two or more real values, and never on a tie, so a one-name comparison marks nothing and
"price" and "market cap" never do. Missing is `n/a`, never zero: Booking's gross margin row says so
because Booking reports no cost of revenue.

**Verification.** The row-building half of the script is pure and exported as
`window.DeskCompare`; `tests/test_compare.py` evaluates the real function in node over the committed
records and asserts that each cell is the record's own number, that valuation carries no `best` and
no colour, that the call and the falsifier share a class, and that the address parser upper-cases,
de-duplicates, caps at four, and reports unknown names rather than dropping them. `shoot.py` renders
the page with four names and with none, at 1440px and 390px, in two themes, with zero console errors
and no horizontal overflow; the table scrolls inside its own container with the row labels sticky.

**Two things the first screenshot caught.** Columns came out in fetch-completion order rather than
address order (fixed: the address decides), and at 390px the row labels inherited the stylesheet's
`white-space: nowrap` on `th` and ran under the first value cell (fixed: the compare table's row
headers wrap).

