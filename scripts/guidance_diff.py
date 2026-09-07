#!/usr/bin/env python3
"""Diff the guidance language of a name's last two 8-K press releases, mechanically.

    export SEC_USER_AGENT="Your Name your@email.com"
    python scripts/guidance_diff.py --dry-run KLAC          # the EDGAR calls it would make, nothing sent
    python scripts/guidance_diff.py KLAC BKNG               # resolve, fetch, diff, write analysis/_guidance/<T>.json
    python scripts/guidance_diff.py --fixture               # the diff's shape on the two hand-built releases
    python scripts/guidance_diff.py --fixture --out /tmp/x.json

For each ticker: the two newest 8-Ks carrying Item 2.02, their Exhibit 99.1,
stripped to text, read by an.guidance.extract, and diffed. The result lands in
dashboard/analysis/_guidance/<TICKER>.json, which build_analysis.py merges into
the page's "What changed since the last report" as a block labelled mechanical.

This script has never run against sec.gov: the machine it was written on could
not reach it. The fixture is a fictional filer, so nothing it prints is about a
real company and nothing it writes goes under dashboard/ unless --out says so.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import edgar, guidance, paths  # noqa: E402
from an.htmltext import to_text  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline  # noqa: E402

FIXTURES = paths.ROOT / "tests" / "fixtures"
OUT_DIR = paths.ANALYSIS_DIR / "_guidance"


def earnings_8ks(client: edgar.EdgarClient, cik: str, *, limit: int = 2) -> List[edgar.Filing]:
    """The newest 8-Ks that announce results (Item 2.02), newest first."""
    out = [f for f in client.filings(cik, forms=["8-K"], limit=60) if any(i.startswith("2.02") for i in f.items)]
    return out[:limit]


def one(client: edgar.EdgarClient, ticker: str, *, dry_run: bool) -> Optional[Dict[str, Any]]:
    cik = client.cik_for(ticker)
    if cik is None:
        print(f"  {ticker}: no CIK in the EDGAR ticker map", file=sys.stderr)
        return None
    filings = earnings_8ks(client, cik)
    if len(filings) < 2:
        print(f"  {ticker}: fewer than two Item 2.02 8-Ks found ({len(filings)})", file=sys.stderr)
        return None
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    for f in filings:
        ex = client.earnings_exhibits(f)
        url = ex.get("ex99_1")
        if not url:
            print(f"  {ticker}: 8-K {f.accession} has no Exhibit 99.1", file=sys.stderr)
            return None
        metas.append({"accession": f.accession, "filed": f.filing_date, "report_date": f.report_date, "url": url})
        if dry_run:
            print(f"  would read {url}")
            continue
        texts.append(client.document_text(url))
    if dry_run:
        return None
    year = None
    try:
        year = int(str(filings[0].filing_date)[:4])
    except ValueError:
        pass
    return guidance.build_block(ticker, current_meta=metas[0], prior_meta=metas[1],
                                current_text=texts[0], prior_text=texts[1], default_year=year)


def print_block(b: Dict[str, Any]) -> None:
    print(f"{b['ticker']}: current {b['current'].get('filed')} vs prior {b['prior'].get('filed')}; "
          f"{b['extracted']['current']} and {b['extracted']['prior']} guided figures read")
    for it in b["items"]:
        print(f"  {it['change']:<13} {it['what']:<28} {it['detail']}")
    for nd in b["not_determinable"]:
        print(f"  no figure     {nd}")
    print(f"  caveat: {b['caveat'][:90]}...")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="print the EDGAR requests, send nothing")
    ap.add_argument("--fixture", action="store_true", help="diff the two hand-built fictional releases")
    ap.add_argument("--out", default=None, help="write the fixture diff here instead of only printing it")
    a = ap.parse_args(argv)

    if a.fixture:
        print("# FIXTURE. Two hand-built releases for a fictional filer, tests/fixtures/ex991_*.htm. Not a download.\n")
        cur = to_text((FIXTURES / "ex991_current.htm").read_text(encoding="utf-8"))
        pri = to_text((FIXTURES / "ex991_prior.htm").read_text(encoding="utf-8"))
        b = guidance.build_block(
            "EXMPL",
            current_meta={"accession": "0000000000-26-000002", "filed": "2026-07-28", "url": "fixture:ex991_current.htm"},
            prior_meta={"accession": "0000000000-26-000001", "filed": "2026-04-28", "url": "fixture:ex991_prior.htm"},
            current_text=cur, prior_text=pri, default_year=2026,
            built_at=dt.datetime.now().replace(microsecond=0).isoformat(),
        )
        print_block(b)
        if a.out:
            Path(a.out).write_text(json.dumps(b, indent=1), encoding="utf-8")
            print(f"\nwrote {a.out}")
        return 0

    if not a.tickers:
        ap.print_usage()
        return 2
    ua = edgar.default_user_agent()
    if a.dry_run:
        print(f"# dry run. User-Agent: {ua}. This script has never made a real request.")
        client = edgar.EdgarClient(transport=DryRunTransport(), cache=Cache(paths.EDGAR_CACHE, default_ttl=float("inf")))
    else:
        paths.ensure_dirs()
        client = edgar.EdgarClient(transport=HttpTransport(ua, rate_per_second=edgar.SEC_RATE_PER_SECOND))
    failures = 0
    for t in [x.upper() for x in a.tickers]:
        try:
            b = one(client, t, dry_run=a.dry_run)
        except (Offline, FetchError) as e:
            print(f"  ! {t}: {e}", file=sys.stderr)
            failures += 1
            continue
        if b is None:
            if not a.dry_run:
                failures += 1
            continue
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUT_DIR / f"{t}.json"
        p.write_text(json.dumps(b, indent=1), encoding="utf-8")
        print_block(b)
        print(f"  wrote {p}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
