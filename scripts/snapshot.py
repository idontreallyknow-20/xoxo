#!/usr/bin/env python3
"""Archive a dated copy of the current screen output.

    python scripts/snapshot.py              # archive the current pull
    python scripts/snapshot.py --list       # what is archived
    python scripts/snapshot.py --report     # what the archive can support yet

Run this MONTHLY, not just after the quarterly re-underwrite. It is the only thing
that turns the backtest question from impossible into merely slow, and the cadence
decides how slow: the engine will not say more than "weak" below twelve independent
periods, which is three years of quarterly snapshots and one year of monthly ones.
See scripts/an/power.py for the measurement behind that.

A snapshot contains only what the pipeline could see on the day, so a panel built
from several has no look-ahead and no survivorship problem.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import paths, snapshots  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="override the date (defaults to the pulled column)")
    ap.add_argument("--force", action="store_true", help="re-archive even if the date exists")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    paths.ensure_dirs()

    if a.list:
        for s in snapshots.list_snapshots():
            print(f"{s.date}  {s.n_scored} scored, {s.n_priced} priced  {len(s.files)} files  {s.directory}")
        return 0

    if a.report:
        print(json.dumps(snapshots.coverage_report(), indent=2))
        return 0

    s = snapshots.archive(a.date, force=a.force)
    print(f"archived {s.date} to {s.directory}")
    print(f"  {s.n_scored} scored names, {s.n_priced} priced")
    for name, digest in sorted(s.files.items()):
        print(f"  {digest}  {name}")
    rep = snapshots.coverage_report()
    print(f"\n{rep['status']}")

    from an import power

    adv = power.archiving_cadence_advice()
    n = rep["n_snapshots"]
    if n < power.VERDICT_FLOOR_PERIODS:
        need = power.VERDICT_FLOOR_PERIODS - n
        print(f"{need} more before the engine will return a verdict above 'weak': "
              f"{need / 12:.1f} years at monthly, {need / 4:.1f} years at quarterly.")
        print(f"{adv['recommendation'].splitlines()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
