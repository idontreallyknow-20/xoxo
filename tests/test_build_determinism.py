"""A rebuild from the same inputs must produce the same bytes.

If it does not, either an input changed or the build picked up something
nondeterministic, and the second matters: a build whose output drifts on its own
makes every diff in the git history meaningless, because you can no longer tell a
real change from noise.
"""
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
]


def build():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_all.py"), "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-2000:]


@pytest.mark.parametrize("rel", GENERATED)
def test_rebuild_is_byte_identical(rel):
    p = ROOT / rel
    build()
    first = p.read_bytes()
    build()
    assert p.read_bytes() == first, f"{rel} changed between two identical builds"


def test_no_generated_file_carries_a_wall_clock_timestamp_under_check():
    build()
    for rel in GENERATED:
        text = (ROOT / rel).read_text()
        if '"built_at"' in text:
            assert '"built_at": "1970-01-01T00:00:00"' in text, rel


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
