"""Claude's own paper book: the calls in ``book.md``, filled and marked by rule, never by hand.

There is no holdings file to edit. The book is *derived*: every ``Buy`` in ``book.md`` fills at
the first close after the call at its ``Target size`` (whole shares), every ``Sell`` exits at
the first close after the call, a swing call (one with ``Horizon:`` and ``Stop:``) is sized and
exited exactly as ``swing.md`` says, and everything is marked at the latest close in the
panel. Rewrite the closes and the same calls give the same book; edit a past call and a test
fails, because ``book.md`` is append-only like ``journal.md``.

The rules applied at a fill, all from ``criteria.md`` and ``swing.md``:

* a long position is capped at 12% of the book's equity at the fill;
* a fill is refused if it would take cash under 20% of equity, or take the month's
  deployment past 25% of equity, and the refusal is written into the position's record;
* a swing fill risks $1,000 (entry less stop), never more than $5,000 of stock, at most five
  swing positions open, two per sector; it exits at the first close strictly below the stop
  (the following close) or at the horizon close; the stop rises to entry after one R.

Long positions are not exited by a falsifier automatically. When a close crosses a ``Wrong
if`` level the position is flagged ``falsifier_breached`` and the morning email says so;
the next call is a judgement, logged with its reason, as the journal's rules require.

Every return is a price return. Nothing here is advice.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from . import journal, positioning, stats, tracker
from .paper import Rules as SwingRules

__all__ = ["Position", "Book", "build_book", "classify", "BENCHMARKS", "DISCLAIMER"]

BENCHMARKS = ("SPY", "QQQ", "VFV.TO")
DISCLAIMER = "Research and analysis from public data, not personalised financial advice."


def classify(action: Optional[str]) -> str:
    """buy | sell | trim | none, from the heading's recommendation."""
    a = (action or "").strip().lower()
    if a.startswith("buy"):
        return "buy"
    if a.startswith(("sell", "exit", "close")):
        return "sell"
    if a.startswith("trim"):
        return "trim"
    return "none"


@dataclass
class Position:
    ticker: str
    kind: str                      # long | swing
    call_date: str
    fill_date: Optional[str]
    entry: Optional[float]
    shares: int
    cost: float
    target_usd: Optional[float]
    thesis: Optional[str]
    wrong_if: Optional[str]
    trigger: Optional[float]
    conviction: Optional[int]
    bucket: Optional[str]
    sector: Optional[str]
    stop: Optional[float] = None
    stop_initial: Optional[float] = None
    horizon_days: Optional[int] = None
    horizon_date: Optional[str] = None
    status: str = "pending"        # pending | open | closed | refused
    refused_because: Optional[str] = None
    exit_date: Optional[str] = None
    exit: Optional[float] = None
    exit_reason: Optional[str] = None
    last: Optional[float] = None
    last_date: Optional[str] = None
    ret: Optional[float] = None
    pnl: Optional[float] = None
    spy_same_window: Optional[float] = None
    excess_vs_spy: Optional[float] = None
    low_since_fill: Optional[float] = None
    falsifier_breached: Optional[bool] = None
    distance_to_trigger: Optional[float] = None
    stop_breached: Optional[bool] = None

    @property
    def market_value(self) -> Optional[float]:
        return None if self.last is None else self.shares * self.last

    def to_json(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["market_value"] = self.market_value
        for k in ("entry", "cost", "exit", "last", "ret", "pnl", "spy_same_window", "excess_vs_spy", "low_since_fill",
                  "distance_to_trigger", "stop", "stop_initial", "market_value"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 4)
        return d


@dataclass
class Book:
    as_of: Optional[str]
    starting_cash: float
    cash: float
    positions: List[Position]
    curve: List[Tuple[str, float]]
    benchmarks: Dict[str, Optional[float]]
    rule_checks: List[Dict[str, Any]]
    limitations: List[str]
    price_source: str = ""
    first_fill: Optional[str] = None
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.limitations:
            raise ValueError("a book may not be created without limitations")

    @property
    def open(self) -> List[Position]:
        return [p for p in self.positions if p.status == "open"]

    @property
    def closed(self) -> List[Position]:
        return [p for p in self.positions if p.status == "closed"]

    @property
    def equity(self) -> float:
        return self.cash + sum(p.market_value or 0.0 for p in self.open)

    @property
    def ret_since_start(self) -> float:
        return self.equity / self.starting_cash - 1.0

    @property
    def deployed(self) -> float:
        return sum(p.market_value or 0.0 for p in self.open)

    def to_json(self) -> Dict[str, Any]:
        r4 = lambda v: None if v is None else round(float(v), 4)  # noqa: E731
        by_kind = {k: [p.to_json() for p in self.positions if p.kind == k] for k in ("long", "swing")}
        excess = {b: None if v is None else self.ret_since_start - v for b, v in self.benchmarks.items()}
        return {
            "as_of": self.as_of, "first_fill": self.first_fill, "price_source": self.price_source,
            "starting_cash": self.starting_cash, "cash": round(self.cash, 2), "deployed": round(self.deployed, 2),
            "equity": round(self.equity, 2), "ret_since_start": r4(self.ret_since_start),
            "benchmarks_since_start": {b: r4(v) for b, v in self.benchmarks.items()},
            "excess_since_start": {b: r4(v) for b, v in excess.items()},
            "n_open": len(self.open), "n_closed": len(self.closed),
            "n_pending": sum(1 for p in self.positions if p.status == "pending"),
            "n_refused": sum(1 for p in self.positions if p.status == "refused"),
            "open": [p.to_json() for p in self.open], "closed": [p.to_json() for p in self.closed],
            "pending": [p.to_json() for p in self.positions if p.status == "pending"],
            "refused": [p.to_json() for p in self.positions if p.status == "refused"],
            "long": by_kind["long"], "swing": by_kind["swing"],
            "curve": _monthly(self.curve), "curve_last_20": [(d, round(v, 2)) for d, v in self.curve[-20:]],
            "max_drawdown": r4(_max_drawdown(self.curve)),
            "rule_checks": list(self.rule_checks), "flags": list(self.flags), "limitations": list(self.limitations),
            "disclaimer": DISCLAIMER,
        }


def _monthly(curve: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    seen: Dict[str, Tuple[str, float]] = {}
    for d, v in curve:
        seen[d[:7]] = (d, round(v, 2))
    return list(seen.values())


def _max_drawdown(curve: Sequence[Tuple[str, float]]) -> Optional[float]:
    if not curve:
        return None
    peak, mdd = -1.0, 0.0
    for _, v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def _col(closes: pd.DataFrame, t: str) -> Optional[pd.Series]:
    if closes is None or t not in closes.columns:
        return None
    s = closes[t].dropna()
    return None if s.empty else s


def _next_session(idx: pd.DatetimeIndex, after: str) -> Optional[int]:
    pos = int(idx.searchsorted(pd.Timestamp(after), side="right"))
    return pos if pos < len(idx) else None


def build_book(entries: Sequence[journal.JournalEntry], closes: Optional[pd.DataFrame], *, as_of: Optional[dt.date] = None,
               starting_cash: float = 100_000.0, rules: positioning.Rules = positioning.RULES,
               swing: SwingRules = SwingRules(), sectors: Optional[Mapping[str, str]] = None,
               price_source: str = "") -> Book:
    """Replay the calls against the closes. ``closes`` may be None: every call is then pending."""
    sectors = sectors or {}
    calls = [e for e in entries if not e.is_system and classify(e.action) != "none"]
    limitations = [
        "Fills are at the first close after each call, with no slippage on the long book and 10 bp each way on the "
        "swing sleeve; a real account would not get every close.",
        "Every return is a price return on adjusted closes; dividends are not reinvested on either side.",
        "The book is marked at the last close in the quotes file, which may be a session or more behind today; "
        "the email prints the age.",
        "Long positions are not exited by a falsifier automatically; a breached one is flagged for a decision.",
        "Sector labels come from the 2026 quality screen; a name outside it counts as 'unknown'.",
    ]
    idx = pd.DatetimeIndex(closes.index) if closes is not None and not closes.empty else pd.DatetimeIndex([])
    if as_of is not None and len(idx):
        idx = idx[idx <= pd.Timestamp(as_of)]
    if len(idx) == 0:
        positions = [_pending(e, sectors) for e in calls]
        return Book(None, starting_cash, starting_cash, positions, [], {b: None for b in BENCHMARKS}, [],
                    limitations, price_source or "no prices", None, ["no prices: every call is pending"])
    last_ts = idx[-1]
    as_of_iso = last_ts.date().isoformat()

    # the timeline of events: (session position, order, kind, entry)
    events: List[Tuple[int, int, str, journal.JournalEntry]] = []
    positions: List[Position] = []
    pending: List[Position] = []
    for n, e in enumerate(calls):
        pos = _next_session(idx, e.date)
        if pos is None:
            pending.append(_pending(e, sectors))
            continue
        events.append((pos, n, classify(e.action), e))
    events.sort(key=lambda x: (x[0], x[1]))

    cash = starting_cash
    open_by: Dict[str, Position] = {}
    deployed_month: Dict[str, float] = {}
    curve: List[Tuple[str, float]] = []
    flags: List[str] = []
    first_fill: Optional[str] = None
    ev_i = 0
    start_pos = events[0][0] if events else len(idx)

    def equity_at(i: int) -> float:
        total = cash
        for p in open_by.values():
            px = _px_at(closes, p.ticker, idx, i)
            total += p.shares * (px if px is not None else (p.entry or 0.0))
        return total

    for i in range(start_pos, len(idx)):
        day = idx[i].date().isoformat()
        # 1. exits due today: swing stops flagged on the previous close, horizons, sells
        for t, p in list(open_by.items()):
            if p.kind == "swing" and p.stop_breached and p.exit_reason is None:
                _close(p, closes, idx, i, "stop", cash_add=lambda v: None)
                cash += p.shares * p.exit * (1.0 - _cost(swing))
                del open_by[t]
            elif p.kind == "swing" and p.horizon_date == day:
                _close(p, closes, idx, i, "horizon", cash_add=lambda v: None)
                cash += p.shares * p.exit * (1.0 - _cost(swing))
                del open_by[t]
        # 2. today's fills
        while ev_i < len(events) and events[ev_i][0] == i:
            _, _, kind, e = events[ev_i]
            ev_i += 1
            px = _px_at(closes, e.ticker, idx, i)
            if kind in ("sell", "trim"):
                p = open_by.get(e.ticker)
                if p is None or px is None:
                    refused = _pending(e, sectors)
                    refused.status = "refused"
                    refused.refused_because = "no open position to sell" if p is None else "no price on the fill session"
                    positions.append(refused)
                    continue
                if kind == "trim":
                    n_out = max(1, p.shares // 2)
                    cash += n_out * px * (1.0 - (_cost(swing) if p.kind == "swing" else 0.0))
                    p.shares -= n_out
                    p.cost = p.shares * (p.entry or px)
                    flags.append(f"{e.ticker}: trimmed {n_out} shares at {px:.2f} on {day}")
                    if p.shares == 0:
                        _close(p, closes, idx, i, "sold", cash_add=lambda v: None)
                        del open_by[e.ticker]
                    continue
                _close(p, closes, idx, i, "sold", cash_add=lambda v: None)
                cash += p.shares * px * (1.0 - (_cost(swing) if p.kind == "swing" else 0.0))
                del open_by[e.ticker]
                continue
            # a buy
            p = _pending(e, sectors)
            if px is None or px <= 0:
                p.status, p.refused_because = "refused", "no price on the fill session"
                positions.append(p)
                continue
            if e.ticker in open_by:
                p.status, p.refused_because = "refused", "already held; no adds"
                positions.append(p)
                continue
            eq = equity_at(i)
            if p.kind == "swing":
                n_swing = sum(1 for q in open_by.values() if q.kind == "swing")
                sec_n = sum(1 for q in open_by.values() if q.kind == "swing" and q.sector == p.sector)
                if e.stop is None or e.stop >= px:
                    p.status, p.refused_because = "refused", "the stop is not below the fill price"
                elif n_swing >= swing.max_open:
                    p.status, p.refused_because = "refused", f"{swing.max_open} swing positions already open"
                elif sec_n >= swing.max_per_sector:
                    p.status, p.refused_because = "refused", f"two swing positions already open in {p.sector}"
                else:
                    shares = int(min(math.floor(swing.risk_usd / (px - e.stop)), math.floor(swing.max_position_usd / px)))
                    cost = shares * px * (1.0 + _cost(swing))
                    if shares <= 0 or cost > cash:
                        p.status, p.refused_because = "refused", "not enough cash for the sized position"
                    else:
                        cash -= cost
                        p.shares, p.entry, p.cost, p.fill_date, p.status = shares, px, shares * px, day, "open"
                        p.stop, p.stop_initial = e.stop, e.stop
                        # the horizon is counted from the call session, as the tracker counts it; the
                        # fill is the session after the call, so the horizon close is fill + horizon - 1
                        h = (i - 1) + (e.horizon_days or 0)
                        p.horizon_date = idx[h].date().isoformat() if h < len(idx) else None
                        open_by[e.ticker] = p
                        first_fill = first_fill or day
                positions.append(p)
                continue
            target = e.target_usd or 0.0
            if target <= 0:
                p.status, p.refused_because = "refused", "no dollar target on the call"
                positions.append(p)
                continue
            cap = eq * rules.max_position_pct
            if target > cap:
                flags.append(f"{e.ticker}: target ${target:,.0f} capped at {rules.max_position_pct:.0%} of equity (${cap:,.0f})")
                target = cap
            month = day[:7]
            if deployed_month.get(month, 0.0) + target > eq * rules.max_deployed_per_month_pct + 1e-6:
                p.status = "refused"
                p.refused_because = (f"the month's deployment would pass {rules.max_deployed_per_month_pct:.0%} of equity "
                                     f"(${deployed_month.get(month, 0.0):,.0f} already this month)")
                positions.append(p)
                continue
            if cash - target < eq * rules.cash_band[0] - 1e-6:
                p.status = "refused"
                p.refused_because = f"cash would fall under {rules.cash_band[0]:.0%} of equity"
                positions.append(p)
                continue
            shares = int(math.floor(target / px))
            if shares <= 0:
                p.status, p.refused_because = "refused", "the target buys no whole share"
                positions.append(p)
                continue
            cash -= shares * px
            deployed_month[month] = deployed_month.get(month, 0.0) + shares * px
            p.shares, p.entry, p.cost, p.fill_date, p.status = shares, px, shares * px, day, "open"
            open_by[e.ticker] = p
            first_fill = first_fill or day
            positions.append(p)
        # 3. marks, stop flags, falsifier flags
        for t, p in open_by.items():
            px = _px_at(closes, t, idx, i)
            if px is None:
                continue
            p.last, p.last_date = px, day
            p.low_since_fill = px if p.low_since_fill is None else min(p.low_since_fill, px)
            if p.kind == "swing" and p.stop is not None:
                if swing.raise_stop_to_entry_after_1r and p.entry is not None and p.stop_initial is not None \
                        and px >= p.entry + (p.entry - p.stop_initial):
                    p.stop = max(p.stop, p.entry)
                if px < p.stop and (p.horizon_date is None or day < p.horizon_date):
                    p.stop_breached = True
            if p.kind == "long" and p.trigger is not None:
                p.falsifier_breached = bool(p.low_since_fill is not None and p.low_since_fill < p.trigger)
                p.distance_to_trigger = px / p.trigger - 1.0 if p.trigger > 0 else None
        curve.append((day, equity_at(i)))

    # final marks and returns
    for p in positions + pending:
        if p.status == "open" and p.last is not None and p.entry:
            p.ret = p.last / p.entry - 1.0
            p.pnl = (p.last - p.entry) * p.shares
            p.spy_same_window = _bench_ret(closes, "SPY", p.fill_date, as_of_iso)
            p.excess_vs_spy = None if p.spy_same_window is None or p.ret is None else p.ret - p.spy_same_window
        elif p.status == "closed" and p.entry and p.exit:
            p.ret = p.exit / p.entry - 1.0
            p.pnl = (p.exit - p.entry) * p.shares
            p.spy_same_window = _bench_ret(closes, "SPY", p.fill_date, p.exit_date)
            p.excess_vs_spy = None if p.spy_same_window is None or p.ret is None else p.ret - p.spy_same_window
    positions.extend(pending)
    bench = {b: _bench_ret(closes, b, first_fill, as_of_iso) if first_fill else None for b in BENCHMARKS}
    state = _state(positions, cash, starting_cash)
    try:
        from . import local
        universe = local.load_quality()
    except Exception:  # noqa: BLE001
        universe = {}
    checks = positioning._constraint_check(state, rules, universe) if first_fill else []
    book = Book(as_of_iso, starting_cash, cash, positions, curve, bench, checks, limitations, price_source or "closes",
                first_fill, flags)
    return book


def _cost(swing: SwingRules) -> float:
    return 0.001   # 5 bp commission + 5 bp slippage each way, the paper sleeve's assumption


def _px_at(closes: pd.DataFrame, t: str, idx: pd.DatetimeIndex, i: int) -> Optional[float]:
    if t not in closes.columns:
        return None
    v = closes[t].reindex(idx).iloc[i] if len(idx) != len(closes.index) else closes[t].iloc[i]
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None


def _close(p: Position, closes: pd.DataFrame, idx: pd.DatetimeIndex, i: int, reason: str, *, cash_add) -> None:
    px = _px_at(closes, p.ticker, idx, i)
    back = i
    while px is None and back > 0:
        back -= 1
        px = _px_at(closes, p.ticker, idx, back)
    p.exit, p.exit_date, p.exit_reason, p.status = px, idx[back].date().isoformat(), reason, "closed"


def _bench_ret(closes: Optional[pd.DataFrame], b: str, start: Optional[str], end: Optional[str]) -> Optional[float]:
    if closes is None or start is None or end is None or b not in closes.columns:
        return None
    s = closes[b].dropna()
    s0 = s[s.index >= pd.Timestamp(start)]
    s1 = s[s.index <= pd.Timestamp(end)]
    if s0.empty or s1.empty or float(s0.iloc[0]) <= 0:
        return None
    return float(s1.iloc[-1]) / float(s0.iloc[0]) - 1.0


def _pending(e: journal.JournalEntry, sectors: Mapping[str, str]) -> Position:
    return Position(
        ticker=e.ticker, kind="swing" if e.is_swing else "long", call_date=e.date, fill_date=None, entry=None, shares=0,
        cost=0.0, target_usd=e.target_usd, thesis=e.thesis, wrong_if=e.wrong_if, trigger=tracker.parse_trigger(e.wrong_if),
        conviction=e.conviction, bucket=e.bucket, sector=sectors.get(e.ticker.upper(), "unknown"),
        stop=e.stop, stop_initial=e.stop, horizon_days=e.horizon_days,
    )


def _state(positions: Sequence[Position], cash: float, starting_cash: float) -> positioning.PortfolioState:
    holdings = [positioning.Holding(p.ticker, float(p.shares), float(p.entry or 0.0), "USD", p.fill_date, p.bucket, p.trigger)
                for p in positions if p.status == "open" and p.kind == "long"]
    total = cash + sum(p.market_value or p.cost for p in positions if p.status == "open")
    return positioning.PortfolioState("book.md, derived", holdings, cash, total, [])
