# Stock research system

A repeatable process for finding good companies at reasonable prices, logging every call, and checking whether the process beats an index. Claude does the research and bookkeeping. You make every buy and sell decision. Nothing here connects to a brokerage.

Time horizon 2 to 5 years per position. Taxable Canadian account. $100,000 USD starting capital, not all of it deployed.

## Folder map

| Path | What it is |
|---|---|
| `criteria.md` | The selection rules. Edit freely. |
| `journal.md` | Append only decision log. Every recommendation goes here before you act. |
| `portfolio.md` | Generated. Holdings, cash, scorecard vs SPY, QQQ and VFV. |
| `portfolio/holdings.csv` | What you actually own. You edit this after a trade. |
| `portfolio/cash.csv` | Cash ledger. Add a row whenever cash changes. |
| `portfolio/picks.csv` | Current recommendations: buy zone, dollars, conviction, thesis, wrong if. |
| `universe/` | Screen outputs as CSV, dated. `*_latest.csv` is always the newest. |
| `research/TICKER.md` | One deep dive per shortlisted company. Template in `research/_TEMPLATE.md`. |
| `data/cache/` | Cached pulls from Yahoo so we do not hammer the API. Safe to delete. |
| `dashboard/index.html` | The dashboard. Run `python scripts/serve.py` and open http://localhost:8765. Tabs: Overview, Picks, Rankings, Holdings, Charts, Settings. |
| `scripts/` | Python. See below. |

## Setup once

```bash
pip install -r requirements.txt
```

Python 3.10 or newer. Data comes from Yahoo Finance through the `yfinance` package. It is free, unofficial and rate limited. If a pull fails with "Too Many Requests", wait ten minutes and run it again. Cached tickers are skipped, so reruns are cheap.

## The pipeline, in order

```bash
python scripts/universe.py          # Step 1. US + Canada, >$2B, liquid, 3+ years listed. ~2 min
python scripts/fundamentals.py      # Pulls 4 years of statements for every name. ~10 min, cached 30 days
python scripts/quality_screen.py    # Step 2. Scores 0 to 100, keeps the top 150
python scripts/price_screen.py      # Step 3. Valuation vs own history, drawdowns, estimate revisions, buckets. ~3 min
python scripts/write_research.py    # Regenerates research/TICKER.md for the shortlist from research_notes.py
python scripts/build_dashboard.py   # Rebuilds dashboard/data.js and portfolio.md
python scripts/serve.py             # Serves the dashboard and rebuilds every 15 min during market hours
```

The page polls for new snapshots on its own. Paste a free Finnhub key in Settings for quotes that update every minute in the browser.

`fundamentals.py AAPL MSFT` forces a fresh pull for specific tickers.

## What the scripts write

- `universe/universe_latest.csv`: the ~1,900 name universe with market cap and dollar volume.
- `universe/quality_scores_latest.csv`: every scored name, all inputs, all six component scores.
- `universe/quality_top150.csv`: the keepers.
- `universe/excluded_*.csv`: what was dropped and why (banks and insurers, Chinese ADRs, too few years of data). Nothing is silently hidden.
- `universe/price_screen_latest.csv`, `bucket_compounders.csv`, `bucket_cyclical_turns.csv`.

Every row carries a `pulled` date and a `source` column.

## After you buy or sell

1. Add or edit the row in `portfolio/holdings.csv` (ticker, shares, cost per share, currency, date, bucket, the "wrong if" price).
2. Add a row to `portfolio/cash.csv` with the new cash balance.
3. Append a journal entry saying what you did and at what price. Never edit old entries.
4. `python scripts/build_dashboard.py`.

## Cadence

- **Weekly, Monday**: ask for the weekly. Price update, news that touches a thesis, earnings in the next two weeks, watchlist names that entered their buy zone. Under 300 words. No trade recommendations unless something material happened.
- **Quarterly** (mid Feb, May, Aug, Nov): full re-underwrite of every holding and watchlist name, rerun the whole pipeline, update the scorecard.
- **Event driven**: a holding drops 15%+ in a week or hits its "wrong if" trigger. Write up whether the thesis is broken or the market is wrong.
- **Annual**: honest review. Hit rate, what the misses had in common, whether `criteria.md` needs changing.

Do not run research more often than this.

## Known data limitations

- yfinance returns 4 fiscal years of annual statements, not 5. Every "5 year" metric is computed over 4 years and labelled as such in `criteria.md`.
- Historical valuation medians use price at each of those 4 fiscal year ends, so they are a 4 point median.
- Analyst estimate history is limited to 7, 30, 60 and 90 days back.
- Banks, insurers and lenders are set aside rather than mis-scored.
- If any of this matters for a decision, a paid source (Financial Modeling Prep or EODHD, roughly $20 to $80 a month; S&P Capital IQ if you have access) fixes all four. Ask and it gets wired in.

## Ground rules

Claude researches, you decide. Every recommendation is logged before you act so the scorecard is honest, and missed calls count. If the process is not beating a simple index after 12 months, `journal.md` will say so plainly. Sales that realise a large gain get flagged for tax timing.

Research and analysis from public data, not personalized financial advice.
