"""Mechanical swing setups: patterns that matched a written rule, never a forecast.

Joseph asked for a days-to-weeks layer across any liquid name. Nothing in this
repository has an edge at that horizon that has been measured, and the one
documented anomaly there, post-earnings drift, has faded for large names. So
this module does the only honest thing a scanner can do: apply rules that were
written down in ``swing.md`` before any of it ran, print every number that made
a row fire, attach the entry, the stop and the horizon those rules dictate, and
leave the grading to the tracker, which measures the calls Joseph actually logs
at 5, 10 and 20 sessions against SPY. Thirty graded calls before any size change
is the rule; the scanner cannot skip it.

Four setups, closes only (the price module deals in adjusted closes and refuses
to fabricate an intraday range it does not have):

* **post-earnings drift** (``pead``): an 8-K with item 2.02 dated in the last
  three sessions, the release session closed 5% or more above the prior close,
  and the latest close is still at or above the release close. Entry the latest
  close, stop the lowest close from the release session on, horizon 20 sessions.
* **guidance raise** (``guidance``): the mechanical 8-K diff says ``raised`` on
  revenue or EPS for a period that has not ended, on a name with a CIK. Entry
  the latest close, stop the lowest close of the last five sessions, horizon 20.
* **breakout with revisions** (``breakout``): the latest close is above every
  close of the prior 251 sessions and next-year EPS estimates rose over 30 days. Quality
  150 only, because that is where the estimate field exists. Stop 8% under
  entry, horizon 10.
* **pullback in trend** (``pullback``): quality 150, the 50-session mean above
  the 200-session mean, close above the 200, close within 3% of the 20-session
  mean, and no worse than 15% under the 252-session high. Stop the lowest close
  of the last 20 sessions, horizon 10.

Every row carries ``what_this_is`` and the language guard runs over the file.
Nothing here has run against live data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd

from . import paths

__all__ = [
    "Setup", "KINDS", "LABELS", "RULES", "pead_setups", "guidance_setups", "breakout_setups", "pullback_setups",
    "all_setups", "build_report", "not_run_report", "SWING_RULES_FILE",
]

SWING_RULES_FILE = "swing.md"
WHAT_THIS_IS = "a mechanical pattern that matched a rule in swing.md, not a forecast and not a recommendation"

PEAD_GAP = 0.05
PEAD_SESSIONS = 3
PEAD_HORIZON = 20
GUIDANCE_HORIZON = 20
BREAKOUT_STOP = 0.08
BREAKOUT_HORIZON = 10
PULLBACK_BAND = 0.03
PULLBACK_MAX_DD = -0.15
PULLBACK_HORIZON = 10
LOOKBACK = 252

KINDS = ("pead", "guidance", "breakout", "pullback")
LABELS = {"pead": "post-earnings drift", "guidance": "guidance raise", "breakout": "breakout with revisions",
          "pullback": "pullback in trend"}
RULES = {
    "pead": (f"An 8-K with item 2.02 in the last {PEAD_SESSIONS} sessions, the release session closed {PEAD_GAP:.0%} "
             f"or more above the prior close, and the latest close holds the release close. Stop: the lowest close "
             f"since the release. Horizon: {PEAD_HORIZON} sessions."),
    "guidance": (f"The mechanical 8-K diff reads 'raised' on revenue or EPS for a period still ahead. Stop: the lowest "
                 f"close of the last five sessions. Horizon: {GUIDANCE_HORIZON} sessions."),
    "breakout": (f"The latest close is above every close of the prior {LOOKBACK - 1} sessions and next-year EPS "
                 f"estimates rose over 30 days. "
                 f"Stop: {BREAKOUT_STOP:.0%} under entry. Horizon: {BREAKOUT_HORIZON} sessions."),
    "pullback": (f"50-session mean above the 200, close above the 200, close within {PULLBACK_BAND:.0%} of the 20-session "
                 f"mean, no worse than {-PULLBACK_MAX_DD:.0%} under the {LOOKBACK}-session high. Stop: the lowest close of "
                 f"the last 20 sessions. Horizon: {PULLBACK_HORIZON} sessions."),
}


@dataclass(frozen=True)
class Setup:
    ticker: str
    kind: str
    entry: float
    stop: float
    horizon_days: int
    as_of: str
    numbers: Dict[str, str] = field(default_factory=dict)
    name: Optional[str] = None
    source: str = ""

    @property
    def risk_pct(self) -> Optional[float]:
        return None if self.entry <= 0 else max(0.0, 1.0 - self.stop / self.entry)

    def to_json(self) -> Dict[str, Any]:
        return {"ticker": self.ticker, "name": self.name, "kind": self.kind, "label": LABELS[self.kind],
                "rule": RULES[self.kind], "entry": round(self.entry, 4), "stop": round(self.stop, 4),
                "risk_pct": None if self.risk_pct is None else round(self.risk_pct, 4),
                "horizon_days": self.horizon_days, "as_of": self.as_of, "numbers": dict(self.numbers),
                "source": self.source, "what_this_is": WHAT_THIS_IS}


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _col(closes: pd.DataFrame, ticker: str, as_of: dt.date) -> Optional[pd.Series]:
    if closes is None or ticker not in closes.columns:
        return None
    s = closes[ticker].dropna()
    s = s[s.index <= pd.Timestamp(as_of)]
    return None if s.empty else s


def _pct(v: float) -> str:
    return f"{v * 100:+.1f}%"


def _money(v: float) -> str:
    return f"${v:,.2f}"


# ---------------------------------------------------------------------------
# the four setups
# ---------------------------------------------------------------------------

def pead_setups(closes: pd.DataFrame, earnings_dates: Mapping[str, Sequence[str]], *, as_of: dt.date,
                names: Optional[Mapping[str, str]] = None) -> List[Setup]:
    """``earnings_dates``: ticker -> filing dates of 8-Ks with item 2.02 (ISO strings)."""
    out: List[Setup] = []
    for t, dates in earnings_dates.items():
        s = _col(closes, t.upper(), as_of)
        if s is None or len(s) < 3:
            continue
        idx = s.index
        latest_pos = len(s) - 1
        for d in sorted(set(dates), reverse=True):
            try:
                filed = pd.Timestamp(d)
            except (TypeError, ValueError):
                continue
            after = idx[idx >= filed]
            if len(after) == 0:
                continue
            pos = idx.get_loc(after[0])
            if pos == 0 or latest_pos - pos >= PEAD_SESSIONS:
                continue
            prior = _f(s.iloc[pos - 1])
            release = _f(s.iloc[pos])
            latest = _f(s.iloc[latest_pos])
            if not prior or release is None or latest is None:
                continue
            gap = release / prior - 1.0
            if gap < PEAD_GAP or latest < release:
                continue
            stop = _f(s.iloc[pos:].min())
            if stop is None or stop >= latest:
                continue
            out.append(Setup(t.upper(), "pead", latest, stop, PEAD_HORIZON, as_of.isoformat(),
                             numbers={"8-K": d, "release session": after[0].date().isoformat(),
                                      "gap": _pct(gap), "since release": _pct(latest / release - 1.0)},
                             name=(names or {}).get(t.upper()), source="EDGAR 8-K item 2.02 + yfinance closes"))
            break
    return out


def guidance_setups(closes: pd.DataFrame, diffs: Mapping[str, Dict[str, Any]], *, as_of: dt.date,
                    names: Optional[Mapping[str, str]] = None, max_age_sessions: int = 5) -> List[Setup]:
    """``diffs``: ticker -> the block ``scripts/guidance_diff.py`` writes."""
    out: List[Setup] = []
    for t, block in diffs.items():
        s = _col(closes, t.upper(), as_of)
        if s is None or len(s) < 6 or not isinstance(block, dict):
            continue
        filed = (block.get("current") or {}).get("filed")
        try:
            filed_ts = pd.Timestamp(filed)
        except (TypeError, ValueError):
            continue
        sessions_since = int((s.index > filed_ts).sum())
        if sessions_since > max_age_sessions:
            continue
        raised = [it for it in (block.get("items") or [])
                  if it.get("change") == "raised" and it.get("metric") in ("revenue", "eps")]
        if not raised:
            continue
        latest = _f(s.iloc[-1])
        stop = _f(s.iloc[-5:].min())
        if latest is None or stop is None or stop >= latest:
            continue
        what = "; ".join(f"{it.get('what')}: {it.get('detail')}" for it in raised[:2])
        out.append(Setup(t.upper(), "guidance", latest, stop, GUIDANCE_HORIZON, as_of.isoformat(),
                         numbers={"8-K": str(filed), "raised": what[:160]},
                         name=(names or {}).get(t.upper()), source="guidance_diff.py + yfinance closes"))
    return out


def breakout_setups(closes: pd.DataFrame, screen: Mapping[str, Any], *, as_of: dt.date,
                    names: Optional[Mapping[str, str]] = None) -> List[Setup]:
    """``screen``: ticker -> an object or dict with ``eps_fy1_chg_30d``."""
    out: List[Setup] = []
    for t, row in screen.items():
        rev = _f(getattr(row, "eps_fy1_chg_30d", None) if not isinstance(row, dict) else row.get("eps_fy1_chg_30d"))
        if rev is None or rev <= 0:
            continue
        s = _col(closes, t.upper(), as_of)
        if s is None or len(s) < 60:
            continue
        window = s.iloc[-LOOKBACK:]
        latest = _f(s.iloc[-1])
        prior_high = _f(window.iloc[:-1].max())
        # strictly above every earlier close in the window: a flat line is not at a new high
        if latest is None or prior_high is None or latest <= prior_high:
            continue
        stop = latest * (1.0 - BREAKOUT_STOP)
        out.append(Setup(t.upper(), "breakout", latest, stop, BREAKOUT_HORIZON, as_of.isoformat(),
                         numbers={f"prior {len(window) - 1}-session high": _money(prior_high),
                                  "above it": _pct(latest / prior_high - 1.0), "EPS FY1 30d": _pct(rev)},
                         name=(names or {}).get(t.upper()), source="price_screen_latest.csv + yfinance closes"))
    return out


def pullback_setups(closes: pd.DataFrame, tickers: Iterable[str], *, as_of: dt.date,
                    names: Optional[Mapping[str, str]] = None) -> List[Setup]:
    out: List[Setup] = []
    for t in tickers:
        s = _col(closes, t.upper(), as_of)
        if s is None or len(s) < 200:
            continue
        latest = _f(s.iloc[-1])
        ma20, ma50, ma200 = _f(s.iloc[-20:].mean()), _f(s.iloc[-50:].mean()), _f(s.iloc[-200:].mean())
        high = _f(s.iloc[-LOOKBACK:].max())
        if None in (latest, ma20, ma50, ma200, high) or not ma200:
            continue
        if not (ma50 > ma200 and latest > ma200 and abs(latest / ma20 - 1.0) <= PULLBACK_BAND
                and latest / high - 1.0 >= PULLBACK_MAX_DD):
            continue
        stop = _f(s.iloc[-20:].min())
        if stop is None or stop >= latest:
            continue
        out.append(Setup(t.upper(), "pullback", latest, stop, PULLBACK_HORIZON, as_of.isoformat(),
                         numbers={"vs 20-session mean": _pct(latest / ma20 - 1.0), "50 over 200": _pct(ma50 / ma200 - 1.0),
                                  "under the high": _pct(latest / high - 1.0)},
                         name=(names or {}).get(t.upper()), source="yfinance closes"))
    return out


def all_setups(closes: pd.DataFrame, *, as_of: dt.date, earnings_dates: Mapping[str, Sequence[str]],
               diffs: Mapping[str, Dict[str, Any]], screen: Mapping[str, Any], quality: Iterable[str],
               names: Optional[Mapping[str, str]] = None) -> List[Setup]:
    out = (pead_setups(closes, earnings_dates, as_of=as_of, names=names)
           + guidance_setups(closes, diffs, as_of=as_of, names=names)
           + breakout_setups(closes, screen, as_of=as_of, names=names)
           + pullback_setups(closes, quality, as_of=as_of, names=names))
    # one row per name: the first kind in KINDS order wins, and the others are noted
    by: Dict[str, Setup] = {}
    also: Dict[str, List[str]] = {}
    for kind in KINDS:
        for x in out:
            if x.kind != kind:
                continue
            if x.ticker in by:
                also.setdefault(x.ticker, []).append(LABELS[kind])
            else:
                by[x.ticker] = x
    rows = []
    for t, x in by.items():
        if t in also:
            x = Setup(x.ticker, x.kind, x.entry, x.stop, x.horizon_days, x.as_of,
                      numbers={**x.numbers, "also": ", ".join(also[t])}, name=x.name, source=x.source)
        rows.append(x)
    rows.sort(key=lambda x: (KINDS.index(x.kind), x.ticker))
    return rows


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------

DISCLAIMER = "Research and analysis from public data, not personalised financial advice."


def _limitations(status: str) -> List[str]:
    lims = [
        "A setup is a pattern that matched, on adjusted closes. Whether any of these patterns carries an edge for "
        "this account is unmeasured until the tracker has graded thirty logged calls; swing.md says so.",
        "Closes only. The stop is a closing level because no intraday range is pulled; a print through it during "
        "the session is not a stop.",
        "Post-earnings drift is documented mostly in small names and has faded in large ones. Every name here is "
        "over $2bn by construction.",
        "The estimate-revision field exists for the quality 150 only, so the breakout and pullback setups never "
        "reach the rest of the universe.",
        "The guidance diff is mechanical and reads figures, not tone. A raise given in words alone is invisible to it.",
    ]
    if status == "SYNTHETIC":
        lims.insert(0, "SYNTHETIC. Prices are a seeded random walk with planted patterns and the filings are fictional. "
                       "Nothing here is a fact about any company.")
    return lims


def build_report(setups: Sequence[Setup], *, as_of: dt.date, universe_n: int, quality_n: int,
                 sources: Dict[str, Dict[str, Any]], status: str = "SCANNED",
                 built_at: Optional[str] = None) -> Dict[str, Any]:
    counts = {k: sum(1 for x in setups if x.kind == k) for k in KINDS}
    return {
        "built_at": built_at or dt.datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "is_real": status == "SCANNED",
        "as_of": as_of.isoformat(),
        "universe_n": universe_n,
        "quality_n": quality_n,
        "n_setups": len(setups),
        "counts": counts,
        "setups": [x.to_json() for x in setups],
        "kinds": {k: {"label": LABELS[k], "rule": RULES[k]} for k in KINDS},
        "rules_source": SWING_RULES_FILE,
        "sources": sources,
        "limitations": _limitations(status),
        "disclaimer": DISCLAIMER,
    }


def not_run_report(*, universe_n: int, quality_n: int, built_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "built_at": built_at or dt.datetime.now().isoformat(timespec="seconds"),
        "status": "NOT RUN",
        "is_real": False,
        "as_of": None,
        "universe_n": universe_n,
        "quality_n": quality_n,
        "n_setups": 0,
        "counts": {k: 0 for k in KINDS},
        "setups": [],
        "kinds": {k: {"label": LABELS[k], "rule": RULES[k]} for k in KINDS},
        "rules_source": SWING_RULES_FILE,
        "sources": {"prices": {"live": False, "detail": "no closes pulled"},
                    "edgar": {"live": False, "detail": "no filings pulled"},
                    "guidance": {"live": False, "detail": "no diffs on disk"}},
        "why_not_run": [
            "No setups scan has run. It needs a year of closes for the liquid universe and the last three "
            "sessions of 8-K filings, neither of which this checkout has pulled.",
            "To run it: python scripts/setups.py --live, on a machine that can reach Yahoo and data.sec.gov. "
            "The morning task runs it every weekday.",
            "The rules it applies are in swing.md. Read that file first; it says what a setup is not.",
        ],
        "limitations": _limitations("NOT RUN"),
        "disclaimer": DISCLAIMER,
    }
