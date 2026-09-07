"""Read the tracker's grades back: did the score at call have anything to do with what happened?

WHY THIS EXISTS
---------------
``an.tracker`` grades every journal call against prices and records the score's
percentile for the name in the snapshot that preceded the call. Nothing read
those grades. Once enough of them exist, the pair (score at call, excess return
since) is the first score-versus-outcome evidence this project can produce
without a backtest. This module is that read path, written while the sample is
still zero so the rules are set before anyone has a result to prefer.

THE RULE THAT MATTERS: REFUSE, DO NOT HEDGE
-------------------------------------------
A rank correlation over eight calls made on one afternoon is not weak evidence,
it is no evidence with a number attached, and a number attached to nothing gets
quoted. So below the floors in :data:`REQUIREMENTS` the readout reports the
sample, names every shortfall, and reports **no statistic at all**: ``rank_ic``
is None, not a small number with a caveat. The floors:

* at least 20 graded calls that carry both a score at call and an excess return;
* those calls spread over at least 4 distinct call dates, because sixteen calls
  dated the same day are one draw from one market regime;
* the longest window at least 63 trading days, because below a quarter the
  benchmark comparison is mostly noise (the tracker says the same);
* prices that were really pulled. A synthetic panel grades to a labelled
  demonstration and can never become evidence.

Above the floors the readout gives the Spearman rank correlation between the
score percentile at call and the excess return against SPY, its n, the mean
excess return of the calls the score liked against the ones it did not, and a
verdict capped at ``suggestive``. Nothing here reaches ``supported``: that word
belongs to the backtest engine, which has a false-positive rate it measured.
One more cap: the calls are the analyst's, not the score's. A high-scoring name
that was Passed and then rallied counts against the analyst and for the score,
and the readout says which question it is answering.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .stats import mean, spearman

__all__ = ["REQUIREMENTS", "OutcomeReadout", "readout", "readout_from_json"]

REQUIREMENTS: Dict[str, int] = {
    "min_graded_with_score": 20,
    "min_distinct_call_dates": 4,
    "min_trading_days": 63,
}


@dataclass
class OutcomeReadout:
    status: str  # "INSUFFICIENT" | "READ"
    is_real: bool
    n_calls: int
    n_graded: int
    n_usable: int
    distinct_call_dates: int
    longest_window_days: Optional[int]
    shortfalls: List[str] = field(default_factory=list)
    rank_ic: Optional[float] = None
    rank_ic_n: Optional[int] = None
    liked_mean_excess: Optional[float] = None
    disliked_mean_excess: Optional[float] = None
    liked_n: int = 0
    disliked_n: int = 0
    verdict: str = "no readout"
    note: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "question": "Did the score's percentile on the day of each call line up with the excess return "
                        "against SPY since? This asks about the score, using the analyst's calls as the sample.",
            "requirements": dict(REQUIREMENTS),
            "sample": {
                "is_real": self.is_real, "n_calls": self.n_calls, "n_graded": self.n_graded,
                "n_with_score_and_return": self.n_usable, "distinct_call_dates": self.distinct_call_dates,
                "longest_window_trading_days": self.longest_window_days,
            },
            "shortfalls": list(self.shortfalls),
            "rank_ic": None if self.rank_ic is None else round(self.rank_ic, 4),
            "rank_ic_n": self.rank_ic_n,
            "liked_by_the_score": {"n": self.liked_n, "mean_excess_vs_spy": _r(self.liked_mean_excess)},
            "not_liked_by_the_score": {"n": self.disliked_n, "mean_excess_vs_spy": _r(self.disliked_mean_excess)},
            "verdict": self.verdict,
            "note": self.note,
        }


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 4)


def _get(g: Any, name: str) -> Any:
    return g.get(name) if isinstance(g, dict) else getattr(g, name, None)


def readout(grades: Iterable[Any], *, is_real: bool) -> OutcomeReadout:
    """The readout over tracker grades (dicts from tracker.json or ``Grade`` objects)."""
    gs = list(grades)
    graded = [g for g in gs if _get(g, "status") != "ungraded"]
    usable = [g for g in graded
              if _get(g, "score_percentile_at_call") is not None and _get(g, "excess_vs_spy") is not None]
    dates = {_get(g, "date") for g in usable}
    days = [d for d in (_get(g, "trading_days") for g in graded) if d is not None]
    longest = max(days) if days else None

    out = OutcomeReadout(status="INSUFFICIENT", is_real=is_real, n_calls=len(gs), n_graded=len(graded),
                         n_usable=len(usable), distinct_call_dates=len(dates), longest_window_days=longest)
    req = REQUIREMENTS
    if not is_real:
        out.shortfalls.append("the prices behind these grades were not really pulled (a synthetic or offline "
                              "panel), so nothing here can be evidence")
    if len(usable) < req["min_graded_with_score"]:
        out.shortfalls.append(f"{len(usable)} graded calls carry both a score at call and an excess return; "
                              f"{req['min_graded_with_score']} are required")
    if len(dates) < req["min_distinct_call_dates"]:
        out.shortfalls.append(f"the usable calls fall on {len(dates)} distinct date{'s' if len(dates) != 1 else ''}; "
                              f"{req['min_distinct_call_dates']} are required, because calls made on one day are "
                              "one draw from one market regime")
    if longest is None or longest < req["min_trading_days"]:
        out.shortfalls.append(f"the longest window is {longest if longest is not None else 0} trading days; "
                              f"{req['min_trading_days']} are required")
    if out.shortfalls:
        out.verdict = "no readout: the sample is too small to say anything, so nothing is said"
        out.note = ("No statistic is reported below the floors on purpose. A rank correlation over a handful "
                    "of same-day calls is not weak evidence; it is a number attached to nothing, and numbers "
                    "attached to nothing get quoted.")
        return out

    xs = [float(_get(g, "score_percentile_at_call")) for g in usable]
    ys = [float(_get(g, "excess_vs_spy")) for g in usable]
    rho, n = spearman(xs, ys)
    liked = [y for x, y in zip(xs, ys) if x >= 50.0]
    disliked = [y for x, y in zip(xs, ys) if x < 50.0]
    out.status = "READ"
    out.rank_ic, out.rank_ic_n = rho, n
    out.liked_n, out.disliked_n = len(liked), len(disliked)
    out.liked_mean_excess, out.disliked_mean_excess = mean(liked), mean(disliked)
    if rho is None:
        out.verdict = "no readout: the correlation is undefined on this sample"
    elif abs(rho) < 0.10:
        out.verdict = "no relationship visible so far"
    elif rho > 0:
        out.verdict = "suggestive so far: higher-scored names did better against SPY in this sample"
    else:
        out.verdict = "suggestive so far, the wrong way: higher-scored names did worse against SPY in this sample"
    out.note = (
        f"Spearman over {n} calls on {len(dates)} dates. Capped at 'suggestive': this is one analyst's calls, "
        "not a universe, the windows overlap, and nothing here has a measured false-positive rate. The "
        "backtest engine is the instrument for anything stronger."
    )
    return out


def readout_from_json(blob: Dict[str, Any]) -> OutcomeReadout:
    """The readout over a written tracker.json."""
    return readout(blob.get("grades") or [], is_real=bool(blob.get("is_real")))
