#!/usr/bin/env python3
"""Run the test suite and write its result where the email can read it.

    python scripts/run_tests.py                 # the whole suite -> data/cache/pytest_last.json
    python scripts/run_tests.py tests/test_book.py -q

Exits with pytest's code. The JSON carries passed, failed, errors, the failing test ids,
seconds and when it ran, so the digest can print "tests: N passed, M failed" and turn the
subject red when something broke. Nothing here skips or hides a test.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import paths  # noqa: E402

OUT = paths.CACHE_DIR / "pytest_last.json"


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:]) or ["-q"]
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "pytest", *args, "-rf", "--no-header"], cwd=str(paths.ROOT),
                       capture_output=True, text=True)
    out = r.stdout + r.stderr
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    counts = {k: 0 for k in ("passed", "failed", "errors", "skipped")}
    for n, word in re.findall(r"(\d+) (passed|failed|error|errors|skipped)", tail):
        counts["errors" if word.startswith("error") else word] = int(n)
    failed = re.findall(r"^FAILED (\S+)", out, re.M) + re.findall(r"^ERROR (\S+)", out, re.M)
    blob = {"ran_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "seconds": round(time.time() - t0, 1),
            "returncode": r.returncode, "summary": tail, **counts, "failed_tests": failed, "args": args}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(blob, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(tail)
    for f in failed:
        print(f"  FAILED {f}")
    print(f"wrote {OUT}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
