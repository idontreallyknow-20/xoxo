import pytest

from an import metrics as M
from an import research_md


def test_cagr_basic():
    assert M.cagr(100.0, 200.0, 1) == pytest.approx(1.0)
    assert M.cagr(100.0, 133.1, 3) == pytest.approx(0.10, abs=1e-3)


def test_cagr_refuses_a_non_positive_start():
    """A CAGR from minus 50 to plus 10 is not 240 percent a year, it is undefined.
    Screens that take an absolute value here produce nonsense."""
    assert M.cagr(-50.0, 10.0, 3) is None
    assert M.cagr(0.0, 10.0, 3) is None
    assert M.cagr(10.0, -5.0, 3) is None
    assert M.cagr(None, 10.0, 3) is None
    assert M.cagr(10.0, 20.0, 0) is None


def test_yoy():
    assert M.yoy([100.0, 110.0, 121.0]) == [None, pytest.approx(0.1), pytest.approx(0.1)]
    assert M.yoy([None, 110.0]) == [None, None]
    assert M.yoy([0.0, 5.0]) == [None, None]


def test_direction_has_a_dead_zone():
    assert M.direction_of(100.0, 102.0) == "flat"
    assert M.direction_of(100.0, 130.0) == "up"
    assert M.direction_of(100.0, 70.0) == "down"
    assert M.direction_of(None, 70.0) == "unknown"


def test_direction_on_a_rate_compares_points_not_percent():
    """60.0% to 61.5% is 1.5 points. Dividing by 60 to call it 2.5% buries it."""
    assert M.direction_of(0.600, 0.615, absolute=True, band=0.01) == "up"
    assert M.direction_of(0.600, 0.605, absolute=True, band=0.01) == "flat"


@pytest.fixture(scope="module")
def klac():
    return research_md.load_all()["KLAC"]


def test_trends_from_the_real_klac_table(klac):
    trends = {t.key: t for t in M.series_from_rows(klac.financials, "research note")}
    rev = trends["revenue"]
    assert rev.first == 10.50 and rev.last == 13.58
    assert rev.periods == 3
    assert "3 intervals" in rev.span_label
    assert rev.cagr == pytest.approx((13.58 / 10.50) ** (1 / 3) - 1, abs=1e-9)
    assert rev.cagr == pytest.approx(0.0895, abs=0.0005)
    assert rev.direction == "up" and rev.reads_well is True


def test_four_fiscal_years_is_three_intervals_not_four(klac):
    """The README already flags that the source gives four annual statements.
    Calling that a four-year CAGR overstates growth by a third."""
    rev = M.series_from_rows(klac.financials, "s")[0]
    assert len(rev.observed) == 4
    assert rev.periods == 3
    wrong = (13.58 / 10.50) ** (1 / 4) - 1
    assert rev.cagr > wrong


def test_share_count_falling_reads_as_good_news(klac):
    trends = {t.key: t for t in M.series_from_rows(klac.financials, "s")}
    sh = trends["diluted_shares"]
    assert sh.first == 1402 and sh.last == 1320
    assert sh.direction == "down"
    assert sh.higher_is_better is False
    assert sh.reads_well is True


def test_margins_are_reported_in_points_not_compounded(klac):
    gm = {t.key: t for t in M.series_from_rows(klac.financials, "s")}["gross_margin"]
    assert gm.unit == "percent"
    assert gm.cagr is None
    assert gm.change == pytest.approx(0.613 - 0.598, abs=1e-9)


def test_every_research_note_produces_six_trends():
    for t, note in research_md.load_all().items():
        trends = M.series_from_rows(note.financials, "s")
        assert len(trends) == 6, t
        usable = [x for x in trends if len(x.observed) >= 3]
        assert len(usable) >= 5, f"{t} has only {len(usable)} usable trends"


def test_a_company_with_no_gross_margin_line_reports_it_as_absent():
    """Booking reports no cost of revenue, so its gross margin row is n/a in all four
    years. That must come through as no data, so the page can say "not reported"
    instead of drawing an empty chart or inventing a zero."""
    note = research_md.load_all()["BKNG"]
    gm = {t.key: t for t in M.series_from_rows(note.financials, "s")}["gross_margin"]
    assert gm.values == [None, None, None, None]
    assert gm.observed == []
    assert gm.span_label == "no data"
    assert gm.direction == "unknown"
    others = [t for t in M.series_from_rows(note.financials, "s") if t.key != "gross_margin"]
    assert all(len(t.observed) == 4 for t in others)


def test_trend_with_no_data_is_not_a_crash():
    t = M.Trend("k", "K", "currency_bn", [None, None], ["FY1", "FY2"], "s")
    assert t.first is None and t.last is None
    assert t.periods == 0 and t.span_label == "no data"
    assert t.direction == "unknown" and t.cagr is None and t.reads_well is None


def test_to_dict_is_serialisable(klac):
    import json

    for t in M.series_from_rows(klac.financials, "research note", as_of="2026-09-04"):
        blob = json.dumps(t.to_dict())
        assert "span_label" in blob and "source" in blob


def test_no_percentage_change_for_a_series_that_crosses_zero():
    """Alphabet went from $84bn of net cash to $16bn of net debt, a hundred-billion
    deterioration. last/first - 1 on a negative base reported that as "-118.8%",
    which is wrong in sign and meaningless in magnitude."""
    t = M.Trend("net_debt", "Net debt", "currency_bn", [-84.08, -5.0, 2.0, 15.84],
                ["FY2022", "FY2023", "FY2024", "FY2025"], "s", higher_is_better=False)
    assert t.crosses_zero is True
    assert t.change_pct is None
    assert t.cagr is None
    assert t.change == pytest.approx(99.92)


def test_no_percentage_change_from_a_negative_base_even_without_a_crossing():
    """Copart's net debt went from -1.26 to -4.69: more net cash, unambiguously good.
    The ratio printed +272.2% under a "lower is better" label."""
    t = M.Trend("net_debt", "Net debt", "currency_bn", [-1.26, -2.0, -3.0, -4.69],
                ["FY2023", "FY2024", "FY2025", "FY2026"], "s", higher_is_better=False)
    assert t.crosses_zero is False
    assert t.change_pct is None
    assert t.change == pytest.approx(-3.43)


def test_a_normal_positive_series_still_gets_a_percentage():
    t = M.Trend("revenue", "Revenue", "currency_bn", [10.0, 11.0, 12.0, 13.0],
                ["FY2023", "FY2024", "FY2025", "FY2026"], "s")
    assert t.change_pct == pytest.approx(0.3)
    assert t.crosses_zero is False


def test_periods_counts_elapsed_years_not_surviving_points():
    """Four points with a year missing spans four years, not three. Dividing by
    three annualises the growth over the wrong window, which is the exact error the
    module exists to prevent."""
    gapped = M.Trend("revenue", "Revenue", "currency_bn", [10.0, None, 12.0, 13.31],
                     ["FY2022", "FY2023", "FY2024", "FY2025"], "s")
    assert len(gapped.observed) == 3
    assert gapped.periods == 3        # FY2022 to FY2025
    assert gapped.has_gap is True
    assert "missing from the middle" in gapped.span_note
    assert gapped.cagr == pytest.approx((13.31 / 10.0) ** (1 / 3) - 1)

    full = M.Trend("revenue", "Revenue", "currency_bn", [10.0, 11.0, 12.0, 13.31],
                   ["FY2022", "FY2023", "FY2024", "FY2025"], "s")
    assert full.periods == 3 and full.has_gap is False and full.span_note is None


def test_unparseable_period_labels_fall_back_to_counting_points():
    t = M.Trend("x", "X", "currency_bn", [1.0, 2.0, 3.0], ["a", "b", "c"], "s")
    assert t.periods == 2
    assert t.has_gap is False
