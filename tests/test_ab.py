"""The A/B harness: the grid, the control, paired differences, pre-registration and the report."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from an import ab, history, paper
from tests.test_language_guard import check as language_check

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "history"


@pytest.fixture(scope="module")
def small_run():
    panel, mem, _ = history.load_fixture(FIX)
    f = paper.compute_features(panel, mem)
    arms = [ab.arm_by_id("pullback_b3_h10"), ab.arm_by_id("pullback_b2_h10"), ab.arm_by_id("breakout_s8_h10"),
            ab.arm_by_id("spy_hold")]
    data = {"scope": "fixture", "membership": {"members_on_first_session": 12, "members_on_last_session": 12,
                                               "n_tickers": 12}, "dataset_verdict": "adjusted"}
    return ab.run_ab(f, arms, split="train", window=(panel.first, panel.last), rules=paper.Rules(), costs=paper.CostModel(),
                     sectors={}, data=data, hypotheses_tested=3, control_replicates=4, run_at="2026-09-07T00:00:00+00:00")


def test_default_grid_is_the_declared_one():
    arms = ab.default_arms()
    ids = [a.id for a in arms]
    assert len(ids) == len(set(ids)) == 25
    assert sum(1 for a in arms if a.kind == "pullback") == 9
    assert sum(1 for a in arms if a.kind == "breakout_price_only") == 9
    assert sum(1 for a in arms if a.kind == "pead_proxy") == 6
    assert ids[-1] == "spy_hold"
    assert ab.arm_by_id("pullback_b3_h10").params == {"band": 0.03, "max_dd": -0.15}
    assert ab.arm_by_id("breakout_s7_h5").params == {"stop_pct": 0.07}   # an ad hoc id from the loop
    assert ab.arm_by_id("nonsense_x1_h5") is None


def test_splits_are_fixed_and_do_not_overlap():
    assert ab.SPLITS["train"] == ("2013-02-08", "2016-06-30")
    assert ab.SPLITS["test"] == ("2016-07-01", "2018-02-07")


def test_run_ab_produces_nulls_pairs_and_a_clean_json(small_run):
    res = small_run
    assert set(res.nulls) == {"pullback_b3_h10", "pullback_b2_h10", "breakout_s8_h10"}
    null = res.nulls["pullback_b3_h10"]
    assert null.n_replicates == 4 and len(null.means) == 4 and null.p95 is not None
    assert [(p.a, p.b) for p in res.pairs] == [("pullback_b2_h10", "pullback_b3_h10")] and res.pairs[0].n_buckets > 10
    j = res.to_json()
    assert j["split"] == "train" and j["verdict"] == "in-sample only" and j["hypotheses_tested"] == 3
    arm = next(a for a in j["arms"] if a["arm"]["id"] == "pullback_b3_h10")
    assert arm["null"]["p95"] is not None and "decision_rule" in arm
    assert len(j["limitations"]) >= 8
    assert not language_check(j, "ab")


def test_paired_difference_inner_joins_month_buckets(small_run):
    a = next(r for r in small_run.arms if r.arm.id == "pullback_b3_h10")
    b = next(r for r in small_run.arms if r.arm.id == "breakout_s8_h10")
    d = ab.paired_difference(a, b)
    months_a = {m for m, v, _ in a.buckets if v is not None}
    months_b = {m for m, v, _ in b.buckets if v is not None}
    assert d.n_buckets == len(months_a & months_b)
    same = ab.paired_difference(a, a)
    assert same.diff_mean == 0.0


def test_ab_result_refuses_without_limitations():
    with pytest.raises(ValueError):
        ab.AbResult("train", ("a", "b"), [], {}, [], 1, [])


def test_null_percentile_and_survival_rule(small_run):
    res = small_run
    r = next(x for x in res.arms if x.arm.id == "pullback_b3_h10")
    null = res.nulls[r.arm.id]
    assert 0.0 <= null.percentile_of(r.mean_excess_vs_spy) <= 1.0
    ok, why = res.survives(r)
    assert isinstance(ok, bool) and "random control" in why
    spy = next(x for x in res.arms if x.arm.id == "spy_hold")
    assert res.survives(spy) == (False, "not a rule arm")


def test_preregistration_is_read_from_lab_md(tmp_path):
    p = tmp_path / "LAB.md"
    p.write_text("# lab\n\n## Pre-registration (written 2026-09-07 before any held-out run)\n\n"
                 "OOS window: 2016-07-01 to 2018-02-07. Never changed.\nBudget: 12 train-window iterations.\n"
                 "Pre-registered arms: pullback_b3_h10, breakout_s8_h20\n\n## Iterations\n", encoding="utf-8")
    pre = ab.load_preregistration(p)
    assert pre.oos_window == ab.SPLITS["test"] and pre.budget == 12 and pre.written == "2026-09-07"
    assert pre.arm_ids == ("pullback_b3_h10", "breakout_s8_h20")
    empty = ab.load_preregistration(tmp_path / "missing.md")
    assert empty.oos_window is None and empty.arm_ids == ()


def test_the_repo_lab_md_preregisters_the_code_window():
    pre = ab.load_preregistration(ab.LAB_MD)
    assert pre.oos_window == ab.SPLITS["test"], "lab/LAB.md and ab.SPLITS['test'] disagree"
    assert pre.budget == 12


def test_hypotheses_are_counted_from_the_results_directory(tmp_path, small_run):
    assert ab.hypotheses_from_results(tmp_path) == 0
    p = ab.write_result(small_run, directory=tmp_path, slug="one")
    assert p.exists() and p.name.endswith("_train_one.json")
    assert ab.hypotheses_from_results(tmp_path) == 3
    assert ab.hypotheses_from_results(tmp_path, extra=["pullback_b3_h10", "new_arm"]) == 4


def test_build_report_renders_from_results_only(tmp_path, small_run):
    empty = ab.build_report(directory=tmp_path, built_at="2026-09-07T00:00:00+00:00")
    assert empty["status"] == "NOT RUN" and empty["is_real"] is False and empty["limitations"]
    ab.write_result(small_run, directory=tmp_path)
    # a fixture-scoped result never becomes the dashboard's real result
    rep = ab.build_report(directory=tmp_path, built_at="2026-09-07T00:00:00+00:00")
    assert rep["status"] == "NOT RUN"
    full = ab.AbResult(**{**small_run.__dict__, "data": {**small_run.data, "scope": "full"}})
    ab.write_result(full, directory=tmp_path, slug="full")
    rep = ab.build_report(directory=tmp_path, built_at="2026-09-07T00:00:00+00:00")
    assert rep["status"] == "RUN" and rep["is_real"] is True and rep["train"]["arms"]
    assert rep["test"] is None and rep["verdict"] == "held-out window not run yet"
    assert rep["preregistration"]["oos_window_matches_code"] is True
    assert not language_check(rep, "paper.json")


def test_results_directory_is_append_only():
    """A result file that was ever committed must still be there: losing arms stay in the record."""
    r = subprocess.run(["git", "ls-files", "lab/results"], capture_output=True, text=True, cwd=str(ROOT))
    for line in r.stdout.splitlines():
        assert (ROOT / line.strip()).exists(), f"{line} was removed from lab/results"


def test_cli_dry_run_and_fixture_mode(tmp_path):
    cli = ROOT / "scripts" / "paper_trade.py"
    r = subprocess.run([sys.executable, str(cli), "--dry-run", "--arms", "pullback_b3_h10"], capture_output=True, text=True)
    assert r.returncode == 0 and "dry run" in r.stdout and "pullback_b3_h10" in r.stdout
    out = tmp_path / "fixture_paper.json"
    r = subprocess.run([sys.executable, str(cli), "--fixture", "--arms", "pullback_b3_h10,spy_hold", "--control-replicates", "2",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    blob = json.loads(out.read_text(encoding="utf-8"))
    assert blob["status"] == "RUN" and blob["is_real"] is False and blob["data_scope"] == "fixture"
    assert "measures nothing" in blob["limitations"][0]
    bad = subprocess.run([sys.executable, str(cli), "--fixture", "--arms", "spy_hold", "--out",
                          str(ROOT / "dashboard" / "_never.json")], capture_output=True, text=True)
    assert bad.returncode == 2 and not (ROOT / "dashboard" / "_never.json").exists()


def test_held_out_run_refuses_an_unregistered_arm(tmp_path, monkeypatch):
    """The guard, exercised against the real cache layout with an empty cache: it must refuse before reading prices."""
    cli = ROOT / "scripts" / "paper_trade.py"
    r = subprocess.run([sys.executable, str(cli), "--live", "--split", "test", "--arms", "breakout_s7_h5",
                        "--root", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 2
