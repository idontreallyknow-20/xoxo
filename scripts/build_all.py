#!/usr/bin/env python3
"""Rebuild every generated artefact, in order.

    python scripts/build_all.py            # normal rebuild
    python scripts/build_all.py --check    # pinned timestamps, so the output is diffable

The --check mode exists so a rebuild can be compared byte for byte against what is
committed. If a rebuild changes anything, either an input changed or the build has
become nondeterministic, and the second is worth knowing immediately: a build that
produces different bytes from the same inputs makes the git history stop meaning
anything.

This does not run the original pipeline (universe, fundamentals, quality_screen,
price_screen, build_dashboard). Those need network access and are unchanged.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ("analysis records and page shells", "build_analysis.py"),
    ("scorecard and positioning memo", "build_positioning.py"),
    ("backtest calibration and score structure", "backtest_run.py"),
    ("journal calls graded against prices", "track_calls.py"),
    ("daily scan, from caches only", "scan.py"),
    ("swing setups, from caches only", "setups.py"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--snapshot", action="store_true", help="also archive a dated snapshot")
    a = ap.parse_args()

    failed = 0
    for label, script in STEPS:
        cmd = [sys.executable, str(HERE / script)]
        if a.check:
            cmd.append("--check")
        print(f"\n=== {label}")
        r = subprocess.run(cmd, cwd=str(HERE.parent))
        if r.returncode:
            print(f"  FAILED with {r.returncode}", file=sys.stderr)
            failed += 1

    if a.snapshot:
        print("\n=== snapshot")
        subprocess.run([sys.executable, str(HERE / "snapshot.py")], cwd=str(HERE.parent))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
