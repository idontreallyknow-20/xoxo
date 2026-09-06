"""an.dera against two hand-built quarter miniatures. See tests/fixtures/dera/README.md.

The Apple figures are the ones the FY2023 10-K and Q3 FY2023 10-Q report; the
test reconstructs them from num.txt, including the fourth quarter the filings do
not contain.
"""
import io
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

from an import dera
from an.http import DryRunTransport, FixtureTransport
from an.store import FetchError, Offline

ROOT = Path(__file__).resolve().parent.parent
AAPL = "0000320193"
XMPL = "0009999999"
Q3 = ROOT / "tests" / "fixtures" / "dera" / "2023q3"
Q4 = ROOT / "tests" / "fixtures" / "dera" / "2023q4"


def zip_of(directory: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(directory.iterdir()):
            zf.write(f, f.name)
    return buf.getvalue()


@pytest.fixture
def both():
    return dera.load_quarters([("2023q3", Q3), ("2023q4", Q4)])


# -- reading -------------------------------------------------------------------


def test_labels_and_urls():
    assert dera.quarter_url(2023, 4) == "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2023q4.zip"
    assert dera.parse_quarter_label("2024Q1") == (2024, 1)
    with pytest.raises(ValueError):
        dera.parse_quarter_label("2024-1")
    with pytest.raises(ValueError):
        dera.quarter_label(2024, 5)


def test_submissions_are_joined_onto_every_fact():
    q = dera.load_quarter(Q4, label="2023q4")
    assert set(q.submissions) == {"0000320193-23-000106", "0009999999-23-000002"}
    s = q.submissions["0000320193-23-000106"]
    assert (s.cik, s.form, s.period, s.fiscal_year, s.fiscal_period) == (AAPL, "10-K", date(2023, 9, 30), 2023, "FY")
    assert s.filed == date(2023, 11, 3) and s.is_annual_report and not s.superseded
    rev = [f for f in q.facts if f.cik == AAPL and f.tag.startswith("RevenueFromContract") and f.qtrs == 4]
    assert all(f.filed == date(2023, 11, 3) and f.form == "10-K" and f.adsh == s.adsh for f in rev)
    assert q.rows_read == 11 and q.rows_kept == 11


def test_a_zip_and_a_directory_load_identically(tmp_path):
    from_dir = dera.load_quarter(Q3)
    z = tmp_path / "2023q3.zip"
    z.write_bytes(zip_of(Q3))
    from_zip = dera.load_quarter(z)
    from_bytes = dera.load_quarter(z.read_bytes())
    assert from_dir.facts == from_zip.facts == from_bytes.facts


def test_coregistrant_rows_are_dropped_unless_asked_for():
    q = dera.load_quarter(Q3, ciks=[XMPL], tags=["Revenues"])
    values = sorted(f.value for f in q.facts if f.ddate == date(2023, 5, 31))
    assert values == [1_000_000.0], "the subsidiary's 250,000 must not appear"
    q2 = dera.load_quarter(Q3, ciks=[XMPL], tags=["Revenues"], include_coreg=True)
    assert sorted(f.value for f in q2.facts if f.ddate == date(2023, 5, 31)) == [250_000.0, 1_000_000.0]
    assert [f.coreg for f in q2.facts if f.value == 250_000.0] == ["FictionalSubsidiaryLLC"]


def test_a_footnote_only_row_has_no_value_and_is_skipped():
    q = dera.load_quarter(Q3)
    assert not any(f.tag == "CommitmentsAndContingencies" for f in q.facts)
    assert (q.rows_read, q.rows_kept) == (14, 12), "one coreg row and one blank value dropped"


def test_filters_by_cik_and_form_and_tag():
    q = dera.load_quarter(Q3, ciks=[320193], forms=["10-Q"], tags=["Assets"])
    assert {f.cik for f in q.facts} == {AAPL}
    assert {f.tag for f in q.facts} == {"Assets"}
    assert all(f.is_instant for f in q.facts)
    assert dera.load_quarter(Q3, ciks=[320193], forms=["10-K"]).facts == []


def test_a_changed_header_is_loud(tmp_path):
    d = tmp_path / "q"
    d.mkdir()
    (d / "sub.txt").write_text((Q4 / "sub.txt").read_text())
    (d / "num.txt").write_text("adsh\ttag\tvalue\n0000320193-23-000106\tAssets\t1\n")
    with pytest.raises(FetchError, match="lacks columns"):
        dera.load_quarter(d)
    with pytest.raises(FetchError, match="not in the archive"):
        dera.load_quarter(b"PK\x05\x06" + b"\x00" * 18)


def test_the_notes_variant_dimension_column_is_respected(tmp_path):
    """The Financial Statement *and Notes* sets add dimh; only the undimensioned row is the company."""
    d = tmp_path / "q"
    d.mkdir()
    (d / "sub.txt").write_text((Q4 / "sub.txt").read_text())
    (d / "num.txt").write_text(
        "adsh\ttag\tversion\tcoreg\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\tfootnote\n"
        "0000320193-23-000106\tRevenueFromContractWithCustomerExcludingAssessedTax\tus-gaap/2023\t\t20230930\t4\tUSD\t0x00000000\t0\t383285000000\t\n"
        "0000320193-23-000106\tRevenueFromContractWithCustomerExcludingAssessedTax\tus-gaap/2023\t\t20230930\t4\tUSD\t0x1a2b3c4d\t0\t200583000000\t\n"
    )
    q = dera.load_quarter(d, ciks=[AAPL])
    assert [f.value for f in q.facts] == [383_285_000_000.0], "the iPhone segment row is a dimension, not the total"


# -- reconstructing known figures ------------------------------------------------


def test_reconstructs_apple_fy2023_revenue_from_the_10k():
    q = dera.load_quarter(Q4, ciks=[AAPL])
    facts = dera.metric_facts(q.facts, AAPL, "revenue")
    fy23 = dera.first_reported(facts, ddate=date(2023, 9, 30), qtrs=4)
    assert fy23 is not None
    assert fy23.value == 383_285_000_000.0
    assert fy23.unit == "USD" and fy23.filed == date(2023, 11, 3) and fy23.form == "10-K"
    assert fy23.taxonomy == "us-gaap" and not fy23.derived


def test_annual_series_is_the_three_years_the_10k_shows(both):
    facts = dera.metric_facts(both.facts, AAPL, "revenue")
    series = dera.annual_series(facts)
    assert [(f.ddate.year, f.value / 1e9) for f in series] == [(2021, 365.817), (2022, 394.328), (2023, 383.285)]
    assert all(f.covers_a_year for f in series)


def test_metric_lookup_follows_each_companys_own_tag(both):
    assert {f.tag for f in dera.metric_facts(both.facts, AAPL, "revenue")} == {"RevenueFromContractWithCustomerExcludingAssessedTax"}
    assert {f.tag for f in dera.metric_facts(both.facts, XMPL, "revenue")} == {"Revenues"}
    assert dera.metric_facts(both.facts, AAPL, "capex") == []
    assert dera.metric_facts(both.facts, "12345", "revenue") == []


def test_the_fourth_quarter_is_derived_and_stamped_with_the_10k_date(both):
    facts = dera.metric_facts(both.facts, AAPL, "revenue")
    q4s = sorted(dera.derive_fourth_quarters(facts), key=lambda f: f.ddate)
    # FY2023 from the 10-K and the 10-Q's nine months; FY2022 from the 10-K's comparative
    # year and the 10-Q's comparative nine months. Both are what Apple reported.
    assert [(f.ddate.isoformat(), round(f.value / 1e9, 3)) for f in q4s] == [("2022-09-24", 90.146), ("2023-09-30", 89.498)]
    for q4 in q4s:
        assert q4.qtrs == 1 and q4.derived and q4.fiscal_period == "Q4"
        assert q4.filed == date(2023, 11, 3), "knowable only once the 10-K was filed, not on the 10-Q's date"


def test_quarterly_series_has_q3_actual_and_q4_derived(both):
    facts = dera.metric_facts(both.facts, AAPL, "revenue")
    series = dera.quarterly_series(facts)
    got = [(f.ddate.isoformat(), round(f.value / 1e9, 3), f.derived) for f in series]
    assert got == [
        ("2022-06-25", 82.959, False),
        ("2022-09-24", 90.146, True),
        ("2023-07-01", 81.797, False),
        ("2023-09-30", 89.498, True),
    ]
    # Point in time: on 2023-10-31 the fourth quarter did not exist yet.
    before = dera.quarterly_series(facts, on_date=date(2023, 10, 31))
    assert [f.ddate.isoformat() for f in before] == ["2022-06-25", "2023-07-01"]


def test_no_fourth_quarter_without_a_nine_month_figure():
    q = dera.load_quarter(Q4, ciks=[AAPL])
    facts = dera.metric_facts(q.facts, AAPL, "revenue")
    assert dera.derive_fourth_quarters(facts) == [], "the 10-K alone cannot give Q4; a hole beats a guess"


# -- point in time ------------------------------------------------------------------


def test_as_known_on_ignores_a_later_restatement(both):
    facts = dera.metric_facts(both.facts, XMPL, "revenue")
    fy = date(2023, 5, 31)
    assert dera.as_known_on(facts, date(2023, 8, 1), ddate=fy, qtrs=4) is None, "not filed yet"
    assert dera.as_known_on(facts, date(2023, 9, 30), ddate=fy, qtrs=4).value == 1_000_000.0
    assert dera.as_known_on(facts, date(2023, 11, 20), ddate=fy, qtrs=4).value == 950_000.0, "the 10-K/A counts from its filing day"
    assert dera.first_reported(facts, ddate=fy, qtrs=4).value == 1_000_000.0
    assert dera.annual_series(facts)[-1].value == 1_000_000.0
    assert dera.annual_series(facts, on_date=date(2023, 12, 31))[-1].value == 950_000.0


def test_as_known_on_without_a_period_gives_the_latest_period_known(both):
    facts = dera.metric_facts(both.facts, AAPL, "revenue")
    # In October 2023 the newest annual figure the market had was FY2022, from a comparative? No:
    # nothing annual for Apple was filed in these two sets before the 10-K. Only quarters.
    assert dera.as_known_on(facts, date(2023, 10, 15), qtrs=4) is None
    assert dera.as_known_on(facts, date(2023, 10, 15), qtrs=1).ddate == date(2023, 7, 1)
    assert dera.as_known_on(facts, date(2023, 12, 1), qtrs=4).ddate == date(2023, 9, 30)


def test_instants_stay_out_of_duration_series(both):
    assets = dera.metric_facts(both.facts, AAPL, "assets")
    assert all(f.is_instant for f in assets)
    assert dera.annual_series(assets) == [] and dera.quarterly_series(assets) == []
    assert dera.as_known_on(assets, date(2023, 12, 1), qtrs=0).value == 352_583_000_000.0
    assert dera.as_known_on(assets, date(2023, 9, 1), qtrs=0).value == 335_038_000_000.0


def test_ytd_and_quarter_with_the_same_end_are_different_facts(both):
    facts = dera.metric_facts(both.facts, AAPL, "revenue")
    end = date(2023, 7, 1)
    assert dera.first_reported(facts, ddate=end, qtrs=1).value == 81_797_000_000.0
    assert dera.first_reported(facts, ddate=end, qtrs=3).value == 293_787_000_000.0


# -- fetching ------------------------------------------------------------------------


def test_plan_and_dry_run(tmp_path):
    seen = []
    c = dera.DeraClient(transport=DryRunTransport(sink=seen.append), cache_dir=tmp_path, user_agent="t me@x")
    plan = c.plan([(2023, 3), (2023, 4)])
    assert [p.url for p in plan] == [dera.quarter_url(2023, 3), dera.quarter_url(2023, 4)]
    assert plan[0].path == tmp_path / "2023q3.zip"
    with pytest.raises(Offline):
        c.fetch(2023, 3)
    assert seen[0].startswith("GET ") and any("User-Agent: t me@x" in s for s in seen)
    assert not (tmp_path / "2023q3.zip").exists()


def test_fetch_writes_the_zip_once_and_reads_it_back(tmp_path):
    t = FixtureTransport().add(dera.quarter_url(2023, 4), zip_of(Q4))
    c = dera.DeraClient(transport=t, cache_dir=tmp_path / "dera", user_agent="t me@x")
    p = c.fetch(2023, 4)
    assert p.exists() and p.name == "2023q4.zip"
    assert c.fetch(2023, 4) == p and len(t.calls) == 1, "the second call must come from disk"
    q = dera.load_quarter(p, ciks=[AAPL])
    assert dera.first_reported(dera.metric_facts(q.facts, AAPL, "revenue"), ddate=date(2023, 9, 30), qtrs=4).value == 383_285_000_000.0
    assert c.cached() == [p]


def test_a_non_zip_or_wrong_zip_is_never_kept(tmp_path):
    t = FixtureTransport().add(dera.quarter_url(2020, 1), b"<html>rate limited</html>")
    c = dera.DeraClient(transport=t, cache_dir=tmp_path, user_agent="t me@x")
    with pytest.raises(FetchError, match="did not return a zip"):
        c.fetch(2020, 1)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.htm", "x")
    t.add(dera.quarter_url(2020, 2), buf.getvalue())
    with pytest.raises(FetchError, match="without num.txt"):
        c.fetch(2020, 2)
    assert list(tmp_path.glob("*")) == []


# -- the script -----------------------------------------------------------------------


def run_script(*args):
    import os

    env = {**os.environ, "DESK_OFFLINE": "1", "SEC_USER_AGENT": "test me@example.com"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_dera.py"), *args],
                          capture_output=True, text=True, cwd=str(ROOT), env=env)


def test_script_dry_run_prints_the_urls():
    r = run_script("--dry-run", "2023q3", "2023q4")
    assert r.returncode == 0, r.stderr
    assert dera.quarter_url(2023, 3) in r.stdout and dera.quarter_url(2023, 4) in r.stdout
    assert "never" in r.stdout.lower()


def test_script_fixture_show_reconstructs_the_series():
    r = run_script("--fixture", "--show", "320193", "--metric", "revenue")
    assert r.returncode == 0, r.stderr
    assert "FIXTURE" in r.stdout
    assert "383,285,000,000" in r.stdout and "89,498,000,000" in r.stdout and "derived" in r.stdout


def test_script_refuses_a_bad_label():
    r = run_script("--dry-run", "2023-3")
    assert r.returncode == 2
