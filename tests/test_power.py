"""The power analysis, and a re-measurement so its published numbers stay true."""
import math

import pytest

from an import power


def test_standard_error_shrinks_with_both_names_and_dates():
    a = power.se_of_mean_ic(150, 1)
    b = power.se_of_mean_ic(150, 4)
    c = power.se_of_mean_ic(600, 1)
    assert b == pytest.approx(a / 2)
    assert c < a


def test_the_formula_matches_the_textbook():
    """2.8 sigma for 80% power at a two-sided 5%."""
    assert power.NEEDED_SIGMAS == pytest.approx(2.8016, abs=0.001)
    assert power.detectable_ic(150, 20) == pytest.approx(
        power.NEEDED_SIGMAS / math.sqrt(20 * 149), abs=1e-12)


def test_too_few_names_or_dates_gives_none():
    assert power.se_of_mean_ic(2, 10) is None
    assert power.se_of_mean_ic(150, 0) is None
    assert power.detectable_ic(2, 10) is None


def test_a_single_quarter_can_only_detect_effects_it_would_disbelieve():
    """The sharpest statement of the whole problem.

    One rebalance of 150 names can only detect a rank IC of about 0.23. The engine
    flags anything above 0.25 as probable look-ahead rather than an edge. So a
    single-quarter backtest sits right on the boundary: the smallest thing it could
    see is roughly the size at which its own honesty check starts calling results
    bugs. There is no window in which one quarter tells you something both
    detectable and believable.
    """
    from an import backtest

    d = power.detectable_ic(150, 1)
    assert 0.20 < d < 0.26
    assert abs(d - backtest.SUSPICIOUS_IC) < 0.03
    assert d > max(power.REFERENCE_EFFECTS.values()) * 3


def test_quarters_needed_is_monotone_in_effect_size():
    strong = power.quarters_needed(0.06)
    typical = power.quarters_needed(0.04)
    weak = power.quarters_needed(0.02)
    assert strong < typical < weak
    assert power.quarters_needed(0.0) is None
    assert power.quarters_needed(-0.05) is None


def test_the_uncomfortable_headline_number():
    """Eight years of quarterly snapshots to see a typical published signal."""
    assert power.quarters_needed(0.04) / 4 == pytest.approx(8.25, abs=0.5)


def test_power_table_covers_the_useful_range():
    rows = power.power_table()
    assert rows[0].quarters == 1 and rows[-1].quarters == 80
    assert all(rows[i].detectable_ic > rows[i + 1].detectable_ic for i in range(len(rows) - 1))
    assert not rows[2].detects["a typical published signal"]   # 4 quarters
    assert rows[-2].detects["a typical published signal"]      # 40 quarters


def test_monthly_beats_quarterly_by_a_factor_of_three():
    a = power.archiving_cadence_advice()
    assert a["monthly"]["years_to_first_verdict"] == pytest.approx(
        a["quarterly"]["years_to_first_verdict"] / 3, abs=0.05)
    assert "Archive monthly rather than quarterly" in a["recommendation"]
    assert "one observation wearing twelve hats" in a["recommendation"]


def test_the_advice_admits_its_own_assumption():
    """Names in a sector move together, so the effective cross-section is smaller
    than 150 and every number here is optimistic. Saying so is the point."""
    a = power.archiving_cadence_advice()
    assert "optimistic" in a["caveat"]
    assert "same sector move together" in a["caveat"]


def test_the_verdict_floor_is_a_policy_not_a_statistic():
    assert power.VERDICT_FLOOR_PERIODS == 12
    # Below the floor the engine refuses regardless of what the interval does.
    assert power.MEASURED_POWER[4][0.06] == 0.0
    assert power.MEASURED_POWER[8][0.06] == 0.0
    assert power.MEASURED_POWER[12][0.06] > 0.5


@pytest.mark.parametrize("quarters,ic,alpha", [(12, 0.06, 0.01296), (20, 0.04, 0.00863)])
def test_measured_power_has_not_drifted(quarters, ic, alpha):
    """Re-run the real engine and check the published table still holds. Eight trials
    rather than the sixteen used to build it, with a wide tolerance, because this is
    a regression guard and not a re-derivation."""
    rate = power.measure_empirically(quarters, true_alpha=alpha, n_names=150, trials=8, base_seed=9000)
    expected = power.MEASURED_POWER[quarters][ic]
    assert abs(rate - expected) <= 0.30, f"{quarters} rebalances at IC {ic}: measured {rate}, table says {expected}"


def test_reference_effects_match_the_published_range():
    from an import backtest

    for name, ic in power.REFERENCE_EFFECTS.items():
        assert 0.0 <= ic <= backtest.SUSPICIOUS_IC, name
