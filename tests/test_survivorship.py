"""The survivorship hole reaches the backtest's limitations only as a measured number.

Until a real Alpha Vantage pull exists the limitation says "unknown" and how to
measure it. Once one exists it quotes the rate. A fixture can never be quoted.
"""
import json
import time
from datetime import date
from pathlib import Path

import pytest

from an import backtest as B
from an import listing_status as ls
from an.http import FixtureTransport
from an.store import Cache

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def _key():
    return "demo"


@pytest.fixture
def warm_cache(tmp_path, monkeypatch):
    """The two fixture files, cached the way a real pull would cache them, with the live flag chosen by the test."""
    monkeypatch.setenv(ls.KEY_ENV, "demo")

    def make(live: bool) -> Cache:
        cache = Cache(tmp_path / ("live" if live else "fixture"), default_ttl=ls.TTL)
        client = ls.ListingStatusClient(transport=FixtureTransport(), cache=cache)
        for state in ls.STATES:
            text = (FIX / f"av_listing_{state}.csv").read_text()
            cache.write(client.cache_key_for(state), text,
                        meta={"url": "redacted", "transport": "HttpTransport" if live else "FixtureTransport",
                              "live": live})
        return cache

    return make


def test_unmeasured_limitation_says_unknown_and_how_to_measure():
    text = B.survivorship_limitation(None)
    assert "unknown amount" in text
    assert "listing_status.py --fetch" in text


def test_nothing_cached_gives_none(tmp_path):
    assert ls.measured_attrition(5, cache=Cache(tmp_path / "empty"), today=date(2026, 9, 6)) is None


def test_a_fixture_pull_is_never_a_measurement(warm_cache):
    cache = warm_cache(live=False)
    assert ls.provenance(cache)["ever_live"] is False
    assert ls.measured_attrition(10, cache=cache, today=date(2026, 9, 6)) is None


def test_a_real_pull_is_measured_on_the_hand_computed_answer(warm_cache):
    """NOTES.md 7a: as of 2016-09-06 the fixture has eleven eligible names and four gone."""
    cache = warm_cache(live=True)
    m = ls.measured_attrition(10, cache=cache, today=date(2026, 9, 6))
    assert m is not None
    assert (m.eligible, m.gone) == (11, 4)
    assert m.as_of == date(2016, 9, 6) and m.today == date(2026, 9, 6)
    assert m.rate == pytest.approx(4 / 11)
    assert m.annualised_rate == pytest.approx(1 - (1 - 4 / 11) ** (1 / m.horizon_years))
    assert m.fetched_at is not None
    d = m.to_dict()
    assert d["synthetic_engine_assumes_per_year"] == round(ls.SYNTHETIC_ATTRITION_PER_YEAR, 4)
    assert "real request" in d["source"]


def test_measured_limitation_quotes_the_number_and_drops_unknown(warm_cache):
    m = ls.measured_attrition(10, cache=warm_cache(live=True), today=date(2026, 9, 6))
    text = B.survivorship_limitation(m)
    assert "unknown amount" not in text
    assert "of the 11 NYSE and Nasdaq common stocks" in text and "4 (36.4%" in text
    assert "upper bound" in text and "sized, not filled" in text
    assert "synthetic engine's assumption" in text


def test_the_engine_carries_the_measurement_into_every_result(warm_cache):
    from tests.test_backtest import linear_panel

    m = ls.measured_attrition(10, cache=warm_cache(live=True), today=date(2026, 9, 6))
    r = B.run_backtest(linear_panel(), label="t", variant="v", horizon_days=21, rebalance_spacing_days=21, attrition=m)
    assert any("36.4%" in x for x in r.limitations)
    assert not any("unknown amount" in x for x in r.limitations)
    r2 = B.run_backtest(linear_panel(), label="t", variant="v", horizon_days=21, rebalance_spacing_days=21)
    assert any("unknown amount" in x for x in r2.limitations)


def test_no_eligible_names_is_none_not_zero(warm_cache):
    cache = warm_cache(live=True)
    # Nothing in the fixture was listed three years before 1990.
    assert ls.measured_attrition(36, cache=cache, today=date(1990, 1, 1)) is None


def test_the_committed_backtest_json_says_not_measured():
    blob = json.loads((ROOT / "dashboard" / "backtest.json").read_text())
    s = blob["survivorship"]
    if list(Path(ls.paths.ALPHAVANTAGE_CACHE).glob("*.json")):
        pytest.skip("an Alpha Vantage cache exists on this machine")
    assert s["status"] == "NOT MEASURED"
    assert "listing_status.py --fetch" in s["how_to_measure"]
    assert any("unknown amount" in x for x in blob["why_not_run"])
