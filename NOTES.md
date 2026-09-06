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

**One snapshot only.** Every row in `universe/` is `pulled = 2026-09-04`. There is no history. This
is the single biggest constraint on the whole backtest question — see section 5 and
`dashboard/backtest.json`.

**`fiscal_years` is four years, not five.** Already documented in README and `criteria.md`. It means
every CAGR here is a 3-year CAGR. The analyse pages label it that way rather than saying "5 year".

---

## 4. Free data sources

Filled in from the research sweep. See section 4b for the verified table.

---

## 5. Why there is no honest backtest yet

Deferred to the backtest section, written when the engine landed.

---

## 6. Tasks skipped, and why

Nothing skipped yet.
