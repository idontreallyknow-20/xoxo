"""Fifteen-minute bars for the watched names, so a rule can be checked during the session.

The daily scan reads closes. Joseph asked for a midday check and an email when
something important happens during the day, which needs a price before the
close. This module is the smallest thing that gives one, and it is deliberately
narrow:

* **Watched names only.** Sixteen to thirty tickers, one batched ``yfinance``
  call, never the 150. NOTES.md 9c explains why hourly across the universe is a
  different problem; this is not that.
* **One interval, one window.** Fifteen-minute bars over the last five sessions,
  which is enough to give the last print, the session's high and low, and the
  prior close to measure a move against. Nothing finer, because a rule written
  as "a close under $X" does not care about a one-minute wick.
* **Cached ten minutes.** The intraday task runs every thirty minutes; a re-run
  inside ten minutes is served from disk and costs Yahoo nothing.
* **Labelled.** Every read says it is an intraday print and not a close, and the
  alerts built on it say the same, because the journal's rules are closing rules.

Same shape as :mod:`an.prices`: a ``Downloader`` protocol, the real one behind
it, an offline one, a fixture, ``None`` for every hole. Nothing here has run
against the live endpoint.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence

import pandas as pd

from . import paths
from .store import Cache, FetchError, Offline, cache_key, is_offline

__all__ = [
    "Bars", "Print", "Downloader", "YFinanceIntradayDownloader", "OfflineDownloader", "RawDownloader",
    "IntradayClient", "bars_from_download", "last_print", "INTERVAL", "PERIOD", "TTL", "INTRADAY_CACHE",
    "MARKET_TZ",
]

INTERVAL = "15m"
PERIOD = "5d"
TTL = 10 * 60.0
INTRADAY_CACHE = paths.CACHE_DIR / "intraday"
MARKET_TZ = "America/New_York"
FIELDS = ("Open", "High", "Low", "Close")

Bars = Dict[str, pd.DataFrame]
"""ticker -> DataFrame[Open, High, Low, Close] indexed by tz-aware bar start (America/New_York)."""


def _tickers(tickers: Sequence[str]) -> List[str]:
    seen: List[str] = []
    for t in tickers:
        u = str(t).strip().upper()
        if u and u not in seen:
            seen.append(u)
    return seen


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _to_market_tz(index: Any) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        # yfinance returns tz-aware intraday indexes; a fixture written without a zone is read as UTC.
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(MARKET_TZ)


def bars_from_download(raw: Optional[pd.DataFrame], tickers: Sequence[str]) -> Bars:
    """Pull one OHLC frame per ticker out of a ``yfinance.download`` result.

    Handles the two shapes that turn up: fields on the outer column level with
    tickers inner (``group_by="column"``), and a flat frame for a single ticker.
    A ticker with nothing in the response gets an empty frame, never a missing key.
    """
    wanted = _tickers(tickers)
    out: Bars = {t: pd.DataFrame(columns=list(FIELDS)) for t in wanted}
    if raw is None or not isinstance(raw, pd.DataFrame) or len(raw.index) == 0:
        return out
    cols = raw.columns
    per: Dict[str, pd.DataFrame] = {}
    if isinstance(cols, pd.MultiIndex):
        field_level = None
        for level in range(cols.nlevels):
            if {str(v) for v in cols.get_level_values(level)} & set(FIELDS):
                field_level = level
                break
        if field_level is None:
            raise FetchError(f"no OHLC field in downloaded columns: {list(cols)[:4]}")
        ticker_level = 1 - field_level
        for t in {str(v).upper() for v in cols.get_level_values(ticker_level)}:
            frame = pd.DataFrame()
            for f in FIELDS:
                key = (f, t) if field_level == 0 else (t, f)
                matches = [c for c in cols if str(c[field_level]) == f and str(c[ticker_level]).upper() == t]
                if matches:
                    frame[f] = pd.to_numeric(raw[matches[0]], errors="coerce")
            per[t] = frame
    else:
        if len(wanted) != 1:
            raise FetchError("a flat intraday frame can only describe one ticker")
        flat = {str(c): c for c in cols}
        frame = pd.DataFrame({f: pd.to_numeric(raw[flat[f]], errors="coerce") for f in FIELDS if f in flat})
        per[wanted[0]] = frame
    for t, frame in per.items():
        if t not in out or frame.empty:
            continue
        frame = frame.copy()
        frame.index = _to_market_tz(raw.index)
        frame = frame.sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        frame = frame.dropna(how="all")
        out[t] = frame
    return out


# ---------------------------------------------------------------------------
# downloaders
# ---------------------------------------------------------------------------

class Downloader(Protocol):
    def download(self, tickers: Sequence[str]) -> Optional[pd.DataFrame]: ...

    @property
    def live(self) -> bool: ...


@dataclass
class YFinanceIntradayDownloader:
    """The only thing here that touches the network. One call for every name."""

    interval: str = INTERVAL
    period: str = PERIOD
    timeout: float = 30.0
    calls: List[tuple] = field(default_factory=list, repr=False)

    @property
    def live(self) -> bool:
        return True

    def download(self, tickers: Sequence[str]) -> Optional[pd.DataFrame]:
        if is_offline():
            raise Offline("DESK_OFFLINE is set, refusing to download intraday bars")
        names = _tickers(tickers)
        if not names:
            return None
        self.calls.append(tuple(names))
        import yfinance

        return yfinance.download(
            tickers=names, period=self.period, interval=self.interval, auto_adjust=False, actions=False,
            group_by="column", threads=2, progress=False, timeout=self.timeout, prepost=False,
        )


@dataclass
class RawDownloader:
    """Replay a recorded frame. For tests and ``--fixture``."""

    frame: Optional[pd.DataFrame]
    calls: List[tuple] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return False

    def download(self, tickers: Sequence[str]) -> Optional[pd.DataFrame]:
        self.calls.append(tuple(_tickers(tickers)))
        return self.frame


@dataclass
class OfflineDownloader:
    @property
    def live(self) -> bool:
        return False

    def download(self, tickers: Sequence[str]) -> Optional[pd.DataFrame]:
        raise Offline("intraday bars are not pulled in offline mode")


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

@dataclass
class IntradayClient:
    downloader: Downloader = field(default_factory=YFinanceIntradayDownloader)
    cache: Cache = field(default_factory=lambda: Cache(INTRADAY_CACHE, default_ttl=TTL))
    last_source: str = "none"
    last_live: bool = False

    @staticmethod
    def _payload(bars: Bars) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for t, frame in bars.items():
            out[t] = {"index": [ts.isoformat() for ts in frame.index],
                      **{f.lower(): [_f(v) for v in frame[f].to_numpy()] if f in frame.columns else [] for f in FIELDS}}
        return out

    @staticmethod
    def _from_payload(payload: Any, tickers: Sequence[str]) -> Bars:
        out: Bars = {}
        for t in _tickers(tickers):
            row = (payload or {}).get(t) if isinstance(payload, dict) else None
            if not isinstance(row, dict) or not row.get("index"):
                out[t] = pd.DataFrame(columns=list(FIELDS))
                continue
            frame = pd.DataFrame({f: row.get(f.lower()) or [None] * len(row["index"]) for f in FIELDS},
                                 index=_to_market_tz(pd.to_datetime(row["index"], utc=True)))
            out[t] = frame.astype("float64")
        return out

    def bars(self, tickers: Sequence[str]) -> Bars:
        """Bars for every name, from the cache when fresh, else one download.

        A failed download with a stale cache returns the stale cache and says so
        in ``last_source``; with nothing cached it raises :class:`Offline`.
        """
        names = _tickers(tickers)
        if not names:
            return {}
        key = cache_key("bars", INTERVAL, *names)
        entry = self.cache.read(key)
        if entry is not None and entry.age_seconds < self.cache.default_ttl:
            self.last_source, self.last_live = "cache", bool((self._meta(key) or {}).get("live"))
            return self._from_payload(entry.payload, names)
        try:
            raw = self.downloader.download(names)
            bars = bars_from_download(raw, names)
        except Exception as exc:  # Offline, FetchError, anything yfinance invents
            if entry is not None:
                self.last_source, self.last_live = "stale", bool((self._meta(key) or {}).get("live"))
                return self._from_payload(entry.payload, names)
            raise Offline(f"no intraday bars for {', '.join(names[:5])}: {type(exc).__name__}: {exc}") from None
        self.cache.write(key, self._payload(bars), meta={"live": self.downloader.live,
                                                         "downloader": type(self.downloader).__name__,
                                                         "interval": INTERVAL, "period": PERIOD})
        self.last_source, self.last_live = "live" if self.downloader.live else "replay", self.downloader.live
        return bars

    def _meta(self, key: str) -> Optional[Dict[str, Any]]:
        p = self.cache.path_for(key)
        if not p.exists():
            return None
        try:
            import json

            return (json.loads(p.read_text(encoding="utf-8")) or {}).get("meta")
        except (OSError, ValueError):
            return None


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Print:
    ticker: str
    price: Optional[float]
    at: Optional[str]
    """ISO timestamp of the last bar's start, America/New_York."""
    session: Optional[str]
    """The date of that bar, so a stale print from yesterday is visible as such."""
    prior_close: Optional[float]
    change: Optional[float]
    session_high: Optional[float]
    session_low: Optional[float]
    bars_in_session: int
    intraday: bool = True

    def to_json(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def last_print(bars: Bars, ticker: str, *, prior_close: Optional[float] = None,
               as_of: Optional[dt.datetime] = None) -> Print:
    """The last bar for a name and the session it belongs to.

    ``prior_close`` is the previous session's close from the daily panel; when it
    is not given the last bar of the previous session in the intraday window is
    used, and when there is no previous session the change is ``None``.
    """
    t = ticker.upper()
    frame = bars.get(t)
    empty = Print(t, None, None, None, prior_close, None, None, None, 0)
    if frame is None or frame.empty or "Close" not in frame.columns:
        return empty
    frame = frame.dropna(subset=["Close"])
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        cutoff = cutoff.tz_localize(MARKET_TZ) if cutoff.tzinfo is None else cutoff.tz_convert(MARKET_TZ)
        frame = frame[frame.index < cutoff]  # a bar starting at the cutoff has not finished
    if frame.empty:
        return empty
    last_ts = frame.index[-1]
    session = last_ts.date()
    today = frame[frame.index.date == session]
    prev = frame[frame.index.date < session]
    prior = prior_close if prior_close is not None else (_f(prev["Close"].iloc[-1]) if not prev.empty else None)
    price = _f(frame["Close"].iloc[-1])
    return Print(
        ticker=t, price=price, at=last_ts.isoformat(), session=session.isoformat(), prior_close=prior,
        change=(price / prior - 1.0) if price is not None and prior else None,
        session_high=_f(today["High"].max()) if "High" in today.columns and not today.empty else None,
        session_low=_f(today["Low"].min()) if "Low" in today.columns and not today.empty else None,
        bars_in_session=int(len(today)),
    )
