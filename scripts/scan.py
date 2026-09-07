#!/usr/bin/env python3
"""The daily scan. Writes dashboard/watch.json.

    python scripts/scan.py                 # from caches only; NOT RUN if nothing is cached
    python scripts/scan.py --live          # pull closes, filings and headlines for the watched names
    python scripts/scan.py --dry-run       # print every URL --live would fetch, send nothing
    python scripts/scan.py --fixture       # the file's shape on committed fixtures; never writes under dashboard/
    python scripts/scan.py --sources prices,edgar   # a subset of the three sources

Watched names are every ticker with a journal call plus whatever is in
portfolio/holdings.csv (not committed). Each is read against its price at call
and its "close under $X" falsifier, its new EDGAR filings since the last scan,
and the headlines on its syndication feed. What crosses a written rule becomes
an alert. Nothing here recommends a trade; see an.watch.

Nothing here has run against a live source. The machine it was written on could
reach none of them. Run --dry-run first, then --live, and read the output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import edgar, journal, paths, positioning, prices, watch  # noqa: E402
from an.http import DryRunTransport, FixtureTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline  # noqa: E402

ALL_SOURCES = ("prices", "edgar", "rss")
FIXTURES = paths.ROOT / "tests" / "fixtures"


def fixture_transport() -> FixtureTransport:
    t = FixtureTransport()
    t.add_file("https://www.sec.gov/files/company_tickers.json", FIXTURES / "company_tickers.json")
    t.add_file("https://data.sec.gov/submissions/CIK0000320193.json", FIXTURES / "submissions_aapl.json")
    t.add_file(watch.RSS_TEMPLATE.format(ticker="AAPL"), FIXTURES / "rss_aapl.xml")
    return t


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--sources", default=",".join(ALL_SOURCES))
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD, default today")
    ap.add_argument("--since-days", type=int, default=14,
                    help="on the first scan, how far back to list filings (later scans use the state file)")
    ap.add_argument("--out", type=Path, default=paths.DASHBOARD_DIR / "watch.json")
    ap.add_argument("--state", type=Path, default=watch.STATE_PATH)
    ap.add_argument("--check", action="store_true", help="accepted for build_all --check; no effect")
    a = ap.parse_args(argv)

    sources = {s.strip() for s in a.sources.split(",") if s.strip()}
    unknown = sources - set(ALL_SOURCES)
    if unknown:
        print(f"unknown source(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()

    if a.fixture:
        return run_fixture(a, as_of, sources)

    state = watch.WatchState.load(a.state)
    entries = journal.load()
    held, _, _ = positioning._read_holdings()
    names = watch.names_to_watch(entries, held=[h.ticker for h in held],
                                 held_triggers={h.ticker: h.wrong_if_price for h in held if h.wrong_if_price})
    tickers = [n.ticker for n in names]
    if not names:
        print("nothing to watch: no journal calls and no holdings", file=sys.stderr)
        return 2
    since = (dt.date.fromisoformat(state.last_scan[:10]) - dt.timedelta(days=1)) if state.last_scan \
        else as_of - dt.timedelta(days=a.since_days)
    start = as_of - dt.timedelta(days=watch.WINDOW_DAYS)

    if a.dry_run:
        print(f"# dry run. --live would scan {len(names)} names: {' '.join(tickers)}")
        if "prices" in sources:
            print(f"#   prices: yfinance adjusted closes for {len(tickers) + len(watch.BENCHMARKS)} names, "
                  f"{start} to {as_of}, cached under {paths.PRICE_CACHE}")
        if "edgar" in sources:
            t = DryRunTransport()
            c = edgar.EdgarClient(transport=t, cache=Cache(paths.EDGAR_CACHE))
            print(f"#   edgar: filings after {since} for each name, forms {', '.join(watch.FORMS_WATCHED)}")
            try:
                c.company_tickers()
            except Offline:
                pass
            for tk in tickers:
                print(f"GET https://data.sec.gov/submissions/CIK<{tk}>.json")
        if "rss" in sources:
            r = watch.RssReader(DryRunTransport(), cache=Cache(watch.RSS_CACHE))
            for tk in tickers:
                print(f"GET {r.url_for(tk)}")
        print(f"# state file: {a.state} ({'exists' if a.state.exists() else 'absent'}; "
              f"last scan {state.last_scan or 'never'})")
        print("# this script has never made these requests")
        return 0

    paths.ensure_dirs()
    reads: Dict[str, watch.PriceRead] = {}
    bench: Dict[str, watch.PriceRead] = {}
    filings: Dict[str, List[watch.FilingNote]] = {}
    headlines: Dict[str, List[watch.Headline]] = {}
    notes: List[str] = []
    src: Dict[str, Dict[str, object]] = {}
    any_data = False

    if "prices" in sources:
        client = prices.PriceClient(downloader=None if a.live else prices.OfflineDownloader(),
                                    cache=Cache(paths.PRICE_CACHE))
        try:
            closes = client.daily_closes(tickers + list(watch.BENCHMARKS), start, as_of)
            live = sum(1 for v in client.last_source.values() if v == "live")
            cached = sum(1 for v in client.last_source.values() if v in ("cache", "stale"))
            missing = sorted(t for t, v in client.last_source.items() if v == "missing")
            for n in names:
                reads[n.ticker] = watch.price_read(closes, n.ticker, as_of=as_of, price_at_call=n.price_at_call,
                                                   trigger=n.trigger)
            for b in watch.BENCHMARKS:
                bench[b] = watch.price_read(closes, b, as_of=as_of)
            any_data = any_data or any(r.last_close is not None for r in reads.values())
            src["prices"] = {"live": live > 0, "detail": f"yfinance adjusted closes: {live} live, {cached} from cache"
                             + (f", missing {', '.join(missing)}" if missing else "")}
        except Offline as e:
            src["prices"] = {"live": False, "detail": f"offline: {e}"}
            notes.append("No closes were available, so no price rule could be checked.")
    else:
        src["prices"] = {"live": False, "detail": "not requested"}

    if "edgar" in sources:
        transport = HttpTransport(edgar.default_user_agent(), rate_per_second=edgar.SEC_RATE_PER_SECOND) \
            if a.live else DryRunTransport(sink=lambda s: None)
        c = edgar.EdgarClient(transport=transport, cache=Cache(paths.EDGAR_CACHE, default_ttl=6 * 3600.0))
        n_new = 0
        try:
            for n in names:
                got, note = watch.filings_since(c, n.ticker, since)
                if note:
                    notes.append(note)
                new = state.new_filings(got)
                filings[n.ticker] = new
                n_new += len(new)
            any_data = True
            src["edgar"] = {"live": a.live, "detail": f"{n_new} new filing(s) since {since}"}
        except (Offline, FetchError) as e:
            src["edgar"] = {"live": False, "detail": f"not pulled: {e}"}
            notes.append("Filings were not pulled, so nothing new from EDGAR is listed.")
    else:
        src["edgar"] = {"live": False, "detail": "not requested"}

    if "rss" in sources:
        transport = HttpTransport("desk personal research feed reader", rate_per_second=2.0) if a.live \
            else DryRunTransport(sink=lambda s: None)
        reader = watch.RssReader(transport, cache=Cache(watch.RSS_CACHE, default_ttl=watch.RSS_TTL))
        n_new = 0
        failed: List[str] = []
        for n in names:
            try:
                got = reader.headlines(n.ticker)
            except (Offline, FetchError):
                failed.append(n.ticker)
                continue
            new = state.new_headlines(got)
            headlines[n.ticker] = new
            n_new += len(new)
            any_data = True
        src["rss"] = {"live": a.live and len(failed) < len(names),
                      "detail": f"{n_new} new headline(s)" + (f"; no feed for {', '.join(failed)}" if failed else "")}
        if failed and len(failed) == len(names):
            notes.append("No feed could be read, so no headlines are listed.")
    else:
        src["rss"] = {"live": False, "detail": "not requested"}

    if not any_data:
        blob = watch.not_run_report(names)
    else:
        blob = watch.build_report(names, reads, filings, headlines, notes, as_of=as_of, sources=src, bench_reads=bench)
        if a.live:
            state.remember([f for v in filings.values() for f in v], [h for v in headlines.values() for h in v],
                           when=dt.datetime.now().isoformat(timespec="seconds"))
            state.save(a.state)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    report(blob)
    print(f"wrote {a.out}")
    return 0


def run_fixture(a: argparse.Namespace, as_of: dt.date, sources: set) -> int:
    """The shape of a scanned file on committed fixtures. Refuses to write under dashboard/."""
    if a.out.resolve() == (paths.DASHBOARD_DIR / "watch.json").resolve() or \
            paths.DASHBOARD_DIR.resolve() in a.out.resolve().parents:
        out = paths.CACHE_DIR / "watch" / "fixture_watch.json"
    else:
        out = a.out
    t = fixture_transport()
    name = watch.WatchName(ticker="AAPL", action="Buy now", kind="buy", date="2026-07-01", price_at_call=100.0,
                           wrong_if="A close under $80.", trigger=80.0, held=False, conviction=4, bucket="compounder")
    start = as_of - dt.timedelta(days=watch.WINDOW_DAYS)
    panel = prices.synthetic_panel(["AAPL", "SPY", "QQQ"], start=start, periods=watch.WINDOW_DAYS, seed=3,
                                   start_price={"AAPL": 100.0, "SPY": 100.0, "QQQ": 100.0}, vol={"AAPL": 0.9})
    panel = panel[panel.index <= str(as_of)]
    reads = {"AAPL": watch.price_read(panel, "AAPL", as_of=as_of, price_at_call=100.0, trigger=80.0)}
    bench = {b: watch.price_read(panel, b, as_of=as_of) for b in watch.BENCHMARKS}
    c = edgar.EdgarClient(transport=t, cache=Cache(paths.CACHE_DIR / "watch" / "fixture_edgar", default_ttl=1.0),
                          user_agent="fixture fixture@example.com")
    got, _ = watch.filings_since(c, "AAPL", dt.date(2026, 1, 1))
    reader = watch.RssReader(t, cache=Cache(paths.CACHE_DIR / "watch" / "fixture_rss", default_ttl=1.0))
    hl = reader.headlines("AAPL")
    src = {"prices": {"live": False, "detail": "synthetic panel, seed 3"},
           "edgar": {"live": False, "detail": "committed fixture submissions_aapl.json"},
           "rss": {"live": False, "detail": "committed fixture rss_aapl.xml"}}
    blob = watch.build_report([name], reads, {"AAPL": got}, {"AAPL": hl}, [], as_of=as_of, sources=src,
                              bench_reads=bench, status="SYNTHETIC")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    print("# FIXTURE. Synthetic prices, a committed filing index and a committed feed. Measures nothing.")
    report(blob)
    print(f"wrote {out}")
    return 0


def report(blob: dict) -> None:
    print(f"status {blob['status']}: {blob['n_watched']} watched, {len(blob.get('alerts', []))} alert(s)")
    for s, v in blob["sources"].items():
        print(f"  {s:<7} {'live' if v.get('live') else 'not live':<9} {v.get('detail')}")
    for row in blob.get("names", []):
        p = row["price"]
        close = "n/a" if p["last_close"] is None else f"{p['last_close']:.2f} ({p['last_date']})"
        d1 = "n/a" if p["chg_1d"] is None else f"{p['chg_1d'] * 100:+.1f}%"
        sc = "n/a" if p["since_call"] is None else f"{p['since_call'] * 100:+.1f}%"
        print(f"  {row['ticker']:<6} close {close:<22} 1d {d1:>7}  since call {sc:>7}  "
              f"{len(row['filings'])} filing(s) {len(row['headlines'])} headline(s) {row['n_alerts']} alert(s)")
    for al in blob.get("alerts", []):
        print(f"  [{al['severity']}] {al['ticker']:<6} {al['kind']:<12} {al['text'][:110]}")


if __name__ == "__main__":
    raise SystemExit(main())
