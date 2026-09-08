#!/usr/bin/env python3
"""Mark Claude's paper book (book.md) and write dashboard/book.json.

    python scripts/build_book.py                 # from data/cache/quotes/ if present, else an honest NOT MARKED file
    python scripts/build_book.py --live          # pull prices through prices.PriceClient (yfinance, or DESK_QUOTES)
    python scripts/build_book.py --quotes DIR    # mark from a quotes directory (closes.csv + manifest.json)
    python scripts/build_book.py --journal book.md --out dashboard/book.json --as-of 2026-09-08
    python scripts/build_book.py --check         # accepted for build_all --check; renders like the default

The book is derived from the calls and the closes every time; nothing is kept by hand.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import book, journal, local, paths, prices, quotes  # noqa: E402
from an.store import Cache, Offline  # noqa: E402

BOOK_MD = paths.ROOT / "book.md"
BOOK_JSON = paths.DASHBOARD_DIR / "book.json"

NOT_MARKED = [
    "No quotes are on this machine, so nothing is filled or marked. The calls below are pending at the first close "
    "after each call date.",
    "python scripts/fetch_quotes.py on a machine that reaches Yahoo, or the quotes branch pulled by "
    "an.quotes.fetch_branch(), changes that.",
]


def sectors() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for t, rec in local.load_quality().items():
            if rec.sector:
                out[t.upper()] = rec.sector
    except (OSError, ValueError):
        pass
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--quotes", type=Path, default=None)
    ap.add_argument("--journal", type=Path, default=BOOK_MD)
    ap.add_argument("--out", type=Path, default=BOOK_JSON)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--starting-cash", type=float, default=100_000.0)
    ap.add_argument("--check", action="store_true", help="accepted for build_all --check")
    a = ap.parse_args(argv)

    entries = journal.load(a.journal)
    calls = [e for e in entries if not e.is_system]
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    tickers = sorted({e.ticker for e in calls} | set(book.BENCHMARKS))
    closes = None
    source = "no prices"
    status = "NOT MARKED"
    age = None

    if a.quotes or (not a.live and quotes.load() is not None):
        panel = quotes.load(a.quotes) if a.quotes else quotes.load()
        if panel is not None and not panel.closes.empty:
            closes = panel.closes.reindex(columns=[t for t in tickers if t in panel.closes.columns])
            source = f"quotes file: {panel.describe()}"
            age = quotes.age_sessions(panel, as_of)
            status = "MARKED"
    elif a.live and calls:
        paths.ensure_dirs()
        start = min(dt.date.fromisoformat(e.date) for e in calls) - dt.timedelta(days=7)
        client = prices.PriceClient(cache=Cache(paths.PRICE_CACHE))
        try:
            closes = client.daily_closes(tickers, start, as_of)
            live = sum(1 for v in client.last_source.values() if v == "live")
            missing = [t for t, v in client.last_source.items() if v == "missing"]
            if closes is not None and closes.dropna(how="all").empty:
                closes = None
            else:
                source = f"{type(client.downloader).__name__}: {live} live" + (f", missing {', '.join(missing)}" if missing else "")
                status = "MARKED"
                age = 0
        except Offline as e:
            print(f"  offline: {e}", file=sys.stderr)
            closes = None

    b = book.build_book(entries, closes, as_of=as_of, starting_cash=a.starting_cash, sectors=sectors(), price_source=source)
    blob = {"built_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "status": status,
            "is_real": status == "MARKED" and "fixture" not in source, "journal": str(a.journal.name),
            "n_calls": len(calls), "quotes_age_sessions": age, **b.to_json()}
    if status == "NOT MARKED":
        blob["why_not_marked"] = NOT_MARKED
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(blob, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{status}: {len(calls)} calls, {blob['n_open']} open, {blob['n_pending']} pending, {blob['n_refused']} refused; "
          f"equity ${blob['equity']:,.0f} ({(blob['ret_since_start'] or 0) * 100:+.2f}%) as of {blob['as_of']}; wrote {a.out}")
    for p in b.open:
        print(f"  {p.ticker:6s} {p.kind:5s} {p.shares:5d} @ {p.entry:8.2f}  last {p.last:8.2f}  "
              f"{(p.ret or 0) * 100:+6.2f}%  vs SPY {(p.excess_vs_spy or 0) * 100:+6.2f}%"
              + ("  FALSIFIER BREACHED" if p.falsifier_breached else "") + ("  STOP" if p.stop_breached else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
