"""Daily adjusted closes, live from yfinance or cached or synthetic.

Three things shaped this module.

*yfinance is not an API.* It is a scraper against an endpoint Yahoo never promised
to keep, it rate limits hard and without warning, and a throttled request looks
like a hang rather than an error. So a pull is batched into one call, concurrency
is pinned at two, the window is bounded, the timeout is explicit, and the result is
cached per ticker rather than per request so that changing one name in a list of
four hundred does not re-fetch the other three hundred and ninety nine.

*The machine most of this was written on had no egress at all.* Offline is a mode,
not an error path. ``DESK_OFFLINE=1``, or any exception out of yfinance, falls back
to whatever is already on disk, including a stale copy, and only raises
:class:`~an.store.Offline` when there is genuinely nothing to fall back to.

*A price panel is where survivorship bias gets in.* A forward filled frame can tell
three specific lies and this module refuses all three.

1. Filling before a ticker's first print invents a price for a company that had not
   listed yet, so a backtest can buy it. The fill starts at the first real print.
2. Filling past a ticker's last print turns a delisting into a flat return, which
   is the most flattering bug a backtest can have: the position that went to zero
   quietly scores 0.0 percent instead. The fill stops at the last real print, and
   :func:`forward_return_detail` reports ``stopped_trading`` with the return to the
   final traded price, or ``None``, but never a fabricated flat line.
3. Dropping a ticker that has no data at all makes the survivors look like the
   whole universe. Every requested ticker gets a column here even when it is
   entirely empty, and :func:`coverage_on` names the empty ones for a given date.

On adjustment. Returns are only meaningful on adjusted closes: a two for one split
on unadjusted prices reads as a fifty percent loss, and a large special dividend
reads as a crash. yfinance controls this with ``auto_adjust``, and the trap is that
the two settings produce different column names. With ``auto_adjust=True`` there is
no ``Adj Close`` column at all and ``Close`` is already split and dividend
adjusted. With ``auto_adjust=False``, ``Close`` is raw and ``Adj Close`` is the
adjusted one. This module always requests ``auto_adjust=True``, and
:func:`closes_from_download` still prefers ``Adj Close`` whenever it is present, so
the adjusted series wins under either setting and a raw close can never reach a
return. The price of adjustment is that Yahoo re-adjusts the whole history on every
pull, so a panel cached before a dividend and one pulled after it disagree on every
bar. Cache entries therefore record their window and their pull time, and a
backtest should run off one panel rather than stitching two together.

On missing values. ``NaN`` exists inside the returned DataFrame because that is the
only hole pandas has. It never leaves this module by any other door: every scalar
accessor returns ``None`` instead, because a ``NaN`` that reaches a ranking sorts
somewhere arbitrary and nobody notices.

Nothing here has ever run against the live Yahoo endpoint. Every test is driven by
a hand written fixture or by :func:`synthetic_panel`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from . import paths
from .http import redact
from .store import Cache, FetchError, Offline, cache_key, is_offline

__all__ = [
    "Downloader",
    "YFinanceDownloader",
    "PanelDownloader",
    "RawDownloader",
    "OfflineDownloader",
    "PriceClient",
    "ForwardReturn",
    "Coverage",
    "closes_from_download",
    "daily_closes",
    "default_client",
    "price_on",
    "first_observation",
    "last_observation",
    "forward_return",
    "forward_return_detail",
    "forward_returns",
    "forward_returns_detail",
    "coverage_on",
    "missing_on",
    "synthetic_panel",
    "delist",
    "MAX_CONCURRENCY",
    "TRADING_DAYS_PER_YEAR",
    "PRICE_FIELDS",
]

# yfinance throttles aggressively and a throttled batch takes the whole run down
# with it. Two is slower than eight and finishes far more often.
MAX_CONCURRENCY = 2

TRADING_DAYS_PER_YEAR = 252

# Daily bars for a closed session never change, so the TTL only needs to be short
# enough to pick up today's close once. Twelve hours does that with one pull a day.
DEFAULT_TTL = 12 * 3600.0

# A ticker Yahoo has nothing for is cached too, otherwise every run re-asks for the
# same four hundred dead names. Short TTL because "nothing" is also what a throttled
# response looks like, and that one should not stick.
EMPTY_TTL = 3600.0

PRICE_FIELDS = ("Open", "High", "Low", "Close", "Adj Close", "Volume")

# The signature default of synthetic_panel's ``vol``, named so that the per ticker
# fallback and the parameter default cannot drift apart: a name left out of a vol
# mapping has to get the documented default, not a silently noiseless flat line.
DEFAULT_VOL = 0.20

# A live downloader is process wide, so its call log is bounded rather than an
# unbounded record of every ticker the process has ever asked about.
_MAX_RECORDED_CALLS = 50

# Preference order, and the whole of the adjustment guarantee. See the module
# docstring: this is correct under auto_adjust either way round.
_CLOSE_PREFERENCE = ("Adj Close", "Close")

DateLike = Union[str, dt.date, dt.datetime, pd.Timestamp]
Numberish = Union[float, Mapping[str, float]]


# ---------------------------------------------------------------------------
# small conversions
# ---------------------------------------------------------------------------


def _num(value: Any) -> Optional[float]:
    """Float or None. ``NaN`` and infinities become None, never 0.0."""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


# A full URL, or the bare path-with-query that urllib3 embeds in its messages
# ("Max retries exceeded with url: /v8/finance/chart/NVDA?crumb=...").
_URL_IN_TEXT = re.compile(r"""https?://[^\s'"<>]+|/[^\s'"<>]*\?[^\s'"<>]*""")


def _safe_error(exc: BaseException) -> str:
    """``Type: message`` with every URL inside the message run through :func:`redact`.

    yfinance surfaces urllib3 and requests exceptions verbatim, and those embed the
    whole request URL including its query string: Yahoo's session crumb, or the API
    key of any keyed vendor put behind the :class:`Downloader` seam. This string is
    stored on the client, interpolated into the raised :class:`~an.store.Offline`,
    and printed by callers, so it is scrubbed the same way every other network
    module here scrubs one.
    """
    return f"{type(exc).__name__}: {_URL_IN_TEXT.sub(lambda m: redact(m.group(0)), str(exc))}"


def _as_ts(value: DateLike) -> pd.Timestamp:
    """A tz naive, midnight Timestamp.

    Yahoo hands back tz aware stamps for some exchanges and naive ones for others.
    Comparing the two raises, and comparing a 21:00 UTC stamp against a date silently
    lands on the wrong side of a day boundary, so everything is flattened on the way in.
    """
    ts = pd.Timestamp(value)
    if ts.tz is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _naive_index(index: Any) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _tickers(tickers: Sequence[str]) -> List[str]:
    """Upper cased, de-duplicated, order preserved. Order is the column order."""
    out: List[str] = []
    seen = set()
    for t in tickers:
        s = str(t).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _per_ticker(value: Numberish, ticker: str, default: float) -> float:
    if isinstance(value, Mapping):
        v = _num(value.get(ticker, default))
    else:
        v = _num(value)
    return default if v is None else v


# ---------------------------------------------------------------------------
# panel shaping
# ---------------------------------------------------------------------------


def _panel(closes: pd.DataFrame) -> pd.DataFrame:
    """Normalise a caller supplied frame without mutating it.

    Every read side function goes through here so that a frame loaded from JSON, or
    hand built in a test, behaves exactly like one this module produced.
    """
    if not isinstance(closes, pd.DataFrame):
        raise TypeError("closes must be a pandas DataFrame indexed by date")
    if closes.shape[1] == 0 and len(closes.index) == 0:
        return closes
    idx = _naive_index(closes.index)
    out = closes
    if not idx.equals(closes.index):
        out = closes.copy()
        out.index = idx
    if not out.index.is_monotonic_increasing:
        out = out.sort_index()
    return out


def _column(panel: pd.DataFrame, ticker: str) -> Optional[pd.Series]:
    if ticker not in panel.columns:
        return None
    col = panel[ticker]
    if isinstance(col, pd.DataFrame):  # duplicated column label
        col = col.iloc[:, 0]
    return pd.to_numeric(col, errors="coerce")


def _fill_within_life(frame: pd.DataFrame) -> pd.DataFrame:
    """Forward fill, but only between each column's first and last real print.

    Nothing before the first print, because a company that had not listed yet has no
    price and a backtest given one will happily buy it. Nothing after the last print,
    because that is what turns a delisting into a flat return. Holes in the middle
    are real trading halts and stale exchange days, and carrying the last price
    across those is what a holder actually experiences.
    """
    if frame.shape[1] == 0 or len(frame.index) == 0:
        return frame
    seen = frame.notna()
    after_first = seen.cummax()
    before_last = seen[::-1].cummax()[::-1]
    return frame.ffill().where(after_first & before_last)


def _empty_panel(tickers: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(
        index=pd.DatetimeIndex([], name=None),
        columns=list(tickers),
        dtype="float64",
    )


def _assemble(series_by_ticker: Mapping[str, Optional[pd.Series]], tickers: Sequence[str]) -> pd.DataFrame:
    """One column per requested ticker, in the requested order, empty ones kept."""
    index: Optional[pd.DatetimeIndex] = None
    for s in series_by_ticker.values():
        if s is None or len(s) == 0:
            continue
        index = s.index if index is None else index.union(s.index)
    if index is None:
        return _empty_panel(tickers)
    panel = pd.DataFrame(index=index.sort_values(), columns=list(tickers), dtype="float64")
    for t in tickers:
        s = series_by_ticker.get(t)
        if s is not None and len(s):
            panel[t] = s.reindex(panel.index)
    return panel


# ---------------------------------------------------------------------------
# yfinance response shapes
# ---------------------------------------------------------------------------


def _single_series_frame(series: pd.Series, wanted: Sequence[str], fallback: str) -> pd.DataFrame:
    """Name a one-column download after the ticker that was asked for.

    Only when exactly one was asked for. A single close series against a list of
    several tickers is a shape mismatch, and naming it after ``wanted[0]`` would file
    one company's price history under another company's name, silently, for as long
    as anyone kept reading the panel.
    """
    names = list(wanted)
    if len(names) == 1:
        return series.to_frame(names[0])
    if not names:
        return series.to_frame(fallback)
    raise FetchError(
        f"flat single-series download for {len(names)} requested tickers: {', '.join(names)}"
    )


def closes_from_download(raw: Optional[pd.DataFrame], tickers: Sequence[str]) -> pd.DataFrame:
    """Pull the adjusted close out of whatever shape yfinance returned.

    Four shapes exist in the wild and all four turn up depending on version, on the
    ``group_by`` argument and on how many tickers were asked for: fields on the outer
    column level, tickers on the outer level, a flat frame for a single ticker, and a
    bare Series. ``Adj Close`` wins over ``Close`` whenever both are present, which is
    the entire adjustment guarantee described in the module docstring.
    """
    wanted = _tickers(tickers)
    if raw is None:
        return _empty_panel(wanted)
    if isinstance(raw, pd.Series):
        # Name the frame after the *field*, never after the ticker. The flat branch
        # below looks up a close column by name, so writing the ticker over the field
        # name made this documented shape impossible to parse: it always raised.
        name = str(raw.name) if raw.name is not None else ""
        raw = raw.to_frame(name if name in PRICE_FIELDS else "Close")
    if not isinstance(raw, pd.DataFrame):
        raise FetchError(f"unexpected download type {type(raw).__name__}")
    if len(raw.index) == 0 or raw.shape[1] == 0:
        return _empty_panel(wanted)

    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        field_level: Optional[int] = None
        for level in range(cols.nlevels):
            values = {str(v) for v in cols.get_level_values(level)}
            if values & set(PRICE_FIELDS):
                field_level = level
                break
        if field_level is None:
            raise FetchError(f"no price field in downloaded columns: {list(cols)[:4]}")
        available = {str(v) for v in cols.get_level_values(field_level)}
        chosen = next((c for c in _CLOSE_PREFERENCE if c in available), None)
        if chosen is None:
            raise FetchError(f"no close column in downloaded columns: {sorted(available)}")
        frame = raw.xs(chosen, axis=1, level=field_level)
        if isinstance(frame, pd.Series):
            frame = _single_series_frame(frame, wanted, chosen)
    else:
        flat = {str(c) for c in cols}
        chosen = next((c for c in _CLOSE_PREFERENCE if c in flat), None)
        if chosen is None:
            raise FetchError(f"no close column in downloaded columns: {sorted(flat)}")
        frame = raw[chosen]
        if isinstance(frame, pd.Series):
            frame = _single_series_frame(frame, wanted, chosen)

    frame = frame.copy()
    frame.columns = [str(c).strip().upper() for c in frame.columns]
    frame.index = _naive_index(frame.index)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    series = {t: pd.to_numeric(frame[t], errors="coerce") for t in wanted if t in frame.columns}
    return _assemble(series, wanted)


# ---------------------------------------------------------------------------
# downloaders
# ---------------------------------------------------------------------------


class Downloader(Protocol):
    """The seam the network sits behind, mirroring ``an.http.Transport``.

    Returns the raw yfinance shaped frame rather than a tidy one, so that the test
    doubles exercise :func:`closes_from_download` instead of routing around it.
    """

    def download(self, tickers: Sequence[str], start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]: ...


def _yf():
    """Imported lazily so this module, and its tests, load without yfinance."""
    import yfinance

    return yfinance


@dataclass
class YFinanceDownloader:
    """The only thing here that touches the network."""

    threads: int = MAX_CONCURRENCY
    timeout: float = 30.0
    # Out of the repr, and bounded. ``default_client()`` keeps one of these alive for
    # the life of the process, and the tickers in it are the portfolio: anything that
    # reprs the object (a debug log, pytest --showlocals on a crash inside download)
    # would otherwise print the whole holdings and watchlist in one line.
    calls: List[Tuple[str, ...]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        # Clamped rather than validated: a caller asking for sixteen threads wants
        # speed, and silently getting throttled for an hour is not speed.
        self.threads = max(1, min(int(self.threads), MAX_CONCURRENCY))

    def download(self, tickers: Sequence[str], start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
        if is_offline():
            raise Offline("DESK_OFFLINE is set, refusing to download prices")
        names = _tickers(tickers)
        if not names:
            return None
        self.calls.append(tuple(names))
        del self.calls[:-_MAX_RECORDED_CALLS]
        return _yf().download(
            tickers=names,
            start=start.date().isoformat(),
            # yfinance treats end as exclusive. This module treats it as inclusive,
            # because every human who writes a date range means it that way.
            end=(end + pd.Timedelta(days=1)).date().isoformat(),
            interval="1d",
            auto_adjust=True,
            actions=False,
            back_adjust=False,
            group_by="column",
            threads=self.threads,
            progress=False,
            timeout=self.timeout,
        )


@dataclass
class PanelDownloader:
    """Serve a pre-built panel, reshaped into yfinance's column layout.

    This is how a synthetic panel is fed to code that expects a live client, and how
    most tests here avoid the network without avoiding the parsing.
    """

    panel: pd.DataFrame
    calls: List[Tuple[str, ...]] = field(default_factory=list)

    def download(self, tickers: Sequence[str], start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
        names = _tickers(tickers)
        self.calls.append(tuple(names))
        panel = _panel(self.panel)
        cols = [t for t in names if t in panel.columns]
        if not cols:
            return None
        mask = (panel.index >= start) & (panel.index <= end)
        sub = panel.loc[mask, cols].copy()
        sub.columns = pd.MultiIndex.from_product([["Close"], cols])
        return sub


@dataclass
class RawDownloader:
    """Replay one recorded yfinance frame verbatim, whatever shape it is in."""

    raw: Optional[pd.DataFrame]
    calls: List[Tuple[str, ...]] = field(default_factory=list)

    def download(self, tickers: Sequence[str], start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
        self.calls.append(tuple(_tickers(tickers)))
        return self.raw


@dataclass
class OfflineDownloader:
    """Never fetches. Used by dry runs, and by any test that must prove the cache path."""

    error: Optional[Exception] = None
    calls: List[Tuple[str, ...]] = field(default_factory=list)

    def download(self, tickers: Sequence[str], start: pd.Timestamp, end: pd.Timestamp) -> Optional[pd.DataFrame]:
        self.calls.append(tuple(_tickers(tickers)))
        raise self.error or Offline("offline downloader: no request was sent")


ENV_QUOTES = "DESK_QUOTES"


def default_downloader() -> Downloader:
    """What a client uses when nobody hands it a downloader.

    ``DESK_QUOTES`` naming a directory with a ``closes.csv`` (the wide panel that
    ``scripts/fetch_quotes.py`` writes and the ``quotes`` branch carries) turns every
    ``--live`` script in the repository into a reader of that file, which is how the
    scheduled desk marks itself on a machine that cannot reach Yahoo. Unset, it is
    the live yfinance downloader, as before.
    """
    root = os.environ.get(ENV_QUOTES)
    if root:
        p = Path(root) / "closes.csv"
        if p.exists():
            panel = pd.read_csv(p, index_col=0, parse_dates=True)
            panel.index = pd.DatetimeIndex(panel.index).tz_localize(None).normalize()
            panel.columns = [str(c).upper() for c in panel.columns]
            return PanelDownloader(panel=panel.astype(float))
    return YFinanceDownloader()


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------


class PriceClient:
    """Cached daily closes with an offline fallback.

    The cache is keyed per ticker and window, not per request, so two screens asking
    for overlapping name lists share their history instead of each paying for it.
    A request whose exact window is not cached will still reuse a cached wider
    window, which is what makes an offline run usable at all.
    """

    def __init__(
        self,
        downloader: Optional[Downloader] = None,
        *,
        cache: Optional[Cache] = None,
        ttl: float = DEFAULT_TTL,
        empty_ttl: float = EMPTY_TTL,
    ):
        self.downloader: Downloader = downloader if downloader is not None else default_downloader()
        self.cache = cache if cache is not None else Cache(paths.PRICE_CACHE, default_ttl=ttl)
        self.ttl = float(ttl)
        # "Nothing" is never remembered for longer than "something". An empty result
        # is also what a throttled response looks like, so a client asking for ten
        # minute freshness must not be handed an hour old absence of data.
        self.empty_ttl = min(float(empty_ttl), float(ttl))
        # Per ticker provenance for the last call: "live", "cache", "stale", "missing".
        self.last_source: Dict[str, str] = {}
        self.last_error: Optional[str] = None

    # -- cache plumbing ---------------------------------------------------

    def _key(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> str:
        return cache_key("closes", ticker, start.date().isoformat(), end.date().isoformat())

    def _to_payload(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp, series: pd.Series) -> Dict[str, Any]:
        return {
            "ticker": ticker,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "adjusted": True,
            "source": "Yahoo Finance via yfinance",
            "index": [pd.Timestamp(d).date().isoformat() for d in series.index],
            # None, not NaN. json.dumps would happily emit a bare NaN token, which is
            # not JSON and which no other reader will accept.
            "close": [_num(v) for v in series.to_numpy()],
        }

    @staticmethod
    def _from_payload(payload: Any) -> Optional[pd.Series]:
        if not isinstance(payload, dict):
            return None
        index = payload.get("index")
        close = payload.get("close")
        if index is None or close is None or len(index) != len(close):
            return None
        if not index:
            return pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
        return pd.Series(
            [None if v is None else float(v) for v in close],
            index=_naive_index(index),
            dtype="float64",
        )

    def _ttl_for(self, rows: int) -> float:
        """Real data gets the full TTL, a remembered "nothing" the short one."""
        return self.ttl if rows else self.empty_ttl

    def _read_cached(
        self, ticker: str, start: pd.Timestamp, end: pd.Timestamp, *, allow_stale: bool
    ) -> Tuple[Optional[pd.Series], bool]:
        """Exact window first, then any cached window that covers it.

        Returns ``(series, was_stale)``. ``series`` is None on a miss, and an empty
        Series when the cache remembers that this ticker has no data.
        """
        entry = self.cache.read(self._key(ticker, start, end))
        payload = entry.payload if entry is not None else None
        age = entry.age_seconds if entry is not None else None
        if payload is None:
            found = self._scan_for_superset(ticker, start, end)
            if found is not None:
                payload, age = found
        if payload is None:
            return None, False
        series = self._from_payload(payload)
        if series is None:
            return None, False
        stale = age is None or age >= self._ttl_for(len(series))
        if stale and not allow_stale:
            return None, True
        sliced = series.loc[(series.index >= start) & (series.index <= end)]
        return sliced, stale

    def _scan_for_superset(
        self, ticker: str, start: pd.Timestamp, end: pd.Timestamp
    ) -> Optional[Tuple[Dict[str, Any], float]]:
        if not self.cache.dir.exists():
            return None
        want_start, want_end = start.date().isoformat(), end.date().isoformat()
        best: Optional[Tuple[Dict[str, Any], float]] = None
        # Freshness first, then the longest window. Ranking on row count alone let an
        # old wide entry beat a fresh narrow one that also covers the request, which
        # serves prices from an earlier adjustment epoch and, worse, hides the fresh
        # entry from the online path so the ticker is re-downloaded anyway.
        best_rank: Tuple[int, int] = (-1, -1)
        prefix = cache_key("closes", ticker)
        for path in sorted(self.cache.dir.glob(f"{prefix}_*.json")):
            entry = self.cache.read(path.stem)
            if entry is None or not isinstance(entry.payload, dict):
                continue
            payload = entry.payload
            if str(payload.get("ticker", "")).upper() != ticker:
                continue
            if str(payload.get("start", "9999")) > want_start:
                continue
            if str(payload.get("end", "")) < want_end:
                continue
            rows = len(payload.get("index") or [])
            rank = (1 if entry.age_seconds < self._ttl_for(rows) else 0, rows)
            if rank > best_rank:
                best_rank = rank
                best = (payload, entry.age_seconds)
        return best

    # -- the interface ----------------------------------------------------

    def daily_closes(
        self,
        tickers: Sequence[str],
        start: DateLike,
        end: Optional[DateLike] = None,
        *,
        ffill: bool = True,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """Daily adjusted closes, one column per requested ticker.

        ``end`` is inclusive, unlike yfinance's own. ``end=None`` reads the clock,
        which is the only clock read in this module and the reason a reproducible
        run should always pass an explicit end date.

        A ticker with no data at all keeps its column and gets nothing but ``NaN`` in
        it. It is never dropped and never zero filled, because a universe that
        quietly loses its failures is a universe that only contains winners. Ask
        :func:`coverage_on` which ones they are.

        The index is the union of the requested tickers' own sessions, because that
        is the only trading calendar the data actually supports. There is no
        pretence of an exchange calendar here, so adding a ticker that trades on a
        day the others do not adds a row, and the others are filled across it.

        Raises :class:`~an.store.Offline` only when the network was unavailable or
        refused and the cache had nothing at all to offer.
        """
        names = _tickers(tickers)
        start_ts = _as_ts(start)
        end_ts = _as_ts(end) if end is not None else _as_ts(pd.Timestamp.today())
        if end_ts < start_ts:
            raise ValueError(f"end {end_ts.date()} is before start {start_ts.date()}")
        self.last_source = {}
        self.last_error = None
        if not names:
            return _empty_panel([])

        offline = is_offline()
        series_by_ticker: Dict[str, Optional[pd.Series]] = {}
        misses: List[str] = []
        for t in names:
            if refresh and not offline:
                misses.append(t)
                continue
            cached, stale = self._read_cached(t, start_ts, end_ts, allow_stale=offline)
            if cached is None:
                misses.append(t)
            else:
                series_by_ticker[t] = cached
                # Offline is the mode this runs in most often, so it is the one that
                # must not label an arbitrarily old entry as a fresh cache hit. Same
                # answer as the failed-download loop below, for the same disk copy.
                self.last_source[t] = "stale" if stale else "cache"

        degraded = offline
        if misses and not offline:
            fetched: Optional[pd.DataFrame] = None
            try:
                raw = self.downloader.download(misses, start_ts, end_ts)
                # Parsing sits inside the try on purpose. A response in a shape this
                # module cannot read is a failed fetch like any other, and it has to
                # degrade to the cache below rather than take a run down that has a
                # complete panel sitting on disk.
                fetched = closes_from_download(raw, misses)
            except Exception as exc:  # Offline, FetchError, or anything yfinance invents
                degraded = True
                self.last_error = _safe_error(exc)
            if not degraded and fetched is not None:
                for t in misses:
                    s = _column(fetched, t)
                    if s is None:
                        s = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
                    s = s.dropna()
                    self.cache.write(self._key(t, start_ts, end_ts), self._to_payload(t, start_ts, end_ts, s))
                    series_by_ticker[t] = s
                    self.last_source[t] = "live" if len(s) else "missing"
                misses = []

        if misses:
            # Offline, or the download failed. Take anything on disk, however old.
            for t in misses:
                cached, stale = self._read_cached(t, start_ts, end_ts, allow_stale=True)
                if cached is None:
                    self.last_source[t] = "missing"
                else:
                    series_by_ticker[t] = cached
                    self.last_source[t] = "stale" if stale else "cache"

        if degraded and not any(s is not None and len(s) for s in series_by_ticker.values()):
            why = self.last_error or "DESK_OFFLINE is set"
            raise Offline(
                f"no prices for {', '.join(names[:5])}"
                f"{'...' if len(names) > 5 else ''} between {start_ts.date()} and {end_ts.date()}: "
                f"{why}, and nothing is cached in {self.cache.dir}"
            )

        panel = _assemble(series_by_ticker, names)
        panel = panel.loc[(panel.index >= start_ts) & (panel.index <= end_ts)]
        return _fill_within_life(panel) if ffill else panel


_DEFAULT_CLIENT: Optional[PriceClient] = None


def default_client() -> PriceClient:
    """Process wide client, built on first use so importing this module is free."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = PriceClient()
    return _DEFAULT_CLIENT


def daily_closes(
    tickers: Sequence[str],
    start: DateLike,
    end: Optional[DateLike] = None,
    *,
    client: Optional[PriceClient] = None,
    ffill: bool = True,
    refresh: bool = False,
) -> pd.DataFrame:
    """See :meth:`PriceClient.daily_closes`. Pass ``client`` to control the source."""
    return (client or default_client()).daily_closes(tickers, start, end, ffill=ffill, refresh=refresh)


# ---------------------------------------------------------------------------
# reading a panel
# ---------------------------------------------------------------------------


def first_observation(closes: pd.DataFrame, ticker: str) -> Optional[dt.date]:
    """Date of the first real print, or None. Nothing before this is a real price."""
    col = _column(_panel(closes), str(ticker).upper())
    if col is None:
        return None
    obs = col.dropna()
    return None if obs.empty else pd.Timestamp(obs.index[0]).date()


def last_observation(closes: pd.DataFrame, ticker: str) -> Optional[dt.date]:
    """Date of the last real print, or None. Past this the ticker stopped trading."""
    col = _column(_panel(closes), str(ticker).upper())
    if col is None:
        return None
    obs = col.dropna()
    return None if obs.empty else pd.Timestamp(obs.index[-1]).date()


def price_on(closes: pd.DataFrame, ticker: str, on_date: DateLike) -> Optional[float]:
    """The last price at or before ``on_date``, or None.

    At or before, never nearest and never the next one. Asking on a Saturday gives
    Friday's close. Asking the day before a company reports gives the price before
    the report, which is the only answer a backtest is allowed to have.
    """
    return _price_on(_panel(closes), str(ticker).upper(), _as_ts(on_date))


def _price_on(panel: pd.DataFrame, ticker: str, ts: pd.Timestamp) -> Optional[float]:
    col = _column(panel, ticker)
    if col is None:
        return None
    obs = col.dropna()
    if obs.empty:
        return None
    pos = int(obs.index.searchsorted(ts, side="right")) - 1
    if pos < 0:
        return None
    return _num(obs.iloc[pos])


@dataclass(frozen=True)
class ForwardReturn:
    """One forward return and the reason it is what it is.

    ``status`` is the field to branch on, not ``ret``. A ``None`` return means five
    different things and a backtest that treats them alike is wrong in at least four
    of them.

    ``ok``                 clean round trip over the full horizon
    ``truncated``          the ticker stopped trading inside the horizon, so this is
                           the return to its final traded price. ``stopped_trading``
                           is True and the position did not survive the window
    ``delisted``           same event, but the caller asked for None instead
    ``already_delisted``   the ticker had already stopped trading on ``from_date``,
                           so there was nothing to buy. Always None, under either
                           policy, because the alternative is to report the flat
                           0.0 percent that this module exists to refuse
    ``not_listed_yet``     no print at or before ``from_date``, and the panel has a
                           row before that date in which this ticker is empty, which
                           is what makes "had not listed" a claim rather than a guess
    ``unknown_before_window``
                           no print at or before ``from_date`` either, but the date
                           is at or before the panel's own first row, so the panel
                           cannot see far enough back to tell a company that had not
                           listed from one that was halted on the window's first day
    ``no_data``            the column exists and is entirely empty
    ``no_column``          the ticker was never in the panel, usually a typo
    ``short_panel``        the panel simply does not extend a horizon past the base
    ``bad_base_price``     a non positive or non finite base price
    """

    ticker: str
    from_date: Optional[dt.date]
    base_date: Optional[dt.date]
    end_date: Optional[dt.date]
    base_price: Optional[float]
    end_price: Optional[float]
    ret: Optional[float]
    horizon_days: int
    stopped_trading: bool = False
    status: str = "ok"

    @property
    def ok(self) -> bool:
        return self.ret is not None


def forward_return_detail(
    closes: pd.DataFrame,
    ticker: str,
    from_date: DateLike,
    horizon_days: int,
    *,
    on_delist: str = "truncate",
) -> ForwardReturn:
    """Forward return with its provenance and a delisting flag.

    ``horizon_days`` counts rows of the panel, which is the trading calendar the
    panel was built from, not calendar days. The count starts at the base row, the
    last row at or before ``from_date``, so a horizon measured from a weekend starts
    from Friday and ends a full horizon later. Since the panel's calendar is the
    union of its own columns' sessions, hold the panel fixed across a comparison
    rather than rebuilding it per ticker.

    ``on_delist`` is the whole delisting policy and there are only two honest
    options. ``"truncate"`` returns the move to the final traded price and sets
    ``stopped_trading``. ``"none"`` returns nothing at all. Forward filling the dead
    ticker to a flat 0.0 percent is not on the menu.
    """
    if on_delist not in ("truncate", "none"):
        raise ValueError(f"on_delist must be 'truncate' or 'none', got {on_delist!r}")
    if int(horizon_days) < 0:
        raise ValueError(f"horizon_days must be >= 0, got {horizon_days}")
    return _forward_one(_panel(closes), str(ticker).upper(), _as_ts(from_date), int(horizon_days), on_delist)


def _forward_one(
    panel: pd.DataFrame, ticker: str, ts: pd.Timestamp, horizon_days: int, on_delist: str
) -> ForwardReturn:
    def result(**kw: Any) -> ForwardReturn:
        base = dict(
            ticker=ticker,
            from_date=ts.date(),
            base_date=None,
            end_date=None,
            base_price=None,
            end_price=None,
            ret=None,
            horizon_days=horizon_days,
            stopped_trading=False,
            status="ok",
        )
        base.update(kw)
        return ForwardReturn(**base)  # type: ignore[arg-type]

    col = _column(panel, ticker)
    if col is None:
        return result(status="no_column")
    obs = col.dropna()
    if obs.empty:
        return result(status="no_data")

    idx = panel.index
    base_pos_obs = int(obs.index.searchsorted(ts, side="right")) - 1
    if base_pos_obs < 0:
        # No print at or before the date asked about. That is evidence of a company
        # which had not listed yet only when the panel has a row before that date and
        # this ticker is empty in it. Asked on (or before) the panel's own first row
        # there is nothing to compare against: a name that has traded for years but
        # is halted on the window's first day looks identical to an IPO, and calling
        # it "not listed" drops it from a rebalance for a reason that is not known.
        return result(status="not_listed_yet" if len(idx) and idx[0] < ts else "unknown_before_window")
    base_date = pd.Timestamp(obs.index[base_pos_obs])
    base_price = _num(obs.iloc[base_pos_obs])

    base_pos = int(idx.searchsorted(base_date, side="left"))
    last_pos = int(idx.searchsorted(pd.Timestamp(obs.index[-1]), side="left"))
    target_pos = base_pos + horizon_days
    last_panel_pos = len(idx) - 1

    # The ticker's data stops before the panel's does. That is a delisting, an
    # acquisition, or a halt that never reopened. It is detected against the panel
    # rather than against the calendar, so a single ticker panel that simply ends at
    # its last print cannot tell a delisting from the end of the window, and does
    # not pretend to.
    dead = last_pos < last_panel_pos
    if dead and base_pos == last_pos and base_date < ts:
        # Already gone on the day we were asked about. There was nothing to buy, so
        # there is no return, and the 0.0 percent that a forward filled panel would
        # have produced here is exactly the lie this module exists to refuse.
        return result(
            base_date=base_date.date(),
            base_price=base_price,
            stopped_trading=True,
            status="already_delisted",
        )

    stopped = dead and last_pos < target_pos
    if stopped:
        if on_delist == "none":
            return result(base_date=base_date.date(), base_price=base_price, stopped_trading=True, status="delisted")
        end_date = pd.Timestamp(obs.index[-1])
        end_price = _num(obs.iloc[-1])
        status = "truncated"
    elif target_pos <= last_panel_pos:
        target_date = idx[target_pos]
        end_pos_obs = int(obs.index.searchsorted(target_date, side="right")) - 1
        end_date = pd.Timestamp(obs.index[end_pos_obs])
        end_price = _num(obs.iloc[end_pos_obs])
        status = "ok"
    else:
        return result(base_date=base_date.date(), base_price=base_price, status="short_panel")

    if base_price is None or base_price <= 0.0:
        return result(
            base_date=base_date.date(),
            base_price=base_price,
            end_date=end_date.date(),
            end_price=end_price,
            stopped_trading=stopped,
            status="bad_base_price",
        )
    if end_price is None:
        return result(
            base_date=base_date.date(),
            base_price=base_price,
            stopped_trading=stopped,
            status="no_data",
        )
    return result(
        base_date=base_date.date(),
        base_price=base_price,
        end_date=end_date.date(),
        end_price=end_price,
        ret=_num(end_price / base_price - 1.0),
        stopped_trading=stopped,
        status=status,
    )


def forward_return(
    closes: pd.DataFrame,
    ticker: str,
    from_date: DateLike,
    horizon_days: int,
    *,
    on_delist: str = "truncate",
) -> Optional[float]:
    """The forward return as a plain float, or None.

    Call :func:`forward_return_detail` when you need to know whether the horizon was
    cut short by a delisting, which for anything that ranks or aggregates you do.
    """
    return forward_return_detail(closes, ticker, from_date, horizon_days, on_delist=on_delist).ret


def forward_returns_detail(
    closes: pd.DataFrame,
    tickers: Sequence[str],
    from_date: DateLike,
    horizon_days: int,
    *,
    on_delist: str = "truncate",
) -> Dict[str, ForwardReturn]:
    """One entry per requested ticker, including the ones with no answer."""
    if on_delist not in ("truncate", "none"):
        raise ValueError(f"on_delist must be 'truncate' or 'none', got {on_delist!r}")
    if int(horizon_days) < 0:
        raise ValueError(f"horizon_days must be >= 0, got {horizon_days}")
    panel = _panel(closes)
    ts = _as_ts(from_date)
    return {t: _forward_one(panel, t, ts, int(horizon_days), on_delist) for t in _tickers(tickers)}


def forward_returns(
    closes: pd.DataFrame,
    tickers: Sequence[str],
    from_date: DateLike,
    horizon_days: int,
    *,
    on_delist: str = "truncate",
) -> Dict[str, Optional[float]]:
    """``{ticker: return or None}``, never missing a key.

    Every requested name comes back. Dropping the ones without an answer is how a
    backtest ends up measuring only the names that made it to the end of the window.
    """
    detail = forward_returns_detail(closes, tickers, from_date, horizon_days, on_delist=on_delist)
    return {t: fr.ret for t, fr in detail.items()}


@dataclass(frozen=True)
class Coverage:
    """Who actually has a price on a given date, and who only looks like they do."""

    on_date: dt.date
    requested: Tuple[str, ...]
    available: Tuple[str, ...]
    not_listed_yet: Tuple[str, ...]
    stopped_trading: Tuple[str, ...]
    no_data: Tuple[str, ...]
    # Not tradeable on this date either, but for a reason the panel cannot see: the
    # date is at or before its first row, so "no price yet" and "not listed yet" are
    # the same picture. Kept apart from ``not_listed_yet`` because that one is used
    # as a statement about the company rather than about the window.
    unknown_before_window: Tuple[str, ...] = ()

    @property
    def missing(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                set(self.not_listed_yet)
                | set(self.stopped_trading)
                | set(self.no_data)
                | set(self.unknown_before_window)
            )
        )

    @property
    def ratio(self) -> Optional[float]:
        if not self.requested:
            return None
        return len(self.available) / len(self.requested)


def coverage_on(closes: pd.DataFrame, on_date: DateLike, tickers: Optional[Sequence[str]] = None) -> Coverage:
    """Split the universe on a date into who has a price and why the rest do not.

    Run this at every rebalance. A backtest that silently drops the names without
    prices is a survivorship biased backtest, and the difference between "not listed
    yet" and "stopped trading" is the difference between a name you could not have
    bought and a name you lost money on.

    Asked about a date at or before the panel's first row, a ticker with no price
    lands in ``unknown_before_window`` rather than ``not_listed_yet``: the panel has
    nothing earlier to compare against, so it declines to claim the company had not
    listed. Give the panel some history before the date you ask about.
    """
    panel = _panel(closes)
    names = _tickers(tickers) if tickers is not None else _tickers(list(panel.columns))
    ts = _as_ts(on_date)
    available: List[str] = []
    not_yet: List[str] = []
    stopped: List[str] = []
    nothing: List[str] = []
    unknown: List[str] = []
    has_earlier_row = len(panel.index) > 0 and pd.Timestamp(panel.index[0]) < ts
    for t in names:
        col = _column(panel, t)
        obs = col.dropna() if col is not None else None
        if obs is None or obs.empty:
            nothing.append(t)
            continue
        first, last = pd.Timestamp(obs.index[0]), pd.Timestamp(obs.index[-1])
        if ts < first:
            # "Not listed yet" is only supportable when the panel actually looks back
            # past this date. On the panel's own first row it does not, and a halted
            # long listed name would otherwise be reported as one you could not have
            # bought, which is the distinction this function exists to get right.
            (not_yet if has_earlier_row else unknown).append(t)
        elif ts > last:
            stopped.append(t)
        else:
            available.append(t)
    return Coverage(
        on_date=ts.date(),
        requested=tuple(names),
        available=tuple(available),
        not_listed_yet=tuple(not_yet),
        stopped_trading=tuple(stopped),
        no_data=tuple(nothing),
        unknown_before_window=tuple(unknown),
    )


def missing_on(closes: pd.DataFrame, on_date: DateLike, tickers: Optional[Sequence[str]] = None) -> List[str]:
    """Sorted list of tickers with no price on ``on_date``, for any reason."""
    return list(coverage_on(closes, on_date, tickers).missing)


# ---------------------------------------------------------------------------
# synthetic prices
# ---------------------------------------------------------------------------


def _rng_for(seed: int, ticker: str) -> np.random.Generator:
    """A generator that depends on the seed and the ticker, and on nothing else.

    Deriving each ticker's stream separately is what makes a planted signal usable:
    adding a name to the list, or reordering it, leaves every other path bit for bit
    identical, so a test can change one ticker and attribute the whole difference to
    it. SHA-256 rather than ``hash()`` because ``hash()`` of a string is salted per
    process and would produce a different panel on every run.
    """
    digest = hashlib.sha256(str(ticker).encode("utf-8")).digest()[:8]
    return np.random.default_rng([int(seed), int.from_bytes(digest, "big")])


def synthetic_panel(
    tickers: Sequence[str],
    start: DateLike = "2020-01-01",
    periods: int = TRADING_DAYS_PER_YEAR,
    seed: int = 0,
    *,
    drift: Numberish = 0.0,
    vol: Numberish = DEFAULT_VOL,
    start_price: Numberish = 100.0,
    freq: str = "B",
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    round_to: Optional[int] = None,
) -> pd.DataFrame:
    """A deterministic geometric Brownian motion panel.

    For tests, and for validating the backtest engine against a signal whose answer
    is known in advance. ``drift`` and ``vol`` are annualised and may be a scalar or
    a per ticker mapping, which is the point: plant a 20 percent drift on one name
    and nothing on the rest, and a working backtest has to find it.

    Determinism is a hard requirement, not a nicety. Same seed, same panel, byte for
    byte, on any machine: numpy's PCG64 through an explicit :class:`SeedSequence`,
    no clock, no ``hash()``, no global random state. The first row is exactly
    ``start_price`` so a horizon can be counted from a known number.

    Set ``vol=0.0`` for a noiseless path, where ``close[t]`` is exactly
    ``start_price * exp(drift * t / trading_days_per_year)`` and any return over the
    path is analytically checkable.

    The index is plain weekdays. There is no exchange holiday calendar here, because
    a fake calendar that claims to be the NYSE and is not would be worse than an
    honest one that never claimed to be.
    """
    names = _tickers(tickers)
    n = int(periods)
    if n < 1:
        raise ValueError(f"periods must be >= 1, got {periods}")
    index = pd.date_range(start=_as_ts(start), periods=n, freq=freq)
    if not names:
        return pd.DataFrame(index=index, columns=[], dtype="float64")

    step = 1.0 / float(trading_days_per_year)
    columns: Dict[str, np.ndarray] = {}
    for t in names:
        mu = _per_ticker(drift, t, 0.0)
        # The parameter default, not zero: a ticker left out of a ``vol`` mapping got
        # a flat, noiseless path, which is a silent control group and exactly the
        # artefact ``vol=0.0`` is supposed to be an explicit request for.
        sigma = abs(_per_ticker(vol, t, DEFAULT_VOL))
        p0 = _per_ticker(start_price, t, 100.0)
        rng = _rng_for(seed, t)
        shocks = rng.standard_normal(n - 1) if n > 1 else np.zeros(0)
        increments = (mu - 0.5 * sigma * sigma) * step + sigma * math.sqrt(step) * shocks
        log_path = np.concatenate([np.zeros(1), np.cumsum(increments)])
        columns[t] = float(p0) * np.exp(log_path)

    panel = pd.DataFrame(columns, index=index, dtype="float64")[names]
    if round_to is not None:
        # Off by default. Rounding to cents is more realistic and it also destroys
        # the exact arithmetic a planted signal test depends on.
        panel = panel.round(int(round_to))
    return panel


def delist(panel: pd.DataFrame, ticker: str, on_or_after: DateLike) -> pd.DataFrame:
    """Copy of ``panel`` where ``ticker`` stops printing on ``on_or_after``.

    Building a dead ticker is fiddly enough to get subtly wrong in each test that
    needs one, and every test of the delisting path needs one.
    """
    out = _panel(panel).copy()
    name = str(ticker).upper()
    if name not in out.columns:
        raise KeyError(f"{name} is not in the panel")
    out.loc[out.index >= _as_ts(on_or_after), name] = np.nan
    return out
