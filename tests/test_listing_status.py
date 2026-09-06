"""an.listing_status against a hand-built fixture whose answers were computed by hand.

The fixture is tests/fixtures/av_listing_{active,delisted}.csv, 22 rows, today
pinned to 2026-09-06. See av_listing_README.md for what is real and what is not.
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from an import listing_status as ls
from an.http import DryRunTransport, FixtureTransport, HttpTransport, redact
from an.store import Cache, FetchError, Offline, RateLimited

ROOT = Path(__file__).resolve().parent.parent
KEY = "AV-T3ST-KEY-DO-NOT-LOG-77"
TODAY = date(2026, 9, 6)
URL_ACTIVE = f"{ls.BASE_URL}?apikey={KEY}&function=LISTING_STATUS&state=active"
URL_DELISTED = f"{ls.BASE_URL}?apikey={KEY}&function=LISTING_STATUS&state=delisted"


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv(ls.KEY_ENV, KEY)
    return KEY


@pytest.fixture
def fixture_text(fixtures_dir):
    return {
        "active": (fixtures_dir / "av_listing_active.csv").read_text(),
        "delisted": (fixtures_dir / "av_listing_delisted.csv").read_text(),
    }


@pytest.fixture
def transport(fixture_text):
    t = FixtureTransport()
    t.add(URL_ACTIVE, fixture_text["active"])
    t.add(URL_DELISTED, fixture_text["delisted"])
    return t


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "av", default_ttl=ls.TTL)


@pytest.fixture
def listings(fixture_text):
    return ls.parse_listing_csv(fixture_text["active"]) + ls.parse_listing_csv(fixture_text["delisted"])


# -- parsing -----------------------------------------------------------------


def test_parses_dates_and_nulls(listings):
    by = {(l.symbol, l.status): l for l in listings}
    aapl = by[("AAPL", "Active")]
    assert aapl.ipo_date == date(1980, 12, 12)
    assert aapl.delisting_date is None
    assert aapl.exchange == "NASDAQ" and aapl.is_common_stock
    twtr = by[("TWTR", "Delisted")]
    assert twtr.delisting_date == date(2022, 11, 8)
    fakf = by[("FAKF", "Delisted")]
    assert fakf.ipo_date is None, "null listing date must be None, never a default"
    spy = by[("SPY", "Active")]
    assert not spy.is_common_stock


def test_a_bom_and_crlf_do_not_break_the_header(fixture_text):
    text = "\ufeff" + fixture_text["active"].replace("\n", "\r\n")
    assert len(ls.parse_listing_csv(text)) == 12


def test_unexpected_columns_are_loud():
    with pytest.raises(FetchError, match="unexpected columns"):
        ls.parse_listing_csv("symbol,name\nAAPL,Apple\n")


def test_a_quota_note_is_rate_limited_not_data(key):
    body = json.dumps({"Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."})
    with pytest.raises(RateLimited):
        ls.parse_listing_csv(body, url=URL_ACTIVE)


def test_an_error_body_is_a_bad_key_and_never_carries_the_key(key):
    body = json.dumps({"Error Message": f"Invalid API call for {URL_ACTIVE}"})
    with pytest.raises(ls.BadKey) as ei:
        ls.parse_listing_csv(body, url=URL_ACTIVE)
    assert KEY not in str(ei.value)
    assert KEY not in (ei.value.url or "")


# -- the client --------------------------------------------------------------


def test_key_comes_from_the_environment_only(monkeypatch):
    monkeypatch.delenv(ls.KEY_ENV, raising=False)
    with pytest.raises(ls.MissingKey):
        ls.api_key()
    monkeypatch.setenv(ls.KEY_ENV, "  padded  ")
    assert ls.api_key() == "padded"


def test_plan_is_two_calls_with_the_key_redacted(cache):
    client = ls.ListingStatusClient(transport=DryRunTransport(sink=lambda s: None), cache=cache)
    plan = client.plan()
    assert [c.state for c in plan] == ["active", "delisted"]
    for c in plan:
        assert "***REDACTED***" in c.url and "LISTING_STATUS" in c.url
        assert redact(c.url) == c.url


def test_fetch_uses_two_requests_and_then_the_cache(key, transport, cache):
    client = ls.ListingStatusClient(transport=transport, cache=cache)
    rows = client.all_listings()
    assert len(rows) == 22
    assert transport.calls == [URL_ACTIVE, URL_DELISTED]
    client.all_listings()
    assert len(transport.calls) == 2, "a second read must come from the cache"
    meta = json.loads(cache.path_for("listing_active").read_text())["meta"]
    assert KEY not in meta["url"]
    assert meta["live"] is False and meta["transport"] == "FixtureTransport"


def test_a_quota_note_is_never_cached(key, cache):
    t = FixtureTransport()
    t.add(URL_ACTIVE, json.dumps({"Note": "call frequency exceeded"}))
    client = ls.ListingStatusClient(transport=t, cache=cache)
    with pytest.raises(RateLimited):
        client.listings("active")
    assert cache.read("listing_active") is None


def test_dry_run_sends_nothing(key, cache):
    seen = []
    client = ls.ListingStatusClient(transport=DryRunTransport(sink=seen.append), cache=cache)
    with pytest.raises(Offline):
        client.listings("active")
    assert seen and all(KEY not in line for line in seen)


def test_provenance_says_no_until_a_live_transport_has_written(cache):
    assert ls.provenance(cache) == {"ever_live": False, "states": {"active": None, "delisted": None}}
    cache.write("listing_active", "x", meta={"live": True, "transport": "HttpTransport"})
    cache.write("listing_delisted", "x", meta={"live": False, "transport": "FixtureTransport"})
    p = ls.provenance(cache)
    assert p["ever_live"] is True
    assert p["states"]["active"]["live"] is True and p["states"]["delisted"]["live"] is False


def test_the_live_flag_is_derived_from_the_transport_type(key, cache, fixture_text, monkeypatch):
    """The only way ``live`` becomes true is an HttpTransport. Patch its get so no socket opens."""
    calls = []

    def fake_get(self, url, *, headers=None, timeout=30.0):
        calls.append(url)
        return fixture_text["active"].encode()

    monkeypatch.setattr(HttpTransport, "get", fake_get)
    client = ls.ListingStatusClient(transport=HttpTransport("t", rate_per_second=0), cache=cache)
    client.listings("active")
    assert calls == [URL_ACTIVE]
    assert ls.provenance(cache)["ever_live"] is True


# -- the measurement, against the hand-computed answers -----------------------


def test_listed_on_uses_a_strict_delisting_boundary(listings):
    by = {(l.symbol, l.status): l for l in listings}
    on = date(2016, 9, 6)
    assert not by[("FAKG", "Delisted")].listed_on(on), "delisted on the day counts as gone"
    assert by[("FAKH", "Delisted")].listed_on(on), "delisted the day after counts as listed"
    assert not by[("FAKF", "Delisted")].listed_on(on), "no listing date means we cannot say it was listed"
    assert not by[("ABNB", "Active")].listed_on(on)


@pytest.mark.parametrize(
    "as_of, eligible, gone",
    [
        (date(2016, 9, 6), 11, 4),
        (date(2021, 9, 6), 11, 3),
        (date(2023, 9, 6), 11, 2),
        (date(2025, 9, 6), 12, 1),
    ],
)
def test_attrition_matches_the_hand_count(listings, as_of, eligible, gone):
    r = ls.attrition(listings, as_of, today=TODAY)
    assert (r.eligible, r.gone, r.still_listed) == (eligible, gone, eligible - gone)
    assert r.rate == pytest.approx(gone / eligible)


def test_attrition_detail_as_of_ten_years(listings):
    r = ls.attrition(listings, date(2016, 9, 6), today=TODAY)
    assert r.by_exchange == {"NASDAQ": (7, 2), "NYSE": (4, 2)}
    assert r.by_year == {2016: 1, 2019: 1, 2024: 1, 2026: 1}
    assert [l.symbol for l in r.examples] == ["FAKH", "FAKA", "FAKB", "FAKD"]
    assert r.reused_symbols == 1, "ZZZA appears twice"
    assert r.unknown_ipo_dates == 1, "FAKF has no listing date"
    assert r.horizon_years == pytest.approx(10.0, abs=0.01)
    assert r.annualised_rate == pytest.approx(1 - (1 - 4 / 11) ** 0.1, abs=1e-3)


def test_the_listing_age_rule_is_the_screens(listings):
    """MEDP listed 2016-08-11: under three years in 2016, eligible by 2021.

    With the age rule off, MEDP, TWTR (2.8y) and FAKC (1.9y) join the eleven."""
    assert ls.attrition(listings, date(2016, 9, 6), today=TODAY, min_years_listed=0).eligible == 14
    r = ls.attrition(listings, date(2021, 9, 6), today=TODAY)
    assert r.eligible == 11


def test_funds_and_other_venues_are_excluded(listings):
    r = ls.attrition(listings, date(2016, 9, 6), today=TODAY, exchanges=("NYSE", "NASDAQ", "NYSE ARCA"))
    assert r.eligible == 11, "SPY and FAKE are ETFs and stay out even when their venue is allowed"


def test_attrition_refuses_a_future_as_of(listings):
    with pytest.raises(ValueError):
        ls.attrition(listings, TODAY, today=TODAY)


def test_empty_eligible_set_reports_none_not_zero(listings):
    r = ls.attrition(listings, date(1961, 1, 1), today=TODAY)
    assert r.eligible == 0 and r.rate is None and r.annualised_rate is None


def test_cross_check_flags_delisted_and_reassigned_symbols(listings):
    c = ls.cross_check(listings, ["AAPL", "TWTR", "FAKA", "SHOP.TO", "ZZZA", "NOPE", "brk.b"], today=TODAY)
    assert c.checked == 7
    assert sorted(t for t, _ in c.delisted_hits) == ["FAKA", "TWTR"]
    assert c.not_covered == ["SHOP.TO", "NOPE"]
    assert c.canadian_not_covered == ["SHOP.TO"]


def test_default_horizons_are_one_three_five_ten_years():
    assert ls.default_horizons(TODAY) == [date(2025, 9, 6), date(2023, 9, 6), date(2021, 9, 6), date(2016, 9, 6)]
    assert ls.default_horizons(date(2028, 2, 29))[0] == date(2027, 2, 28)


def test_synthetic_assumption_is_stated_from_the_test_that_uses_it():
    # 4 of 150 every 63 days, four periods a year
    assert ls.SYNTHETIC_ATTRITION_PER_YEAR == pytest.approx(1 - (1 - 4 / 150) ** 4)


def test_report_leads_with_provenance_and_the_caveats(listings):
    reports = [ls.attrition(listings, d, today=TODAY) for d in ls.default_horizons(TODAY)]
    check = ls.cross_check(listings, ["AAPL", "TWTR"], today=TODAY)
    text = ls.render_report(reports, check, source_line="NOT a real request; test", n_active=12, n_delisted=10)
    lines = text.splitlines()
    assert lines[0].startswith("source: NOT a real request")
    assert "upper bound" in text and "delisting is not a failure" in text
    assert "36.4%" in text and "TWTR" in text


# -- the script ----------------------------------------------------------------


def run_script(*args, env=None):
    import os

    e = {**os.environ, "DESK_OFFLINE": "1"}
    e.pop(ls.KEY_ENV, None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "listing_status.py"), *args],
                          capture_output=True, text=True, cwd=str(ROOT), env=e)


def test_script_dry_run_needs_no_key_and_redacts():
    r = run_script("--dry-run")
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("GET https://www.alphavantage.co/query?") == 2
    assert "***REDACTED***" in r.stdout and "apikey=%2A" not in r.stdout
    assert "ever made a real request: no" in r.stdout


def test_script_fixture_report_says_it_is_a_fixture(tmp_path):
    out = tmp_path / "s.json"
    r = run_script("--report", "--fixture", "--today", "2026-09-06", "--no-cross-check", "--json", str(out))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0].startswith("# FIXTURE")
    assert "NOT a real request" in r.stdout
    blob = json.loads(out.read_text())
    assert blob["is_real"] is False and blob["source"] == "fixture"
    assert blob["limitations"][0].startswith("FIXTURE DATA")
    ten = [a for a in blob["attrition"] if a["as_of"] == "2016-09-06"][0]
    assert (ten["eligible"], ten["gone"]) == (11, 4)


def test_script_report_without_data_or_key_exits_two(tmp_path):
    r = run_script("--report", env={"DESK_ROOT": str(tmp_path)})
    assert r.returncode == 2
    assert "no cached listing" in r.stderr
    r = run_script("--fetch", env={"DESK_ROOT": str(tmp_path)})
    assert r.returncode == 2
    assert ls.KEY_ENV in r.stderr
