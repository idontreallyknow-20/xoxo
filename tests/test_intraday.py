"""an.intraday: fifteen-minute bars for the watched names, read without a socket.

The fixture is two hand-built sessions. LIVE drifts up a cent a bar; DIP gaps
down on the second morning and keeps falling. What is tested is the reading: the
last print, the session it belongs to, the change against the prior close, the
session's extremes, an as-of cut inside the session, and every hole as None.
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from an import intraday
from an.store import Cache, Offline

FIXTURES = Path(__file__).parent / "fixtures"


def raw_frame():
    blob = json.loads((FIXTURES / "yf_intraday.json").read_text())
    cols = pd.MultiIndex.from_tuples([tuple(c) for c in blob["columns"]])
    return pd.DataFrame(blob["data"], index=pd.to_datetime(blob["index"], utc=True), columns=cols)


@pytest.fixture
def bars():
    return intraday.bars_from_download(raw_frame(), ["LIVE", "DIP", "GONE"])


def test_the_download_is_split_per_ticker_in_market_time(bars):
    assert set(bars) == {"LIVE", "DIP", "GONE"}
    assert bars["GONE"].empty
    live = bars["LIVE"]
    assert list(live.columns) == ["Open", "High", "Low", "Close"]
    assert str(live.index.tz) == "America/New_York"
    assert live.index[0].strftime("%Y-%m-%d %H:%M") == "2026-09-03 09:30"
    assert len(live) == 52


def test_the_last_print_and_its_session(bars):
    p = intraday.last_print(bars, "LIVE")
    assert p.price == 103.55 and p.session == "2026-09-04" and p.at.startswith("2026-09-04T15:45")
    assert p.prior_close == 102.55, "the previous session's last bar when no daily close is given"
    assert p.change == pytest.approx(103.55 / 102.55 - 1)
    assert p.bars_in_session == 26 and p.intraday is True
    assert p.session_high == pytest.approx(103.65) and p.session_low == pytest.approx(101.0 - 0.1)


def test_a_daily_close_overrides_the_intraday_prior(bars):
    p = intraday.last_print(bars, "DIP", prior_close=205.2)
    assert p.prior_close == 205.2
    assert p.change == pytest.approx(166.6 / 205.2 - 1)
    assert p.session_low == pytest.approx(166.4)


def test_an_as_of_inside_the_session_cuts_the_bars(bars):
    p = intraday.last_print(bars, "LIVE", as_of=dt.datetime(2026, 9, 4, 12, 0))
    assert p.at.startswith("2026-09-04T11:45") and p.bars_in_session == 10
    before = intraday.last_print(bars, "LIVE", as_of=dt.datetime(2026, 9, 3, 9, 0))
    assert before.price is None and before.bars_in_session == 0


def test_a_missing_name_is_none_everywhere(bars):
    p = intraday.last_print(bars, "GONE")
    assert p.price is None and p.change is None and p.session is None and p.bars_in_session == 0
    assert intraday.last_print({}, "X").to_json()["price"] is None


def test_a_flat_single_ticker_frame_parses():
    raw = raw_frame().xs("LIVE", axis=1, level=1)
    out = intraday.bars_from_download(raw, ["LIVE"])
    assert len(out["LIVE"]) == 52 and out["LIVE"]["Close"].iloc[-1] == 103.55


def test_the_client_caches_and_records_that_the_replay_was_not_live(tmp_path):
    d = intraday.RawDownloader(raw_frame())
    c = intraday.IntradayClient(downloader=d, cache=Cache(tmp_path / "intra", default_ttl=600))
    b1 = c.bars(["LIVE", "DIP"])
    assert c.last_source == "replay" and c.last_live is False
    b2 = c.bars(["LIVE", "DIP"])
    assert c.last_source == "cache" and len(d.calls) == 1
    assert b2["LIVE"]["Close"].iloc[-1] == b1["LIVE"]["Close"].iloc[-1] == 103.55
    assert str(b2["LIVE"].index.tz) == "America/New_York"
    meta = json.loads(next((tmp_path / "intra").glob("*.json")).read_text())["meta"]
    assert meta["live"] is False and meta["downloader"] == "RawDownloader"


def test_offline_serves_the_stale_cache_and_raises_with_nothing(tmp_path):
    cache = Cache(tmp_path / "intra", default_ttl=0.0)
    intraday.IntradayClient(downloader=intraday.RawDownloader(raw_frame()), cache=cache).bars(["LIVE"])
    c = intraday.IntradayClient(downloader=intraday.OfflineDownloader(), cache=cache)
    assert c.bars(["LIVE"])["LIVE"]["Close"].iloc[-1] == 103.55 and c.last_source == "stale"
    with pytest.raises(Offline):
        c.bars(["NEVER"])


def test_the_live_downloader_refuses_when_offline(monkeypatch):
    monkeypatch.setenv("DESK_OFFLINE", "1")
    with pytest.raises(Offline):
        intraday.YFinanceIntradayDownloader().download(["LIVE"])


def test_the_window_is_narrow_by_design():
    assert intraday.INTERVAL == "15m" and intraday.PERIOD == "5d" and intraday.TTL == 600.0
    src = (Path(__file__).parent.parent / "scripts" / "an" / "intraday.py").read_text()
    assert "prepost=False" in src, "no pre-market or after-hours prints; the rules are session rules"
