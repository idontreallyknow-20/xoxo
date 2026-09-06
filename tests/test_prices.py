"""Every test here is offline and deterministic.

The panel fixtures are hand written to the shapes yfinance actually returns, and
everything else comes out of :func:`an.prices.synthetic_panel`, which is seeded. No
test reads a clock, and an autouse fixture makes any attempt to reach yfinance an
assertion failure rather than a hang against a proxy that will refuse it anyway.
"""
import datetime as dt
import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from an import prices
from an.store import Cache, FetchError, Offline


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def raw_frame(blob):
    """Rebuild a yfinance shaped frame from a fixture."""
    columns = blob["columns"]
    if columns and isinstance(columns[0], list):
        cols = pd.MultiIndex.from_tuples([tuple(c) for c in columns])
    else:
        cols = pd.Index(columns)
    return pd.DataFrame(blob["data"], index=pd.to_datetime(blob["index"]), columns=cols, dtype="float64")


@pytest.fixture(autouse=True)
def _no_yfinance(monkeypatch):
    """yfinance is never importable in a test here. A hang is worse than a failure."""

    def boom(*a, **k):
        raise AssertionError("a test tried to reach yfinance")

    monkeypatch.setattr(prices, "_yf", boom)
    monkeypatch.delenv("DESK_OFFLINE", raising=False)


@pytest.fixture
def panel_raw(load_fixture):
    return raw_frame(load_fixture("yf_download_panel.json"))


@pytest.fixture
def closes(panel_raw):
    """The three name panel, through the same normaliser a live pull would use."""
    return prices._fill_within_life(prices.closes_from_download(panel_raw, ["LIVE", "DEAD", "LATE"]))


@pytest.fixture
def client(tmp_path, panel_raw):
    dl = prices.RawDownloader(panel_raw)
    c = prices.PriceClient(dl, cache=Cache(tmp_path, default_ttl=3600), ttl=3600.0)
    c._dl = dl
    return c


def _age_entry(cache, key, seconds):
    """Backdate one cache entry so the next read sees it as older. No clock is read."""
    p = cache.path_for(key)
    blob = json.loads(p.read_text())
    blob["fetched_at"] -= float(seconds)
    p.write_text(json.dumps(blob))


def _cache_series(client, ticker, start, end, series, *, age_seconds=0.0):
    """Put one payload on disk for ``ticker`` over ``[start, end]``, without a download."""
    s_ts, e_ts = prices._as_ts(start), prices._as_ts(end)
    key = client._key(ticker, s_ts, e_ts)
    client.cache.write(key, client._to_payload(ticker, s_ts, e_ts, series))
    if age_seconds:
        _age_entry(client.cache, key, age_seconds)
    return key


class _RecordedYF:
    """Stands in for the yfinance module and keeps the kwargs it was handed."""

    def __init__(self):
        self.kwargs = []

    def download(self, **kw):
        self.kwargs.append(kw)
        return pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-01-02"]))


# ---------------------------------------------------------------------------
# delisting. The most important behaviour in the module, so it goes first.
# ---------------------------------------------------------------------------


def test_a_delisted_ticker_is_never_forward_filled_into_a_flat_return(closes):
    """DEAD collapses from 53 to 10 and stops printing after 2026-01-08.

    A panel that forward fills it would carry 10.0 to the end of the window, which
    makes every later date look like a live, tradeable, perfectly flat position. The
    prices after the last print must stay empty, and the return over a horizon that
    spans the delisting must say so.
    """
    assert closes.loc["2026-01-08", "DEAD"] == 10.0
    assert pd.isna(closes.loc["2026-01-09", "DEAD"])
    assert pd.isna(closes.loc["2026-01-15", "DEAD"])
    assert closes["DEAD"].dropna().index[-1] == pd.Timestamp("2026-01-08")

    fr = prices.forward_return_detail(closes, "DEAD", "2026-01-05", 5)
    assert fr.stopped_trading is True
    assert fr.status == "truncated"
    assert fr.base_date == dt.date(2026, 1, 5) and fr.base_price == 51.0
    # The horizon wanted 2026-01-12. It got the final traded price instead, and the
    # end_date says so rather than pretending the position ran the full window.
    assert fr.end_date == dt.date(2026, 1, 8) and fr.end_price == 10.0
    assert fr.ret == pytest.approx(10.0 / 51.0 - 1.0)
    assert fr.ret < -0.75

    # The live name over the identical window is untouched by any of this.
    alive = prices.forward_return_detail(closes, "LIVE", "2026-01-05", 5)
    assert alive.status == "ok" and alive.stopped_trading is False
    assert alive.end_date == dt.date(2026, 1, 12)


def test_delisted_ticker_can_be_reported_as_none_instead(closes):
    fr = prices.forward_return_detail(closes, "DEAD", "2026-01-05", 5, on_delist="none")
    assert fr.ret is None
    assert fr.status == "delisted"
    assert fr.stopped_trading is True
    assert prices.forward_return(closes, "DEAD", "2026-01-05", 5, on_delist="none") is None


def test_buying_a_ticker_that_had_already_delisted_returns_none_not_zero(closes):
    """Standing on 2026-01-13, DEAD has been gone for days.

    A forward filled panel answers 0.0 percent here, every time, forever, and a
    ranking happily puts that ahead of every name that actually fell.
    """
    fr = prices.forward_return_detail(closes, "DEAD", "2026-01-13", 5)
    assert fr.ret is None
    assert fr.status == "already_delisted"
    assert fr.stopped_trading is True
    assert prices.forward_return_detail(closes, "DEAD", "2026-01-13", 5, on_delist="none").ret is None


def test_delisting_shows_up_in_coverage_not_only_in_returns(closes):
    cov = prices.coverage_on(closes, "2026-01-13")
    assert cov.stopped_trading == ("DEAD",)
    assert set(cov.available) == {"LIVE", "LATE"}
    assert cov.missing == ("DEAD",)
    assert cov.ratio == pytest.approx(2 / 3)


def test_delist_helper_builds_a_dead_ticker(closes):
    panel = prices.synthetic_panel(["AAA", "BBB"], start="2026-01-05", periods=10, seed=3)
    killed = prices.delist(panel, "BBB", "2026-01-12")
    assert killed["BBB"].dropna().index[-1] == pd.Timestamp("2026-01-09")
    assert killed["AAA"].notna().all()
    assert prices.last_observation(killed, "BBB") == dt.date(2026, 1, 9)
    with pytest.raises(KeyError):
        prices.delist(panel, "NOPE", "2026-01-12")


# ---------------------------------------------------------------------------
# fill policy
# ---------------------------------------------------------------------------


def test_no_backfill_before_the_first_observation(closes):
    """LATE lists on 2026-01-08. Before that it has no price, not a guessed one."""
    assert closes["LATE"].loc[: pd.Timestamp("2026-01-07")].isna().all()
    assert closes.loc["2026-01-08", "LATE"] == 20.0
    assert prices.first_observation(closes, "LATE") == dt.date(2026, 1, 8)
    assert prices.price_on(closes, "LATE", "2026-01-06") is None


def test_interior_holes_are_forward_filled(closes):
    """LIVE does not print on 2026-01-07. A holder still held it at Tuesday's price."""
    assert closes.loc["2026-01-07", "LIVE"] == 102.0
    assert closes.loc["2026-01-06", "LIVE"] == 102.0


def test_ffill_false_leaves_the_raw_holes(client):
    raw = client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15", ffill=False)
    assert pd.isna(raw.loc["2026-01-07", "LIVE"])
    filled = client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert filled.loc["2026-01-07", "LIVE"] == 102.0


def test_the_index_is_the_union_of_the_requested_tickers_sessions(client):
    """LIVE alone never traded on 2026-01-07, so that day is not a row of its panel.

    Ask for DEAD as well, which did trade that day, and the row appears with LIVE
    filled across it. The calendar comes from the data, not from a guess about which
    exchange these names belong to.
    """
    alone = client.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")
    assert pd.Timestamp("2026-01-07") not in alone.index
    assert len(alone) == 9
    together = client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert pd.Timestamp("2026-01-07") in together.index
    assert len(together) == 10


def test_a_ticker_with_no_data_at_all_keeps_its_column(client):
    """The documented policy: present, empty, never dropped and never zero filled."""
    out = client.daily_closes(["LIVE", "GHOST"], "2026-01-02", "2026-01-15")
    assert list(out.columns) == ["LIVE", "GHOST"]
    assert out["GHOST"].isna().all()
    assert (out["GHOST"] == 0.0).sum() == 0
    assert prices.coverage_on(out, "2026-01-08").no_data == ("GHOST",)


# ---------------------------------------------------------------------------
# no peeking forward
# ---------------------------------------------------------------------------


def test_price_on_a_weekend_uses_the_friday_close(closes):
    """2026-01-10 is a Saturday. The answer is Friday, never the following Monday."""
    assert prices.price_on(closes, "LIVE", "2026-01-10") == 105.0
    assert prices.price_on(closes, "LIVE", "2026-01-11") == 105.0
    assert prices.price_on(closes, "LIVE", "2026-01-12") == 106.0


def test_price_on_a_holiday_does_not_peek_at_the_next_session():
    """A closed Monday has no row at all in the panel, not a NaN row."""
    idx = pd.to_datetime(["2026-01-15", "2026-01-16", "2026-01-20", "2026-01-21"])
    panel = pd.DataFrame({"AAA": [10.0, 11.0, 12.0, 13.0]}, index=idx)
    assert prices.price_on(panel, "AAA", "2026-01-19") == 11.0
    fr = prices.forward_return_detail(panel, "AAA", "2026-01-19", 1)
    assert fr.base_date == dt.date(2026, 1, 16)
    assert fr.end_date == dt.date(2026, 1, 20)
    assert fr.ret == pytest.approx(12.0 / 11.0 - 1.0)


def test_forward_return_from_a_weekend_starts_from_the_friday_bar(closes):
    fr = prices.forward_return_detail(closes, "LIVE", "2026-01-10", 2)
    assert fr.base_date == dt.date(2026, 1, 9) and fr.base_price == 105.0
    assert fr.end_date == dt.date(2026, 1, 13) and fr.end_price == 107.0
    assert fr.ret == pytest.approx(107.0 / 105.0 - 1.0)
    # The same horizon asked from the Friday itself must give exactly the same answer.
    assert prices.forward_return(closes, "LIVE", "2026-01-09", 2) == pytest.approx(fr.ret)


def test_price_before_any_data_is_none(closes):
    assert prices.price_on(closes, "LIVE", "2025-12-31") is None
    fr = prices.forward_return_detail(closes, "LIVE", "2025-12-31", 3)
    # Before the panel's own first row. There is no price, and there is also no
    # evidence about whether LIVE had listed: the panel starts on 2026-01-02 and
    # cannot see behind itself, so it says so rather than claiming an IPO date.
    assert fr.ret is None and fr.status == "unknown_before_window"
    assert prices.forward_returns(closes, ["LIVE"], "2025-12-31", 3)["LIVE"] is None


# ---------------------------------------------------------------------------
# adjustment
# ---------------------------------------------------------------------------


def test_adjusted_close_wins_when_both_columns_are_present(load_fixture):
    """SPLT splits two for one on 2026-01-07."""
    raw = raw_frame(load_fixture("yf_download_split.json"))
    adj = prices.closes_from_download(raw, ["SPLT"])
    assert list(adj.columns) == ["SPLT"]
    assert adj["SPLT"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert prices.forward_return(adj, "SPLT", "2026-01-06", 1) == pytest.approx(103.0 / 102.0 - 1.0)


def test_the_raw_close_would_have_read_the_split_as_a_crash(load_fixture):
    """Documents the trap the preference order exists to avoid."""
    raw = raw_frame(load_fixture("yf_download_split.json"))
    unadjusted = raw["Close"].copy()
    unadjusted.columns = ["SPLT"]
    assert prices.forward_return(unadjusted, "SPLT", "2026-01-06", 1) == pytest.approx(103.0 / 204.0 - 1.0)
    assert prices.forward_return(unadjusted, "SPLT", "2026-01-06", 1) < -0.49
    # And the module never picks that column when an adjusted one exists.
    assert prices.forward_return(prices.closes_from_download(raw, ["SPLT"]), "SPLT", "2026-01-06", 1) > 0


def test_auto_adjusted_close_is_used_when_there_is_no_adj_close_column(panel_raw):
    """auto_adjust=True removes Adj Close entirely and leaves Close already adjusted."""
    assert "Adj Close" not in set(panel_raw.columns.get_level_values(0))
    out = prices.closes_from_download(panel_raw, ["LIVE"])
    assert out["LIVE"].dropna().tolist() == [100.0, 101.0, 102.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0]


def test_flat_single_ticker_columns_are_understood(load_fixture):
    raw = raw_frame(load_fixture("yf_download_single_flat.json"))
    out = prices.closes_from_download(raw, ["ONE"])
    assert list(out.columns) == ["ONE"]
    assert out["ONE"].tolist() == [10.0, 11.0, 12.0]


def test_group_by_ticker_layout_is_understood():
    """yf.download(group_by='ticker') puts the ticker on the outer level instead."""
    cols = pd.MultiIndex.from_tuples([("AAA", "Close"), ("AAA", "Volume"), ("BBB", "Close"), ("BBB", "Volume")])
    raw = pd.DataFrame(
        [[1.0, 10, 2.0, 20], [1.5, 11, 2.5, 21]],
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
        columns=cols,
    )
    out = prices.closes_from_download(raw, ["AAA", "BBB"])
    assert list(out.columns) == ["AAA", "BBB"]
    assert out["BBB"].tolist() == [2.0, 2.5]


def test_a_download_with_no_close_column_raises(load_fixture):
    raw = pd.DataFrame({"Volume": [1, 2]}, index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    with pytest.raises(FetchError):
        prices.closes_from_download(raw, ["AAA"])


def test_an_empty_download_is_an_empty_panel_not_a_crash():
    assert list(prices.closes_from_download(None, ["AAA", "BBB"]).columns) == ["AAA", "BBB"]
    assert prices.closes_from_download(None, ["AAA"]).empty


def test_timezone_aware_download_index_is_flattened():
    """Yahoo returns tz aware stamps for some exchanges. Mixing the two raises."""
    idx = pd.to_datetime(["2026-01-02 21:00", "2026-01-05 21:00"]).tz_localize("UTC")
    raw = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
    out = prices.closes_from_download(raw, ["AAA"])
    assert out.index.tz is None
    assert list(out.index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]
    assert prices.price_on(out, "AAA", "2026-01-03") == 1.0


# ---------------------------------------------------------------------------
# survivorship reporting
# ---------------------------------------------------------------------------


def test_coverage_splits_not_listed_yet_from_stopped_trading(closes):
    early = prices.coverage_on(closes, "2026-01-06")
    assert early.not_listed_yet == ("LATE",)
    assert early.stopped_trading == ()
    assert set(early.available) == {"LIVE", "DEAD"}

    late = prices.coverage_on(closes, "2026-01-14")
    assert late.not_listed_yet == ()
    assert late.stopped_trading == ("DEAD",)


def test_missing_on_lists_every_absent_name(closes):
    assert prices.missing_on(closes, "2026-01-06") == ["LATE"]
    assert prices.missing_on(closes, "2026-01-14") == ["DEAD"]
    assert prices.missing_on(closes, "2025-06-01") == ["DEAD", "LATE", "LIVE"]
    assert prices.missing_on(closes, "2026-01-06", ["LIVE"]) == []


def test_forward_returns_has_an_entry_for_every_requested_ticker(closes):
    out = prices.forward_returns(closes, ["LIVE", "DEAD", "LATE", "GHOST"], "2026-01-06", 4)
    assert set(out) == {"LIVE", "DEAD", "LATE", "GHOST"}
    assert out["GHOST"] is None
    assert out["LATE"] is None  # not listed yet on 2026-01-06
    assert out["LIVE"] is not None

    detail = prices.forward_returns_detail(closes, ["LIVE", "DEAD", "LATE", "GHOST"], "2026-01-06", 4)
    assert detail["GHOST"].status == "no_column"
    assert detail["LATE"].status == "not_listed_yet"
    assert detail["DEAD"].stopped_trading is True


# ---------------------------------------------------------------------------
# horizon mechanics and the None contract
# ---------------------------------------------------------------------------


def test_zero_horizon_is_exactly_zero(closes):
    assert prices.forward_return(closes, "LIVE", "2026-01-06", 0) == 0.0


def test_horizon_past_the_end_of_the_panel_is_none(closes):
    fr = prices.forward_return_detail(closes, "LIVE", "2026-01-14", 20)
    assert fr.ret is None
    assert fr.status == "short_panel"
    assert fr.base_price == 108.0


def test_bad_arguments_raise_rather_than_returning_something_plausible(closes):
    with pytest.raises(ValueError):
        prices.forward_return(closes, "LIVE", "2026-01-06", -1)
    with pytest.raises(ValueError):
        prices.forward_return(closes, "LIVE", "2026-01-06", 3, on_delist="ffill")
    with pytest.raises(ValueError):
        prices.forward_returns(closes, ["LIVE"], "2026-01-06", 3, on_delist="ffill")


def test_no_nan_ever_leaves_the_scalar_api(closes):
    """A NaN in a ranking sorts somewhere arbitrary and nobody notices.

    Asserted as ``is None`` rather than as "None or finite", because 0.0 satisfies
    that predicate and 0.0 is the one answer this module exists to refuse: a
    delisted name scoring exactly flat outranks every name that merely fell.
    """
    assert prices.price_on(closes, "GHOST", "2026-01-06") is None
    assert prices.price_on(closes, "LATE", "2026-01-02") is None
    assert prices.forward_return(closes, "DEAD", "2026-01-13", 3) is None
    assert prices.forward_return(closes, "GHOST", "2026-01-06", 3) is None

    out = prices.forward_returns(closes, ["LIVE", "DEAD", "LATE", "GHOST"], "2026-01-02", 3)
    assert out["LATE"] is None and out["GHOST"] is None
    assert out["LIVE"] == pytest.approx(104.0 / 100.0 - 1.0)
    assert out["DEAD"] == pytest.approx(10.0 / 50.0 - 1.0)


def test_the_conversion_itself_refuses_nan_and_infinity():
    """The guard the accessors rely on, asserted directly rather than by shape."""
    assert prices._num(float("nan")) is None
    assert prices._num(float("inf")) is None
    assert prices._num(float("-inf")) is None
    assert prices._num("not a number") is None
    assert prices._num(None) is None
    assert prices._num(0.0) == 0.0  # a real zero price is a value, not a hole


def test_a_non_finite_price_in_a_panel_comes_back_as_none_not_as_infinity():
    """dropna() keeps an infinity, so this is the path that actually runs _num."""
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    panel = pd.DataFrame({"AAA": [10.0, float("inf"), 12.0]}, index=idx)
    assert prices.price_on(panel, "AAA", "2026-01-05") is None
    fr = prices.forward_return_detail(panel, "AAA", "2026-01-02", 1)
    assert fr.end_price is None
    assert fr.ret is None and fr.status == "no_data"
    assert prices.forward_return_detail(panel, "AAA", "2026-01-05", 1).ret is None


def test_a_zero_or_negative_base_price_is_refused():
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    panel = pd.DataFrame({"AAA": [0.0, 1.0, 2.0]}, index=idx)
    fr = prices.forward_return_detail(panel, "AAA", "2026-01-02", 1)
    assert fr.ret is None and fr.status == "bad_base_price"


def test_an_unsorted_panel_is_sorted_before_it_is_read():
    idx = pd.to_datetime(["2026-01-06", "2026-01-02", "2026-01-05"])
    panel = pd.DataFrame({"AAA": [12.0, 10.0, 11.0]}, index=idx)
    assert prices.price_on(panel, "AAA", "2026-01-05") == 11.0
    assert prices.forward_return(panel, "AAA", "2026-01-02", 2) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# synthetic prices
# ---------------------------------------------------------------------------


SYNTHETIC_DIGEST = "2a33021a2f50ca166d6f793b2fc512ba7d7f8d4d802cfe522045b7f902567903"


def _sample_panel():
    return prices.synthetic_panel(
        ["AAA", "BBB"],
        start="2021-01-04",
        periods=6,
        seed=42,
        drift={"AAA": 0.10},
        vol={"AAA": 0.25, "BBB": 0.10},
    )


def test_synthetic_panel_is_byte_identical_for_the_same_seed():
    """Pinned to a digest, not just to itself, so a numpy or platform change is loud."""
    a, b = _sample_panel(), _sample_panel()
    assert a.to_numpy().tobytes() == b.to_numpy().tobytes()
    assert hashlib.sha256(a.to_numpy().tobytes()).hexdigest() == SYNTHETIC_DIGEST


def test_a_different_seed_gives_a_different_panel():
    a = prices.synthetic_panel(["AAA"], start="2021-01-04", periods=20, seed=1)
    b = prices.synthetic_panel(["AAA"], start="2021-01-04", periods=20, seed=2)
    assert not np.array_equal(a.to_numpy(), b.to_numpy())


def test_synthetic_paths_do_not_depend_on_the_ticker_list_or_its_order():
    """Adding a name must not perturb the others, or a planted signal is unattributable."""
    one = prices.synthetic_panel(["AAA", "BBB"], start="2021-01-04", periods=30, seed=9)
    two = prices.synthetic_panel(["ZZZ", "BBB", "AAA"], start="2021-01-04", periods=30, seed=9)
    assert np.array_equal(one["AAA"].to_numpy(), two["AAA"].to_numpy())
    assert np.array_equal(one["BBB"].to_numpy(), two["BBB"].to_numpy())


def test_zero_volatility_reproduces_the_planted_drift_exactly():
    """The analytic case: no noise, so the answer is closed form to full precision."""
    panel = prices.synthetic_panel(["ZZZ"], start="2021-01-04", periods=60, seed=1, drift=0.20, vol=0.0)
    assert panel["ZZZ"].iloc[0] == 100.0
    assert panel["ZZZ"].iloc[10] == pytest.approx(100.0 * math.exp(0.20 * 10 / 252), rel=1e-12)
    got = prices.forward_return(panel, "ZZZ", panel.index[0], 20)
    assert got == pytest.approx(math.exp(0.20 * 20 / 252) - 1.0, rel=1e-12)


def test_a_planted_signal_is_recovered_by_forward_returns():
    """What the backtest engine is validated against: known drift, known ordering."""
    tickers = ["UP", "FLAT", "DOWN"]
    panel = prices.synthetic_panel(
        tickers,
        start="2021-01-04",
        periods=504,
        seed=11,
        drift={"UP": 0.40, "FLAT": 0.0, "DOWN": -0.40},
        vol=0.05,
    )
    rets = prices.forward_returns(panel, tickers, panel.index[0], 503)
    assert rets["UP"] > rets["FLAT"] > rets["DOWN"]
    assert rets["UP"] > 0.5 and rets["DOWN"] < -0.4
    ranked = sorted(tickers, key=lambda t: rets[t], reverse=True)
    assert ranked == ["UP", "FLAT", "DOWN"]


def test_synthetic_panel_is_weekdays_and_starts_at_the_start_price():
    panel = prices.synthetic_panel(["AAA"], start="2021-01-02", periods=5, seed=0, start_price=250.0)
    # 2021-01-02 is a Saturday, so the first business day is the Monday.
    assert panel.index[0] == pd.Timestamp("2021-01-04")
    assert panel.index.dayofweek.max() <= 4
    assert panel["AAA"].iloc[0] == 250.0
    assert len(panel) == 5


def test_synthetic_panel_has_no_missing_values_and_is_all_positive():
    panel = prices.synthetic_panel(["A", "B", "C"], start="2021-01-04", periods=200, seed=5, vol=0.6)
    assert panel.notna().all().all()
    assert (panel > 0).all().all()
    assert list(panel.columns) == ["A", "B", "C"]


def test_synthetic_panel_rejects_a_meaningless_length():
    with pytest.raises(ValueError):
        prices.synthetic_panel(["A"], periods=0)
    assert prices.synthetic_panel([], periods=3).shape == (3, 0)


def test_a_synthetic_panel_can_be_served_through_the_client(tmp_path):
    panel = prices.synthetic_panel(["AAA", "BBB"], start="2021-01-04", periods=20, seed=4)
    c = prices.PriceClient(prices.PanelDownloader(panel), cache=Cache(tmp_path))
    out = c.daily_closes(["AAA", "BBB"], "2021-01-04", "2021-01-15")
    assert list(out.columns) == ["AAA", "BBB"]
    assert out.index[-1] == pd.Timestamp("2021-01-15")
    assert out["AAA"].tolist() == pytest.approx(panel["AAA"].iloc[:10].tolist())


# ---------------------------------------------------------------------------
# the client: batching, caching, offline
# ---------------------------------------------------------------------------


def test_every_miss_goes_out_in_one_batched_download(client):
    client.daily_closes(["LIVE", "DEAD", "LATE"], "2026-01-02", "2026-01-15")
    assert len(client._dl.calls) == 1
    assert client._dl.calls[0] == ("LIVE", "DEAD", "LATE")


def test_a_second_call_is_served_from_the_cache(client):
    client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert len(client._dl.calls) == 1
    again = client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert len(client._dl.calls) == 1
    assert client.last_source == {"LIVE": "cache", "DEAD": "cache"}
    assert again.loc["2026-01-08", "DEAD"] == 10.0


def test_only_the_uncached_names_are_fetched(client):
    client.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")
    client.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert client._dl.calls == [("LIVE",), ("DEAD",)]


def test_a_wider_cached_window_is_reused_for_a_narrower_request(tmp_path, panel_raw):
    cache = Cache(tmp_path, default_ttl=3600)
    prices.PriceClient(prices.RawDownloader(panel_raw), cache=cache, ttl=3600.0).daily_closes(
        ["LIVE"], "2026-01-02", "2026-01-15"
    )
    offline = prices.PriceClient(prices.OfflineDownloader(), cache=cache, ttl=3600.0)
    narrow = offline.daily_closes(["LIVE"], "2026-01-06", "2026-01-09")
    assert offline.downloader.calls == []
    assert narrow.index[0] == pd.Timestamp("2026-01-06")
    assert narrow.index[-1] == pd.Timestamp("2026-01-09")
    # 2026-01-07 is not a row: LIVE did not print that day and nothing else was asked for.
    assert narrow["LIVE"].tolist() == [102.0, 104.0, 105.0]


def test_end_is_inclusive_unlike_yfinances_own(tmp_path):
    panel = prices.synthetic_panel(["AAA"], start="2021-01-04", periods=10, seed=2)
    dl = prices.PanelDownloader(panel)
    out = prices.PriceClient(dl, cache=Cache(tmp_path)).daily_closes(["AAA"], "2021-01-04", "2021-01-08")
    assert out.index[-1] == pd.Timestamp("2021-01-08")
    assert len(out) == 5


def test_offline_mode_never_calls_the_downloader_and_serves_the_cache(tmp_path, panel_raw, monkeypatch):
    cache = Cache(tmp_path, default_ttl=3600)
    prices.PriceClient(prices.RawDownloader(panel_raw), cache=cache, ttl=3600.0).daily_closes(
        ["LIVE", "DEAD"], "2026-01-02", "2026-01-15"
    )
    monkeypatch.setenv("DESK_OFFLINE", "1")
    dl = prices.RawDownloader(panel_raw)
    c = prices.PriceClient(dl, cache=cache, ttl=3600.0)
    out = c.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    assert dl.calls == []
    assert out.loc["2026-01-15", "LIVE"] == 109.0
    assert pd.isna(out.loc["2026-01-15", "DEAD"])
    # The entries are inside their TTL, so this really is a fresh cache hit.
    assert c.last_source == {"LIVE": "cache", "DEAD": "cache"}


def test_offline_with_an_empty_cache_raises_a_clear_offline_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_OFFLINE", "1")
    c = prices.PriceClient(prices.RawDownloader(None), cache=Cache(tmp_path))
    with pytest.raises(Offline) as excinfo:
        c.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15")
    message = str(excinfo.value)
    assert "LIVE" in message and "nothing is cached" in message and str(tmp_path) in message


def test_a_failed_download_falls_back_to_a_stale_cache(tmp_path, panel_raw):
    cache = Cache(tmp_path, default_ttl=0.0)
    warm = prices.PriceClient(prices.RawDownloader(panel_raw), cache=cache, ttl=0.0)
    warm.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")
    broken = prices.PriceClient(
        prices.OfflineDownloader(FetchError("429 from yahoo", status=429)), cache=cache, ttl=0.0
    )
    out = broken.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")
    assert broken.last_source == {"LIVE": "stale"}
    assert "429" in (broken.last_error or "")
    assert out.loc["2026-01-15", "LIVE"] == 109.0


def test_a_failed_download_with_no_cache_raises_offline(tmp_path):
    c = prices.PriceClient(prices.OfflineDownloader(FetchError("boom")), cache=Cache(tmp_path))
    with pytest.raises(Offline):
        c.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")


def test_a_name_yahoo_has_nothing_for_is_remembered_as_empty(client, tmp_path):
    client.daily_closes(["GHOST"], "2026-01-02", "2026-01-15")
    assert client.last_source == {"GHOST": "missing"}
    written = list(tmp_path.glob("closes_GHOST_*.json"))
    assert len(written) == 1
    blob = json.loads(written[0].read_text())
    assert blob["payload"]["close"] == []


def test_the_cache_file_is_plain_json_with_no_nan_token(client, tmp_path):
    client.daily_closes(["DEAD"], "2026-01-02", "2026-01-15")
    path = next(iter(tmp_path.glob("closes_DEAD_*.json")))
    text = path.read_text()
    assert "NaN" not in text and "Infinity" not in text
    payload = json.loads(text)["payload"]
    assert payload["adjusted"] is True
    assert payload["close"] == [50.0, 51.0, 52.0, 53.0, 10.0]
    assert payload["index"][-1] == "2026-01-08"


def test_refresh_bypasses_the_cache(client):
    client.daily_closes(["LIVE"], "2026-01-02", "2026-01-15")
    client.daily_closes(["LIVE"], "2026-01-02", "2026-01-15", refresh=True)
    assert len(client._dl.calls) == 2
    assert client.last_source == {"LIVE": "live"}


def test_an_empty_ticker_list_is_an_empty_frame_not_a_download(client):
    out = client.daily_closes([], "2026-01-02", "2026-01-15")
    assert out.empty and list(out.columns) == []
    assert client._dl.calls == []


def test_an_end_before_the_start_is_refused(client):
    with pytest.raises(ValueError):
        client.daily_closes(["LIVE"], "2026-01-15", "2026-01-02")


def test_tickers_are_normalised_and_deduplicated(client):
    out = client.daily_closes([" live ", "LIVE", "dead"], "2026-01-02", "2026-01-15")
    assert list(out.columns) == ["LIVE", "DEAD"]
    assert client._dl.calls == [("LIVE", "DEAD")]


def test_concurrency_is_capped_at_two():
    """yfinance throttles on parallelism, and a throttle costs more than the threads save."""
    assert prices.MAX_CONCURRENCY == 2
    assert prices.YFinanceDownloader(threads=16).threads == 2
    assert prices.YFinanceDownloader(threads=1).threads == 1


def test_the_live_downloader_refuses_to_fetch_while_offline(monkeypatch):
    monkeypatch.setenv("DESK_OFFLINE", "1")
    with pytest.raises(Offline):
        prices.YFinanceDownloader().download(["AAPL"], pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-15"))


def test_the_module_level_function_delegates_to_the_given_client(client):
    """The three argument form in the module namespace is the one callers reach for."""
    out = prices.daily_closes(["LIVE", "DEAD"], "2026-01-02", "2026-01-15", client=client)
    assert list(out.columns) == ["LIVE", "DEAD"]
    assert out.loc["2026-01-15", "LIVE"] == 109.0
    assert pd.isna(out.loc["2026-01-15", "DEAD"])
    assert len(client._dl.calls) == 1
