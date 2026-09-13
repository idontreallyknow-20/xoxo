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
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import paths, prices, quotes  # noqa: E402
from an.store import Cache, Offline  # noqa: E402


BATCH = 100
PAUSE = 3.0


def pull_in_batches(client, tickers: List[str], start: dt.date, as_of: dt.date, *, batch: int = BATCH,
                    pause: float = PAUSE, sleep=time.sleep) -> Tuple[pd.DataFrame, List[str]]:
    """One Yahoo call per ``batch`` names, a pause between calls, and one retry of any batch
    that came back mostly empty, after a longer wait.

    Yahoo rate-limits a runner that asks for fifteen hundred names in one go, and a
    throttled call returns nothing for most of them. Smaller calls with a pause finish; a
    batch that still comes back thin after its retry is reported as missing, not invented."""
    frames: List[pd.DataFrame] = []
    missing: List[str] = []
    chunks = [tickers[i:i + batch] for i in range(0, len(tickers), max(1, batch))]
    for n, chunk in enumerate(chunks):
        if n:
            sleep(pause)
        got = _pull_once(client, chunk, start, as_of)
        if len(got.columns) < len(chunk) / 2:
            sleep(pause * 10)
            got = _pull_once(client, chunk, start, as_of, refresh=True)
        frames.append(got)
        missing.extend(t for t in chunk if t not in got.columns)
    closes = pd.concat([f for f in frames if not f.empty], axis=1).sort_index() if frames else pd.DataFrame()
    return closes, sorted(set(missing))


def _pull_once(client, chunk: List[str], start: dt.date, as_of: dt.date, *, refresh: bool = False) -> pd.DataFrame:
    try:
        closes = client.daily_closes(chunk, start, as_of, refresh=refresh)
    except Offline:
        return pd.DataFrame()
    got = [t for t in chunk if t in closes.columns and closes[t].notna().any()]
    return closes[got].dropna(how="all")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=quotes.QUOTES_CACHE)
    ap.add_argument("--tickers", default=None, help="comma-separated override")
    ap.add_argument("--sessions", type=int, default=450)
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--batch", type=int, default=BATCH, help="names per Yahoo call (default %(default)s)")
    ap.add_argument("--pause", type=float, default=PAUSE, help="seconds between calls; a throttled batch waits ten times this")
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
        closes, missing = pull_in_batches(client, tickers, start, as_of, batch=a.batch, pause=a.pause)
    except Offline as e:
        print(f"offline: {e}", file=sys.stderr)
        return 1
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
