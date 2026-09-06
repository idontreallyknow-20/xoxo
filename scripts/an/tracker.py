"""Grade every call in ``journal.md`` against what the price did next.

WHAT A GRADE IS HERE
--------------------
A journal entry is a dated call at a recorded price with a sentence that says what
would prove it wrong. That is enough to grade it mechanically, and nothing more is
inferred:

* the return from the price at call to the price on the grading date, against
  SPY and QQQ over the same window, because the journal's own inception entry
  names those two as the bar;
* whether the falsifier's price level, where it names one ("a close under
  $130"), has been closed under since the call;
* the score's percentile for the name in the last snapshot taken on or before
  the call, so a later reader can ask whether the score agreed with the analyst.

Each grade carries a ``status`` that says which of those could be computed and
why the rest could not. A call with no price history is ``ungraded``, and stays
in the table with its reason, because a tracker that silently drops the calls
it cannot grade is the survivorship problem again in miniature.

WHAT A GRADE IS NOT
-------------------
*Final.* Every verdict is "so far". The calls were sized for tranches over
three months and underwritten on a twelve-month view; a grade taken after a week
is a fact about a week.

*A P&L.* Nothing here knows what was bought or when. A "Buy in October" call
graded from its September call price measures the call, not a position.

*Symmetric.* A Pass that then beat the market is a missed gain, not a loss, and
the table says "missed" rather than colouring it red.

*Adjusted for anything.* The price at call is the journal's own figure, which
may not be a close. Returns are on adjusted closes from the price client, so a
dividend between the call and the grade is in the return and a split is not a
crash.

The panel comes from ``an.prices`` and its rules apply: a name that stopped
trading grades to its last print and says so, never to a flat zero.

Nothing here has run against live prices. The machine it was written on could
not reach Yahoo. The tests drive it on a synthetic panel with planted paths, and
``scripts/track_calls.py`` writes an honest ``NOT GRADED`` file until it has.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from . import journal, paths, prices, snapshots

__all__ = [
    "Grade",
    "TrackerSummary",
    "parse_trigger",
    "classify_action",
    "score_at_call",
    "grade_entries",
    "summarise",
    "limitations",
    "BENCHMARKS",
]

BENCHMARKS: Tuple[str, ...] = ("SPY", "QQQ")

_TRIGGER = re.compile(r"clos(?:e|es|ing)\s+(?:under|below|beneath)\s+\$?\s*([\d,]+(?:\.\d+)?)", re.I)


def parse_trigger(wrong_if: Optional[str]) -> Optional[float]:
    """The price level in "a close under $130", or None when the falsifier names none.

    Only the close-under form is read. "Down 15%" and "under $3.6 billion" are
    real falsifiers that this cannot check against a price series, and they are
    left to the reader rather than approximated.
    """
    if not wrong_if:
        return None
    m = _TRIGGER.search(wrong_if)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v if v > 0 else None


def classify_action(action: Optional[str]) -> str:
    """``buy``, ``buy_later``, ``buy_on_pullback``, ``watch``, ``pass``, or ``other``.

    The grade's reading of an excess return depends on this: a buy that lagged is
    "behind", a pass that rallied is "missed". A watch is graded as a pass, since
    nothing was bought.
    """
    a = (action or "").lower()
    if "pullback" in a:
        return "buy_on_pullback"
    if a.startswith("buy"):
        return "buy_later" if re.search(r"\b(in|after|from)\b", a) else "buy"
    if "pass" in a:
        return "pass"
    if "watch" in a:
        return "watch"
    return "other"


@dataclass
class Grade:
    date: str
    ticker: str
    action: Optional[str]
    kind: str
    price_at_call: Optional[float]
    conviction: Optional[int]
    bucket: Optional[str]
    wrong_if: Optional[str]
    trigger: Optional[float]
    score_percentile_at_call: Optional[float]
    score_snapshot: Optional[str]
    graded_on: Optional[str] = None
    trading_days: Optional[int] = None
    price_now: Optional[float] = None
    ret: Optional[float] = None
    benchmarks: Dict[str, Optional[float]] = field(default_factory=dict)
    excess_vs_spy: Optional[float] = None
    low_since_call: Optional[float] = None
    trigger_breached: Optional[bool] = None
    stopped_trading: bool = False
    status: str = "ungraded"
    reason: str = ""
    verdict: str = ""

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _snapshot_on_or_before(date: str, root: Optional[Path]) -> Optional[snapshots.Snapshot]:
    best = None
    for s in snapshots.list_snapshots(root):
        if s.date <= date and (best is None or s.date > best.date):
            best = s
    return best


def score_at_call(ticker: str, date: str, *, variant: str = "quality_value",
                  root: Optional[Path] = None) -> Tuple[Optional[float], Optional[str]]:
    """The score's percentile for the name in the last snapshot on or before the call.

    Returns ``(percentile, snapshot_date)``; both None when no snapshot precedes
    the call or the name was not scored in it. A snapshot taken *after* the call
    is never used: that would grade the analyst against a score that had the
    benefit of a later screen.
    """
    s = _snapshot_on_or_before(date, root)
    if s is None:
        return None, None
    p = s.directory / "scores.json"
    if not p.exists():
        return None, s.date
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, s.date
    row = ((blob.get("variants") or {}).get(variant) or {}).get(ticker.upper())
    if not row or row.get("percentile") is None:
        return None, s.date
    return float(row["percentile"]), s.date


def _low_between(closes: pd.DataFrame, ticker: str, start: dt.date, end: dt.date) -> Optional[float]:
    col = closes.get(ticker.upper())
    if col is None:
        return None
    idx = pd.DatetimeIndex(closes.index)
    mask = (idx > pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
    window = col[mask].dropna()
    return None if window.empty else float(window.min())


def _verdict(g: Grade) -> str:
    """One short sentence, always "so far", never a prediction."""
    if g.status == "ungraded":
        return "not graded: " + g.reason
    parts: List[str] = []
    if g.trigger_breached:
        parts.append(f"closed under the ${g.trigger:,.0f} the call named; the thesis is falsified on its own terms")
    if g.ret is not None and g.excess_vs_spy is not None:
        moved = f"{g.ret * 100:+.1f}% since the call, {g.excess_vs_spy * 100:+.1f} pts against SPY"
        if g.kind in ("buy", "buy_later", "buy_on_pullback"):
            parts.append(("ahead so far: " if g.excess_vs_spy > 0 else "behind so far: ") + moved)
        elif g.kind in ("pass", "watch"):
            parts.append(("missed so far: " if g.excess_vs_spy > 0 else "avoided so far: ") + moved)
        else:
            parts.append(moved)
    elif g.ret is not None:
        parts.append(f"{g.ret * 100:+.1f}% since the call; no benchmark to set it against")
    if g.stopped_trading:
        parts.append("the name stopped trading inside the window, so this is the return to its last print")
    if g.trading_days is not None and g.trading_days < 21:
        parts.append(f"{g.trading_days} trading days is too short to mean anything")
    return "; ".join(parts) if parts else "graded, nothing to say"


def grade_entries(
    entries: Sequence[journal.JournalEntry],
    closes: Optional[pd.DataFrame],
    *,
    as_of: dt.date,
    benchmarks: Sequence[str] = BENCHMARKS,
    snapshot_root: Optional[Path] = None,
) -> List[Grade]:
    """One grade per non-system entry, in journal order.

    ``closes`` may be None, in which case every grade is ``ungraded`` with the
    reason, and the parts that need no prices (the trigger, the score at call) are
    still filled in.
    """
    out: List[Grade] = []
    for e in entries:
        if e.is_system:
            continue
        pct, snap = score_at_call(e.ticker, e.date, root=snapshot_root)
        g = Grade(
            date=e.date, ticker=e.ticker, action=e.action, kind=classify_action(e.action),
            price_at_call=e.price_at_call, conviction=e.conviction, bucket=e.bucket,
            wrong_if=e.wrong_if, trigger=parse_trigger(e.wrong_if),
            score_percentile_at_call=pct, score_snapshot=snap,
        )
        call_date = dt.date.fromisoformat(e.date)
        if closes is None:
            g.reason = "no price history has been pulled"
        elif e.price_at_call is None:
            g.reason = "the journal records no price at call"
        elif call_date > as_of:
            g.reason = f"the call is dated after the grading date {as_of}"
        elif e.ticker.upper() not in [str(c).upper() for c in closes.columns]:
            g.reason = "no price column for this name"
        else:
            now = prices.price_on(closes, e.ticker, as_of)
            last = prices.last_observation(closes, e.ticker)
            if now is None:
                g.reason = "no price at or before the grading date"
            else:
                g.graded_on = as_of.isoformat()
                g.price_now = now
                g.ret = now / e.price_at_call - 1.0
                idx = pd.DatetimeIndex(closes.index)
                g.trading_days = int(((idx > pd.Timestamp(call_date)) & (idx <= pd.Timestamp(as_of))).sum())
                g.stopped_trading = bool(last is not None and last < as_of and (as_of - last).days > 7)
                for b in benchmarks:
                    b0 = prices.price_on(closes, b, call_date)
                    b1 = prices.price_on(closes, b, as_of)
                    g.benchmarks[b] = None if b0 is None or b1 is None or b0 <= 0 else b1 / b0 - 1.0
                spy = g.benchmarks.get("SPY")
                g.excess_vs_spy = None if spy is None else g.ret - spy
                g.low_since_call = _low_between(closes, e.ticker, call_date, as_of)
                if g.trigger is not None and g.low_since_call is not None:
                    g.trigger_breached = g.low_since_call < g.trigger
                g.status = "falsified" if g.trigger_breached else "graded"
        g.verdict = _verdict(g)
        out.append(g)
    return out


@dataclass
class TrackerSummary:
    n_calls: int
    n_graded: int
    n_ungraded: int
    n_falsified: int
    buys_graded: int
    buys_ahead_of_spy: int
    passes_graded: int
    passes_missed: int
    mean_excess_vs_spy_buys: Optional[float]
    trading_days: Optional[int]

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def summarise(grades: Sequence[Grade]) -> TrackerSummary:
    graded = [g for g in grades if g.status != "ungraded"]
    buys = [g for g in graded if g.kind.startswith("buy") and g.excess_vs_spy is not None]
    passes = [g for g in graded if g.kind in ("pass", "watch") and g.excess_vs_spy is not None]
    days = [g.trading_days for g in graded if g.trading_days is not None]
    return TrackerSummary(
        n_calls=len(grades),
        n_graded=len(graded),
        n_ungraded=len(grades) - len(graded),
        n_falsified=sum(1 for g in graded if g.status == "falsified"),
        buys_graded=len(buys),
        buys_ahead_of_spy=sum(1 for g in buys if g.excess_vs_spy > 0),
        passes_graded=len(passes),
        passes_missed=sum(1 for g in passes if g.excess_vs_spy > 0),
        mean_excess_vs_spy_buys=(sum(g.excess_vs_spy for g in buys) / len(buys)) if buys else None,
        trading_days=max(days) if days else None,
    )


def limitations(grades: Sequence[Grade], summary: TrackerSummary, *, source: str) -> List[str]:
    """Always non-empty. The reader gets these before the table."""
    out = [
        f"Price source: {source}.",
        "Every verdict is 'so far'. The calls were underwritten on a twelve-month view and sized for "
        "tranches over three months; a grade taken early is a fact about the window, not the call.",
        "The price at call is the journal's own figure and may not be a close. Returns use adjusted "
        "closes, so a dividend between the call and the grade is in the return.",
        "This grades calls, not positions. Nothing here knows what was bought, when, or in what size.",
        "Only a falsifier of the form 'a close under $X' is checked against prices. Guidance and growth "
        "falsifiers are real and are left to the reader.",
        "A Pass that then beat the market is a missed gain, not a loss, and is labelled 'missed'.",
    ]
    dates = sorted({g.date for g in grades})
    if len(dates) == 1:
        out.append(f"Every call carries the same date, {dates[0]}, so the whole table is one window and "
                   "one market regime. It cannot say anything about consistency.")
    if summary.trading_days is not None and summary.trading_days < 63:
        out.append(f"The longest window is {summary.trading_days} trading days. Below a quarter, the "
                   "benchmark comparison is mostly noise.")
    if summary.n_ungraded:
        out.append(f"{summary.n_ungraded} of {summary.n_calls} calls are ungraded and stay in the table with "
                   "the reason; dropping them would flatter whatever remains.")
    return out
