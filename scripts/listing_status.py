#!/usr/bin/env python3
"""Measure the survivorship hole: how many names a past screen would have picked that no longer exist.

    export ALPHAVANTAGE_KEY=your_free_key
    python scripts/listing_status.py --dry-run             # print the two URLs, send nothing, no key needed
    python scripts/listing_status.py --fetch               # two requests, cached a week, then the report
    python scripts/listing_status.py --report              # the report from the cache, no request
    python scripts/listing_status.py --report --as-of 2016-09-06 --as-of 2019-09-06
    python scripts/listing_status.py --report --fixture    # the report's shape, on the committed test fixture
    python scripts/listing_status.py --report --json dashboard/survivorship.json

The key is read from ALPHAVANTAGE_KEY and from nowhere else. Two requests are
spent in total, against a free-tier budget of 25 a day. The response is cached for
a week so re-running the report costs nothing.

The first line of every report says where the data came from. A report on the
fixture is the report's shape and nothing more: the fixture is twenty-two rows
built by hand, and every number in it is an example.

This script has never run against the live endpoint. The machine it was written
on could not reach www.alphavantage.co. Run --dry-run first, then --fetch once,
and read the first line of the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import listing_status as ls, local, paths  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline, RateLimited  # noqa: E402

FIXTURE_DIR = paths.ROOT / "tests" / "fixtures"
FIXTURE_FILES = {"active": FIXTURE_DIR / "av_listing_active.csv", "delisted": FIXTURE_DIR / "av_listing_delisted.csv"}


def load_fixture_listings() -> List[ls.Listing]:
    out: List[ls.Listing] = []
    for state in ls.STATES:
        out += ls.parse_listing_csv(FIXTURE_FILES[state].read_text(encoding="utf-8"))
    return out


def print_plan() -> int:
    client = ls.ListingStatusClient(transport=DryRunTransport(), cache=Cache(paths.ALPHAVANTAGE_CACHE))
    print("# dry run. No request is sent and no key is needed.")
    print(f"# budget: {ls.REQUESTS_PER_DAY} requests a day on a free key, {len(ls.STATES)} needed in total\n")
    for call in client.plan():
        print(f"  GET {call.url}")
        print(f"      cache {call.cache_key}.json, ttl {int(ls.TTL)}s  (state={call.state})")
    prov = ls.provenance()
    print()
    print("ever made a real request: " + ("yes" if prov["ever_live"] else "no, never"))
    return 0


def source_line(prov: dict, *, fixture: bool) -> str:
    if fixture:
        return ("tests/fixtures/av_listing_*.csv, a hand-built fixture of 22 rows. NOT a real request; "
                "every number below is an example of the report's shape")
    parts = []
    for state, info in prov["states"].items():
        if info is None:
            parts.append(f"{state}: nothing cached")
            continue
        when = dt.datetime.fromtimestamp(float(info["fetched_at"] or 0)).strftime("%Y-%m-%d %H:%M")
        parts.append(f"{state}: fetched {when} via {info['transport']}" + ("" if info["live"] else " (not live)"))
    real = "a real Alpha Vantage response" if prov["ever_live"] else "NOT a real request"
    return f"{real}; " + "; ".join(parts)


def run_report(listings: List[ls.Listing], *, as_of: List[dt.date], today: dt.date, fixture: bool,
               cross: bool, json_out: Optional[Path]) -> int:
    prov = ls.provenance()
    n_active = sum(1 for l in listings if l.delisting_date is None)
    n_delisted = len(listings) - n_active
    reports = [ls.attrition(listings, d, today=today) for d in sorted(as_of, reverse=True)]
    check = None
    if cross:
        tickers = sorted(local.load_universe())
        check = ls.cross_check(listings, tickers, today=today)
    text = ls.render_report(reports, check, source_line=source_line(prov, fixture=fixture),
                            n_active=n_active, n_delisted=n_delisted)
    print(text)
    if json_out is not None:
        blob = {
            "built_at": dt.datetime.now().isoformat(timespec="seconds"),
            "source": "fixture" if fixture else ("alphavantage LISTING_STATUS" if prov["ever_live"] else "unknown"),
            "is_real": bool(prov["ever_live"]) and not fixture,
            "today": today.isoformat(),
            "rows": {"active": n_active, "delisted": n_delisted},
            "synthetic_assumption_per_year": ls.SYNTHETIC_ATTRITION_PER_YEAR,
            "attrition": [
                {
                    "as_of": r.as_of.isoformat(), "horizon_years": round(r.horizon_years, 2),
                    "eligible": r.eligible, "gone": r.gone, "rate": r.rate, "annualised_rate": r.annualised_rate,
                    "by_year": r.by_year, "by_exchange": {k: list(v) for k, v in r.by_exchange.items()},
                    "reused_symbols": r.reused_symbols, "unknown_ipo_dates": r.unknown_ipo_dates,
                }
                for r in reports
            ],
            "cross_check": None if check is None else {
                "checked": check.checked,
                "delisted_hits": [[t, l.delisting_date.isoformat() if l.delisting_date else None] for t, l in check.delisted_hits],
                "not_covered": len(check.not_covered),
                "not_covered_canadian": len(check.canadian_not_covered),
            },
            "limitations": [
                "Whole listed market, not the $2bn-plus slice the screen selects: the file carries no market cap "
                "and no volume, so this is an upper bound on the screen's own attrition.",
                "A delisting is not a failure. The file gives no reason, and acquisition is the largest one.",
                "No prices for the departed names, so this sizes a survivorship hole; it cannot repair a backtest.",
            ],
        }
        if fixture:
            blob["limitations"].insert(0, "FIXTURE DATA. Twenty-two hand-built rows. Not a measurement of anything.")
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
        print(f"\nwrote {json_out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the URLs that would be requested and exit")
    ap.add_argument("--fetch", action="store_true", help="make the two requests (needs ALPHAVANTAGE_KEY), then report")
    ap.add_argument("--report", action="store_true", help="print the attrition report from the cache")
    ap.add_argument("--fixture", action="store_true", help="run the report on the committed test fixture instead")
    ap.add_argument("--as-of", action="append", default=[], help="a past date to measure from; repeatable")
    ap.add_argument("--today", default=None, help="override today's date, YYYY-MM-DD (for reproducible output)")
    ap.add_argument("--no-cross-check", action="store_true", help="skip holding the current universe against the list")
    ap.add_argument("--json", type=Path, default=None, help="also write the report as JSON to this path")
    a = ap.parse_args(argv)

    if a.dry_run:
        return print_plan()

    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    as_of = [dt.date.fromisoformat(d) for d in a.as_of] or ls.default_horizons(today)
    for d in as_of:
        if d >= today:
            print(f"--as-of {d} is not before today {today}", file=sys.stderr)
            return 2

    if a.fixture:
        print("# FIXTURE. This is the shape of the report, on 22 hand-built rows. It measures nothing.\n")
        return run_report(load_fixture_listings(), as_of=as_of, today=today, fixture=True,
                          cross=not a.no_cross_check, json_out=a.json)

    paths.ensure_dirs()
    cache = Cache(paths.ALPHAVANTAGE_CACHE, default_ttl=ls.TTL)
    if a.fetch:
        try:
            ls.api_key()
        except ls.MissingKey as e:
            print(f"! {e}", file=sys.stderr)
            return 2
        client = ls.ListingStatusClient(transport=HttpTransport("desk personal research", rate_per_second=ls.RATE_PER_SECOND),
                                        cache=cache)
        try:
            for state in ls.STATES:
                t0 = time.monotonic()
                rows = client.listings(state)
                print(f"  {state:<9} {len(rows):>7,} rows  ({time.monotonic() - t0:.1f}s)")
        except ls.MissingKey as e:
            print(f"! {e}", file=sys.stderr)
            return 2
        except RateLimited as e:
            print(f"! rate limited: {e}\n  the free tier allows {ls.REQUESTS_PER_DAY} requests a day; try tomorrow", file=sys.stderr)
            return 1
        except (ls.BadKey, FetchError, Offline) as e:
            print(f"! {e}", file=sys.stderr)
            return 1
        print()

    prov = ls.provenance(cache)
    if any(v is None for v in prov["states"].values()):
        print("no cached listing. Run with --fetch and ALPHAVANTAGE_KEY set, or --fixture to see the shape.",
              file=sys.stderr)
        return 2
    client = ls.ListingStatusClient(transport=DryRunTransport(sink=lambda s: None), cache=cache, ttl=float("inf"))
    try:
        listings = client.all_listings()
    except (Offline, FetchError) as e:
        print(f"! the cache did not answer: {e}", file=sys.stderr)
        return 2
    return run_report(listings, as_of=as_of, today=today, fixture=False, cross=not a.no_cross_check, json_out=a.json)


if __name__ == "__main__":
    raise SystemExit(main())
