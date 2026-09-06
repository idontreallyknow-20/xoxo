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

**"What changed since the last report" is the section to judge this on.** For the 16 researched
names it has guidance changes with the verbatim quote underneath, tone with its evidence and the
reason its confidence is not higher, new risks flagged as new or already in the thesis, what
management appears not to have addressed, and a list of what the sources do not contain. All 122
quotes across the 16 are checked verbatim against their research note by
`tests/test_narrative_quotes.py`, because a paraphrase presented as a quote is a fabrication with a
citation attached.

**Valuation has two lenses now, not one.** The pipeline only compared a company to its own four-year
median, which says nothing about whether the old multiple was deserved. Each page now also places
the name among its industry peers on price and on the things a price is supposed to reflect, and
reports the gap. Adobe is cheaper than 94% of application software on forward earnings while ranking
better than 81% of them on return on capital. 75 of the 150 are genuinely unremarkable and the page
says so in those words.

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

Full table in `NOTES.md` section 4, with every "it's free" claim adversarially re-checked.

**SEC EDGAR is the only unconditionally free, complete source here.** Statutory US government
disclosure, not a freemium product: no key, no account, no tier, no rate card, and no mechanism by
which a paid tier could appear. Its XBRL facts carry filing dates, which is what makes point-in-time
work possible at all.

**Earnings call transcripts: the honest answer is no.** There is no free, legal, automatable source
of full transcripts with Q&A. FMP puts them on a $139/month tier. Alpha Vantage costs a quarter of
your entire daily quota per ticker-quarter. API Ninjas gates the endpoint and bans commercial use.
Motley Fool publishes genuinely good ones free to read, and their terms prohibit automated access.
The closest free substitute is the 8-K Exhibit 99.1 press release, which carries the guidance
language and the prepared numbers but not the questions, and every analysis page says so in its gaps
section rather than implying a transcript exists.

**The one find worth acting on: Alpha Vantage's `LISTING_STATUS`.** It returns every delisted US
ticker with its delisting date for **two requests in total**, which is affordable even on a
25-per-day free key, and it is the only free source of that list found. It cannot repair a
survivorship-biased backtest, because it gives no prices for those names, but it can *measure* the
hole: run the universe filter as of a past date, count how many selected names no longer exist, and
you have sized the bias instead of guessing at it. On the backlog as X7.

**Other things not worth using**, so you do not waste an evening on them: Stooq now serves a
JavaScript proof-of-work challenge instead of CSV. Alpha Vantage for prices is 25 requests a day, so
150 tickers is a six-day refresh. Nasdaq Data Link's WIKI table stopped at 2018-03-27 and will never
advance.

That research also caught **four real bugs in the EDGAR client I had already written**, all of which
would have produced quietly wrong numbers rather than errors. The worst: XBRL's `fy` and `fp` fields
describe the *filing*, not the fact's own period, so a 2018 revenue figure carries `fy=2021` when it
appears as a comparative column in the FY2021 10-K. My `is_annual` read `fp == "FY"` and would have
called a single quarter annual whenever it turned up in a 10-K. All four are fixed and documented in
`NOTES.md`.

---

## The adversarial review

After building it I had 124 agents review the whole layer, each finding checked by three independent
skeptics who had to reproduce it with a real command. **38 findings survived. All 38 are fixed.**
`NOTES.md` section 5c has the full list; the seven worst made a page state something false:

- Net debt percentages were sign-inverted on nine of sixteen pages. Alphabet's $100bn swing from net
  cash to net debt printed as "-118.8%".
- Forward P/E was compared against a trailing median, so 85% of names read cheap by construction
  against 51% on the like-for-like EV/EBITDA comparison. That component's weight is now the smaller
  of the two.
- **The memo ranked and sized names your notes say to Pass on.** Applied Materials ranks in the top
  third and its note says "Pass for now". There are three lists now: buy candidates, read-and-declined
  with the verdict quoted and no size, and the research queue.
- A journal heading naming five tickers produced one entry, so four logged calls had stopped existing.
- "n/a" is truthy, so Meta's standing call showed its falsifier as the word "n/a".
- Every page said it was built in 1970.
- Eighty pages showed "0.0x" leverage for companies with no debt at all.

And one that changed a number I had already published: the engine's false-positive rate was measured
only in the configuration that flatters it. Under overlapping windows it is **15.3%, three times
nominal**, and widening the bootstrap makes it worse. That is now on every overlapping result.

## A second pass, and the one thing I could not fix

After those 38 landed I ran two more hunting passes, one driving the real pages in a headless
browser and one reading the diffs for regressions the fixes had introduced. **Eleven findings, ten
fixed.** `NOTES.md` section 5d has them all. The three worth knowing about:

- **Two pages printed a median they had just called unusable.** Raising the own-history floor to
  three points changed the flag but not the rows, so Dollar Tree and Paylocity showed
  `own median 19.9x / vs median -16%` directly above "No usable own-history multiples for this
  name." Withheld now, with a caveat that says which situation applies.
- **Six of eight nav links 404'd from `/analyze/` and `/positioning/`.** Introduced by me: the
  chrome function took a depth argument and then ignored it for the front-page anchors. A test now
  runs the real function at each page depth and checks every link against the filesystem.
- **A one-space journal heading parsed to nothing.** My own fix for the five-ticker bug required
  the template's two-space separator, which turns a typo in a hand-written append-only file into
  the whole log vanishing. If you had typed one space tonight you would have woken up to an empty
  journal on both new pages. It falls back now.

All ten are verified in a real browser at 1440px and 390px: nav links resolve from every page depth,
nothing clips on the sixteen deep pages, the footer collapses to one column on a phone, and the
index redraws once per keystroke instead of six times.

**The eleventh I left alone on purpose, and you should decide it.** The front page's Journal tab
still shows thirteen names where `/positioning/` shows sixteen, and one entry titled
`NOW ACN AMAT NVR  Recommendation: Watch or Pass` under META. That is the original five-ticker bug,
still live in `scripts/build_dashboard.py`, which is on the do-not-touch list along with the
`dashboard/data.js` it writes. The fix is one line (give it the grammar in `scripts/an/journal.py`)
and I did not make it, because you said not to modify existing routes. Say the word and it is a
two-minute change. Two tests pin the divergence in the meantime so nobody mistakes it for new.

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
5. **Start archiving snapshots monthly, not quarterly.** `python scripts/snapshot.py`, added to your
   pipeline. This is the single most actionable thing in the whole run and the reason is measured,
   not asserted:

   | archiving | first verdict possible | detects a typical published signal |
   |---|---|---|
   | quarterly | 3.0 years | 8.2 years |
   | monthly | 1.0 years | 2.8 years |

   The engine refuses to say more than "weak" below 12 independent periods, which is 3 years of
   quarterly snapshots and 1 year of monthly ones. Fundamentals only move quarterly, but the score
   does not: prices, drawdowns and estimate revisions move continuously and carry most of its
   realised variance. `/positioning` shows the full table and the detection rates measured through
   the real engine.

   The sharpest way to put the problem: **one quarter of 150 names can only detect a rank IC of
   about 0.23, and the engine flags anything above 0.25 as probable look-ahead.** There is no window
   in which a single-quarter backtest tells you something both detectable and believable.
6. **The `dd_ath` basis mismatch** in finding 1 above is a one-line change in `price_screen.py`. I did
   not make it, because you said not to modify existing routes and that file drives the bucket
   assignments the whole system rests on.

---

## Second session: the backlog

A second session picked up the backlog in HANDOFF.md on the same day. Same constraint: this
container cannot reach Alpha Vantage, the SEC or Finnhub either, so everything below is fixture-tested
and **has never made a real request**. `NOTES.md` section 7 has the detail.

### X7, the survivorship hole, done as far as it can be without egress

```bash
python scripts/listing_status.py --dry-run                        # two URLs, key redacted, nothing sent
export ALPHAVANTAGE_KEY=...                                        # free, 25 requests a day; this uses 2
python scripts/listing_status.py --fetch                          # then the report
python scripts/listing_status.py --report --fixture --today 2026-09-06   # the report's shape, on a fixture
```

It reconstructs the listed US market on a past date from the two Alpha Vantage files, applies the
screen's three-years-listed rule, and counts how many of those NYSE and Nasdaq common stocks no
longer exist, at one, three, five and ten years back. The number it prints is an **upper bound on
the screen's attrition** (the file has no market cap, so it is the whole market, and it gives no
reason, so acquisitions count too), and the report says both of those things above the table. It
also prints the synthetic engine's assumption next to the measured rate so you can see whether the
"more than two points" bias figure was computed on a pessimistic or an optimistic attrition rate.

The first line of every report says where the data came from. Until you run `--fetch` once, it says
"NOT a real request".

## What I skipped, and why

See `NOTES.md` section 6 for the running list.

---

## The numbers

| | |
|---|---|
| tests | 728 (699 from the first session, 29 for X7) |
| review findings confirmed and fixed | 48 of 49 (the last one is out of scope, above) |
| new Python modules | `scripts/an/` |
| analysis pages generated | 150 |
| quarter narratives, quote-verified | 16 (122 quotes, 0 unverified) |
| peer valuations computed | 150 |
| lines added to `dashboard/index.html` | 2 |
| existing files otherwise modified | 0, enforced by `tests/test_nothing_existing_was_touched.py` |
| clean-clone check | full suite passes, rebuild byte-identical apart from `built_at` |
| money spent | none |
| API keys in any generated file | none, enforced by `tests/test_privacy.py` |

Research and analysis from public data, not personalised financial advice.
