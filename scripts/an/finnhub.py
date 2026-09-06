"""Finnhub on a free key: what it will actually hand over, and where it stops.

Finnhub covers three gaps EDGAR leaves open. A quote that is minutes old rather
than a quarter old. A profile that knows the exchange, the currency and the
listing country, which is what tells a screen that a euro reporter is priced in
dollars. And roughly a hundred pre-computed ratios per name, which saves
rebuilding peTTM and roeTTM out of raw XBRL for fifteen hundred tickers.

What a free key will not hand over, whatever a stale blog post says: price
history. ``/stock/candle`` moved behind the paywall and a free key gets a 403
there, so daily bars still come from yfinance. The same goes for transcripts,
price targets, revenue breakdowns, as-reported financials and every sentiment
feed. They are listed in :data:`PREMIUM_ENDPOINTS` and refused here before a call
is spent on them, because a call that comes back 403 still counts against sixty a
minute.

Five decisions worth the words.

*The key lives in FINNHUB_KEY and nowhere else.* Not a constructor argument with
a default, not a dotfile, not a fallback demo token. A key that only ever exists
in the environment cannot be committed by accident, and there is exactly one place
to look when it is wrong. The client does not keep it on the instance either, so
no repr, no pickle and no crash dump can spill it.

*The key rides in the query string, and nothing anywhere prints a raw URL.* Every
log line, every exception message and every cached ``meta`` block goes through
:func:`an.http.redact`, which already treats ``token`` as a secret. Header
authentication would have been tidier were it not for
:class:`an.http.DryRunTransport`, which prints headers verbatim; a query
parameter that redact() understands is the safer of the two.

*A 403 is a tier signal only where the tier is what refused.*
:class:`PremiumEndpoint` carries the endpoint that refused, so a caller can drop
one field and carry on rather than declare the whole pull dead. That only holds
for an endpoint in :data:`PREMIUM_ENDPOINTS`: a 403 on ``/quote``, which every
tier can reach, is a revoked key or something upstream blocking the call, and is
raised as :class:`BadKey` so it stops the run instead of being logged as a gap.
For the same reason a 403 or a 401 is never answered out of a stale cache: a key
that got downgraded or revoked should be visible the day it happens, not six
months later.

*Zero is not a price.* Finnhub answers an unknown or halted symbol with zeros
rather than a 404, and not always with *every* field zeroed -- a stamped
timestamp, or a previous close that survived while the last price did not, are
both common. Left alone a 0.00 sorts to the top of a cheapness screen, so each
price field in :class:`Quote` collapses to None on its own, and a quote with no
price left collapses whole and says so through ``is_empty``.

*Text from the wire is data, not output.* Headlines, company names and
server-supplied error strings are written by someone else and land on a terminal.
Control characters are stripped from every parsed string, and an error body is
redacted before it is quoted in an exception: a proxy that refuses the request
tends to quote the URL back, and that URL carries the key.

Nothing here has been run against the live API. The container this was written in
cannot reach ``finnhub.io`` at all (403 at the egress proxy). Every shape below
was written by hand from the documented fields and is unit tested against
fixtures. Run ``python scripts/fetch_finnhub.py --dry-run AAPL`` first, then the
same without the flag on a machine with egress, before trusting any of it.
"""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import paths
from .http import _SECRET_KEYS, HttpTransport, Transport, redact
from .store import Cache, FetchError, Offline, RateLimited, cache_key

__all__ = [
    "FinnhubClient",
    "MissingKey",
    "BadKey",
    "PremiumEndpoint",
    "Quote",
    "Profile",
    "Metrics",
    "SeriesPoint",
    "EarningsRow",
    "CalendarRow",
    "NewsItem",
    "Recommendation",
    "InsiderTransaction",
    "PlannedCall",
    "api_key",
    "premium_reason",
    "date_window",
    "normalise_symbol",
    "BASE_URL",
    "KEY_ENV",
    "PREMIUM_ENDPOINTS",
    "SECTIONS",
    "RATE_PER_SECOND",
    "CALLS_PER_MINUTE",
    "CALLS_PER_SECOND",
]

BASE_URL = "https://finnhub.io/api/v1"
KEY_ENV = "FINNHUB_KEY"

# The published free limits are 60 calls a minute and 30 a second. The per-second
# cap is generous and the per-minute cap is the one that actually bites, so the
# bucket is set to one a second: under both by construction, and a 300 ticker pull
# that takes five minutes is still faster than being throttled off.
CALLS_PER_MINUTE = 60
CALLS_PER_SECOND = 30
RATE_PER_SECOND = 1.0

# Refresh intervals, chosen by how fast the underlying number can actually move.
TTL_QUOTE = 60.0
TTL_PROFILE = 30 * 86_400.0
TTL_METRIC = 86_400.0
TTL_EARNINGS = 86_400.0
TTL_CALENDAR = 6 * 3_600.0
TTL_NEWS = 6 * 3_600.0
TTL_RECOMMENDATION = 86_400.0
TTL_INSIDER = 86_400.0

NEWS_DAYS = 7
CALENDAR_DAYS = 90
INSIDER_DAYS = 180

SECTIONS: Tuple[str, ...] = (
    "quote",
    "profile",
    "metrics",
    "earnings",
    "calendar",
    "news",
    "recommendations",
    "insiders",
)

# Endpoints a free key cannot reach, and what to do instead. Refused locally so a
# call is not spent discovering something the tier already told us.
PREMIUM_ENDPOINTS: Dict[str, str] = {
    "/stock/candle": "historical OHLCV is paid now; daily bars come from yfinance",
    "/stock/transcripts": "transcripts are paid; the 8-K EX-99.1 press release on EDGAR is the free substitute",
    "/stock/price-target": "analyst price targets are paid",
    "/stock/revenue-breakdown": "segment revenue is paid; the 10-K segment note via EDGAR is free",
    "/stock/financials-reported": "as-reported statements are paid; EDGAR companyfacts is the same data",
    "/news-sentiment": "news sentiment is paid",
    "/stock/social-sentiment": "social sentiment is paid",
    "/stock/similar": "peer lists are paid",
}

_NO_ACCESS_MARKERS = ("don't have access", "do not have access", "access to this resource", "premium")
_BAD_KEY_MARKERS = ("invalid api key", "invalid token", "unauthorized", "api key")


class MissingKey(RuntimeError):
    """FINNHUB_KEY is not set.

    Its own type rather than a KeyError so a caller can tell "you have not
    configured this yet" apart from "the remote said no", and degrade quietly.
    """

    def __init__(self, message: Optional[str] = None):
        super().__init__(
            message
            or f"{KEY_ENV} is not set. Export your free Finnhub key as {KEY_ENV}. "
            f"It is read from the environment only, never from a file or an argument."
        )


class BadKey(FetchError):
    """The key exists but the server will not answer it. Never contains the key itself.

    Usually a 401. Also raised for a 403 on an endpoint every tier can reach, which
    is not a tier signal at all: ``status`` says which one it was.
    """

    def __init__(self, message: Optional[str] = None, *, url: str = "", status: int = 401):
        super().__init__(
            message or f"Finnhub rejected the key in {KEY_ENV} (401). Check it is current and not truncated.",
            status=status,
            url=url,
        )


class PremiumEndpoint(FetchError):
    """403. The endpoint is real but the free tier cannot see it.

    Carries ``endpoint`` so a caller can name the missing piece, skip that one
    field and keep the rest of the pull.
    """

    def __init__(self, endpoint: str, *, url: str = "", detail: str = ""):
        self.endpoint = endpoint
        self.detail = detail or PREMIUM_ENDPOINTS.get(endpoint, "")
        message = f"{endpoint} is not available on a free Finnhub key"
        if self.detail:
            message = f"{message}: {self.detail}"
        super().__init__(message, status=403, url=url)


def api_key() -> str:
    """The key, from the environment, or an explanation of what to set.

    Deliberately not a parameter anywhere. See the module docstring.
    """
    raw = os.environ.get(KEY_ENV) or ""
    key = raw.strip()
    if not key:
        raise MissingKey()
    return key


def normalise_symbol(symbol: str) -> str:
    """Finnhub wants upper case and keeps the dot in class shares (BRK.B, not BRK-B)."""
    s = str(symbol or "").strip().upper()
    if not s:
        raise ValueError("empty symbol")
    return s


def date_window(days: int, *, today: Optional[date] = None, forward: bool = False) -> Tuple[str, str]:
    """An inclusive ``from``, ``to`` pair as YYYY-MM-DD.

    ``today`` is injectable so a test can pin the window; otherwise the cache key
    would change at midnight and the assertion with it.
    """
    anchor = today or date.today()
    span = timedelta(days=max(0, int(days)))
    if forward:
        return anchor.isoformat(), (anchor + span).isoformat()
    return (anchor - span).isoformat(), anchor.isoformat()


def premium_reason(endpoint: str) -> Optional[str]:
    return PREMIUM_ENDPOINTS.get(endpoint)


# -- coercion ---------------------------------------------------------------
# Same rule as an.local: missing is None. A NaN or a stray zero that reaches a
# ranking mis-sorts it silently, which is the worst kind of wrong.


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _i(v: Any) -> Optional[int]:
    x = _f(v)
    return None if x is None else int(x)


def _clean(s: str) -> str:
    """Remote text with the control characters taken out.

    Headlines, sources and company names are written by someone else and get
    printed to a terminal. A bare carriage return or an ANSI escape in one of them
    overwrites the lines above it, which are exactly the failure lines an operator
    is being asked to read. Tabs and line breaks become a space so words do not run
    together; everything else in Unicode category C -- the rest of C0/C1, and the
    format characters such as a right-to-left override -- is dropped.
    """
    out = []
    for ch in s:
        if ch in "\t\n\r\v\f":
            out.append(" ")
        elif unicodedata.category(ch)[0] == "C":
            continue
        else:
            out.append(ch)
    return "".join(out)


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = _clean(str(v)).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    return s


_MAX_ERROR_CHARS = 200


def _safe_error(err: str) -> str:
    """Server-supplied text, made safe to quote in an exception message.

    Whatever answered wrote this, and it is not always Finnhub: a proxy or a WAF in
    front of it routinely quotes the blocked request URL back, and that URL carries
    ``token=<the key>``. Every URL in the text is redacted, an exact copy of the
    configured key is replaced wherever it appears, and the result is truncated so a
    page of HTML cannot become an error message.
    """
    out = re.sub(r"https?://\S+", lambda m: redact(m.group(0)), _clean(err)).strip()
    key = (os.environ.get(KEY_ENV) or "").strip()
    if key:
        out = out.replace(key, "***REDACTED***")
    if len(out) > _MAX_ERROR_CHARS:
        out = out[:_MAX_ERROR_CHARS].rstrip() + "..."
    return out


def _iso_from_epoch(v: Any) -> Optional[str]:
    """Finnhub timestamps are epoch seconds. Zero means "no timestamp", not 1970."""
    ts = _i(v)
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


# -- records ----------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """``/quote``. Fifteen minute delayed on the free tier for most US names."""

    symbol: str
    current: Optional[float] = None
    change: Optional[float] = None
    percent_change: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None
    previous_close: Optional[float] = None
    timestamp: Optional[int] = None

    @staticmethod
    def _price(v: Any) -> Optional[float]:
        """Zero, or anything below it, is Finnhub saying "nothing", not a price."""
        x = _f(v)
        return None if x is None or x <= 0 else x

    @classmethod
    def from_payload(cls, symbol: str, payload: Any) -> "Quote":
        raw = payload if isinstance(payload, dict) else {}
        current = cls._price(raw.get("c"))
        previous_close = cls._price(raw.get("pc"))
        # An unknown, delisted or halted symbol comes back as zeros rather than a
        # 404, and rarely as *every* field zero: a stamped ``t`` with zeroed prices,
        # or a surviving ``pc`` with a zeroed ``c``, are both shapes the API sends.
        # Testing all three at once let those through with current == 0.0, so each
        # price collapses on its own and the record collapses only when no price
        # survives -- the same condition ``is_empty`` reports.
        if current is None and previous_close is None:
            return cls(symbol=symbol)
        return cls(
            symbol=symbol,
            current=current,
            # A change of exactly zero is a flat day, not a missing number.
            change=_f(raw.get("d")),
            percent_change=_f(raw.get("dp")),
            high=cls._price(raw.get("h")),
            low=cls._price(raw.get("l")),
            open=cls._price(raw.get("o")),
            previous_close=previous_close,
            timestamp=_i(raw.get("t")),
        )

    @property
    def is_empty(self) -> bool:
        return self.current is None and self.previous_close is None

    @property
    def as_of(self) -> Optional[str]:
        return _iso_from_epoch(self.timestamp)

    @property
    def range_position(self) -> Optional[float]:
        """Where the last price sits in the day range, 0 at the low and 1 at the high."""
        if self.current is None or self.high is None or self.low is None or self.high <= self.low:
            return None
        return (self.current - self.low) / (self.high - self.low)


@dataclass(frozen=True)
class Profile:
    """``/stock/profile2``. The free profile; ``/stock/profile`` is the paid one."""

    symbol: str
    name: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None
    exchange: Optional[str] = None
    ipo: Optional[str] = None
    market_cap_millions: Optional[float] = None
    share_outstanding_millions: Optional[float] = None
    industry: Optional[str] = None
    finnhub_industry: Optional[str] = None
    weburl: Optional[str] = None
    logo: Optional[str] = None
    phone: Optional[str] = None

    @classmethod
    def from_payload(cls, symbol: str, payload: Any) -> "Profile":
        raw = payload if isinstance(payload, dict) else {}
        return cls(
            symbol=_s(raw.get("ticker")) or symbol,
            name=_s(raw.get("name")),
            country=_s(raw.get("country")),
            currency=_s(raw.get("currency")),
            exchange=_s(raw.get("exchange")),
            ipo=_s(raw.get("ipo")),
            market_cap_millions=_f(raw.get("marketCapitalization")),
            share_outstanding_millions=_f(raw.get("shareOutstanding")),
            industry=_s(raw.get("industry")) or _s(raw.get("finnhubIndustry")),
            finnhub_industry=_s(raw.get("finnhubIndustry")),
            weburl=_s(raw.get("weburl")),
            logo=_s(raw.get("logo")),
            phone=_s(raw.get("phone")),
        )

    @property
    def market_cap(self) -> Optional[float]:
        """Absolute market cap. Finnhub reports millions, and in the listing currency."""
        return None if self.market_cap_millions is None else self.market_cap_millions * 1e6

    @property
    def shares_outstanding(self) -> Optional[float]:
        return None if self.share_outstanding_millions is None else self.share_outstanding_millions * 1e6

    @property
    def is_empty(self) -> bool:
        """An unknown symbol returns ``{}``, so a nameless profile means "not found"."""
        return self.name is None


@dataclass(frozen=True)
class SeriesPoint:
    period: str
    value: float


@dataclass(frozen=True)
class Metrics:
    """``/stock/metric?metric=all``: a flat ``metric{}`` plus a nested ``series{}``.

    Neither half is a stable schema. Which of the hundred odd keys are present
    varies by name, by sector and by day, so every accessor here answers None
    instead of raising, and no caller is allowed to index ``raw`` directly.
    """

    symbol: str
    raw: Dict[str, Any] = field(default_factory=dict)
    series: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, symbol: str, payload: Any) -> "Metrics":
        blob = payload if isinstance(payload, dict) else {}
        raw = blob.get("metric")
        series = blob.get("series")
        return cls(
            symbol=_s(blob.get("symbol")) or symbol,
            raw=dict(raw) if isinstance(raw, dict) else {},
            series=dict(series) if isinstance(series, dict) else {},
        )

    # -- flat metrics -----------------------------------------------------

    def get(self, name: str) -> Optional[float]:
        """One metric as a float, or None. Absent, null and unparseable all read the same."""
        return _f(self.raw.get(name))

    def get_text(self, name: str) -> Optional[str]:
        """A handful of metric values are strings, such as 52WeekHighDate."""
        return _s(self.raw.get(name))

    def first(self, *names: str) -> Optional[float]:
        """First name in preference order that has a value.

        Finnhub carries several spellings of the same idea, and which one is
        populated depends on the company, so a preference list beats a lookup.
        """
        for n in names:
            v = self.get(n)
            if v is not None:
                return v
        return None

    def present(self, names: Iterable[str]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for n in names:
            v = self.get(n)
            if v is not None:
                out[n] = v
        return out

    @property
    def is_empty(self) -> bool:
        return not self.raw and not self.series

    # -- series -----------------------------------------------------------

    def series_names(self, freq: str = "annual") -> List[str]:
        node = self.series.get(freq)
        return sorted(node.keys()) if isinstance(node, dict) else []

    def series_points(self, name: str, freq: str = "annual") -> List[SeriesPoint]:
        """Newest first, valueless points dropped.

        Three things go wrong here constantly and none of them should raise:
        ``series`` is missing outright for thinly covered names, the frequency is
        missing, or an individual point has ``v: null``. A point with no value is
        not a zero and not a data point, so it is dropped rather than carried as a
        hole that some later mean() would swallow. The API tends to send newest
        first, but it is not promised, so the order is imposed here.
        """
        node = self.series.get(freq)
        if not isinstance(node, dict):
            return []
        rows = node.get(name)
        if not isinstance(rows, list):
            return []
        out: List[SeriesPoint] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            period = _s(r.get("period"))
            value = _f(r.get("v"))
            if period is None or value is None:
                continue
            out.append(SeriesPoint(period=period, value=value))
        out.sort(key=lambda p: p.period, reverse=True)
        return out

    def series_latest(self, name: str, freq: str = "annual") -> Optional[float]:
        points = self.series_points(name, freq)
        return points[0].value if points else None

    def series_history(self, name: str, freq: str = "annual", *, limit: int = 8) -> List[float]:
        return [p.value for p in self.series_points(name, freq)[:limit]]

    # -- the ratios this project actually reads ---------------------------

    @property
    def pe(self) -> Optional[float]:
        return self.first("peTTM", "peBasicExclExtraTTM", "peExclExtraTTM")

    @property
    def pb(self) -> Optional[float]:
        return self.first("pbAnnual", "pbQuarterly")

    @property
    def ps(self) -> Optional[float]:
        return self.first("psTTM", "psAnnual")

    @property
    def ev_ebitda(self) -> Optional[float]:
        """Enterprise value over EBITDA, or None. No fallback, on purpose.

        The other enterprise multiple Finnhub sends is ``currentEv/freeCashFlowTTM``,
        which is EV/FCF: a different measure and a structurally larger number,
        commonly one and a half to three times EV/EBITDA for the same business.
        Serving it under this name would rank a company that was never measured on
        this multiple against peers that were, and nothing downstream could tell.
        It is available under its own name as :attr:`ev_fcf`.
        """
        return self.get("evEbitdaTTM")

    @property
    def ev_fcf(self) -> Optional[float]:
        """Enterprise value over free cash flow. A relative of EV/EBITDA, not a synonym."""
        return self.get("currentEv/freeCashFlowTTM")

    @property
    def roe(self) -> Optional[float]:
        return self.first("roeTTM", "roeRfy")

    @property
    def roa(self) -> Optional[float]:
        return self.first("roaTTM", "roaRfy")

    @property
    def roi(self) -> Optional[float]:
        return self.first("roiTTM", "roiAnnual")

    @property
    def gross_margin(self) -> Optional[float]:
        return self.first("grossMarginTTM", "grossMarginAnnual")

    @property
    def operating_margin(self) -> Optional[float]:
        return self.first("operatingMarginTTM", "operatingMarginAnnual")

    @property
    def net_margin(self) -> Optional[float]:
        return self.first("netProfitMarginTTM", "netProfitMarginAnnual")

    @property
    def current_ratio(self) -> Optional[float]:
        return self.first("currentRatioQuarterly", "currentRatioAnnual")

    @property
    def debt_to_equity(self) -> Optional[float]:
        return self.first("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual")

    @property
    def long_term_debt_to_equity(self) -> Optional[float]:
        return self.first("longTermDebt/equityQuarterly", "longTermDebt/equityAnnual")

    @property
    def high_52w(self) -> Optional[float]:
        return self.get("52WeekHigh")

    @property
    def low_52w(self) -> Optional[float]:
        return self.get("52WeekLow")

    @property
    def return_52w(self) -> Optional[float]:
        return self.get("52WeekPriceReturnDaily")

    @property
    def return_26w(self) -> Optional[float]:
        return self.get("26WeekPriceReturnDaily")

    @property
    def return_13w(self) -> Optional[float]:
        return self.get("13WeekPriceReturnDaily")

    @property
    def volatility_3m(self) -> Optional[float]:
        return self.get("3MonthADReturnStd")

    @property
    def beta(self) -> Optional[float]:
        return self.get("beta")

    @property
    def eps_growth_3y(self) -> Optional[float]:
        return self.get("epsGrowth3Y")

    @property
    def eps_growth_5y(self) -> Optional[float]:
        return self.get("epsGrowth5Y")

    @property
    def revenue_growth_3y(self) -> Optional[float]:
        return self.get("revenueGrowth3Y")

    @property
    def revenue_growth_5y(self) -> Optional[float]:
        return self.get("revenueGrowth5Y")

    @property
    def dividend_yield(self) -> Optional[float]:
        return self.first("dividendYieldIndicatedAnnual", "currentDividendYieldTTM")

    @property
    def payout_ratio(self) -> Optional[float]:
        return self.get("payoutRatioTTM")

    @property
    def book_value_per_share(self) -> Optional[float]:
        return self.first("bookValuePerShareAnnual", "bookValuePerShareQuarterly")

    @property
    def cash_flow_per_share(self) -> Optional[float]:
        return self.first("cashFlowPerShareTTM", "cashFlowPerShareAnnual")

    def summary(self) -> Dict[str, Optional[float]]:
        """The subset the scorecard reads, all of it nullable."""
        return {
            "pe": self.pe,
            "pb": self.pb,
            "ps": self.ps,
            "ev_ebitda": self.ev_ebitda,
            "ev_fcf": self.ev_fcf,
            "roe": self.roe,
            "roa": self.roa,
            "roi": self.roi,
            "gross_margin": self.gross_margin,
            "operating_margin": self.operating_margin,
            "net_margin": self.net_margin,
            "current_ratio": self.current_ratio,
            "debt_to_equity": self.debt_to_equity,
            "long_term_debt_to_equity": self.long_term_debt_to_equity,
            "high_52w": self.high_52w,
            "low_52w": self.low_52w,
            "return_52w": self.return_52w,
            "return_26w": self.return_26w,
            "return_13w": self.return_13w,
            "volatility_3m": self.volatility_3m,
            "beta": self.beta,
            "eps_growth_3y": self.eps_growth_3y,
            "eps_growth_5y": self.eps_growth_5y,
            "revenue_growth_3y": self.revenue_growth_3y,
            "revenue_growth_5y": self.revenue_growth_5y,
            "dividend_yield": self.dividend_yield,
            "payout_ratio": self.payout_ratio,
            "book_value_per_share": self.book_value_per_share,
            "cash_flow_per_share": self.cash_flow_per_share,
        }


@dataclass(frozen=True)
class EarningsRow:
    """``/stock/earnings``: the last four reported quarters."""

    symbol: str
    period: Optional[str] = None
    year: Optional[int] = None
    quarter: Optional[int] = None
    actual: Optional[float] = None
    estimate: Optional[float] = None
    surprise: Optional[float] = None
    surprise_percent: Optional[float] = None

    @classmethod
    def from_payload(cls, symbol: str, raw: Any) -> "EarningsRow":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            symbol=_s(r.get("symbol")) or symbol,
            period=_s(r.get("period")),
            year=_i(r.get("year")),
            quarter=_i(r.get("quarter")),
            actual=_f(r.get("actual")),
            estimate=_f(r.get("estimate")),
            surprise=_f(r.get("surprise")),
            surprise_percent=_f(r.get("surprisePercent")),
        )

    @property
    def beat(self) -> Optional[bool]:
        """True, False, or None when either side of the comparison is missing."""
        if self.actual is None or self.estimate is None:
            return None
        return self.actual > self.estimate

    @property
    def label(self) -> str:
        if self.year and self.quarter:
            return f"{self.year}Q{self.quarter}"
        return self.period or "?"


@dataclass(frozen=True)
class CalendarRow:
    """``/calendar/earnings``: the dates that have not happened yet."""

    symbol: str
    date: Optional[str] = None
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    hour: Optional[str] = None
    quarter: Optional[int] = None
    year: Optional[int] = None

    _HOURS = {"bmo": "before market open", "amc": "after market close", "dmh": "during market hours"}

    @classmethod
    def from_payload(cls, symbol: str, raw: Any) -> "CalendarRow":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            symbol=_s(r.get("symbol")) or symbol,
            date=_s(r.get("date")),
            eps_estimate=_f(r.get("epsEstimate")),
            eps_actual=_f(r.get("epsActual")),
            revenue_estimate=_f(r.get("revenueEstimate")),
            revenue_actual=_f(r.get("revenueActual")),
            hour=_s(r.get("hour")),
            quarter=_i(r.get("quarter")),
            year=_i(r.get("year")),
        )

    @property
    def session(self) -> Optional[str]:
        return self._HOURS.get((self.hour or "").lower())

    @property
    def has_consensus(self) -> bool:
        """Whether analysts have published an EPS estimate against this date.

        Named for what it measures, which is coverage rather than confirmation. It
        was called ``is_confirmed``, and that reading does not hold in either
        direction: analysts publish a consensus for future quarters whether or not
        the company has announced a date, so a Finnhub-projected date for a covered
        large cap carries an estimate, and a company that has formally announced its
        date but has no analyst following carries none. ``/calendar/earnings`` has
        no date-confirmation field at all, so that signal has to come from the 8-K
        on EDGAR.
        """
        return self.date is not None and self.eps_estimate is not None


@dataclass(frozen=True)
class NewsItem:
    """``/company-news``. Free, and capped at one year of history."""

    symbol: str
    headline: Optional[str] = None
    summary: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    timestamp: Optional[int] = None
    category: Optional[str] = None
    image: Optional[str] = None
    related: Optional[str] = None
    news_id: Optional[int] = None

    @classmethod
    def from_payload(cls, symbol: str, raw: Any) -> "NewsItem":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            symbol=symbol,
            headline=_s(r.get("headline")),
            summary=_s(r.get("summary")),
            source=_s(r.get("source")),
            url=_s(r.get("url")),
            timestamp=_i(r.get("datetime")),
            category=_s(r.get("category")),
            image=_s(r.get("image")),
            related=_s(r.get("related")),
            news_id=_i(r.get("id")),
        )

    @property
    def published(self) -> Optional[str]:
        return _iso_from_epoch(self.timestamp)

    @property
    def day(self) -> Optional[str]:
        p = self.published
        return p[:10] if p else None


@dataclass(frozen=True)
class Recommendation:
    """``/stock/recommendation``: analyst counts for one month."""

    symbol: str
    period: Optional[str] = None
    strong_buy: Optional[int] = None
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    strong_sell: Optional[int] = None

    @classmethod
    def from_payload(cls, symbol: str, raw: Any) -> "Recommendation":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            symbol=_s(r.get("symbol")) or symbol,
            period=_s(r.get("period")),
            strong_buy=_i(r.get("strongBuy")),
            buy=_i(r.get("buy")),
            hold=_i(r.get("hold")),
            sell=_i(r.get("sell")),
            strong_sell=_i(r.get("strongSell")),
        )

    _BUCKETS = ("strong_buy", "buy", "hold", "sell", "strong_sell")

    @property
    def complete(self) -> bool:
        """All five buckets parsed, so a share computed from them means something."""
        return all(getattr(self, b) is not None for b in self._BUCKETS)

    @property
    def total(self) -> Optional[int]:
        """Analysts covering the name, or None when any bucket is missing.

        Summing only the buckets that parsed would put a smaller number in the
        denominator than the API actually measured and quietly inflate every share
        taken from it -- a null ``hold`` on a row of 52 analysts reads as 40
        covering and turns a 54% bullish share into 70%. Absent and zero are kept
        apart here for the same reason they are everywhere else in this module.
        """
        if not self.complete:
            return None
        return sum(getattr(self, b) for b in self._BUCKETS)

    @property
    def bullish_share(self) -> Optional[float]:
        """Buy and strong buy over the total. None rather than zero when nobody covers it."""
        total = self.total
        if not total:
            return None
        return ((self.strong_buy or 0) + (self.buy or 0)) / total


@dataclass(frozen=True)
class InsiderTransaction:
    """``/stock/insider-transactions``. Form 4 data, deduped by Finnhub."""

    symbol: str
    name: Optional[str] = None
    share: Optional[float] = None
    change: Optional[float] = None
    filing_date: Optional[str] = None
    transaction_date: Optional[str] = None
    transaction_code: Optional[str] = None
    transaction_price: Optional[float] = None

    @classmethod
    def from_payload(cls, symbol: str, raw: Any) -> "InsiderTransaction":
        r = raw if isinstance(raw, dict) else {}
        return cls(
            symbol=_s(r.get("symbol")) or symbol,
            name=_s(r.get("name")),
            share=_f(r.get("share")),
            change=_f(r.get("change")),
            filing_date=_s(r.get("filingDate")),
            transaction_date=_s(r.get("transactionDate")),
            transaction_code=_s(r.get("transactionCode")),
            transaction_price=_f(r.get("transactionPrice")),
        )

    @property
    def is_open_market_sale(self) -> bool:
        """Code S. Option exercises (M) and grants (A) are noise for this purpose."""
        return (self.transaction_code or "").upper() == "S"

    @property
    def is_open_market_buy(self) -> bool:
        return (self.transaction_code or "").upper() == "P"


@dataclass(frozen=True)
class PlannedCall:
    """One line of a --dry-run plan. ``url`` is redacted before it gets here."""

    endpoint: str
    url: str
    ttl_seconds: float
    label: str
    cache_key: str


@dataclass(frozen=True)
class _Call:
    endpoint: str
    params: Dict[str, Any]
    key: str
    ttl: float
    label: str


class FinnhubClient:
    """Free tier Finnhub, cached, with the paid endpoints refused up front.

    The key is never passed in and never stored on the instance. Every request
    reads :func:`api_key` afresh, so a process that never calls anything never
    needs a key at all, and a fresh cache entry is served without one.
    """

    def __init__(
        self,
        transport: Optional[Transport] = None,
        *,
        cache: Optional[Cache] = None,
        user_agent: str = "desk personal research",
        stale_on_error: bool = True,
    ):
        self.user_agent = user_agent
        self.transport = transport or HttpTransport(user_agent, rate_per_second=RATE_PER_SECOND)
        self.cache = cache if cache is not None else Cache(paths.FINNHUB_CACHE, default_ttl=TTL_METRIC)
        self.stale_on_error = stale_on_error

    # -- urls -------------------------------------------------------------

    @staticmethod
    def _build_url(endpoint: str, params: Optional[Dict[str, Any]], token: str) -> str:
        """The request URL, with the query in a fixed order.

        Sorted rather than in call order because the server does not care and
        everything on this side does: the cached ``meta`` URL stays comparable
        across runs, and reordering a param dict here stops looking like a change in
        what is requested.
        """
        query = {k: v for k, v in (params or {}).items() if v is not None}
        query["token"] = token
        return f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(sorted(query.items()))}"

    def preview_url(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
        """The URL a real call would use, with the token already redacted.

        Built with a placeholder rather than the real key, so ``--dry-run`` works
        on a machine that has no key at all, and cannot leak one that does.
        """
        return redact(self._build_url(endpoint, params, "placeholder"))

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # No secret ever goes in a header: DryRunTransport prints headers verbatim.
        return {"User-Agent": self.user_agent, "Accept": "application/json"}

    def _decode(self, raw: bytes, endpoint: str, safe_url: str) -> Any:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise FetchError(f"not JSON from {safe_url}: {e}", url=safe_url) from e
        return self._check_payload(payload, endpoint, safe_url)

    @staticmethod
    def _check_payload(payload: Any, endpoint: str, safe_url: str) -> Any:
        """Finnhub sometimes answers 200 with an error object. Treat it as the status it means."""
        if isinstance(payload, dict):
            raw_err = _s(payload.get("error"))
            if raw_err:
                # Never interpolated raw: the body is the one string here that a
                # proxy could have written, and a blocked-request message quotes the
                # URL, key and all.
                err = _safe_error(raw_err) or "unreadable error body"
                low = err.lower()
                if any(m in low for m in _NO_ACCESS_MARKERS):
                    raise PremiumEndpoint(endpoint, url=safe_url, detail=err)
                if any(m in low for m in _BAD_KEY_MARKERS):
                    raise BadKey(f"Finnhub rejected the key in {KEY_ENV}: {err}", url=safe_url)
                raise FetchError(f"{endpoint}: {err}", url=safe_url)
        return payload

    def _fetch_once(self, endpoint: str, params: Dict[str, Any]) -> Any:
        url = self._build_url(endpoint, params, api_key())
        safe_url = redact(url)
        try:
            raw = self.transport.get(url, headers=self._headers(), timeout=30.0)
        except RateLimited:
            raise
        except FetchError as e:
            status = getattr(e, "status", None)
            if status == 401:
                raise BadKey(url=safe_url) from e
            if status == 403:
                if endpoint in PREMIUM_ENDPOINTS:
                    raise PremiumEndpoint(endpoint, url=safe_url) from e
                # Every tier can reach this one, so the refusal is not about the
                # tier. Calling it a premium gap would let a nightly run report
                # success with nothing fetched, which is the failure the stale-cache
                # carve-out above exists to prevent.
                raise BadKey(
                    f"403 on {endpoint}, which is free on every Finnhub tier: the key in "
                    f"{KEY_ENV} looks revoked, or something upstream is refusing the call",
                    url=safe_url,
                    status=403,
                ) from e
            if status == 429:
                raise RateLimited(
                    f"429 from {safe_url}: free keys get {CALLS_PER_MINUTE} calls a minute",
                    status=429,
                    url=safe_url,
                ) from e
            raise
        return self._decode(raw, endpoint, safe_url)

    def _cached(self, call: _Call, *, allow_premium: bool = False) -> Any:
        """Read through the cache, then fetch, with three deliberate departures.

        Endpoints known to be paid are refused before the cache is even consulted,
        so no call is spent on a certain 403. A tier error (401 or 403) is never
        answered from a stale copy, because that is exactly the failure that would
        otherwise hide for months. Everything else does fall back to stale, since a
        six hour old headline beats no headline.
        """
        if not allow_premium and call.endpoint in PREMIUM_ENDPOINTS:
            raise PremiumEndpoint(call.endpoint)

        entry = self.cache.read(call.key)
        if entry is not None and entry.age_seconds < call.ttl:
            return entry.payload

        try:
            payload = self._fetch_once(call.endpoint, call.params)
        except (BadKey, PremiumEndpoint, MissingKey):
            raise
        except (RateLimited, FetchError, Offline):
            if entry is not None and self.stale_on_error:
                return entry.payload
            raise

        # meta carries the redacted URL only. The raw one holds the key.
        self.cache.write(
            call.key,
            payload,
            meta={"url": self.preview_url(call.endpoint, call.params), "endpoint": call.endpoint, "ttl": call.ttl},
        )
        return payload

    # -- call table -------------------------------------------------------

    @staticmethod
    def _days(kw: Dict[str, Any], default: int) -> int:
        """``days=0`` is a real window -- today only -- so absent is tested for, not falsiness."""
        d = kw.get("days")
        return default if d is None else int(d)

    @staticmethod
    def _window(kw: Dict[str, Any], *, days: int, forward: bool) -> Tuple[str, str]:
        auto = date_window(days, today=kw.get("today"), forward=forward)
        return str(kw.get("start") or auto[0]), str(kw.get("end") or auto[1])

    def _spec(self, section: str, symbol: str, **kw: Any) -> _Call:
        """One table for every endpoint, read by both the fetchers and the dry run.

        Shared on purpose: a plan generated from a second copy of these URLs would
        eventually stop describing what a real run does.
        """
        sym = normalise_symbol(symbol)
        if section == "quote":
            return _Call("/quote", {"symbol": sym}, cache_key("quote", sym), TTL_QUOTE, f"quote {sym}")
        if section == "profile":
            return _Call(
                "/stock/profile2", {"symbol": sym}, cache_key("profile", sym), TTL_PROFILE, f"profile {sym}"
            )
        if section == "metrics":
            return _Call(
                "/stock/metric",
                {"symbol": sym, "metric": "all"},
                cache_key("metric", sym),
                TTL_METRIC,
                f"metrics {sym}",
            )
        if section == "earnings":
            return _Call(
                "/stock/earnings", {"symbol": sym}, cache_key("earnings", sym), TTL_EARNINGS, f"earnings {sym}"
            )
        if section == "calendar":
            start, end = self._window(kw, days=self._days(kw, CALENDAR_DAYS), forward=True)
            return _Call(
                "/calendar/earnings",
                {"from": start, "to": end, "symbol": sym},
                cache_key("calendar", sym, start, end),
                TTL_CALENDAR,
                f"earnings calendar {sym} {start} to {end}",
            )
        if section == "news":
            start, end = self._window(kw, days=self._days(kw, NEWS_DAYS), forward=False)
            return _Call(
                "/company-news",
                {"symbol": sym, "from": start, "to": end},
                cache_key("news", sym, start, end),
                TTL_NEWS,
                f"news {sym} {start} to {end}",
            )
        if section == "recommendations":
            return _Call(
                "/stock/recommendation",
                {"symbol": sym},
                cache_key("recommendation", sym),
                TTL_RECOMMENDATION,
                f"recommendations {sym}",
            )
        if section == "insiders":
            start, end = self._window(kw, days=self._days(kw, INSIDER_DAYS), forward=False)
            return _Call(
                "/stock/insider-transactions",
                {"symbol": sym, "from": start, "to": end},
                cache_key("insider", sym, start, end),
                TTL_INSIDER,
                f"insider transactions {sym} {start} to {end}",
            )
        raise ValueError(f"unknown section {section!r}, expected one of {', '.join(SECTIONS)}")

    def plan(self, symbol: str, sections: Iterable[str] = SECTIONS, **kw: Any) -> List[PlannedCall]:
        """What a real run would request, without a key and without a request."""
        out: List[PlannedCall] = []
        for section in sections:
            call = self._spec(section, symbol, **kw)
            out.append(
                PlannedCall(
                    endpoint=call.endpoint,
                    url=self.preview_url(call.endpoint, call.params),
                    ttl_seconds=call.ttl,
                    label=call.label,
                    cache_key=call.key,
                )
            )
        return out

    # -- endpoints --------------------------------------------------------

    def quote(self, symbol: str) -> Quote:
        call = self._spec("quote", symbol)
        return Quote.from_payload(normalise_symbol(symbol), self._cached(call))

    def profile(self, symbol: str) -> Profile:
        call = self._spec("profile", symbol)
        return Profile.from_payload(normalise_symbol(symbol), self._cached(call))

    def metrics(self, symbol: str) -> Metrics:
        call = self._spec("metrics", symbol)
        return Metrics.from_payload(normalise_symbol(symbol), self._cached(call))

    def earnings(self, symbol: str) -> List[EarningsRow]:
        call = self._spec("earnings", symbol)
        payload = self._cached(call)
        rows = payload if isinstance(payload, list) else []
        out = [EarningsRow.from_payload(normalise_symbol(symbol), r) for r in rows]
        out.sort(key=lambda r: r.period or "", reverse=True)
        return out

    def earnings_calendar(self, symbol: str, **kw: Any) -> List[CalendarRow]:
        call = self._spec("calendar", symbol, **kw)
        payload = self._cached(call)
        rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        rows = rows if isinstance(rows, list) else []
        out = [CalendarRow.from_payload(normalise_symbol(symbol), r) for r in rows]
        out.sort(key=lambda r: r.date or "")
        return out

    def company_news(self, symbol: str, **kw: Any) -> List[NewsItem]:
        call = self._spec("news", symbol, **kw)
        payload = self._cached(call)
        rows = payload if isinstance(payload, list) else []
        out = [NewsItem.from_payload(normalise_symbol(symbol), r) for r in rows]
        out.sort(key=lambda n: n.timestamp or 0, reverse=True)
        return out

    def recommendations(self, symbol: str) -> List[Recommendation]:
        call = self._spec("recommendations", symbol)
        payload = self._cached(call)
        rows = payload if isinstance(payload, list) else []
        out = [Recommendation.from_payload(normalise_symbol(symbol), r) for r in rows]
        out.sort(key=lambda r: r.period or "", reverse=True)
        return out

    def insider_transactions(self, symbol: str, **kw: Any) -> List[InsiderTransaction]:
        call = self._spec("insiders", symbol, **kw)
        payload = self._cached(call)
        rows = payload.get("data") if isinstance(payload, dict) else None
        rows = rows if isinstance(rows, list) else []
        out = [InsiderTransaction.from_payload(normalise_symbol(symbol), r) for r in rows]
        out.sort(key=lambda r: r.transaction_date or "", reverse=True)
        return out

    # -- escape hatches ---------------------------------------------------

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        ttl: Optional[float] = None,
        allow_premium: bool = False,
    ) -> Any:
        """Any endpoint, cached. Raises PremiumEndpoint for the known paid ones.

        ``allow_premium`` skips that guard so :meth:`probe_premium` can ask the
        server rather than the table, which is the only way to notice a tier change.
        """
        params = dict(params or {})
        # The key material becomes a filename, and a cache file outlives the
        # environment variable. This is the one door left open by "there is no
        # api_key parameter anywhere": a caller reaching for the escape hatch can
        # pass auth params of their own, so any secret-named value is masked here
        # exactly as redact() masks it in a URL.
        parts = [f"{k}={'***REDACTED***' if k.lower() in _SECRET_KEYS else v}" for k, v in sorted(params.items())]
        key = cache_key("raw", endpoint, *parts)
        call = _Call(endpoint, params, key, TTL_METRIC if ttl is None else float(ttl), endpoint)
        return self._cached(call, allow_premium=allow_premium)

    def probe_premium(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """Ask whether this key can reach a paid endpoint. ``(reachable, explanation)``.

        Worth running once when a key is upgraded, and never on a schedule: every
        probe is a call, and the answer changes about once a year.

        A key error is not an answer about the tier, so :class:`BadKey` and
        :class:`MissingKey` propagate rather than being reported as "not reachable".
        Swallowing them would spend one call per paid endpoint being told the same
        thing by a key that will not work for any of them.
        """
        try:
            self.get(endpoint, params, ttl=0.0, allow_premium=True)
        except (BadKey, MissingKey):
            raise
        except PremiumEndpoint as e:
            return False, str(e)
        except (RateLimited, FetchError, Offline) as e:
            return False, f"inconclusive: {e}"
        return True, f"{endpoint} answered, this key can reach it"
