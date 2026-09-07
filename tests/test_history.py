"""The history layer: parsers, the split audit, the cross-check, membership and the fixture.

Everything here runs on the committed 12-name slice or on hand-built frames. Nothing
touches the network (conftest makes urlopen raise), and nothing reads data/cache/.
"""
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from an import history

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "history"


def _lean_zip(symbol: str, rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{symbol}.csv", "\n".join(rows) + "\n")
    return buf.getvalue()


def test_no_io_at_import():
    src = (ROOT / "scripts" / "an" / "history.py").read_text(encoding="utf-8")
    head = src.split("def sha256_of")[0]
    assert "read_text(" not in head and "urlopen" not in src


def test_lean_zip_unscales_prices_and_parses_dates():
    df = history.load_lean_zip(_lean_zip("spy", ["19980102 00:00,973100,975300,965300,973600,2150000",
                                                 "19980105 00:00,978400,984400,967800,977500,4030800"]), "spy")
    assert list(df.index) == [pd.Timestamp("1998-01-02"), pd.Timestamp("1998-01-05")]
    assert df["close"].iloc[0] == pytest.approx(97.36)
    assert df["volume"].iloc[1] == 4030800


def test_lean_factor_split_only_adjustment():
    raw = history.load_lean_zip(_lean_zip("x", ["20140606 00:00,6440000,6450000,6400000,6450000,100",
                                               "20140609 00:00,930000,940000,920000,935000,700"]), "x")
    fac = history.load_lean_factor_file("20140606,0.9,0.142857,645\n20501231,1,1,0\n")
    adj = history.apply_lean_factors(raw, fac, dividends=False)
    # the pre-split close is brought onto the post-split basis; the price factor is not applied
    assert adj["close"].iloc[0] == pytest.approx(645.0 * 0.142857, rel=1e-4)
    assert adj["close"].iloc[1] == pytest.approx(93.5)
    with_div = history.apply_lean_factors(raw, fac, dividends=True)
    assert with_div["close"].iloc[0] == pytest.approx(645.0 * 0.142857 * 0.9, rel=1e-4)


def test_plotly_long_pivots_to_wide_and_uppercases():
    text = ("date,open,high,low,close,volume,Name\n"
            "2013-02-08,1,2,0.5,1.5,100,aal\n2013-02-11,1,2,0.5,1.6,110,aal\n"
            "2013-02-08,10,11,9,10.5,5,MSFT\n")
    p = history.load_plotly_long(text)
    assert list(p.close.columns) == ["AAL", "MSFT"]
    assert p.close.loc["2013-02-11", "AAL"] == 1.6
    assert pd.isna(p.close.loc["2013-02-11", "MSFT"])
    assert list(p.benchmarks.columns) == ["SPY", "QQQ"]


def _panel_with_split(adjusted: bool):
    idx = pd.bdate_range("2014-05-01", periods=60)
    close = pd.Series(100.0, index=idx)
    vol = pd.Series(1000.0, index=idx)
    split_on = pd.Timestamp("2014-06-09")
    if not adjusted:
        close[idx >= split_on] = 100.0 / 7.0
        vol[idx >= split_on] = 7000.0
    return pd.DataFrame({"AAPL": close}), pd.DataFrame({"AAPL": vol})


def test_split_audit_flags_an_unadjusted_series_and_passes_an_adjusted_one():
    known = (("AAPL", "2014-06-09", 7.0),)
    c, v = _panel_with_split(adjusted=False)
    f = history.audit_splits(c, v, known)[0]
    assert f.verdict == "unadjusted" and f.ratio_seen == pytest.approx(7.0, rel=1e-3)
    assert history.dataset_adjustment_verdict([f]) == "unadjusted"
    c, v = _panel_with_split(adjusted=True)
    f = history.audit_splits(c, v, known)[0]
    assert f.verdict == "adjusted"


def test_adjust_for_splits_flattens_the_ratio_and_records_it():
    known = (("AAPL", "2014-06-09", 7.0),)
    c, v = _panel_with_split(adjusted=False)
    panel = history.HistoryPanel(c, v, pd.DataFrame(index=c.index))
    findings = history.audit_splits(c, v, known)
    fixed = history.adjust_for_splits(panel, findings)
    assert fixed.close["AAPL"].round(6).nunique() == 1
    assert fixed.adjustments and fixed.adjustments[0].applied
    # the original is untouched
    assert panel.close["AAPL"].iloc[0] == 100.0


def test_split_audit_on_the_committed_fixture_finds_nflx_2015_adjusted():
    panel, _, _ = history.load_fixture(FIX)
    findings = history.audit_splits(panel.close, panel.volume, (("NFLX", "2015-07-15", 7.0),))
    assert findings[0].verdict == "adjusted"


def test_sweep_separates_events_from_artefacts_by_volume():
    idx = pd.bdate_range("2016-01-04", periods=40)
    close = pd.DataFrame({"REAL": 100.0, "FAKE": 100.0}, index=idx)
    vol = pd.DataFrame({"REAL": 1000.0, "FAKE": 1000.0}, index=idx)
    close.iloc[20:, 0] = 200.0
    close.iloc[20:, 1] = 200.0
    vol.iloc[20, 0] = 15000.0
    findings = history.audit_splits(close, vol, ())
    kinds = {f.ticker: f for f in findings}
    assert kinds["REAL"].verdict == "candidate" and kinds["FAKE"].verdict == "candidate"
    assert history.suspect_tickers(findings) == ["FAKE"]


def test_cross_check_compares_returns_not_levels():
    idx = pd.bdate_range("2015-01-01", periods=60)
    a = pd.Series(range(100, 160), index=idx, dtype=float)
    same_returns_scaled = a * 4.0
    ok = history.cross_check(a, same_returns_scaled, ticker="X", a_name="a", b_name="b")
    assert ok.passed and ok.level_ratio == pytest.approx(0.25)
    b = a.copy()
    b.iloc[30:] = b.iloc[30:] / 2.0   # a split one side did not adjust
    bad = history.cross_check(a, b, ticker="X", a_name="a", b_name="b")
    assert not bad.passed and bad.max_abs_rel_diff > 0.4


def test_membership_on_date_and_mask():
    m = history.load_membership("ticker,start_date,end_date\nA,2000-01-01,\nB,2010-05-01,2016-03-01\nB,2018-01-01,\n")
    assert m.on("2015-06-01") == frozenset({"A", "B"})
    assert m.on("2017-01-01") == frozenset({"A"})
    idx = pd.bdate_range("2016-02-25", periods=6)
    mask = m.mask(idx, ["A", "B", "ZZZ"])
    assert mask["A"].all() and not mask["ZZZ"].any()
    assert mask.loc["2016-02-29", "B"] and not mask.loc["2016-03-02", "B"]


def test_membership_refuses_an_unexpected_shape():
    with pytest.raises(ValueError):
        history.load_membership("symbol,from\nA,2000-01-01\n")


def test_audit_gate_fails_on_a_benchmark_split_inside_the_window():
    panel, mem, _ = history.load_fixture(FIX)
    fac = history.load_lean_factor_file("20160101,1,0.5,100\n20501231,1,1,0\n")
    r = history.audit(panel, membership=mem, benchmark_factors={"SPY": fac})
    assert r.benchmark_splits_in_window["SPY"] == 1 and r.gate == "FAIL"
    clean = history.load_lean_factor_file("20501231,1,1,0\n")
    r2 = history.audit(panel, membership=mem, benchmark_factors={"SPY": clean})
    assert r2.benchmark_splits_in_window["SPY"] == 0
    assert r2.membership_first is not None and r2.membership_first == len(panel.close.columns)


def test_fixture_loads_and_matches_its_manifest():
    panel, mem, meta = history.load_fixture(FIX)
    assert panel.sessions == meta["sessions"] and panel.tickers == meta["tickers"]
    assert panel.first == meta["start"] and panel.last == meta["end"]
    assert list(panel.benchmarks.columns) == ["SPY", "QQQ"]
    assert panel.benchmarks["SPY"].notna().sum() == panel.sessions
    assert mem is not None and "NFLX" in mem.on("2016-01-04")
    assert "measures nothing" in meta["note"]


def test_fixture_is_reproducible_from_itself():
    """Cutting the fixture from the fixture must give back the same bytes: sorted, rounded, no clock."""
    panel, mem, meta = history.load_fixture(FIX)
    files = history.make_fixture(panel, mem, tickers=meta["tickers"], start=meta["start"], end=meta["end"],
                                 parent=meta["cut_from"])
    for name in ("close.csv", "volume.csv", "benchmarks.csv", "membership.csv"):
        assert files[name] == (FIX / name).read_text(encoding="utf-8"), name
    assert json.loads(files["manifest.json"]) == meta


def test_write_and_read_panel_round_trip(tmp_path):
    panel, _, _ = history.load_fixture(FIX)
    history.write_panel(panel, tmp_path)
    back = history.read_panel(tmp_path)
    pd.testing.assert_frame_equal(back.close, panel.close)
    pd.testing.assert_frame_equal(back.benchmarks, panel.benchmarks)
    assert history.load_cached(tmp_path) is None   # no manifest, so nothing is "cached"


def test_sources_are_all_public_github_raw_urls():
    for s in history.SOURCES:
        assert s.url.startswith("https://raw.githubusercontent.com/"), s.key
    assert {s.role for s in history.SOURCES} == {"universe", "benchmark", "crosscheck", "membership"}
