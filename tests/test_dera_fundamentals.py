"""an.dera_fundamentals: the SEC data sets as an input to the score, on the Apple miniature.

Every expected number below is computed in the test from the figures in
tests/fixtures/dera_full/README.md, on the quality screen's definitions, before
the adapter's answer is looked at.
"""
import json
import statistics
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from an import dera, dera_fundamentals as df, local, score
from an.local import Fundamentals, TickerRecord, Valuation

ROOT = Path(__file__).resolve().parent.parent
FULL = ROOT / "tests" / "fixtures" / "dera_full"
SOURCES = [("2023q4", FULL / "2023q4"), ("2024q4", FULL / "2024q4")]
AAPL = "0000320193"
M = 1_000_000

# The README table.
REV = {2021: 365_817, 2022: 394_328, 2023: 383_285, 2024: 391_035}
COGS = {2021: 212_981, 2022: 223_546, 2023: 214_137, 2024: 210_352}
OPINC = {2021: 108_949, 2022: 119_437, 2023: 114_301, 2024: 123_216}
PRETAX = {2021: 109_207, 2022: 119_103, 2023: 113_736, 2024: 123_485}
TAX = {2021: 14_527, 2022: 19_300, 2023: 16_741, 2024: 29_749}
OCF = {2021: 104_038, 2022: 122_151, 2023: 110_543, 2024: 118_254}
CAPEX = {2021: 11_085, 2022: 10_708, 2023: 10_959, 2024: 9_447}
DA = {2021: 11_284, 2022: 11_104, 2023: 11_519, 2024: 11_445}
SHARES = {2021: 16_864_919, 2022: 16_325_819, 2023: 15_812_547, 2024: 15_408_095}
CASH = {2022: 23_646, 2023: 29_965, 2024: 29_943}
MS = {2022: 24_658, 2023: 31_590, 2024: 35_228}
CP = {2022: 9_982, 2023: 5_985, 2024: 9_967}
TDC = {2022: 11_128, 2023: 9_822, 2024: 10_912}
TDNC = {2022: 98_959, 2023: 95_281, 2024: 85_750}
EQ = {2022: 50_672, 2023: 62_146, 2024: 56_950}


def roic(y):
    tax = TAX[y] / PRETAX[y]
    ic = EQ[y] + TDNC[y] + TDC[y] + CP[y]
    return OPINC[y] * (1 - tax) / ic


def gm(y):
    return (REV[y] - COGS[y]) / REV[y]


def fcfm(y):
    return (OCF[y] - CAPEX[y]) / REV[y]


@pytest.fixture(scope="module")
def panel():
    return df.load_panel(["AAPL", "MSFT"], sources=SOURCES, cik_map={"AAPL": "320193"}, on_date=date(2024, 11, 1))


@pytest.fixture(scope="module")
def facts(panel):
    cik, fs = panel.facts_for("AAPL")
    assert cik == AAPL
    return fs


# -- fiscal years, point in time -------------------------------------------------


def test_four_fiscal_years_as_known_after_the_second_10k(facts):
    years = df.fiscal_years(facts, AAPL, on_date=date(2024, 11, 1))
    assert [y.label for y in years] == ["FY2021", "FY2022", "FY2023", "FY2024"]
    assert [y.end for y in years] == [date(2021, 9, 25), date(2022, 9, 24), date(2023, 9, 30), date(2024, 9, 28)]
    # FY2021 exists only in the first 10-K; the later years were re-filed as comparatives.
    assert years[0].filed == date(2023, 11, 3)
    assert years[1].filed == date(2024, 11, 1) and years[1].first_filed == date(2023, 11, 3)
    assert years[3].first_filed == years[3].filed == date(2024, 11, 1)


def test_nothing_is_known_before_the_first_filing_and_three_years_on_its_day(facts):
    assert df.fiscal_years(facts, AAPL, on_date=date(2023, 10, 31)) == []
    on_day = df.fiscal_years(facts, AAPL, on_date=date(2023, 11, 3))
    assert [y.label for y in on_day] == ["FY2021", "FY2022", "FY2023"]
    assert all(y.known_on == date(2023, 11, 3) for y in on_day)


def test_a_year_without_a_balance_sheet_has_no_roic_rather_than_a_guess(facts):
    years = {y.label: y for y in df.fiscal_years(facts, AAPL, on_date=date(2024, 11, 1))}
    fy21 = years["FY2021"]
    assert fy21.equity is None and fy21.roic is None and fy21.net_debt is None
    assert fy21.gross_margin == pytest.approx(gm(2021))
    assert fy21.fcf_margin == pytest.approx(fcfm(2021))
    assert fy21.ebitda == (OPINC[2021] + DA[2021]) * M


def test_each_line_matches_the_filing(facts):
    years = {y.year: y for y in df.fiscal_years(facts, AAPL, on_date=date(2024, 11, 1))}
    for y in (2022, 2023, 2024):
        fy = years[y]
        assert fy.revenue == REV[y] * M
        assert fy.gross_margin == pytest.approx(gm(y))
        assert fy.roic == pytest.approx(roic(y))
        assert fy.fcf == (OCF[y] - CAPEX[y]) * M
        assert fy.total_debt == (TDNC[y] + TDC[y] + CP[y]) * M
        assert fy.net_debt == (TDNC[y] + TDC[y] + CP[y] - CASH[y] - MS[y]) * M
        assert fy.diluted_shares == SHARES[y] * 1000


def test_the_window_caps_at_the_requested_years(facts):
    years = df.fiscal_years(facts, AAPL, on_date=date(2024, 11, 1), window_years=2)
    assert [y.label for y in years] == ["FY2023", "FY2024"]


# -- aggregates on the screen's definitions --------------------------------------


def test_aggregates_are_the_screens_arithmetic(facts):
    years = df.fiscal_years(facts, AAPL, on_date=date(2024, 11, 1))
    f = df.aggregate(years)
    assert f.basis == "sec_dera"
    assert f.n_years == 4 and f.fiscal_years == ["2021", "2022", "2023", "2024"]
    rs = [roic(y) for y in (2022, 2023, 2024)]
    assert f.roic_avg == pytest.approx(statistics.fmean(rs))
    assert f.roic_latest == pytest.approx(rs[-1])
    assert f.roic_trend == pytest.approx(rs[-1] - rs[0])
    assert f.fcf_margin_avg == pytest.approx(statistics.fmean(fcfm(y) for y in REV))
    assert (f.fcf_positive_years, f.fcf_years) == (4.0, 4.0)
    assert f.rev_cagr == pytest.approx((REV[2024] / REV[2021]) ** (1 / 3) - 1)
    gms = [gm(y) for y in REV]
    assert f.gm_avg == pytest.approx(statistics.fmean(gms))
    assert f.gm_std == pytest.approx(statistics.pstdev(gms))
    assert f.share_change == pytest.approx(SHARES[2024] / SHARES[2021] - 1)
    nd = (TDNC[2024] + TDC[2024] + CP[2024] - CASH[2024] - MS[2024]) * M
    assert f.net_debt == nd and f.net_cash is False
    assert f.nd_to_ebitda == pytest.approx(nd / ((OPINC[2024] + DA[2024]) * M))


def test_net_cash_is_flagged_and_the_ratio_withheld():
    y = df.FiscalYear("FY2020", date(2020, 12, 31), date(2021, 2, 1), date(2021, 2, 1), date(2021, 3, 1),
                      revenue=100.0, operating_income=20.0, cash=50.0, long_term_debt=10.0,
                      depreciation_amortization=5.0, equity=40.0)
    f = df.aggregate([y, y])
    assert f.net_cash is True and f.nd_to_ebitda is None and f.net_debt == -40.0


def test_cagr_uses_elapsed_fiscal_years_not_point_count():
    a = df.FiscalYear("FY2019", date(2019, 12, 31), date(2020, 2, 1), date(2020, 2, 1), date(2024, 1, 1), revenue=100.0)
    b = df.FiscalYear("FY2023", date(2023, 12, 31), date(2024, 2, 1), date(2024, 2, 1), date(2024, 1, 1), revenue=200.0)
    f = df.aggregate([a, b])
    assert f.rev_cagr == pytest.approx(2 ** (1 / 4) - 1), "two points four years apart is four intervals"


def test_a_filer_with_no_gross_profit_line_derives_it_and_none_stays_none():
    y = df.FiscalYear("FY2020", date(2020, 12, 31), date(2021, 2, 1), date(2021, 2, 1), date(2021, 3, 1),
                      revenue=100.0, cost_of_revenue=60.0)
    assert y.gross_profit == 40.0
    z = df.FiscalYear("FY2020", date(2020, 12, 31), date(2021, 2, 1), date(2021, 2, 1), date(2021, 3, 1), revenue=100.0)
    assert z.gross_profit is None and z.gross_margin is None and z.fcf is None


# -- reconciliation -----------------------------------------------------------------


def yahoo_record(**fund):
    base = dict(n_years=3, fiscal_years=["2022", "2023", "2024"], roic_avg=statistics.fmean(roic(y) for y in (2022, 2023, 2024)),
                roic_trend=roic(2024) - roic(2022), roic_latest=roic(2024),
                fcf_margin_avg=statistics.fmean(fcfm(y) for y in (2022, 2023, 2024)), fcf_positive_years=3.0, fcf_years=3.0,
                rev_cagr=(REV[2024] / REV[2022]) ** 0.5 - 1, nd_to_ebitda=0.31, net_cash=False,
                share_change=SHARES[2024] / SHARES[2022] - 1,
                gm_avg=statistics.fmean(gm(y) for y in (2022, 2023, 2024)), gm_std=statistics.pstdev(gm(y) for y in (2022, 2023, 2024)))
    base.update(fund)
    return TickerRecord(ticker="AAPL", sector="Technology", pulled="2024-11-01", fundamentals=Fundamentals(**base),
                        valuation=Valuation(forward_pe=30.0))


def test_same_window_agrees_when_yahoo_matches_the_filings(facts):
    rec = yahoo_record()
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1), yahoo_years=rec.fundamentals.fiscal_years)
    rc = df.reconcile(rec, lf)
    assert rc.same_window_complete
    assert rc.sec_same_window_years == ["2022", "2023", "2024"]
    assert rc.sec_long_window_years == ["2021", "2022", "2023", "2024"]
    assert rc.disagreements == [] and rc.net_cash_agrees is True
    assert len(rc.compared) == len(df.TOLERANCE)


def test_a_restated_yahoo_figure_is_a_disagreement_not_an_error(facts):
    rec = yahoo_record(rev_cagr=0.10, share_change=0.0)
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1), yahoo_years=rec.fundamentals.fiscal_years)
    rc = df.reconcile(rec, lf)
    assert [d.field for d in rc.disagreements] == ["rev_cagr", "share_change"]
    d = {x.field: x for x in rc.differences}["rev_cagr"]
    assert d.yahoo == 0.10 and d.sec_same_window == pytest.approx((REV[2024] / REV[2022]) ** 0.5 - 1)
    assert d.sec_long_window == pytest.approx((REV[2024] / REV[2021]) ** (1 / 3) - 1)
    blob = rc.to_dict()
    assert blob["n_disagree"] == 2 and blob["disagree_on"] == ["rev_cagr", "share_change"]


def test_no_verdict_when_the_filings_do_not_cover_yahoos_window(facts):
    """Three SEC years against four Yahoo years is a different window, not a disagreement."""
    rec = yahoo_record(fiscal_years=["2022", "2023", "2024", "2025"], n_years=4, rev_cagr=0.5)
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1), yahoo_years=rec.fundamentals.fiscal_years)
    rc = df.reconcile(rec, lf)
    assert not rc.same_window_complete
    assert rc.disagreements == [] and rc.compared == [] and rc.net_cash_agrees is None
    assert all(d.gap is not None for d in rc.differences if d.field == "rev_cagr"), "the numbers are still shown"


def test_a_missing_side_gives_no_verdict(facts):
    rec = yahoo_record(gm_avg=None, gm_std=None)
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1), yahoo_years=rec.fundamentals.fiscal_years)
    rc = df.reconcile(rec, lf)
    by = {d.field: d for d in rc.differences}
    assert by["gm_avg"].agrees is None and by["gm_avg"].sec_same_window is not None


# -- merging into the record ----------------------------------------------------------


def test_merge_takes_the_sec_figures_and_records_the_basis(facts):
    rec = yahoo_record(roic_avg=0.99)
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1))
    merged = df.merge_record(rec, lf)
    f = merged.fundamentals
    assert f.basis == "sec_dera"
    assert f.roic_avg == pytest.approx(lf.long.roic_avg) and f.roic_avg != 0.99
    assert f.n_years == 4 and f.fiscal_years == ["2021", "2022", "2023", "2024"]
    assert set(f.basis_by_field.values()) == {"sec_dera"}
    assert merged.valuation is rec.valuation, "only the fundamentals change"


def test_merge_falls_back_group_by_group_and_says_mixed(facts):
    rec = yahoo_record(gm_avg=0.5, gm_std=0.01)
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1))
    # Blank the margins on the SEC side to force one group back to Yahoo.
    blank = df.LongFundamentals(lf.ticker, lf.cik, lf.on_date, lf.years,
                                Fundamentals(**{**lf.long.__dict__, "gm_avg": None, "gm_std": None}),
                                lf.same_window, lf.same_window_labels)
    merged = df.merge_record(rec, blank)
    f = merged.fundamentals
    assert f.basis == "mixed"
    assert f.gm_avg == 0.5 and f.basis_by_field["gm_avg"] == "yfinance" and f.basis_by_field["gm_std"] == "yfinance"
    assert f.basis_by_field["roic_avg"] == "sec_dera" and f.roic_avg == pytest.approx(lf.long.roic_avg)


def test_too_few_filed_years_leaves_yahoo_alone(facts):
    rec = yahoo_record()
    lf = df.fundamentals_for(facts, "AAPL", AAPL, on_date=date(2024, 11, 1), window_years=2)
    assert lf.long.n_years == 2
    assert df.merge_record(rec, lf) is rec


# -- the panel and apply --------------------------------------------------------------


def test_panel_says_what_it_loaded_and_what_it_could_not_map(panel):
    assert panel.has_data and panel.quarters == ["2023q4", "2024q4"]
    assert panel.unmapped == ["MSFT"] and panel.source == "fixture" and panel.live is False
    assert "2 quarters loaded" in panel.why


def test_an_empty_cache_is_a_not_run_panel(tmp_path):
    p = df.load_panel(["AAPL"], cache_dir=tmp_path / "nothing", on_date=date(2026, 9, 4))
    assert not p.has_data and p.source == "none" and p.live is None
    assert "Nothing has been downloaded" in p.why and "fetch_dera.py" in p.why
    universe = {"AAPL": yahoo_record()}
    out, report = df.apply(universe, p)
    assert out["AAPL"] is universe["AAPL"]
    assert report.status == "NOT RUN" and not report.in_use
    blk = df.status_block(report)
    assert blk["status"] == "NOT RUN" and blk["names_rebased"] == 0
    assert "Nothing on the pages is estimated in its place" in blk["why"]
    assert blk["command"].startswith("SEC_USER_AGENT=")


def test_a_cache_without_the_ticker_map_says_so(tmp_path, monkeypatch):
    (tmp_path / "2023q4.zip").write_bytes(b"PK")
    monkeypatch.setattr(df.paths, "EDGAR_CACHE", tmp_path / "no-edgar")
    p = df.load_panel(["AAPL"], cache_dir=tmp_path, on_date=date(2026, 9, 4))
    assert not p.has_data and "ticker map is not cached" in p.why


def test_apply_rebases_the_mapped_name_and_leaves_the_rest(panel):
    universe = {"AAPL": yahoo_record(), "MSFT": yahoo_record()}
    universe["MSFT"] = TickerRecord(**{**universe["MSFT"].__dict__, "ticker": "MSFT"})
    out, report = df.apply(universe, panel)
    assert report.in_use and set(report.applied) == {"AAPL"}
    assert out["AAPL"].fundamentals.basis == "sec_dera" and out["MSFT"].fundamentals.basis == "yfinance"
    assert report.basis_by_ticker == {"AAPL": "sec_dera"}
    blk = df.status_block(report)
    assert blk["status"] == "IN USE" and blk["names_rebased"] == 1 and blk["tickers_unmapped"] == 1
    assert blk["ever_made_a_real_request"] is False
    assert blk["basis_counts"] == {"sec_dera": 1}


def test_the_score_reads_the_rebased_inputs(panel):
    """The whole point: the same weights, a different input, a different number."""
    others = [TickerRecord(ticker=f"T{i:02d}", sector="Technology",
                           fundamentals=Fundamentals(roic_avg=0.05 + i * 0.02, fcf_margin_avg=0.1, fcf_years=3.0,
                                                     fcf_positive_years=3.0, rev_cagr=-0.05 + i * 0.01, gm_std=0.02,
                                                     nd_to_ebitda=1.0, share_change=0.0),
                           valuation=Valuation(pe_vs_median=0.0, ev_vs_median=0.0, n_hist_years=4))
              for i in range(20)]
    # A deliberately bad Yahoo row: every group the filings can replace is at the bottom.
    yahoo = yahoo_record(roic_avg=0.01, roic_trend=-0.2, rev_cagr=-0.10, fcf_margin_avg=-0.5, fcf_positive_years=0.0,
                         share_change=0.5, gm_std=0.5, nd_to_ebitda=6.0)  # a deliberately bad Yahoo row
    before = score.score_universe(others + [yahoo])["AAPL"]
    rebased, _ = df.apply({"AAPL": yahoo}, panel)
    after = score.score_universe(others + [rebased["AAPL"]])["AAPL"]
    assert after.score > before.score
    assert after.z["roic"] > before.z["roic"] and after.z["growth"] > before.z["growth"]
    assert after.coverage >= before.coverage


def test_basis_comparison_reports_sensitivity_not_returns(panel):
    others = [TickerRecord(ticker=f"T{i:02d}", sector="Technology",
                           fundamentals=Fundamentals(roic_avg=0.05 + i * 0.02, fcf_margin_avg=0.1, fcf_years=3.0,
                                                     fcf_positive_years=3.0, rev_cagr=-0.05 + i * 0.01, gm_std=0.02,
                                                     nd_to_ebitda=1.0, share_change=0.0),
                           valuation=Valuation(pe_vs_median=0.0, ev_vs_median=0.0, n_hist_years=4))
              for i in range(20)]
    # A deliberately bad Yahoo row: every group the filings can replace is at the bottom.
    yahoo = yahoo_record(roic_avg=0.01, roic_trend=-0.2, rev_cagr=-0.10, fcf_margin_avg=-0.5, fcf_positive_years=0.0,
                         share_change=0.5, gm_std=0.5, nd_to_ebitda=6.0)
    rebased, _ = df.apply({"AAPL": yahoo}, panel)
    cmp = df.basis_comparison(others + [rebased["AAPL"]], others + [yahoo], variant="quality_value")
    assert cmp["n"] == 21 and cmp["rank_correlation"] is not None
    assert cmp["names_moved_more_than_10_points"] >= 1
    assert cmp["largest_moves"][0]["ticker"] == "AAPL"
    assert "not whether either ordering predicts anything" in cmp["note"]


# -- the extraction cache and provenance --------------------------------------------


def test_extracted_facts_are_cached_and_read_back_identically(tmp_path):
    import io
    import zipfile

    for label, d in SOURCES:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted(d.iterdir()):
                zf.write(f, f.name)
        (tmp_path / f"{label}.zip").write_bytes(buf.getvalue())
    first = df.load_panel(["AAPL"], cache_dir=tmp_path, cik_map={"AAPL": "320193"}, on_date=date(2024, 11, 1))
    extracted = list((tmp_path / "extract").glob("*.json"))
    assert len(extracted) == 1
    second = df.load_panel(["AAPL"], cache_dir=tmp_path, cik_map={"AAPL": "320193"}, on_date=date(2024, 11, 1))
    assert first.facts_by_cik == second.facts_by_cik
    assert first.live is None, "zips with no provenance sidecar: unknown, not assumed live"


def test_provenance_sidecar_is_written_by_the_client_and_read_back(tmp_path):
    from an.http import FixtureTransport

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted((FULL / "2023q4").iterdir()):
            zf.write(f, f.name)
    t = FixtureTransport().add(dera.quarter_url(2023, 4), buf.getvalue())
    client = dera.DeraClient(transport=t, cache_dir=tmp_path, user_agent="test test@example.com")
    client.fetch(2023, 4)
    meta = json.loads(dera.meta_path(tmp_path / "2023q4.zip").read_text())
    assert meta["transport"] == "FixtureTransport" and meta["live"] is False
    prov = dera.provenance(tmp_path)
    assert prov["ever_made_a_real_request"] is False
    assert prov["quarters"]["2023q4"]["live"] is False
    assert dera.provenance(tmp_path / "empty")["ever_made_a_real_request"] is False


# -- the CLI -------------------------------------------------------------------------


def test_basis_report_on_the_fixture_prints_the_shape():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_dera.py"), "--basis-report", "--fixture", "AAPL"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("# FIXTURE")
    assert "status: IN USE" in r.stdout and "AAPL  CIK 0000320193  4 fiscal years FY2021..FY2024" in r.stdout
    assert "windows differ" in r.stdout, "the committed CSV runs to FY2025, which the miniature does not reach"


def test_basis_report_on_an_empty_cache_says_not_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_ROOT", str(ROOT))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_dera.py"), "--basis-report", "AAPL"],
                       capture_output=True, text=True, cwd=str(ROOT),
                       env={**__import__("os").environ, "DERA_CACHE_OVERRIDE": str(tmp_path)})
    # The real cache is empty in this checkout; the report must say so and exit non-zero.
    assert "ever made a real request" in r.stdout
    assert "status: NOT RUN" in r.stdout and r.returncode == 1


# -- the committed build state ----------------------------------------------------------


def test_the_committed_universe_is_still_on_the_yahoo_basis():
    """No quarter has been downloaded in this checkout, so the score must not claim otherwise."""
    if list(df.paths.DERA_CACHE.glob("*.zip")):
        pytest.skip("a DERA cache exists on this machine")
    universe, report = df.universe_with_basis()
    assert report.status == "NOT RUN"
    assert all(r.fundamentals.basis == "yfinance" for r in universe.values())
    assert universe["AAPL"].fundamentals == local.load_universe()["AAPL"].fundamentals
