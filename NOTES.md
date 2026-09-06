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

## 4. Free data sources

Filled in from the research sweep. See section 4b for the verified table.

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

## 6. Tasks skipped, and why

Nothing skipped. Every task in PLAN.md either passed its stated verification or is still open.

One thing was **deliberately not done**: the `dd_ath` basis mismatch in section 3 is a one-line
change in `scripts/price_screen.py`, and I did not make it. That file drives the bucket assignments
the whole system rests on, and the brief said not to modify existing routes. It is flagged in
SUMMARY.md for Joseph to decide.

Similarly, the score was **not retuned** in response to the redundancy findings in section 3.
Adjusting weights until a diagnostic looks tidy is how overfitting starts, and the finding is more
useful than a quietly fixed number.
