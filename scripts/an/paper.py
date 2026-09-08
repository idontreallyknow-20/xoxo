"""Paper trading: the swing rules replayed on real closes, with fake money and honest arithmetic.

Two measurements, kept apart on purpose:

1. **The event study.** Every signal the rule produces is traded at a fixed $1,000 of risk,
   with no position cap and no cash limit, so overlapping trades are allowed. This is the
   measure of whether the *rule* carries information, because the portfolio caps below throw
   away most signals and what survives depends on a tie-break.
2. **The ledger replay.** What the $20,000 sleeve in ``swing.md`` would actually have done:
   five positions at most, two per sector, $1,000 of risk per trade, no position over $5,000,
   cash never negative. This is the number an account would have shown.

Mechanics, made exact (``swing.md`` deals in closes, and so does this):

* A signal is read off session t's close and **fills at session t+1's close**. Opening prints
  are not closes and the rules forbid trading them.
* A stop is a closing level. The first close strictly below it, on any session after the
  signal, is a stop; the exit is the following close. That is the same test the tracker
  applies to a logged call (``tracker.grade_entries`` compares the lowest close after the
  call with the stop).
* The horizon exit is the close of the ``horizon``-th session after the signal, which is
  exactly the window the tracker grades, so ``ret_from_signal_close`` here equals the
  tracker's horizon return for the same call by construction (a test proves it).
* Once a position is up by its initial risk, the stop moves to the entry price. No adds.
* Costs are commission plus slippage in basis points each way, declared, never hidden.

Everything is a price return: the panel has no dividends and neither does the benchmark.
A result cannot be created without limitations; the constructor refuses.
"""
from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import setups, stats
from .backtest import _block_bootstrap_ci
from .history import HistoryPanel, Membership

__all__ = [
    "Features", "Arm", "Rules", "CostModel", "Event", "Trade", "ArmResult", "PaperResult", "compute_features",
    "signal_mask", "events_from_mask", "event_study", "ledger_replay", "run_arm", "block_ci", "verdict_for",
    "random_events", "ARM_KINDS", "MIN_DOLLAR_VOLUME", "MIN_HISTORY", "FILTERS", "apply_filters",
]

MIN_DOLLAR_VOLUME = 10_000_000.0   # scripts/setups.py::MIN_DOLLAR_VOLUME, the liquid universe
MIN_HISTORY = 200                  # pullback_setups needs 200 closes; the same floor for every rule
ARM_KINDS = ("pullback", "breakout_price_only", "pead_proxy", "random", "spy_hold")
LABELS = {
    "pullback": "pullback in trend (as swing.md)",
    "breakout_price_only": "breakout, price only: no estimate-revision filter exists in this history",
    "pead_proxy": "release-like session proxy (gap on volume), not post-earnings drift: no earnings dates exist in this history",
    "random": "random entries matched on count, horizon and stop distance: the control",
    "spy_hold": "SPY held for the window: the baseline",
}


# ---------------------------------------------------------------------------
# features, once per panel
# ---------------------------------------------------------------------------

@dataclass
class Features:
    close: pd.DataFrame
    volume: pd.DataFrame
    spy: pd.Series
    ret1: pd.DataFrame
    ma20: pd.DataFrame
    ma50: pd.DataFrame
    ma200: pd.DataFrame
    prior_high_251: pd.DataFrame
    high_252: pd.DataFrame
    low20: pd.DataFrame
    vol_mean20: pd.DataFrame
    n_history: pd.DataFrame
    eligible: pd.DataFrame
    spy_above_ma200: Optional[pd.Series] = None
    rs63: Optional[pd.DataFrame] = None
    rs126: Optional[pd.DataFrame] = None
    excluded: List[str] = field(default_factory=list)

    @property
    def index(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close.index)

    @property
    def tickers(self) -> List[str]:
        return list(self.close.columns)


def compute_features(panel: HistoryPanel, membership: Optional[Membership] = None, *,
                     exclude: Iterable[str] = (), min_dollar_volume: float = MIN_DOLLAR_VOLUME,
                     min_history: int = MIN_HISTORY) -> Features:
    c = panel.close.astype(float)
    v = panel.volume.reindex_like(c).astype(float)
    lb = setups.LOOKBACK
    n_hist = c.notna().cumsum()
    dv = (c * v).shift(1).rolling(20, min_periods=20).mean()
    elig = c.notna() & (n_hist >= min_history) & (dv >= min_dollar_volume)
    if membership is not None:
        elig = elig & membership.mask(pd.DatetimeIndex(c.index), list(c.columns))
    excluded = sorted({t.upper() for t in exclude} & set(c.columns))
    if excluded:
        elig.loc[:, excluded] = False
    spy = panel.benchmarks["SPY"] if "SPY" in panel.benchmarks.columns else pd.Series(np.nan, index=c.index)
    spy = spy.reindex(c.index).astype(float)
    return Features(
        close=c, volume=v, spy=spy,
        spy_above_ma200=(spy > spy.rolling(200, min_periods=200).mean()),
        rs63=(c / c.shift(63) - 1.0).sub(spy / spy.shift(63) - 1.0, axis=0),
        rs126=(c / c.shift(126) - 1.0).sub(spy / spy.shift(126) - 1.0, axis=0),
        ret1=c / c.shift(1) - 1.0,
        ma20=c.rolling(20, min_periods=20).mean(), ma50=c.rolling(50, min_periods=50).mean(),
        ma200=c.rolling(200, min_periods=200).mean(),
        prior_high_251=c.shift(1).rolling(lb - 1, min_periods=59).max(),
        high_252=c.rolling(lb, min_periods=200).max(),
        low20=c.rolling(20, min_periods=20).min(),
        vol_mean20=v.shift(1).rolling(20, min_periods=20).mean(),
        n_history=n_hist, eligible=elig, excluded=excluded,
    )


# ---------------------------------------------------------------------------
# arms and their masks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    id: str
    kind: str
    horizon: int
    params: Dict[str, float] = field(default_factory=dict)
    preregistered: bool = False

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "horizon": self.horizon, "params": dict(self.params),
                "preregistered": self.preregistered, "label": self.label}


def signal_mask(f: Features, arm: Arm) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """(fires, stop): boolean and float frames, date x ticker. The vector form of the setups.py producers."""
    p = arm.params
    c = f.close
    if arm.kind == "pullback":
        band = float(p.get("band", setups.PULLBACK_BAND))
        max_dd = float(p.get("max_dd", setups.PULLBACK_MAX_DD))
        fires = ((f.ma50 > f.ma200) & (c > f.ma200) & ((c / f.ma20 - 1.0).abs() <= band)
                 & (c / f.high_252 - 1.0 >= max_dd) & (f.low20 < c))
        stop = f.low20
    elif arm.kind == "breakout_price_only":
        stop_pct = float(p.get("stop_pct", setups.BREAKOUT_STOP))
        fires = c > f.prior_high_251
        stop = c * (1.0 - stop_pct)
    elif arm.kind == "pead_proxy":
        gap = float(p.get("gap", setups.PEAD_GAP))
        vol_mult = float(p.get("vol_mult", 2.0))
        release = (f.ret1 >= gap) & (f.volume >= vol_mult * f.vol_mean20)
        fires = pd.DataFrame(False, index=c.index, columns=c.columns)
        stop = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
        taken = pd.DataFrame(False, index=c.index, columns=c.columns)
        for k in range(setups.PEAD_SESSIONS):
            rel_k = release.shift(k).fillna(False).astype(bool)
            holds = c >= c.shift(k)
            cand = rel_k & holds & ~taken
            low_since = c.rolling(k + 1, min_periods=k + 1).min()
            cand = cand & (low_since < c)
            stop = stop.where(~cand, low_since)
            fires = fires | cand
            taken = taken | rel_k
    elif arm.kind in ("random", "spy_hold"):
        fires = pd.DataFrame(False, index=c.index, columns=c.columns)
        stop = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    else:
        raise ValueError(f"unknown arm kind {arm.kind!r}")
    fires = fires.fillna(False).astype(bool) & f.eligible & c.notna()
    fires = apply_filters(f, fires, arm)
    return fires, stop


FILTERS = {
    "mkt200": "SPY above its own 200-session mean on the signal session (a regime filter)",
    "rs63": "the name outran SPY over the prior 63 sessions (relative strength)",
    "rs126": "the name outran SPY over the prior 126 sessions (relative strength)",
    "x1": "the close is at least 1% above the level the rule keyed on (breakout: the prior high; pullback: the 20-session low)",
}


def apply_filters(f: Features, fires: pd.DataFrame, arm: Arm) -> pd.DataFrame:
    """Optional entry filters named in ``arm.params['filters']`` (a list of FILTERS keys). Each only removes signals."""
    names = list(arm.params.get("filters") or [])
    if not names:
        return fires
    out = fires.copy()
    c = f.close
    for name in names:
        if name == "mkt200":
            if f.spy_above_ma200 is None:
                raise ValueError("mkt200 needs SPY in the panel")
            out = out & pd.DataFrame(np.repeat(f.spy_above_ma200.fillna(False).to_numpy()[:, None], out.shape[1], axis=1),
                                     index=out.index, columns=out.columns)
        elif name in ("rs63", "rs126"):
            rs = f.rs63 if name == "rs63" else f.rs126
            out = out & (rs > 0).fillna(False)
        elif name == "x1":
            if arm.kind == "breakout_price_only":
                out = out & (c >= f.prior_high_251 * 1.01)
            elif arm.kind == "pullback":
                out = out & (c >= f.low20 * 1.01)
        else:
            raise ValueError(f"unknown filter {name!r}; known: {sorted(FILTERS)}")
    return out


# ---------------------------------------------------------------------------
# events and the event study
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    ticker: str
    i: int             # row index of the signal session
    signal_date: str
    stop: float
    horizon: int


def events_from_mask(fires: pd.DataFrame, stop: pd.DataFrame, f: Features, arm: Arm, *,
                     start: Optional[str] = None, end: Optional[str] = None,
                     refractory: bool = True) -> Tuple[List[Event], Dict[str, int]]:
    """Every firing inside the window with a fill session and a full horizon on the panel.

    Firings are read in row order, so the refractory rule sees a name's earlier signal first."""
    idx = f.index
    n = len(idx)
    lo = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start), side="left"))
    hi = n - 1 if end is None else int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    arr = fires.to_numpy()
    st = stop.to_numpy()
    cl = f.close.to_numpy()
    cols = list(fires.columns)
    out: List[Event] = []
    skipped = {"outside_window": 0, "within_previous_horizon": 0, "no_next_session": 0, "truncated_at_panel_end": 0,
               "bad_stop": 0}
    rows, cs = np.nonzero(arr)
    last_by_col: Dict[int, int] = {}
    for i, j in zip(rows.tolist(), cs.tolist()):
        if i < lo or i > hi:
            skipped["outside_window"] += 1
            continue
        # one open position per name: a state that holds day after day is one event, not
        # twenty; a fresh signal is allowed again on the session the previous horizon ends
        prev = last_by_col.get(j)
        if refractory and prev is not None and i < prev + arm.horizon:
            skipped["within_previous_horizon"] += 1
            continue
        if i + 1 >= n:
            skipped["no_next_session"] += 1
            continue
        if i + arm.horizon >= n:
            skipped["truncated_at_panel_end"] += 1
            continue
        s = float(st[i, j])
        entry = float(cl[i, j])
        if not (s == s) or s <= 0 or s >= entry:
            skipped["bad_stop"] += 1
            continue
        out.append(Event(cols[j], i, idx[i].date().isoformat(), s, arm.horizon))
        last_by_col[j] = i
    return out, skipped


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 5.0
    slippage_bps: float = 5.0

    @property
    def one_way(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 10_000.0

    def to_json(self) -> Dict[str, float]:
        return {"commission_bps": self.commission_bps, "slippage_bps": self.slippage_bps,
                "round_trip_bps": 2 * (self.commission_bps + self.slippage_bps)}


@dataclass(frozen=True)
class Rules:
    sleeve_usd: float = 20_000.0
    risk_usd: float = 1_000.0
    max_position_usd: float = 5_000.0
    max_open: int = 5
    max_per_sector: int = 2
    raise_stop_to_entry_after_1r: bool = True

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    ticker: str
    arm: str
    signal_date: str
    fill_date: str
    exit_date: str
    exit_reason: str      # stop | horizon | panel_end
    entry: float
    stop_initial: float
    stop_final: float
    exit: float
    sessions_held: int
    ret_gross: float
    ret_net: float
    ret_from_signal_close: Optional[float]
    spy_same_window: Optional[float]
    excess_vs_spy: Optional[float]
    shares: int = 0
    notional: float = 0.0
    sector: str = "unknown"

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _walk(events: Sequence[Event], f: Features, *, arm_id: str, rules: Rules, costs: CostModel) -> List[Trade]:
    """The mechanics, vectorised over events, stepping through the horizon one session at a time."""
    if not events:
        return []
    cl = f.close.to_numpy()
    spy = f.spy.to_numpy()
    idx = f.index
    col_of = {t: k for k, t in enumerate(f.tickers)}
    n_ev = len(events)
    i0 = np.array([e.i for e in events])
    j = np.array([col_of[e.ticker] for e in events])
    H = np.array([e.horizon for e in events])
    stop0 = np.array([e.stop for e in events])
    entry = cl[i0 + 1, j]
    stop = stop0.copy()
    exit_i = i0 + H                # default: horizon close
    exit_px = np.full(n_ev, np.nan)
    reason = np.array(["horizon"] * n_ev, dtype=object)
    done = np.zeros(n_ev, dtype=bool)
    Hmax = int(H.max())
    one_r = entry - stop0
    for k in range(1, Hmax + 1):
        active = ~done & (k <= H)
        if not active.any():
            break
        ii = i0 + k
        px = cl[ii, j]
        # a missing print inside the window: the name stopped trading; exit at the last real close
        gone = active & ~np.isfinite(px)
        if gone.any():
            for e_ix in np.nonzero(gone)[0]:
                back = ii[e_ix] - 1
                while back > i0[e_ix] + 1 and not np.isfinite(cl[back, j[e_ix]]):
                    back -= 1
                exit_i[e_ix] = back
                reason[e_ix] = "panel_end"
                done[e_ix] = True
            active = active & ~gone
        if rules.raise_stop_to_entry_after_1r:
            up = active & (px >= entry + one_r)
            stop = np.where(up, np.maximum(stop, entry), stop)
        hit = active & (px < stop)
        if hit.any():
            nxt = np.minimum(ii + 1, i0 + H)
            exit_i = np.where(hit, nxt, exit_i)
            reason[hit] = "stop"
            done = done | hit
        at_h = active & (k == H) & ~hit
        done = done | at_h
    exit_px = cl[exit_i, j]
    # if the horizon/exit close itself is missing, walk back to the last real print
    for e_ix in np.nonzero(~np.isfinite(exit_px))[0]:
        back = exit_i[e_ix]
        while back > i0[e_ix] + 1 and not np.isfinite(cl[back, j[e_ix]]):
            back -= 1
        exit_i[e_ix] = back
        exit_px[e_ix] = cl[back, j[e_ix]]
        reason[e_ix] = "panel_end"
    c = costs.one_way
    out: List[Trade] = []
    for e_ix, ev in enumerate(events):
        en, ex = float(entry[e_ix]), float(exit_px[e_ix])
        if not (np.isfinite(en) and np.isfinite(ex)) or en <= 0:
            continue
        gross = ex / en - 1.0
        net = (ex * (1.0 - c)) / (en * (1.0 + c)) - 1.0
        sig_px, h_px = cl[ev.i, j[e_ix]], cl[ev.i + ev.horizon, j[e_ix]]
        r_sig = None if not (np.isfinite(sig_px) and np.isfinite(h_px)) or sig_px <= 0 else float(h_px / sig_px - 1.0)
        s0, s1 = spy[ev.i + 1], spy[exit_i[e_ix]]
        spy_r = None if not (np.isfinite(s0) and np.isfinite(s1)) or s0 <= 0 else float(s1 / s0 - 1.0)
        out.append(Trade(
            ticker=ev.ticker, arm=arm_id, signal_date=ev.signal_date, fill_date=idx[ev.i + 1].date().isoformat(),
            exit_date=idx[int(exit_i[e_ix])].date().isoformat(), exit_reason=str(reason[e_ix]), entry=en,
            stop_initial=float(stop0[e_ix]), stop_final=float(stop[e_ix]), exit=ex,
            sessions_held=int(exit_i[e_ix] - (ev.i + 1)), ret_gross=gross, ret_net=net,
            ret_from_signal_close=r_sig, spy_same_window=spy_r,
            excess_vs_spy=None if spy_r is None else net - spy_r,
        ))
    return out


def event_study(events: Sequence[Event], f: Features, *, arm_id: str, rules: Rules, costs: CostModel) -> List[Trade]:
    return _walk(events, f, arm_id=arm_id, rules=rules, costs=costs)


# ---------------------------------------------------------------------------
# the ledger: what the sleeve would have done
# ---------------------------------------------------------------------------

@dataclass
class Ledger:
    trades: List[Trade]
    equity: List[Tuple[str, float]]     # every session, mark to close
    skipped: Dict[str, int]
    final_equity: float
    max_drawdown: Optional[float]
    exposure: Optional[float]
    turnover: Optional[float]

    def to_json(self, *, monthly_curve: bool = True) -> Dict[str, Any]:
        curve = self.equity
        if monthly_curve and curve:
            seen: Dict[str, Tuple[str, float]] = {}
            for d, v in curve:
                seen[d[:7]] = (d, v)
            curve = list(seen.values())
        return {"n_trades": len(self.trades), "final_equity": round(self.final_equity, 2),
                "max_drawdown": None if self.max_drawdown is None else round(self.max_drawdown, 4),
                "exposure": None if self.exposure is None else round(self.exposure, 4),
                "turnover": None if self.turnover is None else round(self.turnover, 3),
                "skipped": dict(self.skipped), "equity_curve": [(d, round(v, 2)) for d, v in curve]}


def ledger_replay(events: Sequence[Event], f: Features, *, arm_id: str, rules: Rules, costs: CostModel,
                  sectors: Mapping[str, str], start: Optional[str] = None, end: Optional[str] = None) -> Ledger:
    """A Python loop over sessions applying swing.md's caps to the same events, in KINDS-then-ticker order."""
    idx = f.index
    n = len(idx)
    lo = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start), side="left"))
    hi = n - 1 if end is None else int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    cl = f.close.to_numpy()
    spy = f.spy.to_numpy()
    col_of = {t: k for k, t in enumerate(f.tickers)}
    by_day: Dict[int, List[Event]] = {}
    for e in events:
        by_day.setdefault(e.i, []).append(e)
    cash = rules.sleeve_usd
    open_pos: Dict[str, Dict[str, Any]] = {}
    trades: List[Trade] = []
    equity: List[Tuple[str, float]] = []
    skipped = {"capacity": 0, "sector": 0, "cash": 0, "already_open": 0}
    c = costs.one_way
    invested_days = 0.0
    traded_notional = 0.0
    peak, mdd = rules.sleeve_usd, 0.0

    def close_position(t: str, pos: Dict[str, Any], i: int, reason: str) -> None:
        nonlocal cash, traded_notional
        px = cl[i, col_of[t]]
        if not np.isfinite(px):
            back = i - 1
            while back > pos["fill_i"] and not np.isfinite(cl[back, col_of[t]]):
                back -= 1
            px, i, reason = cl[back, col_of[t]], back, "panel_end"
        proceeds = pos["shares"] * px * (1.0 - c)
        cash += proceeds
        traded_notional += pos["shares"] * px
        en = pos["entry"]
        gross = px / en - 1.0
        net = (px * (1.0 - c)) / (en * (1.0 + c)) - 1.0
        s0, s1 = spy[pos["fill_i"]], spy[i]
        spy_r = None if not (np.isfinite(s0) and np.isfinite(s1)) or s0 <= 0 else float(s1 / s0 - 1.0)
        sig_px, h_px = cl[pos["signal_i"], col_of[t]], cl[min(pos["signal_i"] + pos["horizon"], n - 1), col_of[t]]
        r_sig = None if not (np.isfinite(sig_px) and np.isfinite(h_px)) or sig_px <= 0 else float(h_px / sig_px - 1.0)
        trades.append(Trade(
            ticker=t, arm=arm_id, signal_date=idx[pos["signal_i"]].date().isoformat(),
            fill_date=idx[pos["fill_i"]].date().isoformat(), exit_date=idx[i].date().isoformat(), exit_reason=reason,
            entry=en, stop_initial=pos["stop0"], stop_final=pos["stop"], exit=float(px), sessions_held=int(i - pos["fill_i"]),
            ret_gross=gross, ret_net=net, ret_from_signal_close=r_sig, spy_same_window=spy_r,
            excess_vs_spy=None if spy_r is None else net - spy_r, shares=pos["shares"],
            notional=pos["shares"] * en, sector=pos["sector"]))

    pending: List[Event] = []
    for i in range(lo, hi + 1):
        # 1. exits decided on this close (stops flagged yesterday exit now; horizons exit today)
        for t in list(open_pos):
            pos = open_pos[t]
            if pos.get("exit_next"):
                close_position(t, pos, i, "stop")
                del open_pos[t]
                continue
            if i >= pos["horizon_i"]:
                close_position(t, pos, i, "horizon")
                del open_pos[t]
        # 2. fills for yesterday's accepted signals, at today's close
        for e in pending:
            px = cl[i, col_of[e.ticker]]
            if not np.isfinite(px) or px <= 0 or e.ticker in open_pos:
                continue
            risk = px - e.stop
            if risk <= 0:
                # gapped through the stop before the fill; the rule says no entry below the stop
                continue
            shares = int(min(math.floor(rules.risk_usd / risk), math.floor(rules.max_position_usd / px)))
            if shares <= 0:
                continue
            cost = shares * px * (1.0 + c)
            if cost > cash:
                shares = int(math.floor(cash / (px * (1.0 + c))))
                if shares <= 0:
                    skipped["cash"] += 1
                    continue
                cost = shares * px * (1.0 + c)
            cash -= cost
            traded_notional += shares * px
            open_pos[e.ticker] = {"entry": float(px), "stop0": e.stop, "stop": e.stop, "shares": shares, "fill_i": i,
                                  "signal_i": e.i, "horizon": e.horizon, "horizon_i": e.i + e.horizon,
                                  "sector": sectors.get(e.ticker, "unknown"), "exit_next": False}
        pending = []
        # 3. mark stops on today's close for tomorrow's exit; raise stops that earned it
        for t, pos in open_pos.items():
            px = cl[i, col_of[t]]
            if not np.isfinite(px):
                continue
            if rules.raise_stop_to_entry_after_1r and px >= pos["entry"] + (pos["entry"] - pos["stop0"]):
                pos["stop"] = max(pos["stop"], pos["entry"])
            if px < pos["stop"] and i < pos["horizon_i"]:
                pos["exit_next"] = True
        # 4. today's signals, filtered by the caps, queue for tomorrow's fill
        todays = sorted(by_day.get(i, []), key=lambda e: e.ticker)
        n_open = len(open_pos) + len(pending)
        sector_count: Dict[str, int] = {}
        for pos in open_pos.values():
            sector_count[pos["sector"]] = sector_count.get(pos["sector"], 0) + 1
        for e in todays:
            if e.ticker in open_pos or any(p.ticker == e.ticker for p in pending):
                skipped["already_open"] += 1
                continue
            if n_open >= rules.max_open:
                skipped["capacity"] += 1
                continue
            sec = sectors.get(e.ticker, "unknown")
            if sector_count.get(sec, 0) >= rules.max_per_sector:
                skipped["sector"] += 1
                continue
            if i + 1 > hi:
                continue
            pending.append(e)
            n_open += 1
            sector_count[sec] = sector_count.get(sec, 0) + 1
        # 5. mark to close
        mv = cash
        for t, pos in open_pos.items():
            px = cl[i, col_of[t]]
            if np.isfinite(px):
                mv += pos["shares"] * px
                invested_days += pos["shares"] * px / max(mv, 1e-9)
        equity.append((idx[i].date().isoformat(), float(mv)))
        peak = max(peak, mv)
        mdd = min(mdd, mv / peak - 1.0)
    for t in list(open_pos):
        close_position(t, open_pos[t], hi, "panel_end")
    sessions = max(1, hi - lo + 1)
    final = equity[-1][1] if equity else cash
    return Ledger(trades, equity, skipped, float(final), float(mdd) if equity else None,
                  invested_days / sessions if equity else None,
                  traded_notional / rules.sleeve_usd / max(sessions / 252.0, 1e-9) if equity else None)


# ---------------------------------------------------------------------------
# the random control's events
# ---------------------------------------------------------------------------

def random_events(f: Features, *, n: int, horizon: int, risk_pct: float, start: Optional[str], end: Optional[str],
                  seed: int) -> List[Event]:
    """``n`` (session, ticker) pairs drawn uniformly from the eligible cells in the window."""
    idx = f.index
    m = len(idx)
    lo = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start), side="left"))
    hi = m - 1 if end is None else int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    hi = min(hi, m - 1 - horizon)
    if hi < lo or n <= 0:
        return []
    elig = f.eligible.to_numpy()[lo:hi + 1]
    rows, cols = np.nonzero(elig)
    if len(rows) == 0:
        return []
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(rows), size=min(n, len(rows)), replace=n > len(rows))
    cl = f.close.to_numpy()
    cols_list = f.tickers
    out: List[Event] = []
    for p in pick.tolist():
        i, j = int(rows[p]) + lo, int(cols[p])
        px = cl[i, j]
        if not np.isfinite(px) or px <= 0:
            continue
        out.append(Event(cols_list[j], i, idx[i].date().isoformat(), float(px * (1.0 - risk_pct)), horizon))
    out.sort(key=lambda e: (e.i, e.ticker))
    return out


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

def block_ci(series: Sequence[Optional[float]], *, block: int, iterations: int = 3000, seed: int = 20260906
             ) -> Optional[Tuple[float, float]]:
    """A public door onto the engine's moving-block bootstrap, unchanged."""
    return _block_bootstrap_ci([x for x in series if x is not None], block=block, iterations=iterations, seed=seed)


def verdict_for(mean: Optional[float], ci: Optional[Tuple[float, float]], t: Optional[float], *, n_effective: float,
                hypotheses_tested: int) -> str:
    """The BacktestResult.verdict ladder, applied to a mean excess instead of a mean IC."""
    if mean is None or ci is None:
        return "no evidence"
    lo, hi = ci
    if lo <= 0 <= hi:
        return "no evidence"
    if hi < 0:
        # an interval entirely below zero is evidence of the wrong kind; say so rather than calling it suggestive
        return "wrong way"
    if n_effective < 12:
        return "weak"
    if n_effective < 20:
        return "suggestive"
    if hypotheses_tested > 1 and t is not None:
        needed = 1.96 + 0.5 * math.log(max(hypotheses_tested, 1))
        if abs(t) < needed:
            return "suggestive"
    return "supported"


def _pf(rets: Sequence[float]) -> Optional[float]:
    g = sum(r for r in rets if r > 0)
    l = -sum(r for r in rets if r < 0)
    if l <= 0:
        return None
    return g / l


@dataclass
class ArmResult:
    arm: Arm
    window: Tuple[str, str]
    n_signals: int
    n_events: int
    signal_skips: Dict[str, int]
    trades: List[Trade]
    ledger: Optional[Ledger]
    hypotheses_tested: int = 1
    block: int = 2

    # -- event-study statistics ------------------------------------------
    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def hit_rate(self) -> Optional[float]:
        return None if not self.trades else sum(1 for t in self.trades if t.ret_net > 0) / len(self.trades)

    @property
    def mean_ret_net(self) -> Optional[float]:
        return stats.mean([t.ret_net for t in self.trades])

    @property
    def median_ret_net(self) -> Optional[float]:
        return stats.median([t.ret_net for t in self.trades])

    @property
    def excess_list(self) -> List[float]:
        return [t.excess_vs_spy for t in self.trades if t.excess_vs_spy is not None]

    @property
    def mean_excess_vs_spy(self) -> Optional[float]:
        return stats.mean(self.excess_list)

    @property
    def buckets(self) -> List[Tuple[str, Optional[float], int]]:
        by: Dict[str, List[float]] = {}
        for t in self.trades:
            if t.excess_vs_spy is not None:
                by.setdefault(t.fill_date[:7], []).append(t.excess_vs_spy)
        return [(m, stats.mean(v), len(v)) for m, v in sorted(by.items())]

    @property
    def bucket_means(self) -> List[float]:
        return [m for _, m, _ in self.buckets if m is not None]

    @property
    def excess_ci(self) -> Optional[Tuple[float, float]]:
        return block_ci(self.bucket_means, block=self.block)

    @property
    def excess_t(self) -> Optional[float]:
        return stats.t_stat(self.bucket_means)

    @property
    def n_effective(self) -> float:
        return len(self.bucket_means) / max(self.block, 1)

    @property
    def verdict(self) -> str:
        return verdict_for(self.mean_excess_vs_spy, self.excess_ci, self.excess_t, n_effective=self.n_effective,
                           hypotheses_tested=self.hypotheses_tested)

    @property
    def exit_reasons(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for t in self.trades:
            out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
        return dict(sorted(out.items()))

    @property
    def by_year(self) -> Dict[str, Dict[str, Any]]:
        by: Dict[str, List[Trade]] = {}
        for t in self.trades:
            by.setdefault(t.fill_date[:4], []).append(t)
        return {y: {"n": len(v), "mean_excess_vs_spy": stats.mean([t.excess_vs_spy for t in v]),
                    "hit_rate": sum(1 for t in v if t.ret_net > 0) / len(v)} for y, v in sorted(by.items())}

    @property
    def worst_five(self) -> List[Dict[str, Any]]:
        w = sorted(self.trades, key=lambda t: t.ret_net)[:5]
        return [{"ticker": t.ticker, "fill_date": t.fill_date, "ret_net": round(t.ret_net, 4), "exit_reason": t.exit_reason}
                for t in w]

    def to_json(self) -> Dict[str, Any]:
        r4 = lambda v: None if v is None else round(v, 4)  # noqa: E731
        ci = self.excess_ci
        return {
            "arm": self.arm.to_json(), "window": list(self.window), "n_signals": self.n_signals, "n_events": self.n_events,
            "signal_skips": dict(self.signal_skips), "n_trades": self.n_trades, "hit_rate": r4(self.hit_rate),
            "mean_ret_net": r4(self.mean_ret_net), "median_ret_net": r4(self.median_ret_net),
            "mean_excess_vs_spy": r4(self.mean_excess_vs_spy),
            "excess_ci": None if ci is None else [r4(ci[0]), r4(ci[1])], "excess_t": r4(self.excess_t),
            "n_buckets": len(self.bucket_means), "block": self.block, "n_effective": round(self.n_effective, 2),
            "hypotheses_tested": self.hypotheses_tested, "verdict": self.verdict,
            "mean_ret_from_signal_close": r4(stats.mean([t.ret_from_signal_close for t in self.trades])),
            "profit_factor": r4(_pf([t.ret_net for t in self.trades])), "exit_reasons": self.exit_reasons,
            "by_year": {y: {k: (r4(v) if isinstance(v, float) else v) for k, v in d.items()} for y, d in self.by_year.items()},
            "worst_five": self.worst_five,
            "ledger": None if self.ledger is None else self.ledger.to_json(),
        }


@dataclass
class PaperResult:
    label: str
    window: Tuple[str, str]
    rules: Rules
    costs: CostModel
    arms: List[ArmResult]
    hypotheses_tested: int
    limitations: List[str]
    flags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.limitations:
            raise ValueError("a paper-trading result may not be created without limitations")


def _block_for(horizon: int) -> int:
    return int(math.ceil(horizon / 21.0)) + 1


def run_arm(f: Features, arm: Arm, *, start: Optional[str], end: Optional[str], rules: Rules, costs: CostModel,
            sectors: Mapping[str, str], hypotheses_tested: int = 1, seed: int = 20260906,
            match: Optional[ArmResult] = None, with_ledger: bool = True) -> ArmResult:
    """One arm over one window. ``match`` is required for the random control (count, horizon, stop distance)."""
    if arm.kind == "random":
        if match is None:
            raise ValueError("the random control needs an arm to match")
        risk = stats.median([1.0 - t.stop_initial / t.entry for t in match.trades if t.entry > 0]) or 0.05
        events = random_events(f, n=match.n_events, horizon=arm.horizon, risk_pct=float(risk), start=start, end=end,
                               seed=seed)
        n_signals, skips = len(events), {}
    elif arm.kind == "spy_hold":
        events, n_signals, skips = [], 0, {}
    else:
        fires, stop = signal_mask(f, arm)
        n_signals = int(fires.to_numpy().sum())
        events, skips = events_from_mask(fires, stop, f, arm, start=start, end=end)
    trades = event_study(events, f, arm_id=arm.id, rules=rules, costs=costs)
    ledger = None
    if with_ledger and arm.kind != "random":
        ledger = ledger_replay(events, f, arm_id=arm.id, rules=rules, costs=costs, sectors=sectors, start=start, end=end)
        if arm.kind == "spy_hold":
            idx = f.index
            lo = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start), side="left"))
            hi = len(idx) - 1 if end is None else int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
            s = f.spy.to_numpy()
            base = s[lo]
            curve = [(idx[i].date().isoformat(), float(rules.sleeve_usd * s[i] / base)) for i in range(lo, hi + 1)
                     if np.isfinite(s[i]) and np.isfinite(base) and base > 0]
            peak, mdd = -1.0, 0.0
            for _, v in curve:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1.0)
            ledger = Ledger([], curve, {}, curve[-1][1] if curve else rules.sleeve_usd, mdd if curve else None, 1.0, 0.0)
    w = (start or f.index[0].date().isoformat(), end or f.index[-1].date().isoformat())
    return ArmResult(arm, w, n_signals, len(events), skips, trades, ledger, hypotheses_tested, _block_for(arm.horizon))
