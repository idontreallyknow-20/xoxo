import math

from an import local


def test_row_counts():
    q = local.load_quality()
    p = local.load_price_screen()
    assert len(q) == 1505
    assert len(p) == 150


def test_exel_spot_check():
    """Hand-checked against the raw CSV row so a silent column shift is caught."""
    u = local.load_universe()
    e = u["EXEL"]
    assert e.name == "Exelixis, Inc."
    assert e.sector == "Healthcare"
    assert e.quality.rank == 1
    assert e.quality.total == 89.9
    assert e.quality.roic == 88.6
    assert abs(e.fundamentals.roic_avg - 0.17557833) < 1e-6
    assert abs(e.fundamentals.rev_cagr - 0.12927515) < 1e-6
    assert e.fundamentals.net_cash is True
    assert e.fundamentals.fiscal_years == ["2022", "2023", "2024", "2025"]
    assert e.valuation is not None
    assert abs(e.valuation.forward_pe - 13.877317) < 1e-5
    assert abs(e.valuation.dd_52w - -0.019926343) < 1e-6
    assert e.valuation.dd_ath == 0.0  # trading at its own closing-basis high
    assert e.valuation.bucket_compounder is True
    assert e.valuation.bucket_cyclical_turn is False
    assert e.valuation.next_earnings == "2026-10-27"


def test_klac_matches_the_research_note():
    """KLAC.md states forward P/E 25.9 vs a 35.0 median and -43% from the all-time high."""
    u = local.load_universe()
    k = u["KLAC"]
    assert round(k.valuation.forward_pe, 1) == 25.9
    assert round(k.valuation.median_pe_hist, 1) == 35.0
    assert round(k.valuation.dd_ath * 100) == -43
    assert k.valuation.bucket_cyclical_turn is True


def test_drawdown_bases_differ_and_the_loader_does_not_hide_it():
    """`ath` is a max CLOSE, `high_52w` is a max INTRADAY high, so ath < high_52w for
    half the list. They are not comparable and dd_ath understates the true drawdown.
    Recorded here so the fact survives; see NOTES.md."""
    p = local.load_price_screen()
    lower = [t for t, v in p.items() if v.ath and v.high_52w and v.ath < v.high_52w]
    assert len(lower) == 75


def test_no_nan_anywhere():
    """NaN comparing false against everything is how a screen quietly mis-ranks. Ban it."""
    for rec in local.load_universe().values():
        for obj in (rec, rec.fundamentals, rec.quality, rec.valuation):
            if obj is None:
                continue
            for k, v in obj.__dict__.items():
                assert not (isinstance(v, float) and math.isnan(v)), f"{rec.ticker}.{k}"


def test_missing_values_are_none_not_zero():
    """64 names have no net-debt-to-EBITDA. None of them may read as 0.0x leverage."""
    u = local.load_universe()
    missing = [r for r in u.values() if r.fundamentals.nd_to_ebitda is None]
    assert len(missing) == 64
    assert all(r.fundamentals.nd_to_ebitda is not zero for r in missing for zero in (0, 0.0))


def test_twelve_names_have_no_own_history():
    p = local.load_price_screen()
    no_hist = [t for t, v in p.items() if not v.has_own_history]
    assert len(no_hist) == 12
    for t in no_hist:
        assert p[t].pe_vs_median is None


def test_bucket_counts_match_the_written_files():
    p = local.load_price_screen()
    assert sum(v.bucket_compounder for v in p.values()) == 118
    assert sum(v.bucket_cyclical_turn for v in p.values()) == 12


def test_every_record_has_a_pulled_date():
    for rec in local.load_universe().values():
        assert rec.pulled == "2026-09-04"


def test_sectors_are_the_expected_ten():
    seen = {r.sector for r in local.load_universe().values() if r.sector}
    assert seen == set(local.SECTORS)


def test_net_revisions_handles_missing():
    from an.local import Valuation

    assert Valuation().net_revisions_fy1 is None
    assert Valuation(rev_up30_fy1=4.0).net_revisions_fy1 == 4.0
    assert Valuation(rev_up30_fy1=4.0, rev_down30_fy1=6.0).net_revisions_fy1 == -2.0


def test_years_label():
    u = local.load_universe()
    assert u["EXEL"].fundamentals.years_label == "2022–2025"
