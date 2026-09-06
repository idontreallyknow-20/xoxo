import dataclasses
import math

import pytest

from an import local, score
from an.local import Fundamentals, QualityScore, TickerRecord, Valuation


def rec(ticker, sector="Technology", **kw):
    """A record with enough filled in to score, so a test can vary one thing."""
    f_keys = {f.name for f in dataclasses.fields(Fundamentals)}
    v_keys = {f.name for f in dataclasses.fields(Valuation)}
    f = dict(roic_avg=0.15, roic_trend=0.0, fcf_margin_avg=0.15, fcf_positive_years=4.0,
             fcf_years=4.0, rev_cagr=0.10, nd_to_ebitda=1.0, net_cash=False,
             share_change=0.0, gm_avg=0.5, gm_std=0.02)
    v = dict(pe_vs_median=0.0, ev_vs_median=0.0, n_hist_years=4, eps_fy1_chg_30d=0.0,
             eps_fy1_chg_90d=0.0, rev_up30_fy1=3.0, rev_down30_fy1=3.0,
             short_pct_float=0.02, dd_52w=-0.10, dd_ath=-0.15)
    f.update({k: kw.pop(k) for k in list(kw) if k in f_keys})
    v.update({k: kw.pop(k) for k in list(kw) if k in v_keys})
    assert not kw, f"unknown keys {list(kw)}"
    return TickerRecord(ticker=ticker, sector=sector, fundamentals=Fundamentals(**f),
                        quality=QualityScore(), valuation=Valuation(**v))


def universe(n=30, sector="Technology"):
    return [rec(f"T{i:02d}", sector, roic_avg=0.05 + i * 0.005, rev_cagr=0.02 + i * 0.004) for i in range(n)]


# -- structure -------------------------------------------------------------

def test_weights_are_positive_and_documented():
    for c in score.COMPONENTS:
        assert c.weight > 0, c.key
        assert c.why.strip(), f"{c.key} has no stated reason for being in the score"
        assert len(c.label) > 3


def test_variants_are_the_base_plus_one_price_component():
    base = {c.key for c in score.components_for("quality_value")}
    for v in ("with_momentum", "with_reversal"):
        extra = {c.key for c in score.components_for(v)} - base
        assert len(extra) == 1, v
    assert score.components_for("with_momentum")[-1].key == "momentum_52w"
    assert score.components_for("with_reversal")[-1].key == "reversal_ath"


def test_the_two_price_variants_disagree_on_purpose():
    """Momentum wants to be near the high, reversal wants to be far below it.
    If they ever agreed, one of them would be pointless."""
    mom = score.components_for("with_momentum")[-1]
    rev = score.components_for("with_reversal")[-1]
    assert mom.contested and rev.contested
    deep = rec("DEEP", dd_52w=-0.55, dd_ath=-0.60)
    shallow = rec("SHAL", dd_52w=-0.02, dd_ath=-0.03)
    others = universe(20)
    m = score.score_universe(others + [deep, shallow], variant="with_momentum")
    r = score.score_universe(others + [deep, shallow], variant="with_reversal")
    assert m["SHAL"].z["momentum_52w"] > m["DEEP"].z["momentum_52w"]
    assert r["DEEP"].z["reversal_ath"] > r["SHAL"].z["reversal_ath"]


def test_unknown_variant_raises():
    with pytest.raises(ValueError):
        score.components_for("wishful_thinking")


def test_weights_table_is_renderable_and_normalised():
    for v in score.VARIANTS:
        rows = score.weights_table(v)
        assert abs(sum(r["weight"] for r in rows) - 1.0) < 1e-9
        assert all(r["why"] for r in rows)


# -- behaviour -------------------------------------------------------------

def test_deterministic():
    u = universe()
    a = score.score_universe(u)
    b = score.score_universe(u)
    assert {k: v.score for k, v in a.items()} == {k: v.score for k, v in b.items()}


def test_empty_input():
    assert score.score_universe([]) == {}


@pytest.mark.parametrize(
    "field,better,worse",
    [
        ("roic_avg", 0.40, 0.02),
        ("fcf_margin_avg", 0.35, 0.01),
        ("rev_cagr", 0.30, -0.05),
        ("share_change", -0.05, 0.15),
        ("gm_std", 0.005, 0.30),
        ("nd_to_ebitda", 0.2, 6.0),
    ],
)
def test_each_fundamental_component_moves_the_score_the_right_way(field, better, worse):
    others = universe(28)
    good = rec("GOOD", **{field: better})
    bad = rec("BAD", **{field: worse})
    s = score.score_universe(others + [good, bad])
    assert s["GOOD"].score > s["BAD"].score, field


@pytest.mark.parametrize(
    "field,better,worse",
    [
        ("pe_vs_median", -0.45, 0.60),
        ("ev_vs_median", -0.40, 0.55),
        ("eps_fy1_chg_90d", 0.18, -0.12),
        ("eps_fy1_chg_30d", 0.09, -0.07),
        ("short_pct_float", 0.005, 0.22),
    ],
)
def test_each_valuation_component_moves_the_score_the_right_way(field, better, worse):
    others = universe(28)
    good = rec("GOOD", **{field: better})
    bad = rec("BAD", **{field: worse})
    s = score.score_universe(others + [good, bad])
    assert s["GOOD"].score > s["BAD"].score, field


def test_revision_breadth_reads_agreement_not_coverage():
    """Nine up and one down should beat five up and one down, and a widely covered
    name with an even split should not beat a thinly covered unanimous one."""
    others = universe(28)
    strong = rec("STRONG", rev_up30_fy1=9.0, rev_down30_fy1=1.0)
    weak = rec("WEAK", rev_up30_fy1=5.0, rev_down30_fy1=1.0)
    split = rec("SPLIT", rev_up30_fy1=20.0, rev_down30_fy1=20.0)
    s = score.score_universe(others + [strong, weak, split])
    assert s["STRONG"].z["revision_breadth"] > s["WEAK"].z["revision_breadth"]
    assert s["WEAK"].z["revision_breadth"] > s["SPLIT"].z["revision_breadth"]


def test_net_cash_is_scored_as_the_best_balance_sheet():
    others = universe(28)
    cashy = rec("CASH", net_cash=True, nd_to_ebitda=None)
    levered = rec("LEV", net_cash=False, nd_to_ebitda=4.5)
    s = score.score_universe(others + [cashy, levered])
    assert s["CASH"].z["balance"] > s["LEV"].z["balance"]
    assert "balance" not in s["CASH"].missing


def test_lumpy_free_cash_flow_is_discounted():
    """Two good years and two bad ones is not the same business as four steady ones,
    even at the same average margin."""
    others = universe(28)
    steady = rec("STEADY", fcf_margin_avg=0.20, fcf_positive_years=4.0, fcf_years=4.0)
    lumpy = rec("LUMPY", fcf_margin_avg=0.20, fcf_positive_years=2.0, fcf_years=4.0)
    s = score.score_universe(others + [steady, lumpy])
    assert s["STEADY"].z["fcf"] > s["LUMPY"].z["fcf"]


def test_own_history_value_needs_at_least_three_year_ends():
    """A 'cheap versus its own median' claim built on two points is not a claim."""
    others = universe(28)
    thin = rec("THIN", pe_vs_median=-0.5, n_hist_years=2)
    fine = rec("FINE", pe_vs_median=-0.5, n_hist_years=4)
    s = score.score_universe(others + [thin, fine])
    assert "value_pe" in s["THIN"].missing
    assert "value_pe" not in s["FINE"].missing


# -- missing data ----------------------------------------------------------

def test_missing_component_contributes_zero_and_lowers_coverage():
    others = universe(28)
    full = rec("FULL")
    holed = rec("HOLED", roic_avg=None, fcf_margin_avg=None, pe_vs_median=None)
    s = score.score_universe(others + [full, holed])
    assert s["FULL"].coverage == pytest.approx(1.0)
    assert s["HOLED"].coverage < 0.75
    assert s["HOLED"].contributions["roic"] == 0.0
    assert set(s["HOLED"].missing) >= {"roic", "fcf", "value_pe"}


def test_a_name_with_no_data_at_all_scores_zero_not_last():
    """Nothing known is not the same as everything bad."""
    others = universe(28)
    blank = TickerRecord(ticker="BLANK", sector="Technology")
    s = score.score_universe(others + [blank])
    assert s["BLANK"].score == 0.0
    assert s["BLANK"].coverage == 0.0
    assert s["BLANK"].thinly_evidenced is True


def test_coverage_is_bounded():
    for b in score.score_universe(universe(40)).values():
        assert 0.0 <= b.coverage <= 1.0


def test_no_nan_escapes():
    for b in score.score_universe(universe(40)).values():
        assert not math.isnan(b.score)
        for v in b.contributions.values():
            assert not math.isnan(v)


# -- sector neutrality -----------------------------------------------------

def test_a_good_industrial_is_not_buried_by_software_margins():
    """The point of scoring margins within sector: an industrial at the top of its
    own sector must beat a mediocre software name, even though software margins are
    structurally higher."""
    software = [rec(f"SW{i}", "Technology", roic_avg=0.28 + i * 0.002, fcf_margin_avg=0.30 + i * 0.002)
                for i in range(12)]
    industrials = [rec(f"IN{i}", "Industrials", roic_avg=0.08 + i * 0.002, fcf_margin_avg=0.06 + i * 0.002)
                   for i in range(12)]
    best_industrial = rec("BESTIND", "Industrials", roic_avg=0.20, fcf_margin_avg=0.16)
    weak_software = rec("WEAKSW", "Technology", roic_avg=0.26, fcf_margin_avg=0.28)
    s = score.score_universe(software + industrials + [best_industrial, weak_software])
    assert s["BESTIND"].z["roic"] > s["WEAKSW"].z["roic"]


def test_a_tiny_sector_falls_back_to_the_whole_universe():
    """Standardising three utilities against each other manufactures a z-score."""
    tech = [rec(f"T{i}", "Technology", roic_avg=0.10 + i * 0.01) for i in range(20)]
    utils = [rec(f"U{i}", "Utilities", roic_avg=0.05 + i * 0.01) for i in range(3)]
    s = score.score_universe(tech + utils)
    zs = [s[f"U{i}"].z["roic"] for i in range(3)]
    assert all(z is not None for z in zs)
    assert max(zs) < 0.5  # ranked against everyone, not flattered by a sector of three


def test_sector_neutral_flags_match_the_documented_intent():
    by_key = {c.key: c for c in score.COMPONENTS}
    for k in ("roic", "roic_trend", "fcf", "growth", "margin_stability"):
        assert by_key[k].sector_neutral, k
    for k in ("value_pe", "revisions_90d", "share_count", "short_interest"):
        assert not by_key[k].sector_neutral, k


# -- against the real universe --------------------------------------------

def test_scores_the_real_top_150():
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    assert len(recs) == 150
    s = score.score_universe(recs)
    assert len(s) == 150
    assert all(0 <= b.display <= 100 for b in s.values())
    assert all(0 <= b.coverage <= 1 for b in s.values())


def test_names_without_usable_history_lose_value_coverage_not_rank():
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    s = score.score_universe(recs)
    no_hist = [r.ticker for r in recs if r.valuation and not r.valuation.has_own_history]
    assert len(no_hist) == 14  # 12 currency mismatches, 2 with only two year ends
    for t in no_hist:
        assert "value_pe" in s[t].missing
        assert s[t].coverage < 1.0
        assert s[t].display is not None  # still ranked, just flagged


def test_percentiles_span_the_range_on_the_real_universe():
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    d = [b.display for b in score.score_universe(recs).values()]
    assert min(d) == 0.0 and max(d) == 100.0
