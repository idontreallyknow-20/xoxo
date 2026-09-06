#!/usr/bin/env python3
"""Pull SEC EDGAR filings and XBRL facts for one or more tickers.

    python scripts/fetch_edgar.py KLAC BKNG          # fetch and cache
    python scripts/fetch_edgar.py --dry-run KLAC     # print the exact URLs, send nothing
    python scripts/fetch_edgar.py --facts KLAC       # also pull the full XBRL fact set
    python scripts/fetch_edgar.py --exhibits KLAC    # resolve and cache the 8-K earnings exhibits

Set SEC_USER_AGENT to "Your Name your@email.com" first. The SEC asks for a real
contact address and throttles requests without one.

Nothing here has ever run against the live API: the machine it was written on
cannot reach sec.gov. Run it once and read the output before trusting it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import edgar, paths  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline  # noqa: E402

FORMS = ("10-K", "10-Q", "8-K")


def build_client(dry_run: bool) -> edgar.EdgarClient:
    ua = edgar.default_user_agent()
    transport = DryRunTransport() if dry_run else HttpTransport(ua, rate_per_second=edgar.SEC_RATE_PER_SECOND)
    cache = Cache(paths.EDGAR_CACHE, default_ttl=0.0 if dry_run else 86_400.0)
    return edgar.EdgarClient(transport=transport, cache=cache, user_agent=ua)


def run(tickers, *, dry_run=False, facts=False, exhibits=False, limit=40) -> int:
    paths.ensure_dirs()
    client = build_client(dry_run)
    if dry_run:
        print(f"# dry run. User-Agent: {client.user_agent}\n")

    failures = 0
    for t in tickers:
        t = t.upper()
        print(f"\n=== {t}")
        try:
            cik = client.cik_for(t)
        except Offline:
            print("  (dry run stops here: the ticker to CIK map is the first call)")
            print(f"  next: GET https://data.sec.gov/submissions/CIK<10-digit-cik>.json")
            if facts:
                print(f"  then: GET https://data.sec.gov/api/xbrl/companyfacts/CIK<10-digit-cik>.json")
            if exhibits:
                print("  then: GET https://www.sec.gov/Archives/edgar/data/<cik>/<accession>/index.json")
                print("        then the EX-99.1 / EX-99.2 document URLs found in it")
            continue
        except FetchError as e:
            print(f"  ! {e}")
            failures += 1
            continue

        if not cik:
            print("  ! no CIK found on EDGAR for this ticker")
            failures += 1
            continue
        print(f"  CIK {cik}")

        try:
            filings = client.filings(cik, forms=FORMS, limit=limit)
        except FetchError as e:
            print(f"  ! submissions: {e}")
            failures += 1
            continue

        for f in filings[:12]:
            flag = "  <- earnings" if f.is_earnings_8k else ""
            print(f"  {f.filing_date}  {f.form:<6} {f.accession}  period {f.report_date or '?'}{flag}")

        if exhibits:
            for f in [x for x in filings if x.is_earnings_8k][:4]:
                try:
                    ex = client.earnings_exhibits(f)
                except FetchError as e:
                    print(f"    ! exhibits for {f.accession}: {e}")
                    continue
                print(f"    {f.filing_date} 99.1 {ex['ex99_1'] or '-'}")
                print(f"    {f.filing_date} 99.2 {ex['ex99_2'] or '-'}")

        if facts:
            try:
                cf = client.companyfacts(cik)
            except FetchError as e:
                print(f"  ! companyfacts: {e}")
                failures += 1
                continue
            print("  XBRL facts by metric (count, latest period, latest filed):")
            for metric in edgar.KEY_TAGS:
                got = client.metric(cf, metric)
                if got:
                    last = got[-1]
                    print(f"    {metric:<22} {len(got):>4}  {last.end}  filed {last.filed}  [{last.tag}]")
                else:
                    print(f"    {metric:<22}    -  no tag in KEY_TAGS matched")

    print(f"\ncache: {json.dumps(Cache(paths.EDGAR_CACHE).stats())}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--dry-run", action="store_true", help="print the URLs that would be requested and exit")
    ap.add_argument("--facts", action="store_true", help="also pull the full XBRL companyfacts blob")
    ap.add_argument("--exhibits", action="store_true", help="resolve 8-K Exhibit 99.1 / 99.2 URLs")
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    if not os.environ.get("SEC_USER_AGENT") and not a.dry_run:
        print("warning: SEC_USER_AGENT is not set. Set it to 'Your Name your@email.com'.", file=sys.stderr)
    return run(a.tickers, dry_run=a.dry_run, facts=a.facts, exhibits=a.exhibits, limit=a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
