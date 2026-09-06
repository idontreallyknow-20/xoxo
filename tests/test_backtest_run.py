import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "backtest_run.py"


@pytest.fixture(scope="module")
def payload(tmp_path_factory):
    out = tmp_path_factory.mktemp("bt") / "backtest.json"
    r = subprocess.run([sys.executable, str(SCRIPT), "--out", str(out)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text())


def test_status_is_honest_about_not_having_run(payload):
    """The one thing this file must never do is imply a backtest happened."""
    assert payload["status"] == "NOT RUN"
    assert len(payload["why_not_run"]) >= 4
    assert any("one dated cross section" in x for x in payload["why_not_run"])
    assert any("survivorship" in x.lower() for x in payload["why_not_run"])
    assert any("scripts/backtest_run.py --live" in x for x in payload["why_not_run"])


def test_calibration_recovers_a_planted_signal(payload):
    cases = {c["case"]: c for c in payload["engine_calibration"]["cases"]}
    planted = cases["planted alpha, realistic size"]
    assert planted["measured_rank_ic"] > 0
    assert planted["ci"][0] > 0


def test_calibration_finds_nothing_in_nothing(payload):
    cases = {c["case"]: c for c in payload["engine_calibration"]["cases"]}
    none = cases["no relationship at all"]
    assert none["verdict"] == "no evidence"
    assert none["ci"][0] < 0 < none["ci"][1]


def test_calibration_flags_contamination(payload):
    cases = {c["case"]: c for c in payload["engine_calibration"]["cases"]}
    bad = cases["look-ahead contamination"]
    assert bad["measured_rank_ic"] > 0.9
    assert any("look-ahead" in f for f in bad["flags"])


def test_calibration_shows_the_universe_shrinking_when_names_delist(payload):
    cases = {c["case"]: c for c in payload["engine_calibration"]["cases"]}
    d = cases["names that stop trading"]
    assert d["names_last_date"] < d["names_first_date"]


def test_the_measured_false_positive_rate_is_published(payload):
    assert payload["engine_calibration"]["measured_false_positive_rate"] >= 0.05


def test_score_structure_is_reported_for_every_variant(payload):
    s = payload["score_structure"]
    assert set(s) == {"quality_value", "with_momentum", "with_reversal"}
    for v in s.values():
        assert v["n_names"] == 150
        assert v["notes"]
        assert "predicts returns" in v["notes"][0]


def test_the_contested_variants_barely_change_the_ranking(payload):
    """Momentum and reversal take opposite views of a drawdown, and at a 10 percent
    weight they still rank the list about the same. That is worth knowing before
    anyone argues about which is right."""
    rc = payload["variant_disagreement"]["rank_correlations"]
    assert rc["with_momentum vs with_reversal"]["rank_correlation"] > 0.8
    assert "opposite views" in payload["variant_disagreement"]["note"]


def test_output_is_json_and_stable(payload):
    assert json.dumps(payload)
    assert payload["snapshot_date"] == "2026-09-04"


def test_calibrate_only_mode_skips_the_snapshot_work(tmp_path):
    out = tmp_path / "b.json"
    r = subprocess.run([sys.executable, str(SCRIPT), "--calibrate", "--out", str(out)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text())
    assert "engine_calibration" in d
    assert "score_structure" not in d
