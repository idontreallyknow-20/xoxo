#!/usr/bin/env python3
"""Pull the desk's daily closes on a machine that can reach Yahoo, and write them as files.

    python scripts/fetch_quotes.py --dry-run                  # the ticker list and where the files would go
    python scripts/fetch_quotes.py                            # pull, write data/cache/quotes/ (gitignored)
    python scripts/fetch_quotes.py --out data/quotes          # what the GitHub Actions workflow runs
    python scripts/fetch_quotes.py --tickers AAPL,MSFT,SPY    # a narrow pull
    python scripts/fetch_quotes.py --sessions 450             # how much history (default 450 sessions)

The list is an.quotes.ticker_universe(): the quality 150, the memo's three lists, every
name in journal.md and book.md, and SPY, QQQ, IWM, VFV.TO. The pull goes through the
repository's own prices.PriceClient so parsing and provenance are the tested path.
Exit 1 when fewer than half the names come back, so a throttled run is loud.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import paths, prices, quotes  # noqa: E402
from an.store import Cache, Offline  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=quotes.QUOTES_CACHE)
    ap.add_argument("--tickers", default=None, help="comma-separated override")
    ap.add_argument("--sessions", type=int, default=450)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, default today")
    a = ap.parse_args(argv)

    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()] if a.tickers else quotes.ticker_universe()
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    start = as_of - dt.timedelta(days=int(a.sessions * 1.5) + 10)
    if a.dry_run:
        print(f"# dry run. would pull adjusted closes for {len(tickers)} names, {start} to {as_of}, via yfinance,")
        print(f"#   and write closes.csv, latest.csv, manifest.json under {a.out}")
        print("#   " + " ".join(tickers))
        return 0

    client = prices.PriceClient(downloader=prices.YFinanceDownloader(), cache=Cache(paths.PRICE_CACHE))
    try:
        closes = client.daily_closes(tickers, start, as_of)
    except Offline as e:
        print(f"offline: {e}", file=sys.stderr)
        return 1
    missing = [t for t, v in client.last_source.items() if v == "missing"]
    got = [t for t in tickers if t in closes.columns and closes[t].notna().any()]
    closes = closes[got].dropna(how="all")
    if len(closes) > a.sessions:
        closes = closes.iloc[-a.sessions:]
    manifest = quotes.write(closes, a.out, source="yfinance via an.prices.PriceClient (auto_adjust=True)",
                            missing=missing, requested=len(tickers))
    print(f"wrote {a.out}: {manifest['n_tickers']} of {manifest['requested']} names, {manifest['sessions']} sessions, "
          f"{manifest['first']}..{manifest['last']}, missing {len(missing)}")
    if manifest["n_tickers"] < len(tickers) / 2:
        print("fewer than half the names came back; refusing to call this a pull", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
