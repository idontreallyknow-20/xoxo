"""an.setups: four mechanical patterns on a planted panel, and the file that says NOT RUN.

Each rule is exercised on a name built to match it and on names built not to,
so what is tested is the rule, not the market. A setup never carries a
forecast; the language guard runs over the whole file.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from an import prices, setups

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))
from test_language_guard import check  # noqa: E402

AS_OF = dt.date(2026, 9, 4)


@pytest.fixture
def panel():
    from setups import planted_panel  # the CLI's fixture panel, so the test and the fixture agree

    return planted_panel(AS_OF)


def test_post_earnings_drift_needs_the_gap_and_the_hold(panel):
    release = panel.index[-3].date().isoformat()
    got = setups.pead_setups(panel, {"GAPUP": [release], "FLAT": [release], "FALL": [release]}, as_of=AS_OF)
    assert [x.ticker for x in got] == ["GAPUP"]
    x = got[0]
    assert x.kind == "pead" and x.horizon_days == 20
    assert x.entry == 113.0 and x.stop == 112.0, "stop is the lowest close since the release"
    assert x.numbers["gap"].startswith("+") and x.numbers["8-K"] == release
    # a release older than three sessions is not a setup
    old = panel.index[-8].date().isoformat()
    assert setups.pead_setups(panel, {"GAPUP": [old]}, as_of=AS_OF) == []
    # a gap that faded is not a setup
    faded = panel.copy()
    faded.loc[faded.index[-1], "GAPUP"] = 111.0
    assert setups.pead_setups(faded, {"GAPUP": [release]}, as_of=AS_OF) == []


def test_a_guidance_raise_must_be_recent_and_on_revenue_or_eps(panel):
    filed = panel.index[-2].date().isoformat()
    block = {"current": {"filed": filed}, "items": [{"metric": "revenue", "period": "FY2027", "change": "raised",
                                                    "what": "FY2027 revenue", "detail": "up"}]}
    got = setups.guidance_setups(panel, {"GUIDE": block}, as_of=AS_OF)
    assert len(got) == 1 and got[0].kind == "guidance" and got[0].stop < got[0].entry
    stale = {"GUIDE": {**block, "current": {"filed": panel.index[-30].date().isoformat()}}}
    assert setups.guidance_setups(panel, stale, as_of=AS_OF) == []
    capex = {"GUIDE": {**block, "items": [{"metric": "capex", "change": "raised", "what": "capex", "detail": "x"}]}}
    assert setups.guidance_setups(panel, capex, as_of=AS_OF) == []
    lowered = {"GUIDE": {**block, "items": [{"metric": "eps", "change": "lowered", "what": "eps", "detail": "x"}]}}
    assert setups.guidance_setups(panel, lowered, as_of=AS_OF) == []


def test_a_breakout_is_strictly_above_every_prior_close_with_estimates_up(panel):
    screen = {"BREAK": {"eps_fy1_chg_30d": 0.04}, "FLAT": {"eps_fy1_chg_30d": 0.04},
              "FALL": {"eps_fy1_chg_30d": 0.04}, "PULL": {"eps_fy1_chg_30d": 0.04}}
    got = setups.breakout_setups(panel, screen, as_of=AS_OF)
    assert [x.ticker for x in got] == ["BREAK"], "a flat line at its own level is not a new high"
    x = got[0]
    assert x.stop == pytest.approx(x.entry * 0.92) and x.horizon_days == 10 and x.risk_pct == pytest.approx(0.08)
    assert setups.breakout_setups(panel, {"BREAK": {"eps_fy1_chg_30d": -0.01}}, as_of=AS_OF) == []
    assert setups.breakout_setups(panel, {"BREAK": {"eps_fy1_chg_30d": None}}, as_of=AS_OF) == []


def test_a_pullback_sits_on_a_rising_mean_and_not_in_a_fall(panel):
    got = setups.pullback_setups(panel, ["PULL", "FALL", "FLAT", "BREAK"], as_of=AS_OF)
    tickers = [x.ticker for x in got]
    assert "PULL" in tickers and "FALL" not in tickers and "FLAT" not in tickers
    x = [g for g in got if g.ticker == "PULL"][0]
    assert x.stop < x.entry and x.horizon_days == 10
    assert abs(float(x.numbers["vs 20-session mean"].rstrip("%"))) <= 3.0


def test_one_row_per_name_with_the_other_kinds_noted(panel):
    release = panel.index[-3].date().isoformat()
    rows = setups.all_setups(panel, as_of=AS_OF, earnings_dates={"GAPUP": [release]}, diffs={},
                             screen={"BREAK": {"eps_fy1_chg_30d": 0.04}}, quality=["BREAK", "PULL"],
                             names={"BREAK": "Break Co"})
    by = {x.ticker: x for x in rows}
    assert len(rows) == len(by)
    assert by["BREAK"].kind == "breakout" and by["BREAK"].name == "Break Co"
    assert by["BREAK"].numbers.get("also") == "pullback in trend"
    assert [x.kind for x in rows] == sorted((x.kind for x in rows), key=setups.KINDS.index)


def test_every_row_says_what_it_is_and_the_file_is_clean(panel):
    release = panel.index[-3].date().isoformat()
    rows = setups.all_setups(panel, as_of=AS_OF, earnings_dates={"GAPUP": [release]}, diffs={},
                             screen={"BREAK": {"eps_fy1_chg_30d": 0.04}}, quality=["BREAK", "PULL"])
    blob = setups.build_report(rows, as_of=AS_OF, universe_n=6, quality_n=2, sources={}, status="SYNTHETIC", built_at="X")
    assert blob["status"] == "SYNTHETIC" and blob["n_setups"] == len(rows) and blob["limitations"][0].startswith("SYNTHETIC")
    for x in blob["setups"]:
        assert "not a forecast" in x["what_this_is"] and x["rule"] and x["entry"] > x["stop"]
        assert 0 < x["risk_pct"] < 0.5
    assert set(blob["kinds"]) == set(setups.KINDS)
    bad = check(blob, "setups")
    assert not bad, "\n".join(bad)
    assert "NaN" not in json.dumps(blob)


def test_the_not_run_file_is_honest_and_names_the_command():
    blob = setups.not_run_report(universe_n=1895, quality_n=150, built_at="X")
    assert blob["status"] == "NOT RUN" and blob["setups"] == [] and blob["n_setups"] == 0
    assert any("setups.py --live" in s for s in blob["why_not_run"])
    committed = json.loads((ROOT / "dashboard" / "setups.json").read_text())
    assert committed["status"] == "NOT RUN" and committed["setups"] == []


def test_the_rules_are_written_down_in_swing_md():
    md = (ROOT / "swing.md").read_text()
    for label in setups.LABELS.values():
        assert label in md, f"{label} is not described in swing.md"
    assert "thirty" in md.lower() and "1%" in md


def test_the_cli_fixture_never_writes_under_dashboard(tmp_path):
    out = tmp_path / "s.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "setups.py"), "--fixture", "--as-of", "2026-09-04",
                        "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    blob = json.loads(out.read_text())
    assert blob["status"] == "SYNTHETIC" and {x["kind"] for x in blob["setups"]} == set(setups.KINDS)
    assert "FIXTURE" in r.stdout


def test_the_cli_dry_run_prints_the_plan():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "setups.py"), "--dry-run"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "never made these requests" in r.stdout and "EDGAR submissions" in r.stdout
