import math

import pytest

from an import stats as S


def test_mean_stdev_median_ignore_none():
    xs = [1.0, None, 3.0, None, 5.0]
    assert S.mean(xs) == 3.0
    assert S.median(xs) == 3.0
    assert S.stdev(xs) == pytest.approx(2.0)


def test_mean_of_nothing_is_none():
    assert S.mean([]) is None
    assert S.mean([None, None]) is None
    assert S.stdev([1.0]) is None


def test_nan_and_inf_are_treated_as_missing():
    assert S.mean([1.0, float("nan"), 3.0]) == 2.0
    assert S.mean([1.0, float("inf")]) == 1.0


def test_percentile_matches_numpy_linear_interpolation():
    import numpy as np

    xs = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]
    for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        assert S.percentile(xs, q) == pytest.approx(float(np.percentile(xs, q * 100)))


def test_winsorize_clips_both_tails_and_keeps_none():
    xs = [None] + [float(i) for i in range(100)] + [10_000.0]
    w = S.winsorize(xs, 0.05, 0.95)
    assert w[0] is None
    assert max(x for x in w if x is not None) < 10_000


@pytest.mark.parametrize("n", [10, 31, 150, 1505])
def test_one_bad_multiple_does_not_flatten_every_real_z(n):
    """A single misparsed P/E of 100,000 must not compress thirty real names to zero.

    A plain percentile clip cannot save this at small n, which is why zscores uses
    a median and MAD scale instead. The mean and standard deviation version fails
    this test, which is the point of it.
    """
    clean = [float(i) for i in range(10, 10 + n)]
    z = S.zscores(clean + [100_000.0])
    real = z[:-1]
    assert max(real) - min(real) > 2.0
    if n <= 150:
        # What we are avoiding. At n=1505 the real range 10..1514 is itself wide
        # enough that one outlier of 100,000 no longer dominates, so the naive
        # version only fails at the sample sizes this project actually uses.
        naive = S.zscores(clean + [100_000.0], robust=False, winsor=None)
        assert max(naive[:-1]) - min(naive[:-1]) < 0.05


def test_a_point_mass_does_not_wipe_out_the_only_variation():
    """Twenty-eight names on one value plus one high and one low. Clipping by
    position would destroy this; a robust scale must not."""
    xs = [0.15] * 28 + [0.35, 0.01]
    z = S.zscores(xs)
    assert z[-2] > 1.0 and z[-1] < -1.0


def test_winsorize_leaves_a_well_behaved_sample_almost_alone():
    xs = [float(i) for i in range(100)]
    w = S.winsorize(xs)
    assert sum(1 for a, b in zip(xs, w) if a != b) <= 4


def test_mad():
    assert S.mad([1.0, 1.0, 1.0]) == 0.0
    assert S.mad([1.0]) is None
    assert S.mad([0.0, 1.0, 2.0, 3.0, 4.0], scaled=False) == 1.0


def test_zscores_recover_the_scale_of_normal_data():
    """The MAD scale factor is calibrated to the normal distribution, so on normal
    input the robust z and the plain z should agree closely."""
    import random as _r

    rng = _r.Random(5)
    xs = [rng.gauss(10.0, 3.0) for _ in range(4000)]
    z = S.zscores(xs, winsor=None)
    assert S.mean(z) == pytest.approx(0.0, abs=0.05)
    assert S.stdev(z) == pytest.approx(1.0, abs=0.05)


def test_non_robust_mode_is_exactly_the_textbook_formula():
    xs = [float(i) for i in range(1, 51)]
    z = S.zscores(xs, winsor=None, robust=False, clip=None)
    assert S.mean(z) == pytest.approx(0.0, abs=1e-12)
    assert S.stdev(z) == pytest.approx(1.0, abs=1e-12)


def test_clip_bounds_the_influence_of_a_survivor():
    z = S.zscores([1.0] * 40 + [1e9], clip=4.0)
    assert max(z) == 4.0


def test_zscores_with_zero_variance_are_zero_not_a_crash():
    z = S.zscores([5.0, 5.0, 5.0])
    assert z == [0.0, 0.0, 0.0]


def test_zscores_keep_none_as_none():
    z = S.zscores([1.0, None, 3.0])
    assert z[1] is None


def test_rank_percentile_ends_and_ties():
    r = S.rank_percentile([10.0, 20.0, 30.0])
    assert r == [0.0, 0.5, 1.0]
    tied = S.rank_percentile([5.0, 5.0, 9.0])
    assert tied[0] == tied[1]
    assert tied[2] == 1.0


def test_rank_percentile_descending():
    r = S.rank_percentile([10.0, 20.0, 30.0], ascending=False)
    assert r == [1.0, 0.5, 0.0]


def test_spearman_perfect_and_inverse():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    rho, n = S.spearman(xs, [2.0, 4.0, 6.0, 8.0, 10.0])
    assert rho == pytest.approx(1.0) and n == 5
    rho, n = S.spearman(xs, [10.0, 8.0, 6.0, 4.0, 2.0])
    assert rho == pytest.approx(-1.0)


def test_spearman_is_monotone_not_linear():
    """Rank correlation must see y = x^3 as a perfect relationship."""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    rho, _ = S.spearman(xs, [x**3 for x in xs])
    assert rho == pytest.approx(1.0)


def test_spearman_returns_the_pair_count():
    rho, n = S.spearman([1.0, None, 3.0, 4.0], [1.0, 2.0, None, 4.0])
    assert n == 2 and rho is None  # two pairs is not a correlation


def test_spearman_refuses_tiny_samples():
    assert S.spearman([1.0, 2.0], [1.0, 2.0]) == (None, 2)


def test_bootstrap_ci_is_deterministic():
    xs = [0.01 * i for i in range(50)]
    a = S.bootstrap_ci(xs, seed=7)
    b = S.bootstrap_ci(xs, seed=7)
    assert a == b


def test_bootstrap_ci_brackets_the_mean():
    xs = [1.0] * 30 + [3.0] * 30
    lo, hi = S.bootstrap_ci(xs)
    assert lo < 2.0 < hi


def test_bootstrap_ci_is_calibrated_on_noise():
    """A 95% interval should contain the true mean about 95% of the time.

    Testing one sample against one seed tests the seed. Forty independent noise
    samples test the estimator: seed 11 at n=200 genuinely excludes zero, which is
    the 1-in-20 you are supposed to see.
    """
    import random as _r

    covered = 0
    for seed in range(40):
        rng = _r.Random(seed)
        xs = [rng.gauss(0, 1) for _ in range(200)]
        lo, hi = S.bootstrap_ci(xs, seed=seed)
        covered += lo < 0 < hi
    assert covered >= 33, f"only {covered}/40 intervals covered zero"


def test_bootstrap_ci_needs_a_sample():
    assert S.bootstrap_ci([1.0, 2.0]) is None


def test_t_stat():
    assert S.t_stat([1.0] * 10) is None  # zero variance
    t = S.t_stat([1.0, 2.0, 3.0, 4.0, 5.0])
    assert t == pytest.approx(3.0 / (math.sqrt(2.5) / math.sqrt(5)), abs=1e-9)
