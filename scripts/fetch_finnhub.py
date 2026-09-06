#!/usr/bin/env python3
"""Pull the free Finnhub endpoints for one or more tickers.

    export FINNHUB_KEY=your_free_key
    python scripts/fetch_finnhub.py AAPL KLAC        # quote, profile, metrics
    python scripts/fetch_finnhub.py --dry-run AAPL   # print the exact plan, send nothing
    python scripts/fetch_finnhub.py --all AAPL       # add earnings, calendar, news, analysts, insiders
    python scripts/fetch_finnhub.py --probe-premium AAPL   # ask which paid endpoints this key can reach

The key is read from FINNHUB_KEY and from nowhere else. There is no flag for it,
on purpose: a key that only lives in the environment cannot end up in a shell
history file or a commit.

--dry-run needs no key and makes no request. It prints the plan from the same
table the real fetchers read, so it cannot drift from what a real run would do,
and the token is replaced by a placeholder before the URL is ever printed.

Nothing here has run against the live API: the machine it was written on cannot
reach finnhub.io. Run the dry run first, then one ticker, and read the output
before trusting it on the whole universe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import finnhub, paths  # noqa: E402
from an.finnhub import BadKey, MissingKey, PremiumEndpoint  # noqa: E402
from an.http import DryRunTransport, HttpTransport  # noqa: E402
from an.store import Cache, FetchError, Offline, RateLimited  # noqa: E402

DEFAULT_SECTIONS = ("quote", "profile", "metrics")

# section name -> the flag that turns it on. Everything here is free tier.
EXTRA_SECTIONS = {
    "earnings": "earnings",
    "calendar": "calendar",
    "news": "news",
    "recommendations": "analysts",
    "insiders": "insiders",
}


def build_client(dry_run: bool) -> finnhub.FinnhubClient:
    transport = DryRunTransport() if dry_run else HttpTransport(
        "desk personal research", rate_per_second=finnhub.RATE_PER_SECOND
    )
    return finnhub.FinnhubClient(transport=transport, cache=Cache(paths.FINNHUB_CACHE, default_ttl=finnhub.TTL_METRIC))


def sections_for(args: argparse.Namespace) -> List[str]:
    chosen = list(DEFAULT_SECTIONS)
    chosen += [s for s, flag in EXTRA_SECTIONS.items() if args.all or getattr(args, flag, False)]
    return chosen


def print_plan(client: finnhub.FinnhubClient, tickers: List[str], sections: List[str]) -> int:
    print("# dry run. No request is sent and no key is needed.")
    print(f"# rate budget: {finnhub.CALLS_PER_MINUTE} calls a minute, "
          f"{len(sections)} calls a ticker, {len(sections) * len(tickers)} in total\n")
    for t in tickers:
        print(f"=== {t.upper()}")
        for call in client.plan(t, sections=sections):
            print(f"  GET {call.url}")
            print(f"      cache {call.cache_key}.json, ttl {int(call.ttl_seconds)}s  ({call.label})")
        print()
    print("not attempted, a free key gets 403 on all of these:")
    for endpoint, why in sorted(finnhub.PREMIUM_ENDPOINTS.items()):
        print(f"  {endpoint:<28} {why}")
    return 0


def report(client: finnhub.FinnhubClient, ticker: str, sections: List[str]) -> int:
    """Fetch one ticker. Returns the number of sections that failed."""
    failures = 0

    def attempt(name, fn):
        nonlocal failures
        try:
            return fn()
        except BadKey:
            # Not a section failure: the key is wrong for every section of every
            # remaining ticker. Must reach run(), which stops the whole pull.
            # BadKey subclasses FetchError, so without this clause the catch-all
            # below swallows it and the run spends a 401 on all 1500 names.
            # MissingKey needs no clause here -- it is not a FetchError.
            raise
        except PremiumEndpoint as e:
            # Not an outage. Name the gap and keep the rest of the pull.
            print(f"  - {name}: unavailable on this tier ({e.endpoint})")
        except RateLimited as e:
            print(f"  ! {name}: rate limited, back off and retry ({e})")
            failures += 1
        except (FetchError, Offline) as e:
            print(f"  ! {name}: {e}")
            failures += 1
        return None

    if "quote" in sections:
        q = attempt("quote", lambda: client.quote(ticker))
        if q is not None:
            if q.is_empty:
                print("  quote      no data, Finnhub does not know this symbol")
            else:
                # A partial quote is a real shape: a zeroed last price with a
                # previous close that survived leaves current None rather than 0.0.
                price = "-" if q.current is None else q.current
                pct = "-" if q.percent_change is None else f"{q.percent_change}%"
                print(f"  quote      {price}  ({pct})  as of {q.as_of}")

    if "profile" in sections:
        p = attempt("profile", lambda: client.profile(ticker))
        if p is not None:
            cap = "?" if p.market_cap is None else f"{p.market_cap / 1e9:,.1f}bn {p.currency or ''}".strip()
            print(f"  profile    {p.name or '?'}  {p.exchange or '?'}  {p.country or '?'}  cap {cap}")
            print(f"             industry {p.finnhub_industry or p.industry or '?'}  ipo {p.ipo or '?'}")

    if "metrics" in sections:
        m = attempt("metrics", lambda: client.metrics(ticker))
        if m is not None:
            summary = m.summary()
            known = {k: v for k, v in summary.items() if v is not None}
            print(f"  metrics    {len(known)} of {len(summary)} ratios present")
            for name in ("pe", "ev_ebitda", "roe", "gross_margin", "net_margin", "debt_to_equity", "beta"):
                v = summary[name]
                print(f"             {name:<18} {'-' if v is None else round(v, 4)}")
            for freq in ("annual", "quarterly"):
                names = m.series_names(freq)
                print(f"             series.{freq:<10} {len(names)} keys: {', '.join(names[:6]) or '-'}")

    if "earnings" in sections:
        rows = attempt("earnings", lambda: client.earnings(ticker)) or []
        for r in rows:
            beat = "?" if r.beat is None else ("beat" if r.beat else "miss")
            print(f"  earnings   {r.label:<8} actual {r.actual}  est {r.estimate}  {beat}")

    if "calendar" in sections:
        rows = attempt("calendar", lambda: client.earnings_calendar(ticker)) or []
        for r in rows[:4]:
            print(f"  calendar   {r.date}  est {r.eps_estimate}  {r.session or 'time not set'}")

    if "news" in sections:
        items = attempt("news", lambda: client.company_news(ticker)) or []
        print(f"  news       {len(items)} items in the last {finnhub.NEWS_DAYS} days")
        for n in items[:5]:
            print(f"             {n.day or '?'}  {n.source or '?':<12} {(n.headline or '')[:76]}")

    if "recommendations" in sections:
        rows = attempt("recommendations", lambda: client.recommendations(ticker)) or []
        for r in rows[:3]:
            share = "-" if r.bullish_share is None else f"{r.bullish_share:.0%}"
            covering = "?" if r.total is None else r.total
            print(f"  analysts   {r.period}  {covering} covering, {share} bullish")

    if "insiders" in sections:
        rows = attempt("insiders", lambda: client.insider_transactions(ticker)) or []
        sells = [r for r in rows if r.is_open_market_sale]
        buys = [r for r in rows if r.is_open_market_buy]
        print(f"  insiders   {len(rows)} filings, {len(buys)} open market buys, {len(sells)} sales")

    return failures


def probe_premium(client: finnhub.FinnhubClient, ticker: str) -> None:
    print("  probing the paid endpoints, one call each:")
    for endpoint in sorted(finnhub.PREMIUM_ENDPOINTS):
        params = {"symbol": ticker}
        if endpoint == "/stock/candle":
            params.update({"resolution": "D", "from": 0, "to": 1})
        reachable, why = client.probe_premium(endpoint, params)
        print(f"    {'yes' if reachable else 'no ':<4} {endpoint:<28} {why}")


def run(tickers, *, dry_run=False, sections=None, probe=False) -> int:
    paths.ensure_dirs()
    sections = list(sections or DEFAULT_SECTIONS)
    client = build_client(dry_run)

    if dry_run:
        return print_plan(client, tickers, sections)

    failures = 0
    for t in tickers:
        t = t.upper()
        print(f"\n=== {t}")
        try:
            failures += report(client, t, sections)
            if probe:
                probe_premium(client, t)
        except BadKey as e:
            # One bad key means every remaining ticker fails the same way. Stop.
            print(f"  ! {e}", file=sys.stderr)
            return 2
        except MissingKey as e:
            print(f"  ! {e}", file=sys.stderr)
            return 2

    print(f"\ncache: {json.dumps(Cache(paths.FINNHUB_CACHE).stats())}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--dry-run", action="store_true", help="print the URLs that would be requested and exit")
    ap.add_argument("--earnings", action="store_true", help="also pull the last four reported quarters")
    ap.add_argument("--calendar", action="store_true", help="also pull the upcoming earnings dates")
    ap.add_argument("--news", action="store_true", help=f"also pull the last {finnhub.NEWS_DAYS} days of company news")
    ap.add_argument("--analysts", action="store_true", help="also pull the buy/hold/sell counts")
    ap.add_argument("--insiders", action="store_true", help="also pull insider transactions")
    ap.add_argument("--all", action="store_true", help="every free endpoint")
    ap.add_argument("--probe-premium", action="store_true", help="spend a call on each paid endpoint to test the tier")
    a = ap.parse_args()

    if not a.dry_run and not os.environ.get(finnhub.KEY_ENV):
        print(
            f"warning: {finnhub.KEY_ENV} is not set. Export your free Finnhub key, "
            f"or use --dry-run, which needs no key.",
            file=sys.stderr,
        )
    return run(a.tickers, dry_run=a.dry_run, sections=sections_for(a), probe=a.probe_premium)


if __name__ == "__main__":
    raise SystemExit(main())
