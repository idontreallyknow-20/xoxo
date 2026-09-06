#!/usr/bin/env python3
"""Grade every call in journal.md and write dashboard/tracker.json.

    python scripts/track_calls.py                 # from cached prices if any, else an honest NOT GRADED file
    python scripts/track_calls.py --live          # pull prices from yfinance for the journal's names, SPY and QQQ
    python scripts/track_calls.py --synthetic     # a labelled demonstration on a synthetic panel
    python scripts/track_calls.py --dry-run       # print what --live would pull, send nothing
    python scripts/track_calls.py --as-of 2027-03-01 --out /tmp/t.json

Reads journal.md through an.journal, so a heading naming five names produces
five grades. The score's percentile at the time of the call comes from the last
snapshot under universe/snapshots/ dated on or before the call, never after.

The default run never touches the network. With nothing cached it writes a file
whose status is NOT GRADED and whose rows still carry everything that needs no
prices: the call, the falsifier, the parsed trigger and the score at call. A
--synthetic run is labelled SYNTHETIC in the file and on the page and grades
nothing real.

Nothing here has run against live prices. The machine it was written on could
not reach Yahoo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import journal, paths, prices, tracker  # noqa: E402
from an.store import Cache, Offline  # noqa: E402

NOT_GRADED = [
    "No price history has been pulled, so no call has a return, a benchmark or a falsifier check yet. "
    "The rows below carry what needs no prices: the call, the level that would falsify it, and the "
    "score's percentile in the snapshot that preceded it.",
    "To grade for real: python scripts/track_calls.py --live, on a machine that can reach Yahoo. It "
    "pulls adjusted daily closes for the journal's names plus SPY and QQQ from the earliest call date.",
    "Even then, read the limitations. A grade is a fact about a window, not a verdict on a call.",
]


def journal_tickers(entries) -> List[str]:
    return sorted({e.ticker for e in entries if not e.is_system})


def build(entries, closes, *, as_of: dt.date, source: str, status: str, built_at: Optional[str] = None) -> dict:
    grades = tracker.grade_entries(entries, closes, as_of=as_of)
    summary = tracker.summarise(grades)
    lims = tracker.limitations(grades, summary, source=source)
    blob = {
        "built_at": built_at or dt.datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "is_real": status == "GRADED",
        "as_of": as_of.isoformat(),
        "price_source": source,
        "benchmarks": list(tracker.BENCHMARKS),
        "summary": summary.to_json(),
        "limitations": lims,
        "grades": [g.to_json() for g in grades],
        "disclaimer": "Research and analysis from public data, not personalised financial advice.",
    }
    if status == "NOT GRADED":
        blob["why_not_graded"] = NOT_GRADED
    if status == "SYNTHETIC":
        blob["limitations"].insert(0, "SYNTHETIC PRICES. Every return below is a random walk seeded for "
                                      "demonstration. Nothing here is a measurement of any call.")
    return blob


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="pull prices from yfinance")
    ap.add_argument("--synthetic", action="store_true", help="grade on a synthetic panel, labelled as such")
    ap.add_argument("--dry-run", action="store_true", help="print the tickers and window --live would pull")
    ap.add_argument("--as-of", default=None, help="grading date, YYYY-MM-DD (default: today)")
    ap.add_argument("--out", type=Path, default=paths.DASHBOARD_DIR / "tracker.json")
    ap.add_argument("--check", action="store_true", help="accepted for build_all --check; no effect")
    a = ap.parse_args(argv)

    entries = journal.load()
    calls = [e for e in entries if not e.is_system]
    if not calls:
        print("journal.md has no calls to grade", file=sys.stderr)
        return 2
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()
    tickers = journal_tickers(entries) + list(tracker.BENCHMARKS)
    start = min(dt.date.fromisoformat(e.date) for e in calls) - dt.timedelta(days=7)

    if a.dry_run:
        print(f"# dry run. --live would pull adjusted daily closes for {len(tickers)} names")
        print(f"#   {' '.join(tickers)}")
        print(f"#   from {start} to {as_of}, via yfinance, cached under {paths.PRICE_CACHE}")
        print("# this script has never made that pull")
        return 0

    closes = None
    source = "none pulled yet"
    status = "NOT GRADED"
    if a.synthetic:
        # Anchor each name at its price at call, or every trigger fires on day one.
        anchors = {t: 100.0 for t in tickers}
        for e in calls:
            if e.price_at_call:
                anchors[e.ticker] = e.price_at_call
        closes = prices.synthetic_panel(tickers, start=start, periods=max(30, (as_of - start).days + 5), seed=7,
                                        start_price=anchors)
        closes = closes[closes.index <= str(as_of)]
        source = "synthetic geometric Brownian motion, seed 7, each name anchored at its price at call"
        status = "SYNTHETIC"
    else:
        paths.ensure_dirs()
        client = prices.PriceClient(downloader=None if a.live else prices.OfflineDownloader(),
                                    cache=Cache(paths.PRICE_CACHE))
        try:
            closes = client.daily_closes(tickers, start, as_of)
            live = sum(1 for v in client.last_source.values() if v == "live")
            cached = sum(1 for v in client.last_source.values() if v in ("cache", "stale"))
            missing = [t for t, v in client.last_source.items() if v == "missing"]
            if closes is not None and closes.dropna(how="all").empty:
                closes = None
            else:
                source = f"yfinance adjusted closes: {live} live, {cached} from cache" + (
                    f", missing {', '.join(missing)}" if missing else "")
                status = "GRADED"
        except Offline as e:
            print(f"  offline: {e}", file=sys.stderr)
            closes = None

    blob = build(entries, closes, as_of=as_of, source=source, status=status)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(blob, indent=1), encoding="utf-8")

    s = blob["summary"]
    print(f"status {status}: {s['n_calls']} calls, {s['n_graded']} graded, {s['n_falsified']} falsified, "
          f"{s['n_ungraded']} ungraded")
    for g in blob["grades"]:
        pc = "n/a" if g["score_percentile_at_call"] is None else f"p{g['score_percentile_at_call']:.0f}"
        print(f"  {g['date']}  {g['ticker']:<6} {pc:>5}  {g['verdict'][:96]}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
