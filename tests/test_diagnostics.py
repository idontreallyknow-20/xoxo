import math

import pytest

from an import diagnostics as D
from an import local, score
from tests.test_score import rec, universe


@pytest.fixture(scope="module")
def real():
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    return recs, score.score_universe(recs)


def test_eigenvalues_of_identity():
    eig = D._eigenvalues_symmetric([[1.0, 0.0], [0.0, 1.0]])
    assert eig == pytest.approx([1.0, 1.0])


def test_eigenvalues_of_a_known_matrix():
    """[[2,1],[1,2]] has eigenvalues 3 and 1."""
    eig = D._eigenvalues_symmetric([[2.0, 1.0], [1.0, 2.0]])
    assert eig[0] == pytest.approx(3.0, abs=1e-6)
    assert eig[1] == pytest.approx(1.0, abs=1e-6)


def test_effective_signal_count_of_perfectly_correlated_components_is_one():
    """Three copies of the same thing is one signal, whatever the weights say."""
    n = 3
    corr = [[1.0] * n for _ in range(n)]
    eig = [max(e, 0.0) for e in D._eigenvalues_symmetric(corr)]
    s = sum(eig)
    p = [e / s for e in eig if e / s > 1e-12]
    eff = math.exp(-sum(x * math.log(x) for x in p))
    assert eff == pytest.approx(1.0, abs=0.05)


def test_diagnose_runs_on_the_real_universe(real):
    recs, s = real
    d = D.diagnose(recs, s)
    assert d.n_names == 150
    assert d.notes
    assert 1.0 <= d.effective_signal_count <= 13.0


def test_it_notices_the_three_revision_components_are_one_idea(real):
    """revisions_90d, revisions_30d and revision_breadth are three views of the same
    thing and carry a quarter of the weight between them. If that stops being
    reported, the score is quietly more concentrated than its weights table says."""
    recs, s = real
    d = D.diagnose(recs, s)
    names = {frozenset((p.a, p.b)) for p in d.redundant_pairs}
    assert frozenset(("revisions_90d", "revisions_30d")) in names
    assert frozenset(("value_pe", "value_ev")) in names
    assert any("largely one idea" in n for n in d.notes)


def test_variance_share_sums_to_one(real):
    recs, s = real
    d = D.diagnose(recs, s)
    assert sum(d.variance_share.values()) == pytest.approx(1.0)
    assert sum(d.nominal_weight.values()) == pytest.approx(1.0)


def test_it_reports_where_the_score_disagrees_with_the_existing_screen(real):
    recs, s = real
    d = D.diagnose(recs, s)
    assert -1.0 <= d.agreement_with_quality_screen <= 1.0
    assert len(d.biggest_disagreements) == 12
    assert abs(d.biggest_disagreements[0]["moved"]) >= abs(d.biggest_disagreements[-1]["moved"])


def test_the_first_note_always_says_this_is_not_a_backtest(real):
    recs, s = real
    d = D.diagnose(recs, s)
    assert "not evidence" in d.notes[0] or "none of it is evidence" in d.notes[0]
    assert "predicts returns" in d.notes[0]


def test_thinly_evidenced_names_are_listed():
    holed = [rec(f"H{i}", roic_avg=None, fcf_margin_avg=None, pe_vs_median=None,
                 ev_vs_median=None, eps_fy1_chg_90d=None, eps_fy1_chg_30d=None)
             for i in range(4)]
    recs = universe(30) + holed
    s = score.score_universe(recs)
    d = D.diagnose(recs, s)
    assert set(d.thinly_evidenced) == {f"H{i}" for i in range(4)}
    assert any("not really a ranking" in n for n in d.notes)


def test_to_dict_is_json_serialisable(real):
    import json

    recs, s = real
    blob = json.dumps(D.diagnose(recs, s).to_dict())
    assert "component_correlations" in blob
    assert "not really" in blob or "evidence" in blob


def test_variance_shares_sum_to_exactly_one(real):
    """The first version divided each component's variance by the sum of the
    component variances, which is not the variance of the score: the score is a sum
    of correlated components, so it also carries twice the covariances. A covariance
    decomposition is the only version that both sums to one and can go negative."""
    recs, s = real
    d = D.diagnose(recs, s)
    assert sum(d.variance_share.values()) == pytest.approx(1.0, abs=1e-9)


def test_a_component_that_moves_against_the_score_can_be_negative(real):
    recs, s = real
    d = D.diagnose(recs, s)
    negatives = {k: v for k, v in d.variance_share.items() if v < 0}
    assert negatives, "a sum-of-variances denominator could never produce one"
    # The note only fires past 1%, so a -0.7% share is correctly left unremarked.
    assert all(v > -0.01 for v in negatives.values()) or any("negative" in n for n in d.notes)


def test_too_few_names_reports_no_effective_signal_count():
    """With too few names every pairwise correlation is None, the matrix falls back to
    the identity, and the entropy measure reports one independent signal per
    component: diagnose([], {}) claimed thirteen. Inferring maximal diversification
    from no data is the worst possible failure for a measure whose job is to say the
    score is less diversified than it looks."""
    from an import local, score

    recs = [r for r in local.load_universe().values() if r.in_top_150]
    for n in (0, 3, 10, 19):
        subset = recs[:n]
        d = D.diagnose(subset, score.score_universe(subset))
        assert d.effective_signal_count is None, f"{n} names claimed {d.effective_signal_count}"
        assert any("not thirteen" in x for x in d.notes)

    full = D.diagnose(recs, score.score_universe(recs))
    assert full.effective_signal_count is not None
    assert 1.0 < full.effective_signal_count < 13.0


def test_the_first_note_does_not_hard_code_a_snapshot_date():
    """It said "dated 2026-09-04" regardless of what was loaded."""
    from an import local, score

    recs = [r for r in local.load_universe().values() if r.in_top_150]
    d = D.diagnose(recs, score.score_universe(recs))
    assert "2026-09-04" not in d.notes[0]
    assert "single cross section" in d.notes[0]
