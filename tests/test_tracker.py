"""an.tracker: every journal call graded against a price panel with planted paths.

The panel is synthetic and the answers are planted, so what is tested is the
grading, not the market: a name that closes under its trigger is falsified, a
buy that beat SPY reads "ahead", a pass that rallied reads "missed", and a call
with no prices stays in the table as ungraded with its reason.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from an import journal, prices, tracker

ROOT = Path(__file__).resolve().parent.parent
CALL = "2026-09-04"
AS_OF = dt.date(2026, 12, 4)

JOURNAL = """# Decision journal

## 2026-09-04 SYSTEM  Inception
Price at call: n/a
Thesis: x
Wrong if: x
Target size: $0
Conviction: n/a
Bucket: n/a

## 2026-09-04 GOODBUY  Recommendation: Buy now
Price at call: 100.00
Thesis: goes up.
Wrong if: Growth stalls, or a close under $80.
Target size: $5,000 (5%)
Conviction: 4
Bucket: compounder

## 2026-09-04 BADBUY  Recommendation: Buy in October
Price at call: 100.00
Thesis: goes down.
Wrong if: A close under $70.
Target size: $5,000 (5%)
Conviction: 3
Bucket: cyclical turn

## 2026-09-04 PASSED  Recommendation: Watch or Pass
Price at call: 50.00
Thesis: too dear.
Wrong if: n/a
Target size: $0
Conviction: 2
Bucket: n/a

## 2026-09-04 GONE  Recommendation: Buy now
Price at call: 100.00
Thesis: x
Wrong if: Revenue down 10%.
Target size: $1,000 (1%)
Conviction: 3
Bucket: compounder

## 2026-09-04 NOPRICE  Recommendation: Buy now
Price at call: n/a
Thesis: x
Wrong if: x
Target size: $1,000 (1%)
Conviction: 3
Bucket: compounder
"""


def path(start, end, n):
    return np.linspace(start, end, n)


@pytest.fixture
def entries():
    return journal.parse(JOURNAL)


@pytest.fixture
def panel():
    """Planted paths, business days from a week before the call to the grading date."""
    idx = pd.bdate_range("2026-08-28", AS_OF)
    n = len(idx)
    df = pd.DataFrame(index=idx)
    df["GOODBUY"] = path(100, 130, n)              # +30%
    df["BADBUY"] = path(100, 90, n)                # -10%, and dips to 65 on the way
    df.loc[idx[n // 2], "BADBUY"] = 65.0
    df["PASSED"] = path(50, 80, n)                 # +60%: the pass missed it
    df["GONE"] = path(100, 60, n)                  # stops trading half way
    df.loc[idx[n // 2]:, "GONE"] = np.nan
    df["SPY"] = path(100, 110, n)                  # +10%
    df["QQQ"] = path(100, 115, n)
    df["NOPRICE"] = path(100, 100, n)
    return df


# -- the pieces --------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("December quarter guide under $3.6 billion, or a close under $130.", 130.0),
    ("Two quarters of negative room nights, take rate down 100 basis points, or a close under $150.", 150.0),
    ("a close below 1,250", 1250.0),
    ("Closing under $ 42.50 for a week", 42.5),
    ("Billings growth under 5% twice", None),
    ("Insurance unit declines past 8%", None),
    (None, None),
    ("", None),
])
def test_parse_trigger_reads_only_the_close_under_form(text, expected):
    assert tracker.parse_trigger(text) == expected


@pytest.mark.parametrize("action,kind", [
    ("Buy now", "buy"), ("Buy in October", "buy_later"), ("Buy after September 10 earnings", "buy_later"),
    ("Buy on pullback to $300 to $335", "buy_on_pullback"), ("Watch or Pass", "pass"), ("Watchlist", "watch"),
    (None, "other"), ("Hold", "other"),
])
def test_classify_action(action, kind):
    assert tracker.classify_action(action) == kind


def test_score_at_call_uses_the_snapshot_on_or_before_the_call():
    pct, snap = tracker.score_at_call("KLAC", "2026-09-04")
    assert snap == "2026-09-04"
    blob = json.loads((ROOT / "universe/snapshots/2026-09-04/scores.json").read_text())
    assert pct == blob["variants"]["quality_value"]["KLAC"]["percentile"]
    assert tracker.score_at_call("KLAC", "2026-09-03") == (None, None), "a later snapshot is never used"
    assert tracker.score_at_call("ZZZZ", "2026-09-04") == (None, "2026-09-04")


# -- grading on planted paths ----------------------------------------------------------


def test_every_non_system_entry_gets_a_grade_and_the_system_entry_does_not(entries, panel):
    grades = tracker.grade_entries(entries, panel, as_of=AS_OF)
    assert [g.ticker for g in grades] == ["GOODBUY", "BADBUY", "PASSED", "GONE", "NOPRICE"]


def test_a_buy_that_beat_spy_is_ahead(entries, panel):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=AS_OF)}["GOODBUY"]
    assert g.status == "graded" and g.kind == "buy"
    assert g.ret == pytest.approx(0.30, abs=0.01)
    assert g.benchmarks["SPY"] == pytest.approx(0.10, abs=0.01)
    assert g.excess_vs_spy == pytest.approx(0.20, abs=0.02)
    assert g.trigger == 80.0 and g.trigger_breached is False
    assert g.verdict.startswith("ahead so far")
    assert "will" not in g.verdict


def test_a_close_under_the_trigger_is_falsified_even_if_the_price_recovered(entries, panel):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=AS_OF)}["BADBUY"]
    assert g.status == "falsified"
    assert g.low_since_call == 65.0 and g.trigger == 70.0 and g.trigger_breached is True
    assert g.price_now == pytest.approx(90.0, abs=0.5), "it recovered above the trigger, and that does not undo the close"
    assert "falsified on its own terms" in g.verdict and "behind so far" in g.verdict


def test_a_pass_that_rallied_is_missed_not_a_loss(entries, panel):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=AS_OF)}["PASSED"]
    assert g.kind == "pass" and g.trigger is None and g.trigger_breached is None
    assert g.ret == pytest.approx(0.60, abs=0.01)
    assert g.verdict.startswith("missed so far")


def test_a_name_that_stopped_trading_grades_to_its_last_print(entries, panel):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=AS_OF)}["GONE"]
    assert g.stopped_trading is True
    assert g.ret is not None and -0.45 < g.ret < -0.15, "the return to the last print, never a flat zero"
    assert "stopped trading" in g.verdict


def test_no_price_at_call_stays_in_the_table_as_ungraded(entries, panel):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=AS_OF)}["NOPRICE"]
    assert g.status == "ungraded" and "no price at call" in g.reason
    assert g.ret is None and g.verdict.startswith("not graded")


def test_no_panel_at_all_grades_nothing_but_keeps_everything_else(entries):
    grades = tracker.grade_entries(entries, None, as_of=AS_OF)
    assert all(g.status == "ungraded" for g in grades)
    assert {g.ticker: g.trigger for g in grades}["GOODBUY"] == 80.0
    assert all("no price history" in g.reason for g in grades)


def test_a_short_window_says_so(entries, panel):
    soon = dt.date(2026, 9, 11)
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel[panel.index <= str(soon)], as_of=soon)}["GOODBUY"]
    assert g.trading_days == 5
    assert "too short to mean anything" in g.verdict


def test_summary_and_limitations(entries, panel):
    grades = tracker.grade_entries(entries, panel, as_of=AS_OF)
    s = tracker.summarise(grades)
    assert (s.n_calls, s.n_graded, s.n_ungraded, s.n_falsified) == (5, 4, 1, 1)
    assert (s.buys_graded, s.buys_ahead_of_spy) == (3, 1)
    assert (s.passes_graded, s.passes_missed) == (1, 1)
    lims = tracker.limitations(grades, s, source="test")
    assert any("so far" in x for x in lims)
    assert any("missed gain" in x for x in lims)
    assert any("same date" in x for x in lims), "every call on one date is one window"
    assert any("1 of 5 calls are ungraded" in x for x in lims)


# -- the script --------------------------------------------------------------------------


def run_script(*args, offline=True):
    import os

    env = {**os.environ}
    if offline:
        env["DESK_OFFLINE"] = "1"
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "track_calls.py"), *args],
                          capture_output=True, text=True, cwd=str(ROOT), env=env)


def test_script_dry_run_names_the_pull_and_sends_nothing():
    r = run_script("--dry-run")
    assert r.returncode == 0, r.stderr
    assert "SPY QQQ" in r.stdout and "KLAC" in r.stdout and "never made that pull" in r.stdout


def test_script_offline_writes_an_honest_not_graded_file(tmp_path):
    """Offline with nothing cached. The real checkout may carry a price cache after a live run, so
    the script runs against a copy of the journal and the snapshots under an empty DESK_ROOT."""
    import os
    import shutil

    root = tmp_path / "root"
    root.mkdir()
    shutil.copy(ROOT / "journal.md", root / "journal.md")
    shutil.copytree(ROOT / "universe" / "snapshots", root / "universe" / "snapshots")
    out = tmp_path / "t.json"
    env = {**os.environ, "DESK_OFFLINE": "1", "DESK_ROOT": str(root)}
    env.pop("DESK_QUOTES", None)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "track_calls.py"), "--out", str(out), "--as-of", "2026-09-06"],
                       capture_output=True, text=True, cwd=str(ROOT), env=env)
    assert r.returncode == 0, r.stderr
    blob = json.loads(out.read_text())
    assert blob["status"] == "NOT GRADED" and blob["is_real"] is False
    assert len(blob["grades"]) == 16, "sixteen calls, the five-ticker heading included"
    assert all(g["status"] == "ungraded" for g in blob["grades"])
    klac = next(g for g in blob["grades"] if g["ticker"] == "KLAC")
    assert klac["trigger"] == 130.0 and klac["score_percentile_at_call"] is not None
    assert any("--live" in x for x in blob["why_not_graded"])
    assert blob["limitations"]


def test_script_synthetic_is_labelled_on_every_layer(tmp_path):
    out = tmp_path / "s.json"
    r = run_script("--synthetic", "--as-of", "2026-12-04", "--out", str(out))
    assert r.returncode == 0, r.stderr
    blob = json.loads(out.read_text())
    assert blob["status"] == "SYNTHETIC" and blob["is_real"] is False
    assert blob["limitations"][0].startswith("SYNTHETIC PRICES")
    assert "synthetic" in blob["price_source"]
    assert blob["summary"]["n_graded"] == 16
    bkng = next(g for g in blob["grades"] if g["ticker"] == "BKNG")
    assert bkng["price_at_call"] == 195.13 and bkng["price_now"] is not None
    assert "SPY" in bkng["benchmarks"]


def test_the_positioning_page_renders_the_tracker():
    """The block existed for a while without being placed in the page. Pin it."""
    src = (ROOT / "dashboard" / "assets" / "positioning.js").read_text()
    assert "function trackerBlock()" in src and "${trackerBlock()}" in src
    assert '"../tracker.json"' in src



# -- swing calls: a horizon and a stop named before entry --------------------------

SWING = """# Decision journal

## 2026-09-04 SWINGWIN  Recommendation: Buy now
Price at call: 100.00
Thesis: post-earnings drift setup.
Wrong if: n/a
Stop: $92.00
Horizon: 10 trading days
Target size: $5,000
Conviction: 3
Bucket: swing

## 2026-09-04 SWINGSTOP  Recommendation: Buy now
Price at call: 100.00
Thesis: breakout.
Wrong if: n/a
Stop: $95
Horizon: 20 trading days
Target size: $5,000
Conviction: 3
Bucket: swing

## 2026-09-04 SWINGEARLY  Recommendation: Buy now
Price at call: 100.00
Thesis: pullback.
Wrong if: n/a
Stop: $90
Horizon: 120 trading days
Target size: $5,000
Conviction: 3
Bucket: swing
"""


def test_the_journal_reads_a_horizon_and_a_stop():
    es = journal.parse(SWING)
    by = {e.ticker: e for e in es}
    assert by["SWINGWIN"].horizon_days == 10 and by["SWINGWIN"].stop == 92.0 and by["SWINGWIN"].is_swing
    assert by["SWINGSTOP"].stop == 95.0 and by["SWINGEARLY"].horizon_days == 120
    long_only = journal.parse(JOURNAL)
    assert all(not e.is_swing and e.stop is None for e in long_only)
    assert journal._horizon("3 weeks") is None, "calendar units are refused; the tracker counts sessions"


def test_swing_calls_are_graded_at_5_10_20_and_their_horizon(panel):
    n = len(panel.index)
    df = panel.copy()
    df["SWINGWIN"] = path(100, 120, n)                       # rises, never near the stop
    df["SWINGSTOP"] = path(100, 105, n)
    df.loc[df.index[n // 2], "SWINGSTOP"] = 94.0             # one close under the $95 stop, then recovers
    df["SWINGEARLY"] = path(100, 103, n)
    grades = {g.ticker: g for g in tracker.grade_entries(journal.parse(SWING), df, as_of=AS_OF)}

    win = grades["SWINGWIN"]
    assert win.is_swing and win.status == "graded" and win.stop_breached is False
    assert set(win.horizon_grades) == {"5d", "10d", "20d", "horizon"}
    assert win.horizon_grades["horizon"] == win.horizon_grades["10d"]
    h = win.horizon_grades["10d"]
    assert h["sessions"] == 10 and h["ret"] > 0 and h["spy"] is not None
    assert h["excess_vs_spy"] == pytest.approx(h["ret"] - h["spy"])
    assert "at the 10-session horizon" in win.verdict and "too short" not in win.verdict

    stopped = grades["SWINGSTOP"]
    assert stopped.stop_breached is True and stopped.status == "stopped"
    assert "stop named before entry" in stopped.verdict

    early = grades["SWINGEARLY"]
    assert early.horizon_grades["horizon"] is None, "120 sessions have not elapsed"
    assert early.horizon_grades["5d"] is not None
    assert "horizon grade is not in yet" in early.verdict

    s = tracker.summarise(list(grades.values()))
    assert s.swing_calls == 3 and s.swing_graded_at_horizon == 2 and s.swing_stopped == 1
    assert s.swing_floor == 30
    lims = tracker.limitations(list(grades.values()), s, source="test")
    assert any("swing.md" in x and "30" in x for x in lims)
    for g in grades.values():
        assert g.to_json()["is_swing"] == g.is_swing and "NaN" not in json.dumps(g.to_json())


def test_a_long_call_still_gets_the_too_short_note_and_no_horizon(panel, entries):
    g = {x.ticker: x for x in tracker.grade_entries(entries, panel, as_of=dt.date(2026, 9, 25))}["GOODBUY"]
    assert not g.is_swing and g.horizon_grades == {} and "too short" in g.verdict
