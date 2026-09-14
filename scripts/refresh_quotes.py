#!/usr/bin/env python3
"""Make sure the quotes on disk carry the last session's close, firing the runner if they don't.

    python scripts/refresh_quotes.py              # fetch the quotes branch; if it is behind, dispatch
                                                  # quotes.yml on main, wait for the commit, fetch again
    python scripts/refresh_quotes.py --no-dispatch   # just fetch and report
    python scripts/refresh_quotes.py --wait          # someone else dispatched the runner (the desk's GitHub
                                                     # tool, say); poll the branch until it carries the session
    python scripts/refresh_quotes.py --ref mybranch --timeout 900

The token the container pushes with can read the API but is refused for workflow_dispatch
(403, "not accessible by integration"); the session's GitHub tool can dispatch. So the routine
runs this once, and on "refused" dispatches with the tool and runs it again with --wait.

Exit 0 when data/cache/quotes carries the last completed session, 1 when it is still
behind (the routine then marks at whatever close it has, and the email prints the age).
Nothing here invents a price.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import ghactions, quotes  # noqa: E402

WORKFLOW = "quotes.yml"
CLOSE_UTC = dt.time(21, 0)   # the runner's pull is trustworthy from about an hour after the 16:00 New York close


def last_completed_session(now: dt.datetime) -> dt.date:
    """The most recent weekday whose close has printed, as of ``now`` (UTC). Holidays are not known here;
    on one the quotes stay a day behind and the email says so."""
    d = now.date()
    if d.weekday() < 5 and now.time() >= CLOSE_UTC:
        return d
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def wait_for_session(want: dt.date, *, timeout: float, poll: float, fetch=None, sleep=time.sleep,
                     clock=time.monotonic) -> Optional[quotes.QuotePanel]:
    """Fetch the quotes branch every ``poll`` seconds until its last session is ``want`` or later."""
    fetch = fetch or quotes.fetch_branch
    t0 = clock()
    while True:
        panel = fetch()
        if panel and panel.last and panel.last >= want:
            print(f"quotes branch now: {panel.describe()}")
            return panel
        if clock() - t0 >= timeout:
            print("the runner did not commit in time", file=sys.stderr)
            return None
        sleep(poll)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--wait", action="store_true", help="do not dispatch; poll the branch until it carries the session")
    ap.add_argument("--poll", type=float, default=20.0, help="seconds between polls in --wait")
    ap.add_argument("--ref", default="main", help="branch whose quotes.yml to run (default main)")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the runner's commit")
    ap.add_argument("--now", default=None, help="ISO datetime in UTC, for tests")
    a = ap.parse_args(argv)
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    want = last_completed_session(now)

    panel = quotes.fetch_branch()
    print(f"quotes branch: {panel.describe() if panel else 'not reachable'}; last completed session {want}")
    if panel and panel.last and panel.last >= want:
        return 0
    if a.no_dispatch:
        return 1
    if a.wait:
        return 0 if wait_for_session(want, timeout=a.timeout, poll=a.poll) else 1
    slug = ghactions.repo_slug()
    tok = ghactions.token()
    if not slug or not tok:
        print("cannot dispatch: " + ("no origin remote on github.com" if not slug else "no GH_TOKEN or GITHUB_TOKEN"), file=sys.stderr)
        return 1
    old = ghactions.branch_head(slug, quotes.QUOTES_BRANCH, auth=tok)
    if not ghactions.dispatch(slug, WORKFLOW, a.ref, auth=tok):
        print(f"dispatch of {WORKFLOW} on {a.ref} was refused", file=sys.stderr)
        return 1
    print(f"dispatched {WORKFLOW} on {a.ref}; waiting up to {a.timeout:.0f}s for the {quotes.QUOTES_BRANCH} branch to move")
    new = ghactions.wait_for_new_head(slug, quotes.QUOTES_BRANCH, old, timeout=a.timeout, auth=tok)
    if not new:
        print("the runner did not commit in time", file=sys.stderr)
        return 1
    panel = quotes.fetch_branch()
    print(f"quotes branch now: {panel.describe() if panel else 'not reachable'}")
    return 0 if panel and panel.last and panel.last >= want else 1


if __name__ == "__main__":
    raise SystemExit(main())
