"""Size the survivorship hole with Alpha Vantage's ``LISTING_STATUS`` endpoint.

WHAT THIS MEASURES, AND WHAT IT CANNOT
--------------------------------------
Every backtest this project could run is built on the names that exist today, so
every one of them is flattered by the companies that were delisted, acquired or
wiped out along the way. ``backtest.py`` says so in the limitations of every
result. Until now it could only say "by an unknown amount".

``LISTING_STATUS`` is the only free source found that lists delisted US tickers
with their delisting date, and it costs two requests in total (one for
``state=active``, one for ``state=delisted``) rather than one per name, which
makes it affordable even on a 25-a-day key. Each row carries the symbol, the
exchange, the asset type, the listing date and, for a delisted name, the date it
stopped trading. From those two files the listed universe on any past date can be
reconstructed: a name existed on date D if it listed on or before D and had not
been delisted by D.

That is enough to answer one question honestly: **of the names that satisfied the
listing-age rule on a past date, how many no longer exist?** ``criteria.md`` asks
for three years listed, and ``ipoDate`` answers it. It is not enough to apply the
market-cap and dollar-volume rules, because the file carries neither, so the count
here is over the whole listed market, not the $2bn-plus slice this pipeline
screens. Large names fail less often, so the whole-market number is an upper
bound on the attrition the screen would have seen.

Three more limits, stated because each one changes how to read the number.

*A delisting is not a failure.* The file gives no reason. Acquisitions are the
largest single cause of delisting among established companies, and an acquired
shareholder was usually paid a premium, not wiped out. So the count bounds the
number of names a survivor-only backtest is missing; it does not say how many of
them lost money. The bias in the synthetic engine assumes every departure is a
loss, which is the pessimistic end.

*No prices.* Knowing that a name left does not recover what it returned before it
left, so this cannot repair a backtest. It sizes the hole.

*Symbols are reused.* A ticker retired in 2011 can be reassigned in 2019. The
reconstruction treats each row as its own listing, keyed on symbol *and* listing
date, and reports how many symbols carry more than one row so the reader knows
how much of that there is.

WHERE THE KEY LIVES
-------------------
``ALPHAVANTAGE_KEY`` in the environment, and nowhere else, for the reasons
``finnhub.py`` gives: not a constructor argument, not a file, not a default. The
key rides in the query string, so every URL that is printed, logged or cached
goes through :func:`an.http.redact`, which already treats ``apikey`` as a secret.

WHETHER THIS HAS EVER RUN FOR REAL
----------------------------------
No. The container this was written in cannot reach ``www.alphavantage.co``
(connection refused at the egress proxy), so every shape below was written from
the documented CSV columns and is tested against a hand-built fixture whose
answers are known in advance. The report prints its own provenance on the first
line, and :func:`provenance` says whether a real response has ever been cached.
Read that line before believing the count.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths
from .http import HttpTransport, Transport, redact
from .store import Cache, FetchError, RateLimited

__all__ = [
    "ListingStatusClient",
    "Listing",
    "AttritionReport",
    "CrossCheck",
    "PlannedCall",
    "MissingKey",
    "BadKey",
    "api_key",
    "parse_listing_csv",
    "listed_on",
    "attrition",
    "cross_check",
    "provenance",
    "default_horizons",
    "BASE_URL",
    "KEY_ENV",
    "STATES",
    "EXPECTED_COLUMNS",
    "SCREEN_EXCHANGES",
    "MIN_YEARS_LISTED",
    "SYNTHETIC_ATTRITION_PER_YEAR",
]

BASE_URL = "https://www.alphavantage.co/query"
KEY_ENV = "ALPHAVANTAGE_KEY"
STATES: Tuple[str, ...] = ("active", "delisted")

# The published free limit is 25 requests a day. Two are needed. The bucket is set
# well under any per-minute limit so a retry never spends a third.
RATE_PER_SECOND = 0.5
REQUESTS_PER_DAY = 25

# The list moves daily but the question it answers moves over years. A week is
# long enough that a re-run in the same sitting costs nothing.
TTL = 7 * 86_400.0

EXPECTED_COLUMNS = ("symbol", "name", "exchange", "assetType", "ipoDate", "delistingDate", "status")

# criteria.md, step 1: NYSE and Nasdaq common stock, listed at least three years.
# Alpha Vantage spells the venues this way; "NYSE ARCA" and "BATS" are mostly
# funds and are excluded together with assetType != Stock.
SCREEN_EXCHANGES: Tuple[str, ...] = ("NYSE", "NASDAQ")
MIN_YEARS_LISTED = 3

# What tests/test_backtest_synthetic.py assumes when it sizes the bias: four names
# of 150 leave every 63-day period, all at a loss. Stated here so the measured
# whole-market rate can be read against it.
SYNTHETIC_ATTRITION_PER_YEAR = 1.0 - (1.0 - 4.0 / 150.0) ** 4

_LIMIT_MARKERS = ("call frequency", "requests per day", "rate limit", "premium", "thank you for using alpha vantage")


class MissingKey(RuntimeError):
    """ALPHAVANTAGE_KEY is not set. Its own type so a caller can degrade rather than crash."""

    def __init__(self, message: Optional[str] = None):
        super().__init__(
            message
            or f"{KEY_ENV} is not set. Get a free key at alphavantage.co/support/#api-key and export it as "
            f"{KEY_ENV}. It is read from the environment only, never from a file or an argument."
        )


class BadKey(FetchError):
    """The server answered, but not with a listing. Never contains the key."""


def api_key() -> str:
    raw = os.environ.get(KEY_ENV) or ""
    key = raw.strip()
    if not key:
        raise MissingKey()
    return key


@dataclass(frozen=True)
class PlannedCall:
    state: str
    url: str
    cache_key: str


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    exchange: str
    asset_type: str
    ipo_date: Optional[date]
    delisting_date: Optional[date]
    status: str

    @property
    def is_common_stock(self) -> bool:
        return self.asset_type.lower() == "stock"

    def listed_on(self, on: date) -> bool:
        """True if this listing was trading on ``on``. Unknown listing date counts as not listed."""
        if self.ipo_date is None or self.ipo_date > on:
            return False
        return self.delisting_date is None or self.delisting_date > on

    def years_listed_on(self, on: date) -> Optional[float]:
        if self.ipo_date is None or self.ipo_date > on:
            return None
        return (on - self.ipo_date).days / 365.25


def _date(v: str) -> Optional[date]:
    s = (v or "").strip()
    if not s or s.lower() in ("null", "none", "nan", "n/a"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _looks_like_json_error(text: str) -> Optional[Dict[str, Any]]:
    head = text.lstrip()[:1]
    if head != "{":
        return None
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        return None
    return blob if isinstance(blob, dict) else None


def parse_listing_csv(text: str, *, url: str = "") -> List[Listing]:
    """The CSV body -> listings.

    Alpha Vantage reports every failure as a 200 with a small JSON body, so the
    first job is to notice that the CSV is not a CSV. A daily-limit note becomes
    :class:`RateLimited`; anything else becomes :class:`BadKey`. The server's
    text is quoted in the exception after redaction, because an error body tends
    to echo the URL and the URL carries the key.
    """
    blob = _looks_like_json_error(text)
    if blob is not None:
        message = " ".join(str(v) for v in blob.values())
        safe = redact(message) if "apikey" in message else message
        lowered = safe.lower()
        if any(m in lowered for m in _LIMIT_MARKERS):
            raise RateLimited(f"Alpha Vantage refused the request: {safe[:200]}", status=429, url=redact(url))
        raise BadKey(f"Alpha Vantage did not return a listing: {safe[:200]}", status=200, url=redact(url))

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise FetchError("empty response where a listing CSV was expected", url=redact(url))
    header = [h.strip().lstrip("\ufeff") for h in header]
    if tuple(header) != EXPECTED_COLUMNS:
        raise FetchError(
            f"unexpected columns {header!r}; expected {list(EXPECTED_COLUMNS)!r}. "
            f"The endpoint's shape has changed and this parser needs a look.",
            url=redact(url),
        )
    out: List[Listing] = []
    for row in reader:
        if not row or not any(c.strip() for c in row):
            continue
        row = (row + [""] * 7)[:7]
        symbol = row[0].strip().upper()
        if not symbol:
            continue
        out.append(
            Listing(
                symbol=symbol,
                name=row[1].strip(),
                exchange=row[2].strip().upper(),
                asset_type=row[3].strip(),
                ipo_date=_date(row[4]),
                delisting_date=_date(row[5]),
                status=row[6].strip(),
            )
        )
    return out


class ListingStatusClient:
    """Two calls, cached for a week, parsed into :class:`Listing` rows."""

    def __init__(self, transport: Optional[Transport] = None, *, cache: Optional[Cache] = None, ttl: float = TTL):
        self.transport = transport or HttpTransport("desk personal research", rate_per_second=RATE_PER_SECOND)
        self.cache = cache if cache is not None else Cache(paths.ALPHAVANTAGE_CACHE, default_ttl=ttl)
        self.ttl = ttl

    @staticmethod
    def cache_key_for(state: str) -> str:
        return f"listing_{state}"

    def url(self, state: str) -> str:
        if state not in STATES:
            raise ValueError(f"state must be one of {STATES}, not {state!r}")
        params = {"function": "LISTING_STATUS", "state": state, "apikey": api_key()}
        return f"{BASE_URL}?{urllib.parse.urlencode(sorted(params.items()))}"

    def plan(self) -> List[PlannedCall]:
        """Every call a full pull makes, key redacted. Needs no key: the placeholder stands in."""
        out = []
        for state in STATES:
            params = {"function": "LISTING_STATUS", "state": state, "apikey": "***REDACTED***"}
            out.append(PlannedCall(state, f"{BASE_URL}?{urllib.parse.urlencode(sorted(params.items()), safe='*')}",
                                   self.cache_key_for(state)))
        return out

    def _text(self, state: str) -> str:
        url = self.url(state)
        key = self.cache_key_for(state)

        def fetch() -> str:
            raw = self.transport.get(url, headers={"Accept": "text/csv, application/json;q=0.5"})
            text = raw.decode("utf-8", errors="replace")
            # Parse before caching so a quota note is never cached as if it were data.
            parse_listing_csv(text, url=url)
            return text

        return self.cache.get_or_fetch(
            key,
            fetch,
            ttl=self.ttl,
            meta={"url": redact(url), "transport": type(self.transport).__name__,
                  "live": isinstance(self.transport, HttpTransport)},
        )

    def listings(self, state: str) -> List[Listing]:
        return parse_listing_csv(self._text(state), url=self.plan()[STATES.index(state)].url)

    def all_listings(self) -> List[Listing]:
        return self.listings("active") + self.listings("delisted")


# -- the measurement --------------------------------------------------------


def listed_on(listings: Iterable[Listing], on: date) -> List[Listing]:
    return [l for l in listings if l.listed_on(on)]


@dataclass
class AttritionReport:
    as_of: date
    today: date
    eligible: int
    gone: int
    still_listed: int
    by_year: Dict[int, int] = field(default_factory=dict)
    by_exchange: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    reused_symbols: int = 0
    unknown_ipo_dates: int = 0
    examples: List[Listing] = field(default_factory=list)

    @property
    def horizon_years(self) -> float:
        return (self.today - self.as_of).days / 365.25

    @property
    def rate(self) -> Optional[float]:
        return None if self.eligible == 0 else self.gone / self.eligible

    @property
    def annualised_rate(self) -> Optional[float]:
        """The constant yearly rate that would produce ``rate`` over the horizon."""
        r = self.rate
        if r is None or self.horizon_years <= 0:
            return None
        if r >= 1.0:
            return 1.0
        return 1.0 - (1.0 - r) ** (1.0 / self.horizon_years)


def attrition(
    listings: Sequence[Listing],
    as_of: date,
    *,
    today: Optional[date] = None,
    min_years_listed: float = MIN_YEARS_LISTED,
    exchanges: Sequence[str] = SCREEN_EXCHANGES,
    examples: int = 12,
) -> AttritionReport:
    """Of the common stocks that met the listing-age rule on ``as_of``, how many are gone by ``today``?

    Eligible: assetType Stock, exchange in ``exchanges``, listed on ``as_of`` for
    at least ``min_years_listed`` years. Gone: eligible and carrying a delisting
    date on or before ``today``. Everything the file cannot say (market cap,
    volume, the reason for leaving) is left out and named in the docstring above.
    """
    today = today or date.today()
    if as_of >= today:
        raise ValueError(f"as_of {as_of} must be before today {today}")
    wanted = {e.upper() for e in exchanges}
    eligible: List[Listing] = []
    unknown = 0
    for l in listings:
        if not l.is_common_stock or l.exchange not in wanted:
            continue
        if l.ipo_date is None:
            unknown += 1
            continue
        if not l.listed_on(as_of):
            continue
        years = l.years_listed_on(as_of)
        if years is None or years < min_years_listed:
            continue
        eligible.append(l)

    gone = [l for l in eligible if l.delisting_date is not None and l.delisting_date <= today]
    by_year = Counter(l.delisting_date.year for l in gone)  # type: ignore[union-attr]
    by_exchange: Dict[str, Tuple[int, int]] = {}
    for ex in sorted(wanted):
        e_n = sum(1 for l in eligible if l.exchange == ex)
        g_n = sum(1 for l in gone if l.exchange == ex)
        by_exchange[ex] = (e_n, g_n)
    symbols = Counter(l.symbol for l in listings if l.is_common_stock)
    reused = sum(1 for _, n in symbols.items() if n > 1)
    sample = sorted(gone, key=lambda l: (l.delisting_date, l.symbol))[:examples]  # type: ignore[arg-type]
    return AttritionReport(
        as_of=as_of,
        today=today,
        eligible=len(eligible),
        gone=len(gone),
        still_listed=len(eligible) - len(gone),
        by_year=dict(sorted(by_year.items())),
        by_exchange=by_exchange,
        reused_symbols=reused,
        unknown_ipo_dates=unknown,
        examples=sample,
    )


@dataclass
class CrossCheck:
    """The current universe held against the delisted list.

    Every hit is a question, not an answer: either the symbol was reassigned to a
    new company (common) or the universe file is carrying a name that has since
    left (a stale screen). Both are worth a look and neither is decided here.
    """

    checked: int
    delisted_hits: List[Tuple[str, Listing]]
    not_covered: List[str]

    @property
    def canadian_not_covered(self) -> List[str]:
        return [t for t in self.not_covered if t.endswith((".TO", ".V"))]


def cross_check(listings: Sequence[Listing], tickers: Iterable[str], *, today: Optional[date] = None) -> CrossCheck:
    today = today or date.today()
    by_symbol: Dict[str, List[Listing]] = {}
    for l in listings:
        by_symbol.setdefault(l.symbol, []).append(l)
    hits: List[Tuple[str, Listing]] = []
    missing: List[str] = []
    seen = []
    for t in tickers:
        t = t.upper().strip()
        seen.append(t)
        rows = by_symbol.get(t) or by_symbol.get(t.replace(".", "-")) or []
        if not rows:
            missing.append(t)
            continue
        alive_now = any(l.listed_on(today) for l in rows)
        if not alive_now:
            latest = max(rows, key=lambda l: (l.delisting_date or date.min))
            hits.append((t, latest))
    return CrossCheck(checked=len(seen), delisted_hits=hits, not_covered=missing)


def default_horizons(today: Optional[date] = None) -> List[date]:
    """One, three, five and ten years back: the horizons a backtest here would use."""
    today = today or date.today()
    out = []
    for years in (1, 3, 5, 10):
        try:
            out.append(today.replace(year=today.year - years))
        except ValueError:  # 29 February
            out.append(today.replace(year=today.year - years, day=28))
    return out


def provenance(cache: Optional[Cache] = None) -> Dict[str, Any]:
    """Whether a real response has ever been stored, and when. Read this before the numbers."""
    cache = cache if cache is not None else Cache(paths.ALPHAVANTAGE_CACHE, default_ttl=TTL)
    out: Dict[str, Any] = {"ever_live": False, "states": {}}
    for state in STATES:
        key = ListingStatusClient.cache_key_for(state)
        p = cache.path_for(key)
        if not p.exists():
            out["states"][state] = None
            continue
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["states"][state] = None
            continue
        meta = blob.get("meta") or {}
        live = bool(meta.get("live"))
        out["states"][state] = {"fetched_at": blob.get("fetched_at"), "transport": meta.get("transport"), "live": live}
        out["ever_live"] = out["ever_live"] or live
    return out


def _fmt_rate(r: Optional[float]) -> str:
    return "n/a" if r is None else f"{100.0 * r:.1f}%"


def render_report(
    reports: Sequence[AttritionReport],
    check: Optional[CrossCheck],
    *,
    source_line: str,
    n_active: int,
    n_delisted: int,
) -> str:
    """Plain text, written to be read once by a person."""
    lines: List[str] = []
    lines.append(f"source: {source_line}")
    lines.append(f"rows: {n_active:,} active, {n_delisted:,} delisted")
    lines.append("")
    lines.append("Of the NYSE/Nasdaq common stocks that had been listed three years or more on the date,")
    lines.append("how many are no longer listed today. This is the whole market, not the $2bn-plus slice")
    lines.append("the screen selects, so read it as an upper bound on the screen's own attrition. A")
    lines.append("delisting is not a failure: the file gives no reason, and acquisition is the largest one.")
    lines.append("")
    lines.append(f"{'as of':<12} {'horizon':>8} {'eligible':>9} {'gone':>6} {'rate':>7} {'per year':>9}")
    for r in reports:
        lines.append(
            f"{r.as_of.isoformat():<12} {r.horizon_years:>7.1f}y {r.eligible:>9,} {r.gone:>6,} "
            f"{_fmt_rate(r.rate):>7} {_fmt_rate(r.annualised_rate):>9}"
        )
    lines.append("")
    lines.append(
        f"the synthetic engine assumes {_fmt_rate(SYNTHETIC_ATTRITION_PER_YEAR)} a year, every departure a "
        f"50% loss (tests/test_backtest_synthetic.py). That is the pessimistic end: the measured rate above"
    )
    lines.append("counts acquisitions too, and the screen's names are larger than the market's median.")
    if reports:
        longest = max(reports, key=lambda r: r.horizon_years)
        lines.append("")
        lines.append(f"by exchange, as of {longest.as_of.isoformat()}:")
        for ex, (e_n, g_n) in longest.by_exchange.items():
            rate = None if e_n == 0 else g_n / e_n
            lines.append(f"  {ex:<8} {e_n:>7,} eligible  {g_n:>6,} gone  {_fmt_rate(rate):>7}")
        if longest.by_year:
            lines.append("")
            lines.append(f"departures by year, as of {longest.as_of.isoformat()}:")
            for y, n in longest.by_year.items():
                lines.append(f"  {y}  {n:>6,}")
        if longest.examples:
            lines.append("")
            lines.append("first to leave:")
            for l in longest.examples:
                lines.append(f"  {l.symbol:<8} {l.exchange:<7} listed {l.ipo_date}  left {l.delisting_date}  {l.name[:48]}")
        lines.append("")
        lines.append(
            f"caveats: {longest.reused_symbols:,} symbol{'s' if longest.reused_symbols != 1 else ''} on more than one "
            f"listing (reassigned tickers); {longest.unknown_ipo_dates:,} common-stock row"
            f"{'s' if longest.unknown_ipo_dates != 1 else ''} with no listing date, skipped."
        )
    if check is not None:
        lines.append("")
        lines.append(f"cross-check against the current universe ({check.checked:,} tickers):")
        if check.delisted_hits:
            lines.append(f"  {len(check.delisted_hits)} appear only as delisted. Reassigned symbol or stale row; look:")
            for t, l in check.delisted_hits[:20]:
                lines.append(f"    {t:<8} left {l.delisting_date}  {l.name[:48]}")
        else:
            lines.append("  none of them appears only as delisted")
        ca = check.canadian_not_covered
        other = [t for t in check.not_covered if t not in ca]
        lines.append(
            f"  {len(check.not_covered)} not in either list: {len(ca)} Canadian (the endpoint is US only), "
            f"{len(other)} other"
        )
        if other:
            lines.append(f"    {' '.join(other[:30])}")
    return "\n".join(lines)
