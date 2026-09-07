#!/usr/bin/env python3
"""Scan for mechanical swing setups. Writes dashboard/setups.json.

    python scripts/setups.py                  # from caches only; NOT RUN if nothing is cached
    python scripts/setups.py --live           # pull a year of closes for the liquid universe and 3 sessions of 8-Ks
    python scripts/setups.py --dry-run        # print the plan, send nothing
    python scripts/setups.py --fixture --out /tmp/s.json   # the file's shape on a synthetic panel with planted setups
    python scripts/setups.py --universe quality   # the quality 150 only (faster; no post-earnings drift outside it)

The rules are in swing.md and in an.setups. A setup is a pattern that matched,
never a forecast. Nothing here recommends a trade, and the tracker grades what
Joseph logs, not what this file lists.

The live pull: one batched yfinance download for about 1,900 names over 260
sessions (the size price_screen.py already pulls), then one EDGAR submissions
read per name with a CIK, cached six hours, at the SEC's rate. About five
minutes. Nothing here has run against a live source.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import edgar, guidance, local, paths, prices, setups, watch  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline  # noqa: E402

MIN_DOLLAR_VOLUME = 10_000_000.0


def liquid_universe() -> Dict[str, str]:
    """ticker -> name from universe_latest.csv, dollar volume over $10M (criteria.md step 1)."""
    out: Dict[str, str] = {}
    p = paths.UNIVERSE_CSV
    if not p.exists():
        return out
    with p.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                if float(r.get("dollar_volume_usd") or 0) < MIN_DOLLAR_VOLUME:
                    continue
            except ValueError:
                continue
            t = (r.get("ticker") or "").strip().upper()
            if t:
                out[t] = r.get("name") or t
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--universe", choices=["liquid", "quality"], default="liquid")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out", type=Path, default=paths.DASHBOARD_DIR / "setups.json")
    ap.add_argument("--check", action="store_true", help="accepted for build_all --check; no effect")
    a = ap.parse_args(argv)
    as_of = dt.date.fromisoformat(a.as_of) if a.as_of else dt.date.today()

    screen = local.load_price_screen()
    quality = sorted(screen)
    names = liquid_universe() if a.universe == "liquid" else {}
    for t, rec in local.load_quality().items():
        if t in quality:
            names.setdefault(t, rec.name or t)
    tickers = sorted(names)

    if a.fixture:
        return run_fixture(a, as_of)

    start = as_of - dt.timedelta(days=400)
    if a.dry_run:
        print(f"# dry run. --live would pull adjusted closes for {len(tickers)} names ({a.universe} universe), "
              f"{start} to {as_of}, in batched yfinance calls, cached under {paths.PRICE_CACHE}")
        print(f"#   then EDGAR submissions for each name with a CIK, forms 8-K, cached 6 hours, "
              f"at {edgar.SEC_RATE_PER_SECOND:.0f}/s: about {len(tickers) / edgar.SEC_RATE_PER_SECOND / 60:.0f} minutes")
        print(f"#   guidance diffs are read from {paths.ANALYSIS_DIR / '_guidance'} and never fetched here")
        print(f"#   the quality {len(quality)} carry the estimate field the breakout and pullback rules need")
        print("# this script has never made these requests")
        return 0

    paths.ensure_dirs()
    src: Dict[str, Dict[str, object]] = {}
    closes = None
    try:
        client = prices.PriceClient(downloader=None if a.live else prices.OfflineDownloader(), cache=Cache(paths.PRICE_CACHE))
        closes = client.daily_closes(tickers, start, as_of)
        live = sum(1 for v in client.last_source.values() if v == "live")
        cached = sum(1 for v in client.last_source.values() if v in ("cache", "stale"))
        missing = sum(1 for v in client.last_source.values() if v == "missing")
        if closes.dropna(how="all").empty:
            closes = None
        src["prices"] = {"live": live > 0, "detail": f"yfinance adjusted closes: {live} live, {cached} cached, {missing} missing"}
    except Offline as e:
        src["prices"] = {"live": False, "detail": f"offline: {e}"}

    earnings: Dict[str, List[str]] = {}
    if closes is not None:
        transport = HttpTransport(edgar.default_user_agent(), rate_per_second=edgar.SEC_RATE_PER_SECOND) if a.live \
            else DryRunTransport(sink=lambda s: None)
        c = edgar.EdgarClient(transport=transport, cache=Cache(paths.EDGAR_CACHE, default_ttl=6 * 3600.0))
        since = (as_of - dt.timedelta(days=7)).isoformat()
        n_cik, n_hit = 0, 0
        for t in tickers:
            try:
                cik = c.cik_for(t)
                if cik is None:
                    continue
                n_cik += 1
                dates = [f.filing_date for f in c.filings(cik, forms=["8-K"], limit=8)
                         if f.is_earnings_8k and f.filing_date >= since]
                if dates:
                    earnings[t] = dates
                    n_hit += 1
            except (Offline, FetchError):
                continue
        src["edgar"] = {"live": a.live, "detail": f"{n_cik} names with a CIK read, {n_hit} with an earnings 8-K since {since}"}
    else:
        src["edgar"] = {"live": False, "detail": "not read: no closes"}

    diffs: Dict[str, dict] = {}
    for t in tickers:
        b = guidance.load_diff(t)
        if b:
            diffs[t] = b
    src["guidance"] = {"live": False, "detail": f"{len(diffs)} diff(s) on disk under analysis/_guidance"}

    if closes is None:
        blob = setups.not_run_report(universe_n=len(tickers), quality_n=len(quality))
    else:
        rows = setups.all_setups(closes, as_of=as_of, earnings_dates=earnings, diffs=diffs, screen=screen,
                                 quality=quality, names=names)
        blob = setups.build_report(rows, as_of=as_of, universe_n=len(tickers), quality_n=len(quality), sources=src)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    report(blob)
    print(f"wrote {a.out}")
    return 0


def planted_panel(as_of: dt.date):
    """A synthetic panel with one name per setup and two that match nothing."""
    import numpy as np

    start = as_of - dt.timedelta(days=420)
    tk = ["GAPUP", "GUIDE", "BREAK", "PULL", "FLAT", "FALL"]
    panel = prices.synthetic_panel(tk, start=start, periods=300, seed=11, vol=0.0, start_price=100.0)
    panel = panel[panel.index <= str(as_of)]
    n = len(panel)
    t = np.arange(n)
    panel["GAPUP"] = 100.0 + 0.02 * t
    panel.loc[panel.index[-3]:, "GAPUP"] = [112.0, 112.5, 113.0]           # +8% release session, holding
    panel["GUIDE"] = 100.0 + 0.03 * t
    panel["BREAK"] = 100.0 + 0.15 * t                                         # a rising line closes at its high
    trend = 100.0 + 0.10 * t
    panel["PULL"] = trend + 3.0 * np.sin(t / 3.0)                             # around a rising mean
    panel.loc[panel.index[-1], "PULL"] = float(trend[-1])                     # today sits on the mean
    panel["FLAT"] = 100.0
    panel["FALL"] = 200.0 - 0.3 * t
    return panel


def run_fixture(a: argparse.Namespace, as_of: dt.date) -> int:
    out = a.out
    if paths.DASHBOARD_DIR.resolve() in out.resolve().parents:
        out = paths.CACHE_DIR / "watch" / "fixture_setups.json"
    panel = planted_panel(as_of)
    release = panel.index[-3].date().isoformat()
    earnings = {"GAPUP": [release], "FLAT": [release]}
    diffs = {"GUIDE": {"current": {"filed": panel.index[-2].date().isoformat()},
                       "items": [{"metric": "revenue", "period": "FY2027", "change": "raised", "what": "FY2027 revenue",
                                  "detail": "Midpoint +2.5%: $4.0bn last quarter, $4.1bn now."}]}}
    screen = {"BREAK": {"eps_fy1_chg_30d": 0.04}, "FLAT": {"eps_fy1_chg_30d": 0.04}, "FALL": {"eps_fy1_chg_30d": 0.04}}
    rows = setups.all_setups(panel, as_of=as_of, earnings_dates=earnings, diffs=diffs, screen=screen,
                             quality=["BREAK", "PULL", "FLAT", "FALL"],
                             names={t: f"{t} Fictional Corp" for t in panel.columns})
    src = {"prices": {"live": False, "detail": "synthetic panel with planted patterns"},
           "edgar": {"live": False, "detail": "fictional 8-K dates"},
           "guidance": {"live": False, "detail": "a fictional raised guide"}}
    blob = setups.build_report(rows, as_of=as_of, universe_n=len(panel.columns), quality_n=4, sources=src, status="SYNTHETIC")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    print("# FIXTURE. Planted patterns on fictional names. Measures nothing.")
    report(blob)
    print(f"wrote {out}")
    return 0


def report(blob: dict) -> None:
    print(f"status {blob['status']}: {blob['n_setups']} setup(s) over {blob['universe_n']} names "
          f"({blob['quality_n']} with the estimate field)")
    for s, v in blob["sources"].items():
        print(f"  {s:<9} {'live' if v.get('live') else 'not live':<9} {v.get('detail')}")
    for x in blob["setups"]:
        print(f"  {x['ticker']:<6} {x['label']:<24} entry {x['entry']:>9.2f} stop {x['stop']:>9.2f} "
              f"risk {x['risk_pct'] * 100:4.1f}%  {x['horizon_days']:>2}d  "
              + "; ".join(f"{k} {v}" for k, v in x["numbers"].items())[:90])


if __name__ == "__main__":
    raise SystemExit(main())
