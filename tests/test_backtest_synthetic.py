"""Calibration of the backtest engine against panels with a known answer.

An engine nobody calibrated is a rumour. Each test here plants a specific truth
and checks the engine reports it, including the cases where the correct report is
"there is nothing here".
"""
import pytest

from an import backtest as B
from an.synthetic import PanelSpec, make_panel


def run(spec, **kw):
    panel, truth = make_panel(spec)
    kw.setdefault("horizon_days", 63)
    kw.setdefault("rebalance_spacing_days", 63)
    r = B.run_backtest(panel, label="synthetic", variant=kw.pop("variant", "quality_value"), **kw)
    return r, truth


def test_planted_alpha_is_recovered_with_the_right_sign_and_size():
    """alpha 0.03 against 0.20 idiosyncratic vol should read as a rank IC near 0.1."""
    r, truth = run(PanelSpec(n_dates=40, n_names=150, alpha=0.03, seed=1))
    assert r.mean_ic > 0
    assert r.mean_ic == pytest.approx(truth["approx_rank_ic"], abs=0.05)
    assert r.ic_ci[0] > 0, "a real effect this size over 40 periods should exclude zero"
    assert r.verdict in ("suggestive", "supported")


def test_a_negative_signal_is_recovered_as_negative():
    r, truth = run(PanelSpec(n_dates=40, n_names=150, alpha=-0.03, seed=2))
    assert r.mean_ic < 0
    assert r.ic_ci[1] < 0


def test_pure_noise_is_reported_as_no_evidence():
    """The most important calibration. An engine that finds an edge in noise is worse
    than useless, because it is confidently useless."""
    r, _ = run(PanelSpec(n_dates=40, n_names=150, alpha=0.0, seed=3))
    assert r.ic_ci[0] < 0 < r.ic_ci[1]
    assert r.verdict == "no evidence"
    assert abs(r.mean_ic) < 0.05


@pytest.mark.parametrize("seed", [11, 22, 33, 44, 55, 66, 77, 88])
def test_noise_does_not_produce_a_false_positive_across_seeds(seed):
    """One seed tests the seed. Eight test the engine."""
    r, _ = run(PanelSpec(n_dates=30, n_names=120, alpha=0.0, seed=seed))
    assert r.verdict in ("no evidence", "weak"), f"seed {seed} claimed {r.verdict}"


@pytest.mark.parametrize("seed", [5, 8, 11, 16, 22])
def test_a_small_effect_at_a_small_sample_never_reaches_supported(seed):
    """alpha of 0.004 is a real but tiny effect. Twelve rebalances is not enough to
    establish it, whatever the interval happens to do on any one seed, so the
    verdict must stop at suggestive. These are the five seeds out of twenty where
    the interval did exclude zero, chosen deliberately as the hard cases."""
    r, _ = run(PanelSpec(n_dates=12, n_names=100, alpha=0.004, seed=seed))
    assert r.verdict != "supported"
    assert r.effective_independent_periods < 20


def test_the_overlapping_false_positive_rate_is_recorded_and_worse():
    """The engine's honesty machinery has a hole and it is written down rather than
    left to be found. With four-to-one overlap and a persistent score, the rate at
    the point the twelve-period floor stops protecting is three times nominal."""
    assert B.MEASURED_FALSE_POSITIVE_RATE_OVERLAPPING > 2 * B.MEASURED_FALSE_POSITIVE_RATE
    assert B.measured_false_positive_rate(63, 63) == B.MEASURED_FALSE_POSITIVE_RATE
    assert B.measured_false_positive_rate(252, 63) == B.MEASURED_FALSE_POSITIVE_RATE_OVERLAPPING
    src = " ".join(open(B.__file__).read().split())
    assert "three times nominal" in src, "the measured table must stay in the module"
    assert "worse each time" in src, "the failed block-widening attempt is part of the finding"


def test_an_overlapping_run_says_so_in_its_own_limitations():
    panel, _ = make_panel(PanelSpec(n_dates=30, n_names=120, alpha=0.02, seed=5,
                                    score_persistence=0.85, horizon_periods=4))
    r = B.run_backtest(panel, label="o", variant="v", horizon_days=252, rebalance_spacing_days=63)
    joined = " ".join(r.limitations)
    assert "windows overlap" in joined
    assert "three times too" in joined
    assert "do not overlap" in joined
    assert r.effective_t is not None and abs(r.effective_t) < abs(r.ic_t)


def test_score_persistence_actually_persists():
    """Without it a panel with overlapping horizons still behaves independently, and
    any test of the overlap machinery passes for the wrong reason."""
    from an.stats import spearman

    flat, _ = make_panel(PanelSpec(n_dates=6, n_names=100, seed=3, score_persistence=0.0))
    sticky, _ = make_panel(PanelSpec(n_dates=6, n_names=100, seed=3, score_persistence=0.9))
    names = sorted(set(flat[0].scores) & set(flat[1].scores))
    a, _ = spearman([flat[0].scores[t] for t in names], [flat[1].scores[t] for t in names])
    b, _ = spearman([sticky[0].scores[t] for t in names], [sticky[1].scores[t] for t in names])
    assert abs(a) < 0.3
    assert b > 0.8


def test_an_overlapping_horizon_shares_return_periods():
    panel, _ = make_panel(PanelSpec(n_dates=8, n_names=60, seed=7, horizon_periods=4))
    assert len(panel) == 5  # 8 dates minus the 3 that have no full window ahead
    assert all(len(rb.with_returns) > 20 for rb in panel)


def test_measured_false_positive_rate_has_not_drifted():
    """Generate panels where the score cannot possibly predict the return, and count
    how often the engine says it does. This number is quoted in the module docstring
    and printed on every result, so it has to stay true."""
    claims = 0
    trials = 60
    for seed in range(trials):
        r, _ = run(PanelSpec(n_dates=30, n_names=120, alpha=0.0, seed=seed))
        if r.verdict != "no evidence":
            claims += 1
    rate = claims / trials
    assert rate <= 0.12, f"false positive rate drifted to {rate:.1%}"
    assert B.MEASURED_FALSE_POSITIVE_RATE >= 0.05


def test_every_result_carries_its_own_false_positive_rate():
    r, _ = run(PanelSpec(n_dates=20, n_names=120, alpha=0.02, seed=31))
    assert any("false-positive" in x for x in r.limitations)


def test_look_ahead_contamination_is_flagged_not_celebrated():
    r, truth = run(PanelSpec(n_dates=24, n_names=120, alpha=0.01, contaminate=True, seed=7))
    assert truth["contaminated"]
    assert r.mean_ic > 0.9
    assert any("look-ahead" in f for f in r.flags), r.flags
    assert any(str(B.SUSPICIOUS_IC) in f for f in r.flags)


def test_delistings_are_counted_and_named():
    spec = PanelSpec(n_dates=20, n_names=150, alpha=0.02, delist_per_date=3, seed=9)
    r, truth = run(spec)
    assert len(truth["delisted"]) > 0
    total_names = sum(r.names_per_date)
    assert total_names > 0
    # The engine sees the delisting return, so nothing is missing here. What matters
    # is that names_per_date shrinks over time and the shrinkage is visible.
    assert r.names_per_date[0] > r.names_per_date[-1]


def test_survivorship_bias_has_a_measurable_size():
    """Run the same world twice: once keeping the names that failed, once dropping
    them. The gap between the two is the survivorship bias, and it is not small."""
    spec = PanelSpec(n_dates=20, n_names=150, alpha=0.0, delist_per_date=4, delist_return=-0.5, seed=13)
    panel, truth = make_panel(spec)

    survivors_only = []
    for rb in panel:
        keep = {t: v for t, v in rb.forward_returns.items() if v != spec.delist_return}
        survivors_only.append(
            B.Rebalance(date=rb.date, scores={t: rb.scores[t] for t in keep},
                        forward_returns=keep, sectors=rb.sectors, buckets=rb.buckets)
        )

    full = B.run_backtest(panel, label="full", variant="v", horizon_days=63, rebalance_spacing_days=63)
    surv = B.run_backtest(survivors_only, label="survivors", variant="v", horizon_days=63,
                          rebalance_spacing_days=63)

    full_mean = full.quintiles[0].mean_return
    surv_mean = surv.quintiles[0].mean_return
    assert surv_mean > full_mean, "dropping the failures must flatter the bottom bucket"
    assert (surv_mean - full_mean) > 0.02
    assert any("urvivorship" in x for x in surv.limitations)


def test_quintile_monotonicity_appears_when_the_signal_is_real():
    r, _ = run(PanelSpec(n_dates=40, n_names=200, alpha=0.05, seed=17))
    means = [q.mean_return for q in r.quintiles]
    assert means[-1] > means[0]
    assert not any("monotonic" in f for f in r.failure_modes)


def test_no_sector_is_falsely_accused_when_the_signal_is_uniform():
    r, _ = run(PanelSpec(n_dates=40, n_names=200, alpha=0.05, seed=19))
    negatives = [s for s, (rho, n) in r.by_sector.items() if rho is not None and rho < 0 and n >= 30]
    assert not negatives, negatives


def test_the_engine_is_deterministic_across_runs():
    a, _ = run(PanelSpec(n_dates=20, n_names=120, alpha=0.02, seed=23))
    b, _ = run(PanelSpec(n_dates=20, n_names=120, alpha=0.02, seed=23))
    assert a.mean_ic == b.mean_ic
    assert a.ic_ci == b.ic_ci
    assert a.verdict == b.verdict


def test_generator_is_reproducible():
    p1, t1 = make_panel(PanelSpec(seed=101))
    p2, t2 = make_panel(PanelSpec(seed=101))
    assert [r.date for r in p1] == [r.date for r in p2]
    assert p1[0].scores == p2[0].scores
    assert p1[0].forward_returns == p2[0].forward_returns


def test_different_seeds_give_different_worlds():
    p1, _ = make_panel(PanelSpec(seed=101))
    p2, _ = make_panel(PanelSpec(seed=202))
    assert p1[0].scores != p2[0].scores
