"""Real daily price history, the first this repository has ever had on disk.

Every market-data host is refused at the egress proxy of the build environment, but
``raw.githubusercontent.com`` is not, and a handful of public repositories carry real
daily closes. This module parses them into one panel, audits it, and cuts a small
committed fixture from it. It never fetches; ``scripts/fetch_history.py`` does that and
writes a manifest with the provenance of every byte.

The sources, and what each is for:

* **plotly/datasets ``all_stocks_5yr.csv``** (the Kaggle "S&P 500" set): 505 names as
  constituted in February 2018, daily OHLCV from 2013-02-08 to 2018-02-07. The universe.
  Split-adjusted (checked on AAPL 2014 and NFLX 2015), **not** dividend-adjusted.
* **QuantConnect Lean ``Data/equity/usa/daily/*.zip``**: raw daily bars, prices scaled by
  10000, with a factor file per symbol. SPY and QQQ from here are the benchmarks; AAPL, IBM,
  BAC and AIG are cross-checks against the plotly set.
* **yumoxu/stocknet-dataset ``price/raw/*.csv``**: yfinance-shaped, a second cross-check.
* **fja05680/sp500 ``sp500_ticker_start_end.csv``**: membership intervals, so a name is only
  eligible on dates it was actually in the index. This removes the *inclusion* side of
  survivorship (names added later are not traded before they were added); it cannot add
  back names that left, because their prices are not in the set.

The basis decision, made once here: **every return in this work is a price return**. The
universe has no dividends in it, so the benchmark must not have them either, or every excess
figure is biased down by roughly the yield. That is limitation number one in every report.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from . import paths

__all__ = [
    "Source", "SOURCES", "HISTORY_CACHE", "HistoryPanel", "Membership", "SplitFinding", "CrossCheck", "AuditReport",
    "KNOWN_SPLITS", "FIXTURE_TICKERS", "load_plotly_long", "load_lean_zip", "load_lean_factor_file", "apply_lean_factors",
    "load_stocknet_csv", "load_membership", "audit_splits", "dataset_adjustment_verdict", "adjust_for_splits",
    "cross_check", "suspect_tickers", "audit", "write_panel", "read_panel", "load_cached", "make_fixture", "load_fixture", "sha256_of",
]

HISTORY_CACHE = paths.CACHE_DIR / "history"
RAW_DIR = HISTORY_CACHE / "raw"
PANEL_DIR = HISTORY_CACHE / "panel"
MANIFEST = HISTORY_CACHE / "manifest.json"
FIXTURE_DIR = paths.ROOT / "tests" / "fixtures" / "history"

_RAW = "https://raw.githubusercontent.com"


@dataclass(frozen=True)
class Source:
    key: str
    url: str
    filename: str
    kind: str   # plotly_long | lean_zip | lean_factor | stocknet_csv | membership_csv
    role: str   # universe | benchmark | crosscheck | membership


SOURCES: Tuple[Source, ...] = (
    Source("plotly", f"{_RAW}/plotly/datasets/master/all_stocks_5yr.csv", "all_stocks_5yr.csv", "plotly_long", "universe"),
    Source("membership", f"{_RAW}/fja05680/sp500/master/sp500_ticker_start_end.csv", "sp500_ticker_start_end.csv",
           "membership_csv", "membership"),
    Source("lean_spy", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/daily/spy.zip", "spy.zip", "lean_zip", "benchmark"),
    Source("lean_qqq", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/daily/qqq.zip", "qqq.zip", "lean_zip", "benchmark"),
    Source("factor_spy", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/factor_files/spy.csv", "factor_spy.csv",
           "lean_factor", "benchmark"),
    Source("factor_qqq", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/factor_files/qqq.csv", "factor_qqq.csv",
           "lean_factor", "benchmark"),
) + tuple(
    Source(f"lean_{s}", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/daily/{s}.zip", f"{s}.zip", "lean_zip", "crosscheck")
    for s in ("aapl", "ibm", "bac", "aig")
) + tuple(
    Source(f"factor_{s}", f"{_RAW}/QuantConnect/Lean/master/Data/equity/usa/factor_files/{s}.csv", f"factor_{s}.csv",
           "lean_factor", "crosscheck")
    for s in ("aapl", "ibm", "bac", "aig")
) + tuple(
    Source(f"stocknet_{s}", f"{_RAW}/yumoxu/stocknet-dataset/master/price/raw/{s}.csv", f"stocknet_{s}.csv",
           "stocknet_csv", "crosscheck")
    for s in ("AAPL", "MSFT")
)

# (ticker, first session at the new price, ratio). Checked, not remembered: audit_splits
# looks at the actual closes around each and reports what it found.
KNOWN_SPLITS: Tuple[Tuple[str, str, float], ...] = (
    ("AAPL", "2014-06-09", 7.0),
    ("NFLX", "2015-07-15", 7.0),
    ("V", "2015-03-19", 4.0),
    ("NKE", "2015-12-24", 2.0),
)

FIXTURE_TICKERS: Tuple[str, ...] = ("AAPL", "MSFT", "AMZN", "JPM", "XOM", "JNJ", "NFLX", "NVDA", "BA", "CAT", "PG", "KO")
FIXTURE_START = "2015-01-02"
FIXTURE_END = "2017-09-29"
BENCHMARKS: Tuple[str, ...] = ("SPY", "QQQ")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# panel
# ---------------------------------------------------------------------------

@dataclass
class HistoryPanel:
    close: pd.DataFrame
    volume: pd.DataFrame
    benchmarks: pd.DataFrame
    open: Optional[pd.DataFrame] = None
    high: Optional[pd.DataFrame] = None
    low: Optional[pd.DataFrame] = None
    source: str = ""
    adjustments: List["SplitFinding"] = field(default_factory=list)

    @property
    def tickers(self) -> List[str]:
        return list(self.close.columns)

    @property
    def first(self) -> Optional[str]:
        return None if self.close.empty else self.close.index[0].date().isoformat()

    @property
    def last(self) -> Optional[str]:
        return None if self.close.empty else self.close.index[-1].date().isoformat()

    @property
    def sessions(self) -> int:
        return int(len(self.close.index))

    def window(self, start: str, end: str) -> "HistoryPanel":
        """Rows between two ISO dates inclusive. Columns are unchanged."""
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        cut = lambda df: None if df is None else df[(df.index >= s) & (df.index <= e)]  # noqa: E731
        return HistoryPanel(cut(self.close), cut(self.volume), cut(self.benchmarks), cut(self.open), cut(self.high),
                            cut(self.low), self.source, list(self.adjustments))


def _wide(df: pd.DataFrame, value: str) -> pd.DataFrame:
    w = df.pivot_table(index="date", columns="ticker", values=value, aggfunc="first")
    w.index = pd.DatetimeIndex(pd.to_datetime(w.index)).tz_localize(None).normalize()
    w = w.sort_index()
    w.columns = [str(c).upper() for c in w.columns]
    w = w.reindex(sorted(w.columns), axis=1)
    return w.astype(float)


def load_plotly_long(text_or_path: Any) -> HistoryPanel:
    """``date,open,high,low,close,volume,Name`` in long form -> wide panels."""
    if isinstance(text_or_path, (str, bytes)) and not str(text_or_path).lstrip().startswith("date"):
        df = pd.read_csv(text_or_path)
    else:
        df = pd.read_csv(io.StringIO(text_or_path.decode() if isinstance(text_or_path, bytes) else text_or_path))
    df = df.rename(columns={"Name": "ticker"})
    need = {"date", "open", "high", "low", "close", "volume", "ticker"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"plotly file is missing columns: {sorted(missing)}")
    df["ticker"] = df["ticker"].astype(str).str.upper()
    close = _wide(df, "close")
    empty_bench = pd.DataFrame(index=close.index, columns=list(BENCHMARKS), dtype=float)
    return HistoryPanel(close=close, volume=_wide(df, "volume"), benchmarks=empty_bench, open=_wide(df, "open"),
                        high=_wide(df, "high"), low=_wide(df, "low"), source="plotly/datasets all_stocks_5yr.csv")


def load_lean_zip(zip_bytes: bytes, symbol: str) -> pd.DataFrame:
    """QuantConnect daily bars: ``yyyyMMdd HH:mm,open,high,low,close,volume`` with prices x10000."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = z.namelist()
        want = f"{symbol.lower()}.csv"
        name = want if want in names else names[0]
        text = z.read(name).decode("utf-8")
    df = pd.read_csv(io.StringIO(text), header=None, names=["stamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["stamp"].astype(str).str.slice(0, 8), format="%Y%m%d")
    df = df.drop(columns=["stamp"]).set_index("date").sort_index()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float) / 10000.0
    df["volume"] = df["volume"].astype(float)
    df.index = pd.DatetimeIndex(df.index).normalize()
    return df[~df.index.duplicated(keep="last")]


def load_lean_factor_file(text: str) -> pd.DataFrame:
    """``yyyyMMdd,price_factor,split_factor,reference_price``. Each row applies to dates on or before it."""
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        rows.append({"date": pd.Timestamp(dt.datetime.strptime(parts[0], "%Y%m%d")),
                     "price_factor": float(parts[1]), "split_factor": float(parts[2]),
                     "reference_price": float(parts[3]) if len(parts) > 3 and parts[3] else None})
    if not rows:
        raise ValueError("empty factor file")
    return pd.DataFrame(rows).set_index("date").sort_index()


def apply_lean_factors(raw: pd.DataFrame, factors: pd.DataFrame, *, dividends: bool = False) -> pd.DataFrame:
    """Adjust raw Lean bars. ``dividends=False`` applies splits only, which is this project's basis."""
    if raw.empty:
        return raw.copy()
    # The factor in force on a date is the first factor row whose date is >= that date.
    fdates = factors.index
    pos = fdates.searchsorted(raw.index, side="left")
    pos = [min(p, len(fdates) - 1) for p in pos]
    split = factors["split_factor"].to_numpy()[pos]
    price = factors["price_factor"].to_numpy()[pos] if dividends else 1.0
    out = raw.copy()
    for c in ("open", "high", "low", "close"):
        if c in out.columns:
            out[c] = out[c] * split * price
    if "volume" in out.columns:
        out["volume"] = out["volume"] / split
    return out


def load_stocknet_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    df.index = pd.DatetimeIndex(df.index).normalize()
    return df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Adj Close": "adj_close",
                              "Volume": "volume"})


# ---------------------------------------------------------------------------
# membership
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Membership:
    """Index membership intervals: ticker -> [(start, end or None), ...]."""
    intervals: Mapping[str, Tuple[Tuple[dt.date, Optional[dt.date]], ...]]
    source: str = ""

    def on(self, date: Any) -> FrozenSet[str]:
        d = pd.Timestamp(date).date()
        out = set()
        for t, spans in self.intervals.items():
            for s, e in spans:
                if s <= d and (e is None or d <= e):
                    out.add(t)
                    break
        return frozenset(out)

    def mask(self, index: pd.DatetimeIndex, tickers: Sequence[str]) -> pd.DataFrame:
        """Boolean date x ticker: was the name a member on that date. Unknown names are False."""
        m = pd.DataFrame(False, index=index, columns=list(tickers))
        for t in tickers:
            for s, e in self.intervals.get(t, ()):
                lo = pd.Timestamp(s)
                hi = pd.Timestamp(e) if e is not None else index[-1] if len(index) else lo
                m.loc[(index >= lo) & (index <= hi), t] = True
        return m

    def restricted(self, tickers: Iterable[str]) -> "Membership":
        keep = {t for t in tickers}
        return Membership({t: v for t, v in self.intervals.items() if t in keep}, self.source)

    def to_csv(self) -> str:
        lines = ["ticker,start_date,end_date"]
        for t in sorted(self.intervals):
            for s, e in self.intervals[t]:
                lines.append(f"{t},{s.isoformat()},{'' if e is None else e.isoformat()}")
        return "\n".join(lines) + "\n"


def load_membership(text: str, *, source: str = "fja05680/sp500 sp500_ticker_start_end.csv") -> Membership:
    df = pd.read_csv(io.StringIO(text), dtype=str).fillna("")
    cols = {c.lower().strip(): c for c in df.columns}
    if "ticker" not in cols or "start_date" not in cols:
        raise ValueError(f"unexpected membership columns: {list(df.columns)}")
    out: Dict[str, List[Tuple[dt.date, Optional[dt.date]]]] = {}
    for _, r in df.iterrows():
        t = str(r[cols["ticker"]]).strip().upper().replace(".", "-")
        s = str(r[cols["start_date"]]).strip()
        e = str(r[cols["end_date"]]).strip() if "end_date" in cols else ""
        if not t or not s:
            continue
        sd = pd.Timestamp(s).date()
        ed = pd.Timestamp(e).date() if e else None
        out.setdefault(t, []).append((sd, ed))
    return Membership({t: tuple(sorted(v)) for t, v in out.items()}, source)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SplitFinding:
    ticker: str
    date: str
    ratio_seen: Optional[float]
    ratio_known: Optional[float]
    volume_multiple: Optional[float]
    verdict: str   # adjusted | unadjusted | ambiguous | no_data | candidate
    applied: bool = False

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


_COMMON_RATIOS = (1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0)


def _ratio_at(close: pd.Series, volume: Optional[pd.Series], date: str) -> Tuple[Optional[float], Optional[float]]:
    s = close.dropna()
    ts = pd.Timestamp(date)
    after = s.index[s.index >= ts]
    if len(after) == 0:
        return None, None
    pos = s.index.get_loc(after[0])
    if pos == 0:
        return None, None
    prev, cur = float(s.iloc[pos - 1]), float(s.iloc[pos])
    if cur <= 0 or prev <= 0:
        return None, None
    vm = None
    if volume is not None:
        v = volume.reindex(s.index)
        base = v.iloc[max(0, pos - 20):pos].mean()
        if base and base > 0 and pd.notna(v.iloc[pos]):
            vm = float(v.iloc[pos] / base)
    return prev / cur, vm


def audit_splits(close: pd.DataFrame, volume: Optional[pd.DataFrame], known: Sequence[Tuple[str, str, float]] = KNOWN_SPLITS,
                 *, big_move: float = 0.40) -> List[SplitFinding]:
    """Known events first, then a sweep for anything that looks like an unadjusted split."""
    out: List[SplitFinding] = []
    for t, d, ratio in known:
        if t not in close.columns:
            out.append(SplitFinding(t, d, None, ratio, None, "no_data"))
            continue
        seen, vm = _ratio_at(close[t], volume[t] if volume is not None and t in volume.columns else None, d)
        if seen is None:
            out.append(SplitFinding(t, d, None, ratio, vm, "no_data"))
        elif abs(seen - 1.0) < 0.10:
            out.append(SplitFinding(t, d, round(seen, 4), ratio, vm, "adjusted"))
        elif abs(seen / ratio - 1.0) < 0.12:
            out.append(SplitFinding(t, d, round(seen, 4), ratio, vm, "unadjusted"))
        else:
            out.append(SplitFinding(t, d, round(seen, 4), ratio, vm, "ambiguous"))
    # sweep: a one-day move past big_move that the next three sessions do not reverse, near a common ratio
    ret = close / close.shift(1) - 1.0
    hits = ret.abs() > big_move
    for t in close.columns:
        col = hits[t]
        for ts in col.index[col.fillna(False)]:
            s = close[t].dropna()
            pos = s.index.get_loc(ts)
            if pos < 1 or pos + 3 >= len(s):
                continue
            prev, cur = float(s.iloc[pos - 1]), float(s.iloc[pos])
            later = float(s.iloc[pos + 1:pos + 4].mean())
            if cur <= 0 or prev <= 0 or abs(later / cur - 1.0) > 0.15:
                continue
            r = prev / cur
            near = any(abs(r / c - 1.0) < 0.08 or abs(r * c - 1.0) < 0.08 for c in _COMMON_RATIOS)
            if not near:
                continue
            if any(f.ticker == t and f.date == ts.date().isoformat() for f in out):
                continue
            vm = None
            if volume is not None and t in volume.columns:
                _, vm = _ratio_at(close[t], volume[t], ts.date().isoformat())
            out.append(SplitFinding(t, ts.date().isoformat(), round(r, 4), None, vm, "candidate"))
    return out


def dataset_adjustment_verdict(findings: Sequence[SplitFinding]) -> str:
    known = [f for f in findings if f.ratio_known is not None and f.verdict in ("adjusted", "unadjusted", "ambiguous")]
    if not known:
        return "unknown"
    if any(f.verdict == "ambiguous" for f in known):
        return "mixed"
    kinds = {f.verdict for f in known}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def adjust_for_splits(panel: HistoryPanel, findings: Sequence[SplitFinding], *, only_known: bool = True) -> HistoryPanel:
    """Divide history before each unadjusted event by the ratio seen. Never applied silently: the
    returned panel's ``adjustments`` lists what was done."""
    close, volume = panel.close.copy(), panel.volume.copy()
    o = None if panel.open is None else panel.open.copy()
    h = None if panel.high is None else panel.high.copy()
    lo = None if panel.low is None else panel.low.copy()
    done: List[SplitFinding] = list(panel.adjustments)
    for f in findings:
        if f.verdict != "unadjusted" or f.ratio_seen is None or f.ticker not in close.columns:
            continue
        if only_known and f.ratio_known is None:
            continue
        before = close.index < pd.Timestamp(f.date)
        for df in (close, o, h, lo):
            if df is not None:
                df.loc[before, f.ticker] = df.loc[before, f.ticker] / f.ratio_seen
        volume.loc[before, f.ticker] = volume.loc[before, f.ticker] * f.ratio_seen
        done.append(SplitFinding(f.ticker, f.date, f.ratio_seen, f.ratio_known, f.volume_multiple, f.verdict, True))
    return HistoryPanel(close, volume, panel.benchmarks, o, h, lo, panel.source, done)


@dataclass(frozen=True)
class CrossCheck:
    ticker: str
    a: str
    b: str
    n_overlap: int
    median_abs_rel_diff: Optional[float]
    max_abs_rel_diff: Optional[float]
    passed: bool
    level_ratio: Optional[float] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def cross_check(a: pd.Series, b: pd.Series, *, ticker: str, a_name: str, b_name: str, median_tol: float = 0.001,
                max_tol: float = 0.10) -> CrossCheck:
    """Two series of the same name from two sources should agree on every shared day's *return*.

    Returns, not levels: one source may carry a later split the other predates (Lean's AAPL
    factor includes 2020's 4:1, the 2013-2018 panel cannot), which is a constant ratio and no
    defect. A split one source adjusted and the other did not shows up as a one-day return
    disagreement of 50% or more, which is what the ``max_tol`` catches. ``level_ratio`` is
    reported so the constant is visible."""
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    j = j[(j["a"] > 0) & (j["b"] > 0)]
    n = int(len(j))
    if n < 20:
        return CrossCheck(ticker, a_name, b_name, n, None, None, False, None)
    ra, rb = j["a"] / j["a"].shift(1) - 1.0, j["b"] / j["b"].shift(1) - 1.0
    rel = (ra - rb).abs().dropna()
    med, mx = float(rel.median()), float(rel.max())
    ratio = float((j["a"] / j["b"]).median())
    return CrossCheck(ticker, a_name, b_name, n, round(med, 6), round(mx, 6), med <= median_tol and mx <= max_tol,
                      round(ratio, 4))


def suspect_tickers(findings: Sequence[SplitFinding], *, event_volume_multiple: float = 2.0) -> List[str]:
    """Names with a split-like move on ordinary volume: a data artefact until proven otherwise.

    A real 40% move (an acquisition, a drug trial) prints on several times the usual volume;
    an unadjusted split or a bad print does not. The simulator drops these names from the
    eligible universe and says which, rather than trading a phantom doubling."""
    out = set()
    for f in findings:
        if f.verdict == "candidate" and (f.volume_multiple is None or f.volume_multiple < event_volume_multiple):
            out.add(f.ticker)
    return sorted(out)


@dataclass
class AuditReport:
    dataset_verdict: str
    findings: List[SplitFinding]
    cross_checks: List[CrossCheck]
    membership_first: Optional[int]
    membership_last: Optional[int]
    n_tickers: int
    sessions: int
    benchmark_splits_in_window: Dict[str, int]
    thin_tickers: List[str]
    suspect_tickers: List[str]
    gate: str

    def to_json(self) -> Dict[str, Any]:
        return {"dataset_verdict": self.dataset_verdict, "gate": self.gate,
                "findings": [f.to_json() for f in self.findings], "cross_checks": [c.to_json() for c in self.cross_checks],
                "membership": {"members_on_first_session": self.membership_first,
                               "members_on_last_session": self.membership_last, "n_tickers": self.n_tickers},
                "sessions": self.sessions, "benchmark_splits_in_window": dict(self.benchmark_splits_in_window),
                "thin_tickers": list(self.thin_tickers), "suspect_tickers": list(self.suspect_tickers)}


def audit(panel: HistoryPanel, *, membership: Optional[Membership], cross: Sequence[Tuple[str, str, pd.Series, str]] = (),
          benchmark_factors: Optional[Mapping[str, pd.DataFrame]] = None, min_sessions: int = 500) -> AuditReport:
    """``cross``: (ticker, other_source_name, other_close_series, panel_source_name) tuples to compare."""
    findings = audit_splits(panel.close, panel.volume)
    verdict = dataset_adjustment_verdict(findings)
    checks: List[CrossCheck] = []
    for t, other_name, other, mine_name in cross:
        if t in panel.close.columns:
            checks.append(cross_check(panel.close[t], other, ticker=t, a_name=mine_name, b_name=other_name))
        elif t in panel.benchmarks.columns:
            checks.append(cross_check(panel.benchmarks[t], other, ticker=t, a_name=mine_name, b_name=other_name))
    mf = ml = None
    if membership is not None and not panel.close.empty:
        cols = set(panel.close.columns)
        mf = len(membership.on(panel.close.index[0]) & cols)
        ml = len(membership.on(panel.close.index[-1]) & cols)
    bsplits: Dict[str, int] = {}
    if benchmark_factors and not panel.close.empty:
        lo, hi = panel.close.index[0], panel.close.index[-1]
        for b, fac in benchmark_factors.items():
            # a factor row dated D applies to sessions on or before D; a split on the session after D
            # shows as that row's split_factor differing from the next row's
            sf = fac["split_factor"]
            steps = (sf != sf.shift(-1)).fillna(False) & (sf.shift(-1).notna())
            bsplits[b] = int(steps[(fac.index >= lo) & (fac.index <= hi)].sum())
    thin = [t for t in panel.close.columns if int(panel.close[t].notna().sum()) < min_sessions]
    gate = "PASS"
    if verdict in ("mixed", "unknown"):
        gate = "FAIL"
    if any(not c.passed for c in checks):
        gate = "FAIL"
    if any(v > 0 for v in bsplits.values()):
        gate = "FAIL"
    return AuditReport(verdict, findings, checks, mf, ml, len(panel.close.columns), panel.sessions, bsplits, thin,
                       suspect_tickers(findings), gate)


# ---------------------------------------------------------------------------
# on-disk panel and fixture
# ---------------------------------------------------------------------------

def _frame_text(df: pd.DataFrame, *, decimals: int = 4) -> str:
    out = df.copy()
    out.index = [i.date().isoformat() for i in pd.DatetimeIndex(out.index)]
    out.index.name = "date"
    return out.round(decimals).to_csv(lineterminator="\n")


def _read_frame(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), index_col="date")
    df.index = pd.DatetimeIndex(pd.to_datetime(df.index)).normalize()
    df.columns = [str(c).upper() for c in df.columns]
    return df.astype(float)


def write_panel(panel: HistoryPanel, directory: Path) -> Dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    written: Dict[str, str] = {}
    for name, df in (("close", panel.close), ("volume", panel.volume), ("benchmarks", panel.benchmarks),
                     ("open", panel.open), ("high", panel.high), ("low", panel.low)):
        if df is None:
            continue
        text = _frame_text(df, decimals=4 if name != "volume" else 0)
        (directory / f"{name}.csv").write_text(text, encoding="utf-8")
        written[name] = sha256_of(text.encode("utf-8"))
    return written


def read_panel(directory: Path, *, source: str = "") -> Optional[HistoryPanel]:
    if not (directory / "close.csv").exists():
        return None
    rd = lambda n: _read_frame((directory / f"{n}.csv").read_text(encoding="utf-8")) if (directory / f"{n}.csv").exists() else None  # noqa: E731
    close, volume, bench = rd("close"), rd("volume"), rd("benchmarks")
    if close is None or volume is None:
        return None
    if bench is None:
        bench = pd.DataFrame(index=close.index, columns=list(BENCHMARKS), dtype=float)
    return HistoryPanel(close, volume, bench, rd("open"), rd("high"), rd("low"), source or "cache")


def load_cached(root: Optional[Path] = None) -> Optional[Tuple[HistoryPanel, Optional[Membership], Dict[str, Any]]]:
    """The parsed panel, membership and manifest from the cache, or None when nothing has been fetched."""
    base = root or HISTORY_CACHE
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    panel = read_panel(base / "panel", source=str(manifest.get("panel", {}).get("source") or "cache"))
    if panel is None:
        return None
    mem = None
    mpath = base / "raw" / "sp500_ticker_start_end.csv"
    if mpath.exists():
        try:
            mem = load_membership(mpath.read_text(encoding="utf-8"))
        except ValueError:
            mem = None
    return panel, mem, manifest


def make_fixture(panel: HistoryPanel, membership: Optional[Membership], *, tickers: Sequence[str] = FIXTURE_TICKERS,
                 start: str = FIXTURE_START, end: str = FIXTURE_END, parent: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """A small, deterministic slice: file name -> text. Sorted columns, rounded values, no clock."""
    have = [t for t in sorted(tickers) if t in panel.close.columns]
    missing = sorted(set(tickers) - set(have))
    win = panel.window(start, end)
    files: Dict[str, str] = {
        "close.csv": _frame_text(win.close[have]),
        "volume.csv": _frame_text(win.volume[have], decimals=0),
        "benchmarks.csv": _frame_text(win.benchmarks),
    }
    if membership is not None:
        files["membership.csv"] = membership.restricted(have).to_csv()
    meta = {"tickers": have, "missing": missing, "start": win.first, "end": win.last, "sessions": win.sessions,
            "cut_from": dict(parent or {}), "note": "a 12-name slice for tests; it measures nothing about any market"}
    files["manifest.json"] = json.dumps(meta, indent=1, sort_keys=True) + "\n"
    return files


def load_fixture(directory: Optional[Path] = None) -> Tuple[HistoryPanel, Optional[Membership], Dict[str, Any]]:
    d = directory or FIXTURE_DIR
    panel = read_panel(d, source="fixture")
    if panel is None:
        raise FileNotFoundError(f"no fixture panel under {d}")
    mem = None
    if (d / "membership.csv").exists():
        mem = load_membership((d / "membership.csv").read_text(encoding="utf-8"), source="fixture")
    meta = json.loads((d / "manifest.json").read_text(encoding="utf-8")) if (d / "manifest.json").exists() else {}
    return panel, mem, meta
