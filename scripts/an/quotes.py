"""Daily quotes that arrive through git, because the desk's container cannot reach a price host.

``scripts/fetch_quotes.py`` runs where Yahoo is reachable (a GitHub Actions runner on
weekdays, or Joseph's machine) and writes a small wide panel of adjusted closes plus a
manifest. The workflow commits that to the ``quotes`` branch. Here, :func:`fetch_branch`
pulls the branch and copies the files under ``data/cache/quotes/`` (gitignored), and
:func:`load` reads them. With ``DESK_QUOTES`` pointing at that directory,
``prices.PriceClient()`` serves every ``--live`` script from the file (see
``prices.default_downloader``).

Provenance travels: the manifest carries when the pull happened, how many names came
back, which were missing, and the first and last session. ``age_sessions`` says how
stale a mark is, and the email prints it.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from . import paths

__all__ = ["QUOTES_CACHE", "QUOTES_BRANCH", "QUOTES_DIR_IN_BRANCH", "QuotePanel", "load", "write", "fetch_branch",
           "ticker_universe", "age_sessions", "BENCHMARKS", "trim_partial_tail", "intraday_tail"]

QUOTES_CACHE = paths.CACHE_DIR / "quotes"
QUOTES_BRANCH = "quotes"
QUOTES_DIR_IN_BRANCH = "data/quotes"
BENCHMARKS = ("SPY", "QQQ", "IWM", "VFV.TO")
FILES = ("closes.csv", "manifest.json")


@dataclass
class QuotePanel:
    closes: pd.DataFrame          # DatetimeIndex x ticker, adjusted closes
    manifest: Dict[str, Any]
    root: Path

    @property
    def last(self) -> Optional[dt.date]:
        return None if self.closes.empty else self.closes.index[-1].date()

    @property
    def first(self) -> Optional[dt.date]:
        return None if self.closes.empty else self.closes.index[0].date()

    @property
    def tickers(self) -> List[str]:
        return list(self.closes.columns)

    def describe(self) -> str:
        m = self.manifest
        return (f"{len(self.tickers)} names, {len(self.closes)} sessions, {self.first} to {self.last}, "
                f"pulled {m.get('pulled_at', 'unknown')} via {m.get('source', 'unknown')}"
                + (f", missing {', '.join(m['missing'][:8])}" if m.get("missing") else "")
                + (f"; {', '.join(m['partial_sessions_set_aside'])} set aside as a partial session"
                   if m.get("partial_sessions_set_aside") else "")
                + (f"; {m['intraday_session_set_aside']} set aside, pulled during the session"
                   if m.get("intraday_session_set_aside") else ""))


PARTIAL_SESSION_FLOOR = 0.5


def trim_partial_tail(closes: pd.DataFrame, *, floor: float = PARTIAL_SESSION_FLOOR) -> Tuple[pd.DataFrame, List[str]]:
    """Drop trailing sessions where fewer than ``floor`` of the names have a close.

    A pull made before a session's data has fully landed (or a batch that came back
    half empty) leaves a last row with a few dozen names. Marking a book at that row would
    call it "today's close" for the names that have one and silently keep yesterday's for
    the rest. The row is set aside instead and the manifest says so; the next pull carries it."""
    dropped: List[str] = []
    out = closes
    while len(out) and out.shape[1] and out.iloc[-1].notna().sum() < floor * out.shape[1]:
        dropped.append(out.index[-1].date().isoformat())
        out = out.iloc[:-1]
    return out, dropped


SESSION_CLOSE_UTC = dt.time(20, 30)   # 16:00 New York plus a settle; 15:30 during standard time, still after the bell


def intraday_tail(closes: pd.DataFrame, pulled_at: Optional[str]) -> Tuple[pd.DataFrame, Optional[str]]:
    """Drop the last row when it is dated the day of the pull and the pull came before the close.

    Yahoo returns a row for today during the session, carrying the last trade so far. A
    runner that fires at 10:30 Toronto would otherwise write that print as "today's close"
    and the book would be marked on it. The row is set aside; the evening pull carries the
    real close."""
    if closes.empty or not pulled_at:
        return closes, None
    try:
        when = dt.datetime.fromisoformat(pulled_at.replace("Z", "+00:00"))
    except ValueError:
        return closes, None
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone.utc).replace(tzinfo=None)
    last = closes.index[-1].date()
    if last == when.date() and when.time() < SESSION_CLOSE_UTC:
        return closes.iloc[:-1], last.isoformat()
    return closes, None


def load(root: Optional[Path] = None) -> Optional[QuotePanel]:
    d = Path(root) if root else QUOTES_CACHE
    p = d / "closes.csv"
    if not p.exists():
        return None
    try:
        closes = pd.read_csv(p, index_col=0, parse_dates=True)
    except (OSError, ValueError):
        return None
    closes.index = pd.DatetimeIndex(closes.index).tz_localize(None).normalize()
    closes.columns = [str(c).upper() for c in closes.columns]
    closes = closes.sort_index().astype(float)
    manifest: Dict[str, Any] = {}
    mp = d / "manifest.json"
    if mp.exists():
        try:
            manifest = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    closes, intraday = intraday_tail(closes, manifest.get("pulled_at"))
    if intraday:
        manifest = {**manifest, "intraday_session_set_aside": intraday}
    closes, dropped = trim_partial_tail(closes)
    if dropped:
        manifest = {**manifest, "partial_sessions_set_aside": dropped}
    return QuotePanel(closes, manifest, d)


def write(closes: pd.DataFrame, root: Path, *, source: str, missing: Sequence[str] = (), pulled_at: Optional[str] = None,
          requested: int = 0) -> Dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    out = closes.copy()
    out.index = [i.date().isoformat() for i in pd.DatetimeIndex(out.index)]
    out.index.name = "date"
    out = out.reindex(sorted(out.columns), axis=1).round(4)
    out.to_csv(root / "closes.csv", lineterminator="\n")
    manifest = {
        "pulled_at": pulled_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": source, "requested": requested, "n_tickers": int(len(out.columns)), "sessions": int(len(out)),
        "first": None if out.empty else str(out.index[0]), "last": None if out.empty else str(out.index[-1]),
        "missing": sorted(missing), "basis": "adjusted daily closes as yfinance returns them (auto_adjust=True)",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    latest = out.ffill().iloc[-1] if not out.empty else pd.Series(dtype=float)
    lf = latest.rename("close").to_frame().assign(date=manifest["last"])
    lf.index.name = "ticker"
    lf.to_csv(root / "latest.csv", lineterminator="\n")
    return manifest


def fetch_branch(repo_root: Optional[Path] = None, *, branch: str = QUOTES_BRANCH, dest: Optional[Path] = None,
                 timeout: float = 120.0) -> Optional[QuotePanel]:
    """``git fetch origin quotes`` and copy the panel files out of it, without a checkout."""
    root = Path(repo_root) if repo_root else paths.ROOT
    d = Path(dest) if dest else QUOTES_CACHE
    r = subprocess.run(["git", "fetch", "--quiet", "origin", branch], cwd=str(root), capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        return None
    d.mkdir(parents=True, exist_ok=True)
    for name in FILES + ("latest.csv",):
        show = subprocess.run(["git", "show", f"origin/{branch}:{QUOTES_DIR_IN_BRANCH}/{name}"], cwd=str(root),
                              capture_output=True, text=True, timeout=timeout)
        if show.returncode != 0:
            if name in FILES:
                return None
            continue
        (d / name).write_text(show.stdout, encoding="utf-8")
    return load(d)


def ticker_universe(*, extra: Sequence[str] = ()) -> List[str]:
    """Everything the desk marks: the quality 150, the memo's lists, both journals, the benchmarks."""
    names = set(t.upper() for t in extra)
    try:
        from . import local
        names.update(local.load_quality().keys())
    except Exception:  # noqa: BLE001 - a missing CSV must not stop a pull
        pass
    memo = paths.DASHBOARD_DIR / "positioning.json"
    if memo.exists():
        try:
            blob = json.loads(memo.read_text(encoding="utf-8"))
            for key in ("candidates", "reviewed_and_declined", "research_queue"):
                names.update(c["ticker"].upper() for c in blob.get(key) or [] if c.get("ticker"))
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    from . import journal
    for p in (paths.JOURNAL_MD, paths.ROOT / "book.md"):
        for e in journal.load(p):
            if not e.is_system:
                names.add(e.ticker.upper())
    names.update(BENCHMARKS)
    return sorted(names)


def age_sessions(panel: QuotePanel, today: Optional[dt.date] = None) -> Optional[int]:
    """Business days between the last session in the panel and today, 0 when today is covered."""
    if panel.last is None:
        return None
    today = today or dt.date.today()
    if panel.last >= today:
        return 0
    return int(len(pd.bdate_range(panel.last, today)) - 1)
