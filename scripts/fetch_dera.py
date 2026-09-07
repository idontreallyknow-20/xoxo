#!/usr/bin/env python3
"""Download SEC DERA Financial Statement Data Sets and read as-reported figures out of them.

    export SEC_USER_AGENT="Your Name your@email.com"
    python scripts/fetch_dera.py --dry-run 2023q3 2023q4          # print the URLs, send nothing
    python scripts/fetch_dera.py 2023q3 2023q4                    # download to data/cache/dera/
    python scripts/fetch_dera.py --since 2021q1                   # every quarter from there to the last complete one
    python scripts/fetch_dera.py --show 320193 --metric revenue   # the series from every cached quarter
    python scripts/fetch_dera.py --show AAPL --metric revenue --as-of 2024-03-31
    python scripts/fetch_dera.py --fixture --show 320193 --metric revenue    # on the committed miniature
    python scripts/fetch_dera.py --basis-report                    # what the score would read from the cache
    python scripts/fetch_dera.py --basis-report --fixture AAPL     # the report's shape, on the Apple miniature

One request per quarter, 50 to 100 MB each, kept on disk and never re-fetched.
The SEC asks for a real contact in the User-Agent and throttles without one.

--show takes a CIK or a ticker. A ticker needs the EDGAR ticker map, which
fetch_edgar.py caches on its first run; without it, give the CIK.

This script has never run against sec.gov. The machine it was written on could
not reach it. Run --dry-run, then one quarter, then --show, and read the numbers
against the filing before trusting them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import dera, edgar, paths  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline  # noqa: E402

FIXTURE_DIR = paths.ROOT / "tests" / "fixtures" / "dera"


def last_complete_quarter(today: Optional[dt.date] = None) -> Tuple[int, int]:
    """The data set for a quarter is cut a few weeks after it ends; the safe target is the one before the current."""
    today = today or dt.date.today()
    q = (today.month - 1) // 3 + 1
    return (today.year, q - 1) if q > 1 else (today.year - 1, 4)


def quarters_since(label: str, today: Optional[dt.date] = None) -> List[Tuple[int, int]]:
    y, q = dera.parse_quarter_label(label)
    last = last_complete_quarter(today)
    out = []
    while (y, q) <= last:
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def resolve_cik(ident: str) -> Optional[str]:
    s = ident.strip().upper()
    if s.isdigit():
        return edgar.cik_to_str(s)
    client = edgar.EdgarClient(transport=DryRunTransport(sink=lambda _: None),
                               cache=Cache(paths.EDGAR_CACHE, default_ttl=float("inf")))
    try:
        return client.cik_for(s)
    except (Offline, FetchError):
        return None


def show(sources, ident: str, metric: str, as_of: Optional[dt.date], *, fixture: bool) -> int:
    cik = resolve_cik(ident)
    if cik is None:
        print(f"cannot resolve {ident!r} to a CIK: give the CIK, or run fetch_edgar.py once to cache the ticker map",
              file=sys.stderr)
        return 2
    q = dera.load_quarters(sources, ciks=[cik])
    if not q.submissions:
        print(f"CIK {cik} has no submissions in {q.label}")
        return 1
    facts = dera.metric_facts(q.facts, cik, metric)
    name = next(iter(q.submissions.values())).name
    print(f"{name}  CIK {cik}  metric {metric}  from {q.label}"
          + (f"  as known on {as_of}" if as_of else "  as first reported"))
    print(f"  {q.rows_kept:,} of {q.rows_read:,} rows kept; {len(q.submissions)} submissions; "
          f"{len(facts)} facts for this metric" + ("" if facts else " (tag not used by this filer)"))
    if not facts:
        return 1
    unit = facts[0].unit
    print(f"\n  annual ({unit}):")
    for f in dera.annual_series(facts, on_date=as_of):
        print(f"    FY ending {f.ddate}  {f.value:>22,.0f}   filed {f.filed}  {f.form}  {f.adsh}")
    print(f"\n  quarterly ({unit}):")
    for f in dera.quarterly_series(facts, on_date=as_of):
        tag = "derived: FY minus nine months, dated by the 10-K" if f.derived else f.form
        print(f"    Q ending  {f.ddate}  {f.value:>22,.0f}   filed {f.filed}  {tag}")
    return 0


def basis_report(tickers: List[str], *, fixture: bool) -> int:
    """What the score's fundamentals would rest on, name by name, from the cache or the miniature."""
    from an import dera_fundamentals as df
    from an import local

    universe = local.load_universe()
    if fixture:
        print("# FIXTURE. Apple's FY2023 and FY2024 10-Ks, hand-built under tests/fixtures/dera_full/. Not a download.\n")
        srcs = [(d.name, d) for d in sorted((paths.ROOT / "tests" / "fixtures" / "dera_full").iterdir()) if d.is_dir()]
        panel = df.load_panel(tickers or ["AAPL"], sources=srcs, cik_map={"AAPL": "320193"},
                              on_date=dt.date(2024, 11, 1))
    else:
        prov = dera.provenance()
        ever = prov["ever_made_a_real_request"]
        print("# ever made a real request: " + ("yes" if ever else "no, never" if ever is False else
                                                "unknown (zips present without provenance sidecars)"))
        pulled = sorted({r.pulled for r in universe.values() if r.pulled})
        on_date = dt.date.fromisoformat(pulled[-1]) if pulled else dt.date.today()
        panel = df.load_panel(tickers or list(universe), on_date=on_date)
    print(f"# {panel.why}\n")
    subset = {t: universe[t] for t in (tickers or universe) if t in universe}
    if fixture and "AAPL" not in subset and "AAPL" in universe:
        subset["AAPL"] = universe["AAPL"]
    rebased, report = df.apply(subset, panel)
    print(f"status: {report.status}. {len(report.applied)} of {len(subset)} names re-based; "
          f"{len(report.skipped_short)} had too few filed years; {len(panel.unmapped)} could not be mapped to a CIK.")
    if not report.in_use:
        print(f"nothing for the score to read. {df.FETCH_COMMAND}")
        return 1
    counts = report.disagreement_counts()
    print("disagreements beyond tolerance, by field: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "none"))
    for t in sorted(report.applied):
        lf, rc = report.applied[t], report.reconciliations[t]
        print(f"\n{t}  CIK {lf.cik}  {len(lf.years)} fiscal years {lf.years[0].label}..{lf.years[-1].label}"
              f"  as reported through {lf.as_reported_through}  basis {rebased[t].fundamentals.basis}")
        print(f"  {'field':<16}{'yahoo':>12}{'sec same yrs':>14}{'sec long':>12}  verdict")
        for d in rc.differences:
            fmt = lambda x: "n/a" if x is None else f"{x:.4f}"  # noqa: E731
            verdict = ("agree" if d.agrees else "DISAGREE" if d.agrees is False else
                       "no verdict" + ("" if rc.same_window_complete else " (windows differ)"))
            print(f"  {d.field:<16}{fmt(d.yahoo):>12}{fmt(d.sec_same_window):>14}{fmt(d.sec_long_window):>12}  {verdict}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("quarters", nargs="*", help="labels like 2023q4")
    ap.add_argument("--basis-report", action="store_true",
                    help="print, per name, what the score's fundamentals would rest on and where the two sources disagree")
    ap.add_argument("--since", help="every quarter from this label to the last complete one")
    ap.add_argument("--dry-run", action="store_true", help="print the URLs and exit")
    ap.add_argument("--show", metavar="CIK_OR_TICKER", help="print a metric's series from the cached quarters")
    ap.add_argument("--metric", default="revenue", help=f"one of {', '.join(edgar.KEY_TAGS)}")
    ap.add_argument("--as-of", default=None, help="point in time: the values known on this date (YYYY-MM-DD)")
    ap.add_argument("--fixture", action="store_true", help="use the committed two-quarter miniature instead of the cache")
    a = ap.parse_args(argv)

    if a.metric not in edgar.KEY_TAGS:
        print(f"unknown metric {a.metric!r}; choose from {', '.join(edgar.KEY_TAGS)}", file=sys.stderr)
        return 2
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else None

    if a.basis_report:
        # Positional arguments are tickers here, not quarter labels.
        return basis_report([t.upper() for t in a.quarters], fixture=a.fixture)

    try:
        wanted = [dera.parse_quarter_label(l) for l in a.quarters]
        if a.since:
            wanted += quarters_since(a.since)
    except ValueError as e:
        print(f"! {e}", file=sys.stderr)
        return 2
    wanted = sorted(set(wanted))

    if a.fixture:
        if not a.show:
            print("--fixture needs --show", file=sys.stderr)
            return 2
        print("# FIXTURE. Two hand-built miniatures under tests/fixtures/dera/. Not a download.\n")
        sources = [(d.name, d) for d in sorted(FIXTURE_DIR.iterdir()) if d.is_dir()]
        return show(sources, a.show, a.metric, as_of, fixture=True)

    ua = edgar.default_user_agent()
    if a.dry_run:
        client = dera.DeraClient(transport=DryRunTransport(), cache_dir=paths.DERA_CACHE, user_agent=ua)
        print(f"# dry run. User-Agent: {ua}")
        print("# one request per quarter, 50 to 100 MB each. This script has never made one.\n")
        for call in client.plan(wanted):
            state = "cached" if call.path.exists() else "would fetch"
            print(f"  GET {call.url}\n      -> {call.path}  ({state})")
        if not wanted:
            print("  (no quarters given; try 2023q4 or --since 2021q1)")
        return 0

    paths.ensure_dirs()
    client = dera.DeraClient(transport=HttpTransport(ua, rate_per_second=edgar.SEC_RATE_PER_SECOND),
                             cache_dir=paths.DERA_CACHE, user_agent=ua)
    failures = 0
    for y, q in wanted:
        label = dera.quarter_label(y, q)
        try:
            p = client.fetch(y, q)
            print(f"  {label}  {p.stat().st_size / 1e6:6.1f} MB  {p}")
        except (FetchError, Offline) as e:
            print(f"  ! {label}: {e}", file=sys.stderr)
            failures += 1

    if a.show:
        sources = [(p.stem, p) for p in client.cached()]
        if not sources:
            print("nothing cached under data/cache/dera/; fetch a quarter first or use --fixture", file=sys.stderr)
            return 2
        return show(sources, a.show, a.metric, as_of, fixture=False) or (1 if failures else 0)
    if not wanted:
        ap.print_usage()
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
