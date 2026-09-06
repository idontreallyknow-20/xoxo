# SUMMARY — read this first

Overnight run of 2026-09-06. Branch `claude/stock-analysis-scoring-xhuyl9`.

---

## The one thing to know before you look at anything

**The brief described a Next.js site on Vercel with `/app/api` routes and a `FINNHUB_KEY`. This
repository is not that.** It is a Python pipeline plus one static page, `dashboard/index.html`,
served by `scripts/serve.py`. I checked the private twin `idontreallyknow-20/investing` as well; it
is the same project with `portfolio/` committed instead of gitignored. Neither has a `package.json`.

I built into the stack that is actually here rather than scaffolding a Next.js app on top of it,
because "do not restructure or break any existing pages" and "add a whole framework" cannot both be
done. The URLs you asked for exist and work: `/analyze/<TICKER>` and `/positioning` are real
directories with an `index.html` in them, which the existing server already serves, with **zero
changes to `serve.py`** and a **two-line diff to `index.html`** (the two nav links). Same layout
works on Vercel if you deploy `dashboard/` as static.

If you did mean a different repository, say so and I will port this across; the whole analysis layer
is framework-independent Python plus vanilla JS.

**Second thing:** this container had no network access to any market-data host. `finnhub.io`,
`data.sec.gov` and `www.sec.gov` are all refused at the egress proxy. So the EDGAR and Finnhub
clients are written, documented and unit-tested against fixtures, **and have never made a real
request**. Everything built from local data is real and verified end to end.

---

## Open it

```bash
python scripts/serve.py       # then http://localhost:8765
```

- `/analyze/` — index of all 150 names, sortable, filterable by depth
- `/analyze/KLAC/` — the deep page. KLAC and BKNG are the best two to judge it on
- `/analyze/AAPL/` — what a screen-only name looks like, so you can see it degrade honestly
- `/positioning/` — the scorecard, the memo, and why there is no backtest

If nothing renders, run `python scripts/build_analysis.py && python scripts/build_positioning.py`.

---

## What I built

### 1. Deep analysis pages, `/analyze/<TICKER>`

150 pages, one per name in the quality top 150. Three depths, labelled on the page:

| depth | count | what is behind it |
|---|---|---|
| research note | 16 | a hand-written deep dive: business, four-year statement table, last two calls, bear case, named thesis killers, sourced URLs |
| screen only | 134 | fundamentals, valuation against own history, estimate revisions. Nobody has read a filing |

Each page carries, in this order: the standing call with its falsifier, what changed since the last
report, what the business does, four fiscal years of numbers, screen measures, valuation, what would
break it, why it is on the list, where the score comes from, and provenance.

Two design rules are enforced in the stylesheet rather than in a comment:

- **The call and the falsifier share one CSS class with no modifier.** The thing that would prove
  the idea wrong physically cannot end up smaller than the idea.
- **Valuation multiples and drawdowns are never coloured.** Green and red are reserved for business
  numbers. A 44% fall is the reason the cyclical-turn bucket exists, and colouring it red would
  settle that question before you had read anything.

### 2. Scorecard and positioning, `/positioning`

A documented 13-component cross-sectional score with three variants, over the 150. Every component's
docstring says what evidence it rests on. Missing data contributes zero and every name carries a
coverage fraction, so a name ranked 8th on 55% coverage is not really ranked 8th and the page says so.

The memo is **two lists on purpose**:

1. **Candidates with a written falsifier** — the 16 researched names, ranked, sized by the rules in
   `criteria.md`, each with what would prove it wrong.
2. **The research queue** — the names the score ranks highest that nobody has read. Labelled as
   candidates for the next deep dive, **not** candidates for capital.

That split matters: the score's single top name is NVDA, which nobody has researched. The page says
that is a gap in the process rather than a recommendation.

### 3. The backtest, and why there isn't one

**`dashboard/backtest.json` says `status: NOT RUN`, and I think that is the most valuable thing in
this whole run.**

There is exactly one dated cross section in this repo (2026-09-04) and no price history. A
cross-sectional score is validated by asking, on many past dates, whether its ordering predicted what
came next. With one date there is no next. I could have produced something that looked like a
backtest. I did not.

What exists instead, and is real:

- **A backtest engine**, calibrated. A `BacktestResult` cannot be constructed with an empty
  limitations list; that is enforced in `__post_init__`. Overlapping windows get a moving-block
  bootstrap and both the raw and effective independent sample counts are reported. Verdicts top out
  at "supported" and never reach "proven".
- **Its own false-positive rate, measured**: 5.5% to 7.5% against a nominal 5%, over 200 synthetic
  null panels per sample size. Roughly one "suggestive" finding in thirteen from this engine is
  nothing at all. That number is printed in the limitations of every result it produces.
- **Calibration against known answers**: planted alpha recovered, pure noise reported as no evidence
  across eight seeds, a deliberately look-ahead-contaminated panel flagged rather than celebrated,
  and survivorship bias given a number (dropping the names that failed flatters the bottom quintile
  by more than two points).
- **`edgar.as_known_on()`**, which reads XBRL facts by their filing date so a future backtest sees
  what was public on the day rather than a figure restated two years later. A test proves it on a
  simulated restatement.

---

## What the analysis found that you did not ask for

These came out of building it and are worth your attention:

1. **`dd_ath` and `dd_52w` are not on the same basis.** `price_screen.py:40` computes the all-time
   high as a maximum *closing* price; `high_52w` is an *intraday* high from the Yahoo screener. For
   75 of the 150 names `ath < high_52w`. The cyclical-turn bucket filters on `dd_ath <= -0.35`, so
   that filter is stricter than `criteria.md` describes and the bucket of 12 is probably too small.

2. **Your three estimate-revision components are one idea.** They correlate 0.54 to 0.77 with each
   other and carry a quarter of the score's weight between them. `revisions_90d` alone drives 21% of
   realised score variance on a nominal weight of 11%. Thirteen named components behave like about
   9.8 independent ones. I did **not** retune in response: tuning weights until a diagnostic looks
   tidy is how overfitting starts.

3. **The new score and your existing quality screen agree only +0.45 on ranks.** They disagree
   substantially, which means at least one of them is wrong about something. Worth an hour.

4. **The momentum and reversal variants rank the list at 0.88 correlation** despite taking opposite
   views of a drawdown on purpose. At a 10% weight, the argument between "buy the drawdown" (your
   cyclical bucket) and "buy near the high" (the literature) barely moves the answer here. That does
   not settle it; it says the question is cheaper than it looks.

5. **Booking reports no gross margin line** (no cost of revenue), so its gross-margin row is `n/a` in
   all four years. The page says "not reported" rather than drawing an empty chart.

6. **Three of your sixteen notes name no price trigger** (ACN, AMAT, NVR). AMAT says so outright. The
   pages show "not set" rather than inventing one, because an absent trigger is not a trigger of zero.

---

## Data sources: what is actually free

Full detail in `NOTES.md`. The short version:

- **SEC EDGAR** is the real prize: no key, no tier, complete, and its XBRL facts carry filing dates,
  which is what makes point-in-time backtesting possible at all. Client written, never run.
- **Earnings call transcripts are not reliably free.** The closest free substitute is the 8-K
  Exhibit 99.1 press release on EDGAR, which has the guidance language but not the analyst Q&A. The
  analysis pages say this explicitly in their gaps section rather than implying a transcript exists.
- **Finnhub free tier** covers quotes, profile, ~100 pre-computed metrics, earnings and news.
  Historical candles moved to premium. Client written, never run.

---

## Review this in the morning

1. **Confirm the repo question.** Is this the site you meant, or is there a Next.js project
   somewhere I could not see?
2. **Run one live pull** and read the output before trusting either client:
   ```bash
   export SEC_USER_AGENT="Joseph <your email>"
   python scripts/fetch_edgar.py --facts --exhibits KLAC
   export FINNHUB_KEY=...          # never commit it
   python scripts/fetch_finnhub.py KLAC
   ```
3. **Look at `/analyze/KLAC/` and `/analyze/AAPL/` side by side.** The second is what 134 of the 150
   pages look like. Tell me whether that degradation is honest enough or too thin to be worth having.
4. **Read `dashboard/backtest.json`'s `why_not_run`.** If you disagree that a backtest is impossible
   here, say so and tell me what you think I missed.
5. **Decide on snapshot archiving** (`PLAN.md` item X1). It is the only thing that turns the backtest
   question from impossible into merely slow: archive a dated panel every pipeline run and after four
   quarters you have four real rebalances with no look-ahead and no survivorship problem.
6. **The `dd_ath` basis mismatch** in finding 1 above is a one-line change in `price_screen.py`. I did
   not make it, because you said not to modify existing routes and that file drives the bucket
   assignments the whole system rests on.

---

## What I skipped, and why

See `NOTES.md` section 6 for the running list.

---

## The numbers

| | |
|---|---|
| tests | see `python -m pytest -q` |
| new Python modules | `scripts/an/` |
| analysis pages generated | 150 |
| lines added to `dashboard/index.html` | 2 |
| existing files otherwise modified | 0 |
| money spent | none |
| API keys in any generated file | none, enforced by `tests/test_privacy.py` |

Research and analysis from public data, not personalised financial advice.
