"""The paper-trading simulator: mechanics, sizing, the caps, parity with the tracker and the scanner."""
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from an import history, journal, paper, setups, tracker
from tests.test_language_guard import check as language_check

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "history"
RULES = paper.Rules()
COSTS = paper.CostModel()
NO_COST = paper.CostModel(0.0, 0.0)


def _panel(closes: dict, *, spy=None, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(next(iter(closes.values()))))
    close = pd.DataFrame(closes, index=idx, dtype=float)
    vol = pd.DataFrame(1_000_000.0, index=idx, columns=close.columns)
    bench = pd.DataFrame({"SPY": spy if spy is not None else 100.0, "QQQ": 100.0}, index=idx, dtype=float)
    return history.HistoryPanel(close, vol, bench, source="test")


def _features(panel, **kw):
    return paper.compute_features(panel, None, min_dollar_volume=0.0, min_history=1, **kw)


def _event(f, ticker, i, stop, horizon):
    return paper.Event(ticker, i, f.index[i].date().isoformat(), stop, horizon)


@pytest.fixture(scope="module")
def fixture_features():
    panel, mem, _ = history.load_fixture(FIX)
    return panel, paper.compute_features(panel, mem)


# -- mechanics ------------------------------------------------------------

def test_fill_is_the_next_close_and_horizon_exit_is_the_tracker_window():
    px = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111]
    f = _features(_panel({"A": px}))
    [t] = paper.event_study([_event(f, "A", 2, 95.0, 5)], f, arm_id="x", rules=RULES, costs=NO_COST)
    assert t.fill_date == f.index[3].date().isoformat() and t.entry == 103.0
    assert t.exit_date == f.index[7].date().isoformat() and t.exit == 107.0 and t.exit_reason == "horizon"
    assert t.ret_gross == pytest.approx(107 / 103 - 1)
    assert t.ret_from_signal_close == pytest.approx(107 / 102 - 1)
    assert t.sessions_held == 4


def test_stop_is_a_close_strictly_below_and_the_exit_is_the_following_close():
    px = [100, 100, 100, 96, 95, 97, 98, 99, 100, 101, 102]
    f = _features(_panel({"A": px}))
    [t] = paper.event_study([_event(f, "A", 0, 96.0, 8)], f, arm_id="x", rules=RULES, costs=NO_COST)
    # closes at 96 (not below), then 95 (below) on session 4; exit at session 5's close
    assert t.exit_reason == "stop" and t.exit == 97.0 and t.exit_date == f.index[5].date().isoformat()


def test_a_stop_flagged_on_the_horizon_session_exits_there():
    px = [100, 100, 100, 100, 90, 80, 70]
    f = _features(_panel({"A": px}))
    [t] = paper.event_study([_event(f, "A", 0, 95.0, 4)], f, arm_id="x", rules=RULES, costs=NO_COST)
    assert t.exit_date == f.index[4].date().isoformat() and t.exit == 90.0 and t.exit_reason == "stop"


def test_stop_rises_to_entry_once_up_by_the_initial_risk():
    px = [100, 100, 100, 110, 104, 99.5, 120, 121, 122, 123, 124]
    f = _features(_panel({"A": px}))
    on = paper.Rules(raise_stop_to_entry_after_1r=True)
    off = paper.Rules(raise_stop_to_entry_after_1r=False)
    [a] = paper.event_study([_event(f, "A", 0, 90.0, 8)], f, arm_id="x", rules=on, costs=NO_COST)
    [b] = paper.event_study([_event(f, "A", 0, 90.0, 8)], f, arm_id="x", rules=off, costs=NO_COST)
    # entry 100 at session 1, risk 10; session 3 closes 110 so the stop moves to 100; session 5 closes 99.5
    assert a.exit_reason == "stop" and a.stop_final == 100.0 and a.exit == 120.0
    assert b.exit_reason == "horizon" and b.stop_final == 90.0


def test_costs_are_charged_each_way_in_bps():
    px = [100.0] * 8
    f = _features(_panel({"A": px}))
    [t] = paper.event_study([_event(f, "A", 0, 90.0, 5)], f, arm_id="x", rules=RULES, costs=paper.CostModel(5, 5))
    assert t.ret_gross == 0.0
    assert t.ret_net == pytest.approx((1 - 0.001) / (1 + 0.001) - 1)


def test_missing_spy_gives_none_not_zero():
    px = [100, 101, 102, 103, 104, 105]
    panel = _panel({"A": px})
    panel.benchmarks["SPY"] = np.nan
    f = _features(panel)
    [t] = paper.event_study([_event(f, "A", 0, 90.0, 3)], f, arm_id="x", rules=RULES, costs=NO_COST)
    assert t.spy_same_window is None and t.excess_vs_spy is None


def test_a_name_that_stops_trading_exits_at_its_last_print():
    px = [100, 101, 102, 103, np.nan, np.nan, np.nan, np.nan]
    f = _features(_panel({"A": px}))
    [t] = paper.event_study([_event(f, "A", 0, 90.0, 6)], f, arm_id="x", rules=RULES, costs=NO_COST)
    assert t.exit_reason == "panel_end" and t.exit == 103.0


def test_events_respect_the_window_the_refractory_rule_and_the_panel_end():
    idx = pd.bdate_range("2020-01-01", periods=30)
    fires = pd.DataFrame(True, index=idx, columns=["A"])
    stop = pd.DataFrame(90.0, index=idx, columns=["A"])
    f = _features(_panel({"A": [100.0] * 30}))
    arm = paper.Arm("x", "pullback", 5)
    ev, sk = paper.events_from_mask(fires, stop, f, arm, start=idx[5].date().isoformat(), end=idx[20].date().isoformat())
    assert [e.i for e in ev] == [5, 10, 15, 20]
    assert sk["outside_window"] == 14 and sk["within_previous_horizon"] == 12
    ev2, sk2 = paper.events_from_mask(fires, stop, f, arm)
    assert sk2["truncated_at_panel_end"] == 4 and sk2["no_next_session"] == 1 and ev2[-1].i == 20


# -- sizing and the caps --------------------------------------------------

def test_ledger_sizes_by_risk_and_caps_the_position():
    f = _features(_panel({"A": [20.0] * 15, "B": [100.0] * 15, "C": [100.0] * 15}))
    ev = [_event(f, "A", 0, 12.0, 5), _event(f, "B", 0, 99.0, 5), _event(f, "C", 0, 92.0, 5)]
    led = paper.ledger_replay(ev, f, arm_id="x", rules=RULES, costs=NO_COST, sectors={"A": "s1", "B": "s2", "C": "s3"})
    by = {t.ticker: t for t in led.trades}
    assert by["A"].shares == 125           # 1000 / 8, under the $5,000 cap (250 shares)
    assert by["B"].shares == 50            # 1000 / 1 = 1000 shares, capped at 5000 / 100
    assert by["C"].shares == 50            # 1000 / 8 = 125 shares would be $12,500; capped at $5,000
    assert led.final_equity == pytest.approx(20_000.0)


def test_ledger_enforces_max_open_and_two_per_sector_and_counts_skips():
    names = list("ABCDEFGH")
    f = _features(_panel({n: [100.0] * 20 for n in names}))
    ev = [_event(f, n, 0, 60.0, 5) for n in names]      # 25 shares each, so five fit inside the sleeve
    sectors = {n: ("tech" if n in "ABC" else n) for n in names}
    led = paper.ledger_replay(ev, f, arm_id="x", rules=RULES, costs=NO_COST, sectors=sectors)
    taken = sorted(t.ticker for t in led.trades)
    assert taken == ["A", "B", "D", "E", "F"]          # C is the third tech name; G and H are the sixth and seventh
    assert led.skipped["sector"] >= 1 and led.skipped["capacity"] >= 1


def test_ledger_never_spends_cash_it_does_not_have():
    f = _features(_panel({"A": [1000.0] * 20, "B": [1000.0] * 20, "C": [1000.0] * 20, "D": [1000.0] * 20, "E": [1000.0] * 20}))
    rules = paper.Rules(sleeve_usd=6_000.0, max_position_usd=5_000.0)
    ev = [_event(f, n, 0, 990.0, 5) for n in "ABCDE"]
    led = paper.ledger_replay(ev, f, arm_id="x", rules=rules, costs=NO_COST, sectors={})
    assert sum(t.notional for t in led.trades) <= 6_000.0
    assert min(v for _, v in led.equity) >= 0.0


def test_ledger_stop_exits_at_the_following_close_like_the_event_study():
    px = [100, 100, 100, 96, 95, 97, 98, 99, 100, 101, 102]
    f = _features(_panel({"A": px}))
    ev = [_event(f, "A", 0, 96.0, 8)]
    led = paper.ledger_replay(ev, f, arm_id="x", rules=RULES, costs=NO_COST, sectors={})
    [es] = paper.event_study(ev, f, arm_id="x", rules=RULES, costs=NO_COST)
    [lt] = led.trades
    assert (lt.exit_date, lt.exit, lt.exit_reason) == (es.exit_date, es.exit, es.exit_reason)


# -- the rules find what they should --------------------------------------

def test_pullback_finds_a_planted_trend_and_not_a_flat_line():
    n = 300
    up = [100.0 * (1.003 ** i) for i in range(n)]
    flat = [100.0] * n
    f = _features(_panel({"UP": up, "FLAT": flat}))
    fires, stop = paper.signal_mask(f, paper.Arm("p", "pullback", 10))
    assert fires["UP"].iloc[-1] and not fires["FLAT"].any()
    assert stop["UP"].iloc[-1] == pytest.approx(min(up[-20:]))


def test_breakout_needs_a_close_strictly_above_every_prior_close():
    px = [100.0] * 300
    px[-1] = 100.0001
    f = _features(_panel({"A": px, "B": [100.0] * 300}))
    fires, stop = paper.signal_mask(f, paper.Arm("b", "breakout_price_only", 10, {"stop_pct": 0.08}))
    assert fires["A"].iloc[-1] and not fires["B"].iloc[-1]
    assert stop["A"].iloc[-1] == pytest.approx(100.0001 * 0.92)


def test_pead_proxy_fires_on_a_gap_with_volume_and_holds_the_release_close():
    n = 40
    px = [100.0] * n
    px[30] = 106.0
    px[31] = 106.5
    px[32] = 105.0    # gave the release close back: no fire
    panel = _panel({"A": px})
    panel.volume.iloc[30, 0] = 5_000_000.0
    f = _features(panel)
    fires, stop = paper.signal_mask(f, paper.Arm("g", "pead_proxy", 20, {"gap": 0.05, "vol_mult": 2.0}))
    # on the release session itself the stop (the lowest close since the release) is the close: no row, as in setups.py
    assert not fires["A"].iloc[30] and fires["A"].iloc[31] and not fires["A"].iloc[32]
    assert stop["A"].iloc[31] == pytest.approx(106.0)
    quiet = _panel({"A": px})
    assert not paper.signal_mask(_features(quiet), paper.Arm("g", "pead_proxy", 20))[0]["A"].any()


def test_masks_agree_with_the_setups_producers_on_the_fixture(fixture_features):
    """The vectorised rules must fire on the same names, with the same stops, as scripts/an/setups.py."""
    panel, f = fixture_features
    fires_p, stop_p = paper.signal_mask(f, paper.Arm("p", "pullback", 10))
    fires_b, stop_b = paper.signal_mask(f, paper.Arm("b", "breakout_price_only", 10, {"stop_pct": setups.BREAKOUT_STOP}))
    screen = {t: {"eps_fy1_chg_30d": 1.0} for t in panel.tickers}
    checked = 0
    for pos in range(260, panel.sessions, 15):
        as_of = f.index[pos].date()
        expect_p = {s.ticker: s.stop for s in setups.pullback_setups(panel.close, panel.tickers, as_of=as_of)
                    if f.eligible.iloc[pos][s.ticker]}
        got_p = {t: float(stop_p.iloc[pos][t]) for t in panel.tickers if fires_p.iloc[pos][t]}
        assert set(got_p) == set(expect_p), as_of
        for t in got_p:
            assert got_p[t] == pytest.approx(expect_p[t])
        expect_b = {s.ticker: s.stop for s in setups.breakout_setups(panel.close, screen, as_of=as_of)
                    if f.eligible.iloc[pos][s.ticker]}
        got_b = {t: float(stop_b.iloc[pos][t]) for t in panel.tickers if fires_b.iloc[pos][t]}
        assert set(got_b) == set(expect_b), as_of
        for t in got_b:
            assert got_b[t] == pytest.approx(expect_b[t])
        checked += 1
    assert checked >= 20


def test_simulated_grades_agree_with_the_tracker(fixture_features):
    """A simulated trade and the same call in the journal must get the same horizon return and stop verdict."""
    panel, f = fixture_features
    arm = paper.Arm("p", "pullback", 10)
    r = paper.run_arm(f, arm, start=None, end=None, rules=paper.Rules(raise_stop_to_entry_after_1r=False),
                      costs=NO_COST, sectors={}, with_ledger=False)
    closes = pd.concat([panel.close, panel.benchmarks], axis=1)
    sample = r.trades[:40]
    entries = [journal.JournalEntry(date=t.signal_date, ticker=t.ticker, title="Recommendation: Buy (swing)",
                                    price_at_call=float(panel.close.loc[t.signal_date, t.ticker]), thesis=None,
                                    wrong_if=None, target_size=None, conviction=None, bucket=None,
                                    horizon_days=10, stop=t.stop_initial) for t in sample]
    grades = tracker.grade_entries(entries, closes, as_of=f.index[-1].date())
    assert len(grades) == len(sample)
    for t, g in zip(sample, grades):
        h = g.horizon_grades.get("horizon") or g.horizon_grades.get("10d")
        assert h is not None and h["ret"] == pytest.approx(t.ret_from_signal_close, abs=1e-9), t
    # a stop the tracker sees is a stop the simulator took; the simulator can only stop inside its horizon,
    # while the tracker looks at every session up to as_of, so the implication runs one way
    for t, g in zip(sample, grades):
        if t.exit_reason == "stop":
            assert g.stop_breached is True, t


# -- results ---------------------------------------------------------------

def test_result_refuses_to_exist_without_limitations():
    with pytest.raises(ValueError):
        paper.PaperResult("x", ("a", "b"), RULES, COSTS, [], 1, [])


def test_verdict_ladder_matches_the_backtest_engine():
    assert paper.verdict_for(None, None, None, n_effective=30, hypotheses_tested=1) == "no evidence"
    assert paper.verdict_for(0.01, (-0.01, 0.02), 1.0, n_effective=30, hypotheses_tested=1) == "no evidence"
    assert paper.verdict_for(0.01, (0.001, 0.02), 3.0, n_effective=8, hypotheses_tested=1) == "weak"
    assert paper.verdict_for(0.01, (0.001, 0.02), 3.0, n_effective=15, hypotheses_tested=1) == "suggestive"
    assert paper.verdict_for(0.01, (0.001, 0.02), 3.0, n_effective=30, hypotheses_tested=1) == "supported"
    # 24 hypotheses need |t| >= 1.96 + 0.5 ln 24 = 3.55
    assert paper.verdict_for(0.01, (0.001, 0.02), 3.0, n_effective=30, hypotheses_tested=24) == "suggestive"
    assert paper.verdict_for(0.01, (0.001, 0.02), 3.6, n_effective=30, hypotheses_tested=24) == "supported"


def test_random_control_matches_count_and_horizon_and_differs_by_seed(fixture_features):
    _, f = fixture_features
    r = paper.run_arm(f, paper.Arm("p", "pullback", 10), start=None, end=None, rules=RULES, costs=COSTS, sectors={},
                      with_ledger=False)
    c1 = paper.run_arm(f, paper.Arm("r", "random", 10), start=None, end=None, rules=RULES, costs=COSTS, sectors={},
                       match=r, seed=1, with_ledger=False)
    c2 = paper.run_arm(f, paper.Arm("r", "random", 10), start=None, end=None, rules=RULES, costs=COSTS, sectors={},
                       match=r, seed=2, with_ledger=False)
    assert c1.n_events == r.n_events and all(t.sessions_held <= 10 for t in c1.trades)
    assert [(t.ticker, t.fill_date) for t in c1.trades] != [(t.ticker, t.fill_date) for t in c2.trades]
    risk = np.median([1 - t.stop_initial / t.entry for t in r.trades])
    assert np.median([1 - t.stop_initial / t.entry for t in c1.trades]) == pytest.approx(risk, abs=0.02)


def test_zero_drift_panel_gives_no_evidence():
    from an import prices
    tick = [f"T{i}" for i in range(30)]
    panel_px = prices.synthetic_panel(tick + ["SPY"], start="2019-01-01", periods=700, seed=3, drift=0.0, vol=0.25)
    panel = history.HistoryPanel(panel_px[tick], pd.DataFrame(1e6, index=panel_px.index, columns=tick),
                                 pd.DataFrame({"SPY": panel_px["SPY"], "QQQ": panel_px["SPY"]}), source="synthetic")
    f = _features(panel)
    for kind, params in (("pullback", {}), ("breakout_price_only", {"stop_pct": 0.08})):
        r = paper.run_arm(f, paper.Arm(kind, kind, 10, params), start=None, end=None, rules=RULES, costs=COSTS,
                          sectors={}, with_ledger=False)
        assert r.n_trades > 20
        assert r.verdict in ("no evidence", "weak"), (kind, r.mean_excess_vs_spy, r.excess_ci)


def test_arm_json_is_complete_and_clean(fixture_features):
    _, f = fixture_features
    r = paper.run_arm(f, paper.Arm("pullback_b3_h10", "pullback", 10), start=None, end=None, rules=RULES, costs=COSTS,
                      sectors={"AAPL": "Technology"})
    j = r.to_json()
    for k in ("n_signals", "n_events", "n_trades", "hit_rate", "mean_excess_vs_spy", "excess_ci", "n_buckets", "verdict",
              "exit_reasons", "by_year", "worst_five", "ledger", "mean_ret_from_signal_close", "profit_factor"):
        assert k in j, k
    assert j["ledger"]["final_equity"] > 0 and j["ledger"]["equity_curve"]
    assert j["arm"]["label"].startswith("pullback")
    assert not language_check(j, "paper")
