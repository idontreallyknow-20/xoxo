import math

import pytest

from an import backtest as B


def rb(date, scores, rets, sectors=None, buckets=None):
    return B.Rebalance(date=date, scores=scores, forward_returns=rets,
                       sectors=sectors or {}, buckets=buckets or {})


def linear_panel(n_dates=8, n_names=40, alpha=0.5, noise=0.0, seed=1):
    """Scores 0..1, returns = alpha * score + noise. The IC is knowable by hand."""
    import random

    rng = random.Random(seed)
    out = []
    for d in range(n_dates):
        scores = {f"N{i:03d}": i / (n_names - 1) for i in range(n_names)}
        rets = {t: alpha * s + (rng.gauss(0, noise) if noise else 0.0) for t, s in scores.items()}
        out.append(rb(f"2020-{d+1:02d}-01", scores, rets))
    return out


# -- the guard rail --------------------------------------------------------

def test_a_result_cannot_exist_without_limitations():
    """The single most important line in this module."""
    with pytest.raises(ValueError, match="limitations"):
        B.BacktestResult(
            label="x", variant="v", horizon_days=1, rebalance_spacing_days=1,
            n_rebalances=0, n_rebalances_scored=0, effective_independent_periods=0.0,
            names_per_date=[], missing_per_date=[], missing_names={},
            ic_series=[], mean_ic=None, median_ic=None, ic_stdev=None, ic_ci=None,
            ic_t=None, ic_hit_rate=None, quintiles=[], long_short_spread=None,
            long_short_ci=None, by_sector={}, by_bucket={}, worst_dates=[],
            hypotheses_tested=1, limitations=[], failure_modes=[], flags=[],
        )


def test_run_backtest_always_produces_limitations():
    r = B.run_backtest(linear_panel(), label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert len(r.limitations) >= 3
    assert any("urvivorship" in x for x in r.limitations)
    assert any("ook-ahead" in x for x in r.limitations)
    assert any("verlapping" in x for x in r.limitations)


# -- the arithmetic --------------------------------------------------------

def test_perfect_ordering_gives_ic_of_one():
    r = B.run_backtest(linear_panel(alpha=1.0, noise=0.0), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert r.mean_ic == pytest.approx(1.0)
    assert r.ic_hit_rate == 1.0


def test_inverted_ordering_gives_ic_of_minus_one():
    panel = linear_panel(alpha=1.0)
    flipped = [rb(x.date, x.scores, {t: -v for t, v in x.forward_returns.items()}) for x in panel]
    r = B.run_backtest(flipped, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.mean_ic == pytest.approx(-1.0)


def test_a_date_with_too_few_names_is_skipped_not_averaged_in():
    good = linear_panel(n_dates=4, alpha=1.0)
    thin = rb("2020-09-01", {"A": 1.0, "B": 2.0}, {"A": -0.5, "B": 0.5})
    r = B.run_backtest(good + [thin], label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.n_rebalances == 5
    assert r.n_rebalances_scored == 4
    assert any(str(B.MIN_NAMES_PER_DATE) in f for f in r.flags)


def test_missing_returns_are_named_not_silently_dropped():
    """The names that vanish are the ones that lost money. Counting them is the
    minimum defence against survivorship bias."""
    panel = linear_panel(n_dates=3, n_names=40, alpha=1.0)
    doomed = ["N000", "N001", "N002"]
    holed = []
    for x in panel:
        rets = dict(x.forward_returns)
        for t in doomed:
            rets[t] = None
        holed.append(rb(x.date, x.scores, rets))
    r = B.run_backtest(holed, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.missing_per_date == [3, 3, 3]
    assert r.missing_names[panel[0].date] == doomed
    assert any("no forward return" in x for x in r.limitations)
    assert all(n == 37 for n in r.names_per_date)


# -- honest uncertainty ----------------------------------------------------

def test_overlapping_windows_reduce_the_effective_sample():
    """12 monthly rebalances held for a year is not 12 independent observations."""
    panel = linear_panel(n_dates=12, alpha=0.2, noise=0.3)
    r = B.run_backtest(panel, label="t", variant="v", horizon_days=252, rebalance_spacing_days=21)
    assert r.n_rebalances_scored == 12
    assert r.effective_independent_periods == pytest.approx(1.0)


def test_non_overlapping_windows_keep_the_full_sample():
    r = B.run_backtest(linear_panel(n_dates=12, alpha=0.2, noise=0.3), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert r.effective_independent_periods == pytest.approx(12.0)


def test_block_bootstrap_is_wider_than_an_iid_bootstrap_on_dependent_data():
    from an.stats import bootstrap_ci

    series = [0.05 + 0.001 * i for i in range(40)]  # strongly trending, hence dependent
    iid = bootstrap_ci(series)
    blocked = B._block_bootstrap_ci(series, block=12)
    assert (blocked[1] - blocked[0]) > (iid[1] - iid[0])


def test_bootstrap_is_deterministic():
    p = linear_panel(n_dates=10, alpha=0.2, noise=0.4)
    a = B.run_backtest(p, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    b = B.run_backtest(p, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert a.ic_ci == b.ic_ci


# -- verdicts --------------------------------------------------------------

def test_verdict_is_no_evidence_when_the_interval_covers_zero():
    import random

    rng = random.Random(3)
    panel = []
    for d in range(30):
        scores = {f"N{i:03d}": rng.random() for i in range(60)}
        rets = {t: rng.gauss(0, 0.1) for t in scores}
        panel.append(rb(f"2020-{d+1:03d}", scores, rets))
    r = B.run_backtest(panel, label="noise", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.verdict == "no evidence"
    assert "no evidence" in r.verdict_sentence


def test_verdict_is_weak_with_few_independent_periods():
    r = B.run_backtest(linear_panel(n_dates=6, alpha=1.0), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert r.effective_independent_periods == 6
    assert r.verdict == "weak"


def test_verdict_can_reach_supported_with_enough_clean_periods():
    r = B.run_backtest(linear_panel(n_dates=30, alpha=1.0), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert r.verdict == "supported"


def test_multiple_testing_makes_the_bar_higher():
    p = linear_panel(n_dates=25, n_names=40, alpha=0.10, noise=0.42, seed=9)
    one = B.run_backtest(p, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21,
                         hypotheses_tested=1)
    many = B.run_backtest(p, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21,
                          hypotheses_tested=12)
    order = ["no evidence", "weak", "suggestive", "supported"]
    assert order.index(many.verdict) <= order.index(one.verdict)
    assert any("12 variant" in x for x in many.limitations)


def test_nothing_ever_reaches_proven():
    for n in (5, 30, 200):
        r = B.run_backtest(linear_panel(n_dates=n, alpha=1.0), label="t", variant="v",
                           horizon_days=21, rebalance_spacing_days=21)
        assert r.verdict in ("no evidence", "weak", "suggestive", "supported")


# -- the look-ahead smell test ---------------------------------------------

def test_an_implausible_ic_is_flagged_not_celebrated():
    r = B.run_backtest(linear_panel(n_dates=20, alpha=1.0), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert any("look-ahead" in f for f in r.flags)
    assert any(str(B.SUSPICIOUS_IC) in f for f in r.flags)


def test_a_realistic_ic_is_not_flagged():
    p = linear_panel(n_dates=20, n_names=60, alpha=0.05, noise=0.5, seed=4)
    r = B.run_backtest(p, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert not any("look-ahead" in f for f in r.flags)


# -- quintiles -------------------------------------------------------------

def test_quintiles_are_formed_within_a_date_not_across_dates():
    """One date where everything rose and one where everything fell. Bucketing across
    dates would put the whole good date in the top bucket regardless of the score."""
    up = rb("2020-01-01", {f"N{i:02d}": i for i in range(40)}, {f"N{i:02d}": 0.30 for i in range(40)})
    down = rb("2020-02-01", {f"N{i:02d}": i for i in range(40)}, {f"N{i:02d}": -0.30 for i in range(40)})
    r = B.run_backtest([up, down], label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    means = [q.mean_return for q in r.quintiles]
    assert all(m == pytest.approx(0.0) for m in means)
    assert all(q.n_dates == 2 for q in r.quintiles)


def test_quintile_spread_matches_the_planted_signal():
    r = B.run_backtest(linear_panel(n_dates=10, n_names=100, alpha=1.0), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert r.long_short_spread == pytest.approx(0.8, abs=0.02)
    assert r.quintiles[-1].mean_return > r.quintiles[0].mean_return


def test_quintiles_carry_their_own_sample_sizes():
    r = B.run_backtest(linear_panel(n_dates=10, n_names=100), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21)
    assert all(q.n_observations == 200 for q in r.quintiles)
    assert sum(q.n_observations for q in r.quintiles) == 1000


# -- failure modes ---------------------------------------------------------

def test_a_sector_where_the_score_fails_is_reported():
    panel = []
    for d in range(10):
        scores, rets, sectors = {}, {}, {}
        for i in range(40):
            t = f"T{i:02d}"
            scores[t] = i / 39
            sectors[t] = "Technology"
            rets[t] = scores[t]
        for i in range(40):
            t = f"E{i:02d}"
            scores[t] = i / 39
            sectors[t] = "Energy"
            rets[t] = -scores[t]  # the score is upside down here
        panel.append(rb(f"2020-{d+1:02d}-01", scores, rets, sectors=sectors))
    r = B.run_backtest(panel, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.by_sector["Energy"][0] < 0
    assert r.by_sector["Technology"][0] > 0
    assert any("Energy" in f for f in r.failure_modes)


def test_non_monotonic_quintiles_are_called_out():
    """A score that only pays at the extremes is a weaker claim than one that orders."""
    panel = []
    for d in range(6):
        scores = {f"N{i:02d}": i / 39 for i in range(40)}
        def r(s):
            # deliberately humped: the second quintile pays best, the third pays worst
            return 0.10 if s < 0.2 else 0.50 if s < 0.4 else 0.05 if s < 0.6 else 0.30 if s < 0.8 else 0.60

        rets = {t: r(s) for t, s in scores.items()}
        panel.append(rb(f"2020-{d+1:02d}-01", scores, rets))
    r = B.run_backtest(panel, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert any("monotonic" in f for f in r.failure_modes)


def test_worst_dates_are_listed():
    r = B.run_backtest(linear_panel(n_dates=10, alpha=0.1, noise=0.5, seed=2), label="t",
                       variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert len(r.worst_dates) == 5
    assert r.worst_dates[0][1] <= r.worst_dates[-1][1]


def test_bucket_ic_is_reported_per_bucket():
    panel = []
    for d in range(8):
        scores, rets, buckets = {}, {}, {}
        for i in range(50):
            t = f"N{i:02d}"
            scores[t] = i / 49
            buckets[t] = ["compounder"] if i % 2 else ["cyclical turn"]
            rets[t] = scores[t] if i % 2 else -scores[t]
        panel.append(rb(f"2020-{d+1:02d}-01", scores, rets, buckets=buckets))
    r = B.run_backtest(panel, label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.by_bucket["compounder"][0] > 0
    assert r.by_bucket["cyclical turn"][0] < 0
    assert any("cyclical turn" in f for f in r.failure_modes)


# -- serialisation ---------------------------------------------------------

def test_to_dict_is_json_serialisable_and_keeps_the_caveats():
    import json

    r = B.run_backtest(linear_panel(n_dates=10, alpha=0.2, noise=0.3), label="t", variant="v",
                       horizon_days=21, rebalance_spacing_days=21, hypotheses_tested=6)
    d = r.to_dict()
    blob = json.dumps(d)
    assert "limitations" in blob and "verdict" in blob
    assert d["limitations"] and d["verdict"] in ("no evidence", "weak", "suggestive", "supported")
    assert d["hypotheses_tested"] == 6
    assert not math.isnan(d["mean_ic"])


def test_empty_input_is_no_evidence_not_a_crash():
    r = B.run_backtest([], label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert r.verdict == "no evidence"
    assert r.mean_ic is None
    assert r.limitations
