"""A rebuild from the same inputs must produce the same bytes.

If it does not, either an input changed or the build picked up something
nondeterministic, and the second matters: a build whose output drifts on its own
makes every diff in the git history meaningless, because you can no longer tell a
real change from noise.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATED = [
    "dashboard/analysis/KLAC.json",
    "dashboard/analysis/index.json",
    "dashboard/scorecard.json",
    "dashboard/positioning.json",
    "dashboard/backtest.json",
    "dashboard/tracker.json",
    "dashboard/watch.json",
    "dashboard/setups.json",
]


def build():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_all.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-2000:]


def strip_stamp(text: str) -> str:
    """Everything except when the build ran.

    An earlier version pinned built_at to a 1970 sentinel so the files could be
    diffed byte for byte, and then shipped those files, so every page's provenance
    line told the reader the data was built on 1 January 1970. The stamp is honest
    now and excluded here instead.
    """
    return re.sub(r'"built_at":\s*"[^"]*"', '"built_at": "<stamp>"', text)


@pytest.mark.parametrize("rel", GENERATED)
def test_rebuild_is_identical_apart_from_the_timestamp(rel):
    p = ROOT / rel
    build()
    first = strip_stamp(p.read_text())
    build()
    assert strip_stamp(p.read_text()) == first, f"{rel} changed between two identical builds"


@pytest.mark.parametrize("rel", GENERATED)
def test_the_shipped_timestamp_is_a_real_date(rel):
    """A page that says it was built in 1970 tells the reader the data is 56 years
    old. Whatever else built_at is, it has to be plausible."""
    import datetime as dt
    import json

    blob = json.loads((ROOT / rel).read_text())
    stamp = blob.get("built_at")
    if stamp is None:
        return
    when = dt.datetime.fromisoformat(stamp)
    assert when.year >= 2025, f"{rel} claims it was built at {stamp}"


def test_the_page_shells_are_regenerated_identically():
    build()
    p = ROOT / "dashboard" / "analyze" / "KLAC" / "index.html"
    first = p.read_bytes()
    build()
    assert p.read_bytes() == first


def test_build_all_reports_a_failure_rather_than_exiting_zero(tmp_path, monkeypatch):
    """A build script that swallows a failed step is worse than one that crashes."""
    src = (ROOT / "scripts" / "build_all.py").read_text()
    assert "return 1 if failed else 0" in src
    assert "FAILED with" in src
