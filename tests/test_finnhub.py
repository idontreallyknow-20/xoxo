import json
import time
from dataclasses import dataclass, field
from datetime import date
from inspect import signature
from pathlib import Path

import pytest

from an import finnhub
from an.finnhub import BadKey, FinnhubClient, MissingKey, PremiumEndpoint
from an.http import DryRunTransport, FixtureTransport, redact
from an.store import Cache, FetchError, Offline, RateLimited

# A key shaped like a real one, so a leak into a filename or a log is obvious.
KEY = "sk-t3st-DO-NOT-LOG-9f2a1b"

BASE = "https://finnhub.io/api/v1"
TODAY = date(2026, 9, 6)

# Query parameters in sorted order, which is what the client emits and what the
# server does not care about either way. Keyed on the exact string because
# FixtureTransport looks up the whole URL, so this table only stays honest as long
# as the ordering is canonical rather than whatever a param dict happened to hold.
URLS = {
    "quote": f"{BASE}/quote?symbol=AAPL&token={KEY}",
    "profile": f"{BASE}/stock/profile2?symbol=AAPL&token={KEY}",
    "metric": f"{BASE}/stock/metric?metric=all&symbol=AAPL&token={KEY}",
    "earnings": f"{BASE}/stock/earnings?symbol=AAPL&token={KEY}",
    "calendar": f"{BASE}/calendar/earnings?from=2026-09-06&symbol=AAPL&to=2026-12-05&token={KEY}",
    "news": f"{BASE}/company-news?from=2026-08-30&symbol=AAPL&to=2026-09-06&token={KEY}",
    "recommendation": f"{BASE}/stock/recommendation?symbol=AAPL&token={KEY}",
    "insider": f"{BASE}/stock/insider-transactions?from=2026-03-10&symbol=AAPL&to=2026-09-06&token={KEY}",
}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", KEY)
    return KEY


@pytest.fixture
def transport(load_fixture):
    t = FixtureTransport()
    t.add(URLS["quote"], load_fixture("finnhub_quote_aapl.json"))
    t.add(URLS["profile"], load_fixture("finnhub_profile_aapl.json"))
    t.add(URLS["metric"], load_fixture("finnhub_metric_aapl.json"))
    t.add(URLS["earnings"], load_fixture("finnhub_earnings_aapl.json"))
    t.add(URLS["calendar"], load_fixture("finnhub_calendar_aapl.json"))
    t.add(URLS["news"], load_fixture("finnhub_news_aapl.json"))
    t.add(URLS["recommendation"], load_fixture("finnhub_recommendation_aapl.json"))
    t.add(URLS["insider"], load_fixture("finnhub_insider_aapl.json"))
    t.add(f"{BASE}/stock/metric?metric=all&symbol=TINY&token={KEY}", load_fixture("finnhub_metric_sparse.json"))
    t.add(f"{BASE}/stock/profile2?symbol=TINY&token={KEY}", load_fixture("finnhub_profile_sparse.json"))
    t.add(f"{BASE}/quote?symbol=NOPE&token={KEY}", load_fixture("finnhub_quote_unknown.json"))
    t.add(f"{BASE}/quote?symbol=HALT&token={KEY}", load_fixture("finnhub_quote_halted.json"))
    return t


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "finnhub", default_ttl=86_400.0)


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin date.today() so the windowed endpoints resolve to the fixture URLs.

    Without this, every news and calendar assertion would start failing at
    midnight, which is the least useful kind of flake.
    """

    class _Frozen(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(finnhub, "date", _Frozen)
    return TODAY


@pytest.fixture
def client(key, transport, cache):
    return FinnhubClient(transport=transport, cache=cache)


@dataclass
class StatusTransport:
    """Fails every call with one HTTP status, the way HttpTransport would."""

    status: int
    calls: list = field(default_factory=list)

    def get(self, url, *, headers=None, timeout=30.0):
        self.calls.append(url)
        safe = redact(url)
        if self.status == 429:
            raise RateLimited(f"429 from {safe}", status=429, url=safe)
        raise FetchError(f"{self.status} from {safe}", status=self.status, url=safe)


@dataclass
class RawFetchErrorTransport:
    """A transport that reports 429 as a plain FetchError, to prove the client maps it."""

    calls: list = field(default_factory=list)

    def get(self, url, *, headers=None, timeout=30.0):
        self.calls.append(url)
        raise FetchError("429 Too Many Requests", status=429, url=redact(url))


@dataclass
class BodyTransport:
    """Answers 200 with a fixed body. Finnhub does this with error objects."""

    body: bytes
    calls: list = field(default_factory=list)

    def get(self, url, *, headers=None, timeout=30.0):
        self.calls.append(url)
        return self.body


def _age_entry(cache, key_name, seconds):
    """Backdate a cache entry so the next read sees it as stale."""
    p = cache.path_for(key_name)
    blob = json.loads(p.read_text())
    blob["fetched_at"] = time.time() - seconds
    p.write_text(json.dumps(blob))


# -- the key ----------------------------------------------------------------


def test_api_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("FINNHUB_KEY", KEY)
    assert finnhub.api_key() == KEY


def test_api_key_missing_names_the_env_var(monkeypatch):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    with pytest.raises(MissingKey) as e:
        finnhub.api_key()
    assert "FINNHUB_KEY" in str(e.value)


def test_api_key_blank_or_whitespace_counts_as_missing(monkeypatch):
    for blank in ("", "   ", "\n"):
        monkeypatch.setenv("FINNHUB_KEY", blank)
        with pytest.raises(MissingKey):
            finnhub.api_key()


def test_the_key_is_not_a_constructor_argument():
    """There must be no way to pass a key in, so there is no way to commit one."""
    params = signature(FinnhubClient.__init__).parameters
    assert not [p for p in params if "key" in p.lower() or "token" in p.lower()]


def test_the_client_never_stores_the_key(client):
    """Nothing on the instance can spill it into a repr, a pickle or a crash dump.

    The transport is exempt only because FixtureTransport is a test double that
    keys its recorded responses by full URL; a real transport keeps nothing.
    """
    client.quote("AAPL")
    assert KEY not in repr(client)
    assert not [k for k in vars(client) if "key" in k.lower() or "token" in k.lower()]
    assert not [v for k, v in vars(client).items() if k != "transport" and KEY in str(v)]


def test_missing_key_is_raised_at_call_time_not_construction(monkeypatch, transport, cache):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    c = FinnhubClient(transport=transport, cache=cache)  # constructing is fine
    with pytest.raises(MissingKey) as e:
        c.quote("AAPL")
    assert "FINNHUB_KEY" in str(e.value)
    assert transport.calls == []


def test_key_never_reaches_a_log_line(key, cache):
    lines = []
    c = FinnhubClient(transport=DryRunTransport(sink=lines.append), cache=cache)
    with pytest.raises(Offline):
        c.quote("AAPL")
    assert lines, "the dry run transport should have printed something"
    joined = "\n".join(lines)
    assert KEY not in joined
    assert "***REDACTED***" in joined


def test_key_never_reaches_an_exception_message(key, cache):
    c = FinnhubClient(transport=FixtureTransport(), cache=cache)
    with pytest.raises(FetchError) as e:
        c.quote("AAPL")
    assert KEY not in str(e.value)
    assert KEY not in repr(e.value)
    assert KEY not in str(getattr(e.value, "url", ""))

    for status, exc in ((401, BadKey), (403, BadKey), (429, RateLimited)):
        c = FinnhubClient(transport=StatusTransport(status), cache=Cache(cache.dir / str(status)))
        with pytest.raises(exc) as e:
            c.quote("AAPL")
        assert KEY not in str(e.value)
        assert KEY not in repr(e.value)


def test_key_never_reaches_a_cache_filename(client, cache):
    client.quote("AAPL")
    client.metrics("AAPL")
    client.company_news("AAPL", today=TODAY)
    names = [p.name for p in cache.dir.glob("*.json")]
    assert names
    assert not [n for n in names if KEY in n]
    assert "quote_AAPL.json" in names


def test_a_secret_passed_to_the_escape_hatch_never_reaches_a_cache_filename(key, cache):
    """get() is the documented "any endpoint" door, so a caller passing auth is expected.

    The value never reaches the wire -- _build_url overwrites token -- but the cache
    key was built from the caller's raw params, so it became a filename, and a
    filename outlives the environment variable, the process and any rotation.
    """
    passed_in = "sk-passed-in-DO-NOT-LOG-0c4d"
    t = FixtureTransport()
    # token is overwritten by the client, apiKey rides along as the caller sent it.
    t.add(f"{BASE}/quote?apiKey={passed_in}&symbol=AAPL&token={KEY}", {"c": 268.32, "pc": 269.76, "t": 1788724800})
    c = FinnhubClient(transport=t, cache=cache)
    c.get("/quote", {"symbol": "AAPL", "token": passed_in, "apiKey": passed_in})

    names = [p.name for p in cache.dir.glob("*.json")]
    assert names, "the call should have been cached"
    assert not [n for n in names if passed_in in n or KEY in n]
    assert "symbol-AAPL" in names[0], "the harmless params still identify the entry"
    for p in cache.dir.glob("*.json"):
        assert passed_in not in p.read_text() and KEY not in p.read_text()


def test_key_never_reaches_the_cache_file_on_disk(client, cache):
    """The stored meta carries a URL, so this is the easiest place to leak one."""
    client.quote("AAPL")
    client.profile("AAPL")
    for p in cache.dir.glob("*.json"):
        text = p.read_text()
        assert KEY not in text, p
        blob = json.loads(text)
        assert "***REDACTED***" in blob["meta"]["url"]
        assert "token=" in blob["meta"]["url"]


def test_preview_url_is_redacted_and_needs_no_key(monkeypatch, cache):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    c = FinnhubClient(transport=FixtureTransport(), cache=cache)
    url = c.preview_url("/quote", {"symbol": "AAPL"})
    assert url == f"{BASE}/quote?symbol=AAPL&token=***REDACTED***"


# -- tier and rate limit ----------------------------------------------------


def test_403_on_a_paid_endpoint_raises_premium_endpoint_carrying_the_endpoint(key, cache):
    c = FinnhubClient(transport=StatusTransport(403), cache=cache)
    with pytest.raises(PremiumEndpoint) as e:
        c.get("/stock/candle", {"symbol": "AAPL", "resolution": "D"}, allow_premium=True)
    assert e.value.endpoint == "/stock/candle"
    assert e.value.status == 403
    assert isinstance(e.value, FetchError)  # a caller catching FetchError still sees it


def test_403_on_a_free_endpoint_is_a_key_problem_not_a_tier_gap(key, cache):
    """A blanket 403 used to report every section as a benign premium gap.

    /quote is free on every tier, so a 403 there is a revoked key or something
    upstream refusing the call. Called a PremiumEndpoint it cost nothing and counted
    as nothing: a nightly run over the universe printed "unavailable on this tier"
    for every section and exited 0 with an empty cache.
    """
    t = StatusTransport(403)
    c = FinnhubClient(transport=t, cache=cache)
    with pytest.raises(BadKey) as e:
        c.quote("AAPL")
    assert e.value.status == 403
    assert not isinstance(e.value, PremiumEndpoint)
    assert "/quote" in str(e.value) and "FINNHUB_KEY" in str(e.value)
    assert KEY not in str(e.value)

    import fetch_finnhub

    with pytest.raises(BadKey):
        fetch_finnhub.report(c, "AAPL", ["quote", "profile", "metrics"])
    assert len(t.calls) == 2, "one probe above, one in report(): the run stops, it does not sweep"


def test_probe_premium_does_not_report_a_key_error_as_a_tier_answer(key, cache):
    """Eight paid endpoints times one call each, all answering "your key is dead"."""
    t = StatusTransport(401)
    c = FinnhubClient(transport=t, cache=cache)
    with pytest.raises(BadKey):
        c.probe_premium("/stock/candle", {"symbol": "AAPL"})
    assert len(t.calls) == 1


def test_known_premium_endpoints_are_refused_before_a_call_is_spent(key, cache):
    t = StatusTransport(403)
    c = FinnhubClient(transport=t, cache=cache)
    for endpoint in ("/stock/candle", "/stock/price-target", "/news-sentiment"):
        with pytest.raises(PremiumEndpoint) as e:
            c.get(endpoint, {"symbol": "AAPL"})
        assert e.value.endpoint == endpoint
        assert e.value.detail
    assert t.calls == [], "a refusal must not burn one of the 60 calls a minute"


def test_premium_can_be_probed_deliberately(key, cache):
    t = StatusTransport(403)
    c = FinnhubClient(transport=t, cache=cache)
    reachable, why = c.probe_premium("/stock/candle", {"symbol": "AAPL"})
    assert reachable is False
    assert "/stock/candle" in why
    assert len(t.calls) == 1, "an explicit probe is allowed to spend a call"


def test_error_body_returned_with_a_200_is_still_a_premium_refusal(key, cache, fixtures_dir):
    body = (Path(fixtures_dir) / "finnhub_no_access.json").read_bytes()
    c = FinnhubClient(transport=BodyTransport(body), cache=cache)
    with pytest.raises(PremiumEndpoint) as e:
        c.quote("AAPL")
    assert "access" in str(e.value).lower()
    assert list(cache.dir.glob("*.json")) == [], "an error body must never be cached as data"


@pytest.mark.parametrize(
    "message, expected, unexpected",
    [
        ("You don't have access to this resource.", PremiumEndpoint, BadKey),
        ("Invalid API key", BadKey, PremiumEndpoint),
        # Not mapped to RateLimited today: the 429 status is, a 200 body saying so
        # is not. Pinned here so a change to that is a deliberate one.
        ("You have exceeded your rate limits", FetchError, BadKey),
    ],
)
def test_every_branch_of_the_200_error_object_is_pinned(key, cache, message, expected, unexpected):
    c = FinnhubClient(transport=BodyTransport(json.dumps({"error": message}).encode()), cache=cache)
    with pytest.raises(expected) as e:
        c.quote("AAPL")
    assert not isinstance(e.value, unexpected)
    assert list(cache.dir.glob("*.json")) == []


def test_an_error_body_never_echoes_the_key_back_to_the_terminal(key, cache, capsys):
    """The server's own text is the one string here that skipped redact().

    The realistic writer is not Finnhub but something in front of it: a proxy or a
    WAF answering a blocked request quotes the URL it refused, and that URL carries
    token=<the key>. report() prints these messages straight to stdout.
    """
    import fetch_finnhub

    blocked = f"blocked: {BASE}/quote?symbol=AAPL&token={KEY}"
    c = FinnhubClient(transport=BodyTransport(json.dumps({"error": blocked}).encode()), cache=cache)
    with pytest.raises(FetchError) as e:
        c.quote("AAPL")
    assert KEY not in str(e.value) and KEY not in repr(e.value)
    assert "***REDACTED***" in str(e.value)

    assert fetch_finnhub.report(c, "AAPL", ["quote"]) == 1
    assert KEY not in capsys.readouterr().out


def test_an_error_body_that_quotes_the_key_bare_is_scrubbed_and_truncated(key, cache):
    body = json.dumps({"error": f"Invalid API key {KEY} " + "x" * 500}).encode()
    c = FinnhubClient(transport=BodyTransport(body), cache=cache)
    with pytest.raises(BadKey) as e:
        c.quote("AAPL")
    assert KEY not in str(e.value)
    assert len(str(e.value)) < 400, "a page of HTML must not become an exception message"


def test_401_raises_bad_key(key, cache):
    c = FinnhubClient(transport=StatusTransport(401), cache=cache)
    with pytest.raises(BadKey) as e:
        c.quote("AAPL")
    assert e.value.status == 401
    assert "FINNHUB_KEY" in str(e.value)
    assert not isinstance(e.value, PremiumEndpoint)


def test_429_raises_rate_limited(key, cache):
    c = FinnhubClient(transport=RawFetchErrorTransport(), cache=cache)
    with pytest.raises(RateLimited) as e:
        c.quote("AAPL")
    assert e.value.status == 429
    assert "60 calls a minute" in str(e.value)


def test_rate_limited_from_the_transport_passes_straight_through(key, cache):
    c = FinnhubClient(transport=StatusTransport(429), cache=cache)
    with pytest.raises(RateLimited):
        c.quote("AAPL")


def test_an_ordinary_failure_stays_a_fetch_error(key, cache):
    c = FinnhubClient(transport=StatusTransport(500), cache=cache)
    with pytest.raises(FetchError) as e:
        c.quote("AAPL")
    assert not isinstance(e.value, (BadKey, PremiumEndpoint, RateLimited))


def test_the_published_rate_limit_is_wired_into_the_transport_the_client_builds():
    """Restating the constants proved nothing: every other test injects a transport.

    HttpTransport defaults to 8 calls a second, which is 480 a minute against a
    60-a-minute key, so dropping the argument would spend the first real run in
    backoff. Constructing the default client touches neither disk nor network.
    """
    assert finnhub.RATE_PER_SECOND <= finnhub.CALLS_PER_MINUTE / 60.0
    assert FinnhubClient().transport.rate_per_second == finnhub.RATE_PER_SECOND

    import fetch_finnhub

    assert fetch_finnhub.build_client(dry_run=False).transport.rate_per_second == finnhub.RATE_PER_SECOND


# -- parsing ----------------------------------------------------------------


def test_quote_parses(client):
    q = client.quote("aapl")
    assert q.symbol == "AAPL"
    assert q.current == 268.32
    assert q.change == -1.44 and q.percent_change == -0.5338
    assert q.high == 271.5 and q.low == 266.11 and q.open == 270.2
    assert q.previous_close == 269.76
    assert q.as_of == "2026-09-06T20:00:00+00:00"
    assert q.is_empty is False
    # 268.32 in a 266.11-271.50 day: 41% of the way up from the low, not down from
    # the high. An inverted formula also satisfies 0 < x < 1, which is what the old
    # assertion checked and all it checked.
    assert q.range_position == pytest.approx(0.410019, abs=1e-6)


@pytest.mark.parametrize("high, low", [(271.5, 271.5), (266.11, 271.5), (0, 0)])
def test_range_position_is_none_when_the_day_range_is_degenerate(high, low):
    """A halted name prints h == l == c. Dividing by that range is a crash, not a number."""
    q = finnhub.Quote.from_payload("X", {"c": 268.32, "pc": 269.76, "h": high, "l": low, "t": 1788724800})
    assert q.range_position is None


def test_quote_for_an_unknown_symbol_is_none_never_zero(client):
    """Finnhub answers an unknown ticker with zeros. A zero price sorts first on cheapness."""
    q = client.quote("NOPE")
    assert q.is_empty is True
    assert q.current is None and q.previous_close is None and q.high is None
    assert q.timestamp is None and q.as_of is None
    assert q.range_position is None
    assert 0.0 not in (q.current, q.high, q.low, q.open, q.previous_close)


def test_a_zeroed_quote_with_a_server_timestamp_is_still_empty(client):
    """The delisted-or-halted shape: prices zeroed, ``t`` stamped all the same.

    The collapse used to require t, c and pc to be falsy together, so this body
    escaped it and produced current == 0.0 with is_empty False -- a $0.00 price at
    the top of a cheapness ranking, which is the one thing the guard exists for.
    """
    q = client.quote("HALT")
    assert q.is_empty is True
    assert q.current is None and q.previous_close is None and q.timestamp is None
    assert 0.0 not in (q.current, q.high, q.low, q.open, q.previous_close)


def test_a_zeroed_last_price_next_to_a_real_previous_close_is_not_a_price_of_zero():
    """The other partial shape: c failed to populate, pc did not."""
    q = finnhub.Quote.from_payload(
        "X", {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 269.76, "t": 0}
    )
    assert q.current is None, "0.00 is Finnhub saying nothing, not a price"
    assert q.previous_close == 269.76
    assert q.high is None and q.low is None and q.open is None
    assert q.is_empty is False, "there is one usable number here, so the record is not empty"
    assert q.range_position is None


def test_a_flat_day_keeps_its_zero_change():
    """Zero is not a price, but it is a perfectly good change: only prices collapse."""
    q = finnhub.Quote.from_payload("X", {"c": 269.76, "d": 0, "dp": 0, "pc": 269.76, "t": 1788724800})
    assert q.change == 0.0 and q.percent_change == 0.0
    assert q.current == 269.76 and q.is_empty is False


def test_profile_parses(client):
    p = client.profile("AAPL")
    assert p.name == "Apple Inc"
    assert p.country == "US" and p.currency == "USD"
    assert p.exchange.startswith("NASDAQ")
    assert p.ipo == "1980-12-12"
    assert p.finnhub_industry == "Technology"
    assert p.industry == "Consumer Electronics"
    assert p.weburl == "https://www.apple.com/" and p.logo.endswith("AAPL.svg")
    assert p.market_cap_millions == 3982140.5
    assert p.market_cap == pytest.approx(3.9821405e12)
    assert p.shares_outstanding == pytest.approx(1.484039e10)
    assert p.is_empty is False


def test_profile_with_empty_fields_is_none_not_zero(client):
    p = client.profile("TINY")
    assert p.name == "Tiny Holdings Corp"
    assert p.country is None and p.ipo is None
    assert p.market_cap_millions is None and p.market_cap is None
    assert p.shares_outstanding is None
    assert p.finnhub_industry is None and p.industry is None


def test_metrics_parse_the_ratios_the_scorecard_reads(client):
    m = client.metrics("AAPL")
    assert m.symbol == "AAPL"
    assert m.pe == 33.9147
    assert m.pb == 61.3714 and m.ps == 8.9411 and m.ev_ebitda == 24.1806
    assert m.roe == 151.3119 and m.roa == 28.3106 and m.roi == 61.2004
    assert m.gross_margin == 46.8312 and m.operating_margin == 31.7702 and m.net_margin == 26.4419
    assert m.current_ratio == 0.8683
    assert m.debt_to_equity == 1.8871 and m.long_term_debt_to_equity == 1.4712
    assert m.high_52w == 288.62 and m.low_52w == 191.4
    assert m.return_52w == 21.7714 and m.return_26w == 14.0327 and m.return_13w == 8.4213
    assert m.volatility_3m == 27.5148 and m.beta == 1.1092
    assert m.eps_growth_3y == 12.6104 and m.eps_growth_5y == 15.9218
    assert m.revenue_growth_3y == 6.2044 and m.revenue_growth_5y == 8.7311
    assert m.dividend_yield == 0.4102 and m.payout_ratio == 15.2033
    assert m.book_value_per_share == 4.4217 and m.cash_flow_per_share == 7.3118
    assert m.get_text("52WeekHighDate") == "2026-08-14"


def test_metrics_missing_keys_return_none_rather_than_raising(client):
    """Roughly a hundred keys exist and any of them can be absent on any day."""
    m = client.metrics("TINY")
    assert m.beta == 0.9114
    assert m.get("peTTM") is None  # present but null
    assert m.get("evEbitdaTTM") is None  # absent entirely
    assert m.get("noSuchMetricAnywhere") is None
    assert m.pe is None and m.roe is None and m.current_ratio is None
    assert m.get_text("52WeekHighDate") is None
    assert m.summary()["pe"] is None


def test_ev_ebitda_is_never_filled_in_from_ev_free_cash_flow(client):
    """Every other first() chain lists synonyms. This one used to list a different multiple.

    EV/FCF runs one and a half to three times EV/EBITDA on the same business, so a
    name with only the fallback populated read as several times more expensive than
    a peer measured on the real thing, on a key labelled EV/EBITDA all the way
    down into the scorecard.
    """
    tiny = client.metrics("TINY")
    assert tiny.get("evEbitdaTTM") is None and tiny.get("currentEv/freeCashFlowTTM") == 45.0
    assert tiny.ev_ebitda is None
    assert tiny.ev_fcf == 45.0
    assert tiny.summary()["ev_ebitda"] is None
    assert tiny.summary()["ev_fcf"] == 45.0

    aapl = client.metrics("AAPL")
    assert aapl.ev_ebitda == 24.1806 and aapl.ev_fcf is None


def test_metrics_first_is_preference_ordered(client):
    m = client.metrics("AAPL")
    assert m.first("notThere", "peTTM") == 33.9147
    assert m.first("peBasicExclExtraTTM", "peTTM") == 34.0219
    assert m.first("notThere", "alsoNotThere") is None
    assert m.present(["peTTM", "notThere"]) == {"peTTM": 33.9147}


def test_series_helper_survives_a_payload_with_no_series_at_all(client):
    """The sparse fixture has no series key. This is the common case for small names."""
    m = client.metrics("TINY")
    assert m.series == {}
    assert m.series_names() == []
    assert m.series_points("currentRatio") == []
    assert m.series_points("currentRatio", "quarterly") == []
    assert m.series_latest("currentRatio") is None
    assert m.series_history("salesPerShare") == []


def test_series_points_parse_newest_first_and_drop_valueless_points(client):
    m = client.metrics("AAPL")
    assert m.series_names() == ["currentRatio", "netMargin", "salesPerShare"]
    assert m.series_names("quarterly") == ["currentRatio", "netMargin"]

    pts = m.series_points("currentRatio")
    assert [p.period for p in pts] == ["2026-09-26", "2025-09-27", "2024-09-28", "2023-09-30"]
    assert pts[0].value == 0.8683

    margins = m.series_points("netMargin")
    assert [p.period for p in margins] == ["2026-09-26", "2025-09-27", "2023-09-30"]
    assert None not in [p.value for p in margins]

    assert m.series_points("nothingLikeThis") == []
    assert m.series_points("currentRatio", "monthly") == []


def test_series_latest_and_history(client):
    m = client.metrics("AAPL")
    assert m.series_latest("currentRatio") == 0.8683
    assert m.series_latest("currentRatio", "quarterly") == 0.8683
    assert m.series_latest("netMargin", "quarterly") == 0.2712
    assert m.series_history("salesPerShare") == [28.1104, 26.0113, 25.0091]
    assert m.series_history("salesPerShare", limit=2) == [28.1104, 26.0113]
    assert m.series_latest("nothingLikeThis") is None


def test_metric_summary_is_floats_or_none(client):
    """A smoke test over the real fixtures. The NaN guarantee is pinned below."""
    for symbol in ("AAPL", "TINY"):
        for name, value in client.metrics(symbol).summary().items():
            assert value is None or isinstance(value, float), name


def test_nan_and_infinity_never_survive_parsing():
    """Python's json accepts NaN and Infinity, and Finnhub is not the only writer upstream.

    No fixture can carry these -- ``json.dumps`` would not round-trip them through
    FixtureTransport -- so the guard needs a payload built the way a real
    ``json.loads`` would build it. A NaN compares False against every threshold and
    sorts arbitrarily, which is the failure this module calls the worst kind of
    wrong.
    """
    payload = json.loads(
        '{"metric": {"peTTM": NaN, "pbAnnual": 1e999, "psTTM": -1e999, "roeTTM": 12.0,'
        ' "52WeekHighDate": "nan"}}'
    )
    m = finnhub.Metrics.from_payload("XYZ", payload)
    assert m.pe is None and m.pb is None and m.ps is None
    assert m.roe == 12.0, "the neighbours still parse"
    assert m.get_text("52WeekHighDate") is None
    for name, value in m.summary().items():
        assert value is None or value == value, name

    q = finnhub.Quote.from_payload("XYZ", {"c": float("nan"), "pc": float("inf"), "t": 1788724800})
    assert q.is_empty is True and q.current is None


def test_earnings_rows_parse(client):
    rows = client.earnings("AAPL")
    assert len(rows) == 4
    assert [r.period for r in rows] == ["2026-06-27", "2026-03-28", "2025-12-27", "2025-09-27"]
    top = rows[0]
    assert top.actual == 2.41 and top.estimate == 2.3534
    assert top.surprise == 0.0566 and top.surprise_percent == 2.4046
    assert top.year == 2026 and top.quarter == 3
    assert top.label == "2026Q3"
    assert top.beat is True

    unestimated = rows[-1]
    assert unestimated.estimate is None and unestimated.surprise is None
    assert unestimated.beat is None, "no estimate means unknown, not a miss"


def test_calendar_rows_parse(client):
    rows = client.earnings_calendar("AAPL", today=TODAY)
    assert [r.date for r in rows] == ["2026-10-29", "2027-01-28"]
    nxt = rows[0]
    assert nxt.eps_estimate == 2.6104
    assert nxt.revenue_estimate == 141230000000
    assert nxt.eps_actual is None and nxt.revenue_actual is None
    assert nxt.hour == "amc" and nxt.session == "after market close"
    assert nxt.quarter == 4 and nxt.year == 2026
    assert nxt.has_consensus is True

    placeholder = rows[1]
    assert placeholder.hour is None and placeholder.session is None
    assert placeholder.eps_estimate is None and placeholder.has_consensus is False


def test_the_calendar_reports_coverage_and_does_not_claim_to_confirm_a_date(client):
    """``is_confirmed`` measured whether an analyst had published an estimate.

    That is coverage, in both directions: a Finnhub-projected date for a covered
    large cap carries an estimate and would have read as confirmed, and a company
    that has announced its date but has no following carries none. The endpoint has
    no confirmation field, so the property is named for what it can actually see.
    """
    row = finnhub.CalendarRow.from_payload("X", {"date": "2026-11-04", "epsEstimate": None, "hour": "amc"})
    assert row.has_consensus is False
    assert row.date == "2026-11-04" and row.session == "after market close"
    assert not hasattr(row, "is_confirmed")


def test_news_items_parse(client):
    items = client.company_news("AAPL", today=TODAY)
    assert len(items) == 3
    assert items[0].headline.startswith("Apple lifts services guidance")
    assert items[0].source == "Reuters"
    assert items[0].published == "2026-09-05T00:00:00+00:00"
    assert items[0].day == "2026-09-05"
    assert items[1].summary is None and items[1].image is None
    assert items[2].timestamp is None and items[2].published is None and items[2].day is None
    assert items[2].url is None


def test_control_characters_are_stripped_from_remote_text(key, cache, frozen_today):
    """A headline is written by someone else and printed to a terminal.

    A bare CR or an ANSI escape in one rewrites the lines above it, which are the
    ``! quote: ...`` failure lines the operator is being asked to read before
    trusting the run. Stripping happens in the parser, so every record type is
    covered at once and no print site has to remember.
    """
    hostile = [
        {
            "headline": "CLEAN LINE\r\x1b[31mINJECTED\x1b[0m",
            "source": "Wire\x1b[2K",
            "summary": "one\nline\ttwo",
            "datetime": 1788566400,
            "id": 1,
            "url": "https://example.com/x",
        }
    ]
    c = FinnhubClient(transport=BodyTransport(json.dumps(hostile).encode()), cache=cache)
    n = c.company_news("AAPL", today=TODAY)[0]
    assert n.headline == "CLEAN LINE [31mINJECTED[0m"
    assert n.source == "Wire[2K"
    assert n.summary == "one line two"
    for text in (n.headline, n.source, n.summary):
        assert not [ch for ch in text if ord(ch) < 32 or ord(ch) == 127]

    p = finnhub.Profile.from_payload("X", {"name": "Acme\r\x1b[2KInc", "ticker": "X"})
    assert p.name == "Acme [2KInc"


def test_recommendations_parse(client):
    rows = client.recommendations("AAPL")
    assert [r.period for r in rows] == ["2026-09-01", "2026-08-01", "2026-07-01"]
    top = rows[0]
    assert top.strong_buy == 9 and top.buy == 24 and top.hold == 12
    assert top.sell == 1 and top.strong_sell == 0
    assert top.total == 46
    assert top.bullish_share == pytest.approx(33 / 46)
    assert top.complete is True
    assert finnhub.Recommendation(symbol="X").bullish_share is None


def test_a_recommendation_with_a_missing_bucket_reports_no_total():
    """Summing what parsed puts a smaller denominator in than the API measured.

    A null hold on a row of 52 analysts summed to 40 and turned a 54% bullish share
    into 70%, printed by the CLI as "40 covering, 70% bullish" with nothing to say a
    bucket was missing.
    """
    partial = finnhub.Recommendation.from_payload(
        "X", {"buy": 24, "hold": None, "sell": 12, "strongBuy": 4, "strongSell": 0}
    )
    assert partial.complete is False
    assert partial.total is None
    assert partial.bullish_share is None

    full = finnhub.Recommendation.from_payload(
        "X", {"buy": 24, "hold": 12, "sell": 12, "strongBuy": 4, "strongSell": 0}
    )
    assert full.complete is True and full.total == 52
    assert full.bullish_share == pytest.approx(28 / 52)


def test_insider_transactions_parse(client):
    rows = client.insider_transactions("AAPL", start="2026-03-10", end="2026-09-06")
    assert len(rows) == 2
    assert [r.transaction_date for r in rows] == ["2026-04-01", "2026-02-11"]
    sale = [r for r in rows if r.transaction_code == "S"][0]
    assert sale.name == "COOK TIMOTHY D"
    assert sale.change == -511000 and sale.share == 3280000
    assert sale.transaction_price == 223.42
    assert sale.is_open_market_sale is True and sale.is_open_market_buy is False
    exercise = [r for r in rows if r.transaction_code == "M"][0]
    assert exercise.is_open_market_sale is False


# -- caching ----------------------------------------------------------------


def test_ttls_are_set_per_endpoint(client):
    ttls = {p.endpoint: p.ttl_seconds for p in client.plan("AAPL", today=TODAY)}
    assert ttls["/quote"] == 60.0
    assert ttls["/stock/profile2"] == 30 * 86_400.0
    assert ttls["/stock/metric"] == 86_400.0
    assert ttls["/stock/earnings"] == 86_400.0
    assert ttls["/calendar/earnings"] == 6 * 3_600.0
    assert ttls["/company-news"] == 6 * 3_600.0


def test_a_fresh_cache_entry_prevents_a_second_call(client, transport):
    client.quote("AAPL")
    n = len(transport.calls)
    client.quote("AAPL")
    assert len(transport.calls) == n


def test_a_fresh_cache_entry_is_served_without_a_key(monkeypatch, client, transport, cache):
    client.metrics("AAPL")
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    assert client.metrics("AAPL").pe == 33.9147


def test_a_stale_entry_is_served_when_the_fetch_fails(key, transport, cache):
    c = FinnhubClient(transport=transport, cache=cache)
    assert c.quote("AAPL").current == 268.32
    _age_entry(cache, "quote_AAPL", 10_000)
    broken = FinnhubClient(transport=StatusTransport(503), cache=cache)
    assert broken.quote("AAPL").current == 268.32


def test_a_stale_entry_never_hides_a_tier_error(key, transport, cache):
    """A revoked or downgraded key has to surface the day it happens."""
    c = FinnhubClient(transport=transport, cache=cache)
    c.quote("AAPL")
    _age_entry(cache, "quote_AAPL", 10_000)
    for status in (401, 403):
        with pytest.raises(BadKey):
            FinnhubClient(transport=StatusTransport(status), cache=cache).quote("AAPL")


def test_a_stale_entry_never_hides_a_premium_refusal(key, cache):
    """The same carve-out on the paid side, where the 403 really is the tier."""
    params = {"symbol": "AAPL", "resolution": "D"}
    t = FixtureTransport()
    t.add(f"{BASE}/stock/candle?resolution=D&symbol=AAPL&token={KEY}", {"s": "ok", "c": [1.0]})
    FinnhubClient(transport=t, cache=cache).get("/stock/candle", params, allow_premium=True)
    stored = [p.stem for p in cache.dir.glob("*.json")]
    assert len(stored) == 1
    _age_entry(cache, stored[0], 200_000)
    downgraded = FinnhubClient(transport=StatusTransport(403), cache=cache)
    with pytest.raises(PremiumEndpoint):
        downgraded.get("/stock/candle", params, allow_premium=True)


def test_stale_on_error_false_refuses_the_stale_copy_it_was_told_to_refuse(key, transport, cache):
    """The flag exists for a freshness-critical run, so it has to actually do something."""
    FinnhubClient(transport=transport, cache=cache).quote("AAPL")
    _age_entry(cache, "quote_AAPL", 10_000)
    strict = FinnhubClient(transport=StatusTransport(503), cache=cache, stale_on_error=False)
    with pytest.raises(FetchError):
        strict.quote("AAPL")
    lenient = FinnhubClient(transport=StatusTransport(503), cache=cache)
    assert lenient.quote("AAPL").current == 268.32


# -- plan and helpers -------------------------------------------------------


def test_plan_describes_every_section_without_a_key_or_a_call(monkeypatch, transport, cache):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    c = FinnhubClient(transport=transport, cache=cache)
    plan = c.plan("aapl", today=TODAY)
    assert [p.endpoint for p in plan] == [
        "/quote",
        "/stock/profile2",
        "/stock/metric",
        "/stock/earnings",
        "/calendar/earnings",
        "/company-news",
        "/stock/recommendation",
        "/stock/insider-transactions",
    ]
    assert transport.calls == []
    for p in plan:
        assert "token=***REDACTED***" in p.url
        assert KEY not in p.url and "symbol=AAPL" in p.url
    news = [p for p in plan if p.endpoint == "/company-news"][0]
    assert "from=2026-08-30&symbol=AAPL&to=2026-09-06" in news.url
    assert news.cache_key == "news_AAPL_2026-08-30_2026-09-06"


def test_the_query_string_is_built_in_a_canonical_order(client):
    """Sorted, so a reordered param dict is not a change and the cached URL is stable."""
    urls = {p.endpoint: p.url for p in client.plan("AAPL", today=TODAY)}
    assert urls["/company-news"].endswith(
        "?from=2026-08-30&symbol=AAPL&to=2026-09-06&token=***REDACTED***"
    )
    assert urls["/stock/metric"].endswith("?metric=all&symbol=AAPL&token=***REDACTED***")
    for url in urls.values():
        query = url.split("?", 1)[1]
        keys = [pair.split("=", 1)[0] for pair in query.split("&")]
        assert keys == sorted(keys), url


@pytest.mark.parametrize(
    "section, prefix", [("news", "news"), ("calendar", "calendar"), ("insiders", "insider")]
)
def test_days_zero_asks_for_one_day_rather_than_the_default_window(client, section, prefix):
    """``days or DEFAULT`` read an explicit zero as absent and handed back a week.

    date_window supports days=0 and is tested for it one layer down, so the zero was
    meaningful right up to the point where this table threw it away -- and the cache
    key then recorded the window the caller did not ask for.
    """
    p = client.plan("AAPL", sections=[section], days=0, today=TODAY)[0]
    assert "from=2026-09-06" in p.url and "to=2026-09-06" in p.url
    assert p.cache_key == f"{prefix}_AAPL_2026-09-06_2026-09-06"


def test_the_fixtures_arrive_out_of_order_so_the_sorts_are_load_bearing(load_fixture):
    """Every ordering assertion above is only worth reading if the file disagrees with it.

    The module imposes an order because the API does not promise one. These files
    are stored in an order it might plausibly send, so deleting a sort() breaks a
    test instead of going unnoticed.
    """
    assert [r["period"] for r in load_fixture("finnhub_earnings_aapl.json")][0] == "2025-09-27"
    assert [r["period"] for r in load_fixture("finnhub_recommendation_aapl.json")][0] == "2026-07-01"
    assert load_fixture("finnhub_news_aapl.json")[0]["datetime"] is None
    assert load_fixture("finnhub_calendar_aapl.json")["earningsCalendar"][0]["date"] == "2027-01-28"
    insiders = load_fixture("finnhub_insider_aapl.json")["data"]
    assert [r["transactionDate"] for r in insiders] == ["2026-02-11", "2026-04-01"]
    series = load_fixture("finnhub_metric_aapl.json")["series"]
    assert series["annual"]["currentRatio"][0]["period"] == "2023-09-30"
    assert series["quarterly"]["netMargin"][0]["period"] == "2026-03-28"


def test_plan_urls_match_what_a_real_call_requests(client, transport):
    client.quote("AAPL")
    requested = transport.calls[-1]
    planned = client.plan("AAPL", sections=["quote"])[0].url
    assert redact(requested) == planned


def test_date_window_is_injectable_in_both_directions():
    assert finnhub.date_window(7, today=TODAY) == ("2026-08-30", "2026-09-06")
    assert finnhub.date_window(90, today=TODAY, forward=True) == ("2026-09-06", "2026-12-05")
    assert finnhub.date_window(0, today=TODAY) == ("2026-09-06", "2026-09-06")
    # These three pin the windows the fixture URLs above were written against.
    assert finnhub.date_window(finnhub.NEWS_DAYS, today=TODAY) == ("2026-08-30", "2026-09-06")
    assert finnhub.date_window(finnhub.CALENDAR_DAYS, today=TODAY, forward=True) == ("2026-09-06", "2026-12-05")
    assert finnhub.date_window(finnhub.INSIDER_DAYS, today=TODAY) == ("2026-03-10", "2026-09-06")
    start, end = finnhub.date_window(30)
    assert start < end


def test_symbols_are_normalised_and_class_shares_keep_the_dot():
    assert finnhub.normalise_symbol(" brk.b ") == "BRK.B"
    assert finnhub.normalise_symbol("aapl") == "AAPL"
    with pytest.raises(ValueError):
        finnhub.normalise_symbol("  ")


def test_an_unknown_section_is_a_clear_error(client):
    with pytest.raises(ValueError) as e:
        client.plan("AAPL", sections=["candles"])
    assert "candles" in str(e.value)


def test_premium_table_names_a_free_substitute_where_one_exists():
    assert "/stock/candle" in finnhub.PREMIUM_ENDPOINTS
    assert "yfinance" in finnhub.PREMIUM_ENDPOINTS["/stock/candle"]
    assert "EDGAR" in finnhub.PREMIUM_ENDPOINTS["/stock/financials-reported"]
    assert finnhub.premium_reason("/quote") is None
    assert set(finnhub.PREMIUM_ENDPOINTS).isdisjoint({"/quote", "/stock/metric", "/company-news"})


# -- the CLI ----------------------------------------------------------------


def _args(**over):
    import argparse

    base = dict(all=False, earnings=False, calendar=False, news=False, analysts=False, insiders=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_cli_flags_select_sections():
    import fetch_finnhub

    assert fetch_finnhub.sections_for(_args()) == ["quote", "profile", "metrics"]
    assert fetch_finnhub.sections_for(_args(news=True))[-1] == "news"
    assert fetch_finnhub.sections_for(_args(analysts=True))[-1] == "recommendations"
    assert fetch_finnhub.sections_for(_args(all=True)) == list(finnhub.SECTIONS)


def test_cli_dry_run_prints_a_plan_and_never_the_key(key, cache, capsys):
    """The dry run is the one place a key would be printed by hand. It must not be."""
    import fetch_finnhub

    c = FinnhubClient(transport=DryRunTransport(sink=lambda _: None), cache=cache)
    assert fetch_finnhub.print_plan(c, ["AAPL"], ["quote", "metrics"]) == 0
    out = capsys.readouterr().out
    assert KEY not in out
    assert out.count("token=***REDACTED***") == 2
    assert "/stock/candle" in out and "yfinance" in out


def test_cli_reports_a_premium_gap_without_calling_it_a_failure(key, cache, capsys, fixtures_dir):
    """Degrading gracefully is the whole point of the distinct 403 type.

    Driven by the body Finnhub sends for a paid endpoint rather than a bare 403,
    because a bare 403 on a free endpoint is no longer read as a tier signal.
    """
    import fetch_finnhub

    body = (Path(fixtures_dir) / "finnhub_no_access.json").read_bytes()
    c = FinnhubClient(transport=BodyTransport(body), cache=cache)
    assert fetch_finnhub.report(c, "AAPL", ["quote", "profile"]) == 0
    out = capsys.readouterr().out
    assert "unavailable on this tier" in out
    assert KEY not in out


def test_cli_does_not_count_a_rejected_key_as_one_more_section_failure(key, cache):
    """report() caught BadKey in its generic (FetchError, Offline) clause.

    BadKey subclasses FetchError, so the tier error never left report() and run()'s
    "one bad key means every remaining ticker fails the same way, stop" was
    unreachable: 8 sections x 1500 tickers of 401s at a call a second, and an exit
    code of 1 (partial outage) rather than 2 (your key is dead).
    """
    import fetch_finnhub

    t = StatusTransport(401)
    c = FinnhubClient(transport=t, cache=cache)
    with pytest.raises(BadKey):
        fetch_finnhub.report(c, "AAPL", ["quote", "profile", "metrics"])
    assert len(t.calls) == 1, "the first refusal ends the ticker"


def test_cli_run_stops_on_the_first_bad_key_and_exits_2(key, cache, monkeypatch, capsys):
    import fetch_finnhub

    t = StatusTransport(401)
    c = FinnhubClient(transport=t, cache=cache)
    monkeypatch.setattr(fetch_finnhub.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(fetch_finnhub, "build_client", lambda dry_run: c)

    code = fetch_finnhub.run(["AAPL", "MSFT", "NVDA"], sections=["quote", "profile", "metrics"])
    assert code == 2, "1 would mean a partial outage worth retrying"
    assert len(t.calls) == 1, "9 calls would be one per section per ticker on a key known to be dead"
    err = capsys.readouterr().err
    assert "FINNHUB_KEY" in err and KEY not in err


def test_cli_run_exits_1_when_a_section_really_did_fail(key, cache, monkeypatch, capsys):
    """The other side of the same fence: a 500 is an outage, and the run carries on."""
    import fetch_finnhub

    t = StatusTransport(500)
    c = FinnhubClient(transport=t, cache=cache)
    monkeypatch.setattr(fetch_finnhub.paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(fetch_finnhub, "build_client", lambda dry_run: c)

    assert fetch_finnhub.run(["AAPL", "MSFT"], sections=["quote"]) == 1
    assert len(t.calls) == 2, "both tickers are attempted"


def test_cli_counts_a_real_outage_as_a_failure(key, cache, capsys):
    import fetch_finnhub

    c = FinnhubClient(transport=StatusTransport(500), cache=cache)
    assert fetch_finnhub.report(c, "AAPL", ["quote", "profile"]) == 2
    assert KEY not in capsys.readouterr().out


def test_cli_prints_what_it_pulled(client, frozen_today, capsys):
    import fetch_finnhub

    assert fetch_finnhub.report(client, "AAPL", list(finnhub.SECTIONS)) == 0
    out = capsys.readouterr().out
    assert "268.32" in out
    assert "Apple Inc" in out
    assert "2026Q3" in out
    assert "3 items in the last 7 days" in out
    assert "46 covering, 72% bullish" in out
    assert "2 filings, 0 open market buys, 1 sales" in out
    assert KEY not in out
