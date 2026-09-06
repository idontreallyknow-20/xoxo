"""Generate synthetic panels with a known answer, to validate the backtest engine.

A backtest engine is a measuring instrument, and an instrument nobody calibrated
is a rumour. These generators produce panels where the true relationship between
score and forward return is set by hand, so the engine can be checked against
cases with a known answer:

*planted alpha*   the score really does predict returns, by a stated amount. The
                  engine must recover roughly that, with the right sign.
*pure noise*      returns are independent of the score. The engine must say so,
                  with an interval covering zero, rather than finding something.
*contaminated*    the score is computed from the return it is supposed to predict.
                  The engine must flag the result as implausible rather than
                  reporting a spectacular edge.
*delisting*       names disappear mid-panel, and the ones that disappear did badly.
                  The engine must count them rather than quietly dropping them,
                  and the difference between including and excluding them is the
                  size of the survivorship bias.

Everything is seeded. The same seed gives byte-identical output on any machine, so
a number in a report can be reproduced rather than merely believed.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .backtest import Rebalance

__all__ = ["PanelSpec", "make_panel", "SECTORS"]

SECTORS = ("Technology", "Industrials", "Healthcare", "Consumer Cyclical", "Energy")


@dataclass(frozen=True)
class PanelSpec:
    """How much of the return is signal and how much is everything else.

    ``alpha`` is the coefficient on the standardised score in the return equation.
    With ``idio_vol`` at 0.20 a quarter, an alpha of 0.01 is a realistic effect
    size and produces a rank IC in the 0.03 to 0.06 range, which is what a real
    published signal looks like. An alpha of 0.20 produces an IC near 0.6, which
    is what a bug looks like.
    """

    n_dates: int = 24
    n_names: int = 120
    alpha: float = 0.01
    idio_vol: float = 0.20
    market_vol: float = 0.08
    sector_vol: float = 0.05
    delist_per_date: int = 0
    delist_return: float = -0.45
    contaminate: bool = False
    seed: int = 20260906
    start_year: int = 2019
    months_between: int = 3


def _dates(spec: PanelSpec) -> List[str]:
    out = []
    y, m = spec.start_year, 1
    for _ in range(spec.n_dates):
        out.append(f"{y:04d}-{m:02d}-01")
        m += spec.months_between
        while m > 12:
            m -= 12
            y += 1
    return out


def make_panel(spec: PanelSpec) -> Tuple[List[Rebalance], Dict[str, object]]:
    """Return the panel and a dict of the truth used to build it.

    The truth dict is what a test asserts against. It is deliberately separate from
    the panel so nothing in the engine can see it.
    """
    rng = random.Random(spec.seed)
    names = [f"S{i:04d}" for i in range(spec.n_names)]
    sectors = {t: SECTORS[i % len(SECTORS)] for i, t in enumerate(names)}
    buckets = {t: (["compounder"] if i % 3 else ["cyclical turn"]) for i, t in enumerate(names)}

    alive = set(names)
    delisted: Dict[str, str] = {}
    rebalances: List[Rebalance] = []
    expected_ic_inputs: List[float] = []

    for date in _dates(spec):
        live = sorted(alive)
        if len(live) < 20:
            break

        # A score with no relationship to anything, standardised across the date.
        raw = {t: rng.gauss(0.0, 1.0) for t in live}
        m = sum(raw.values()) / len(raw)
        sd = math.sqrt(sum((v - m) ** 2 for v in raw.values()) / max(1, len(raw) - 1)) or 1.0
        scores = {t: (v - m) / sd for t, v in raw.items()}

        market = rng.gauss(0.0, spec.market_vol)
        sector_shock = {s: rng.gauss(0.0, spec.sector_vol) for s in SECTORS}

        rets: Dict[str, Optional[float]] = {}
        for t in live:
            idio = rng.gauss(0.0, spec.idio_vol)
            r = market + sector_shock[sectors[t]] + idio + spec.alpha * scores[t]
            rets[t] = r

        if spec.contaminate:
            # The sin: the score is built from the answer. This is what look-ahead
            # actually looks like in code, and it is a two-line mistake to make.
            scores = {t: rets[t] for t in live}

        # Names that stop trading. They leave at a loss, which is exactly why a
        # universe of survivors flatters every statistic computed on it.
        if spec.delist_per_date and len(live) > 30:
            ordered = sorted(live, key=lambda t: scores[t])
            for t in ordered[: spec.delist_per_date]:
                delisted[t] = date
                rets[t] = spec.delist_return
                alive.discard(t)

        expected_ic_inputs.append(spec.alpha)
        rebalances.append(
            Rebalance(
                date=date,
                scores=scores,
                forward_returns=rets,
                sectors={t: sectors[t] for t in live},
                buckets={t: buckets[t] for t in live},
            )
        )

    total_vol = math.sqrt(spec.idio_vol**2 + spec.sector_vol**2)
    approx_pearson = spec.alpha / math.sqrt(spec.alpha**2 + total_vol**2) if total_vol else 1.0
    return rebalances, {
        "alpha": spec.alpha,
        "approx_pearson_ic": approx_pearson,
        # Spearman on normal data runs about 3/pi of Pearson.
        "approx_rank_ic": approx_pearson * (3.0 / math.pi),
        "n_dates": len(rebalances),
        "delisted": delisted,
        "sectors": sectors,
        "contaminated": spec.contaminate,
    }
