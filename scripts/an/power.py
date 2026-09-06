"""How long until the backtest could detect anything, and how strong it would have to be.

"There is no backtest yet" is true but unhelpful on its own. The useful question is
the one it implies: if you start archiving a snapshot every quarter today, how many
quarters until the result could mean something, and what size of edge would still be
invisible when you got there?

This answers both, twice over.

*Analytically.* Under the null, a rank information coefficient computed on n names
has a standard error of roughly 1/sqrt(n-1). Averaged over k independent
rebalances that becomes 1/sqrt(k(n-1)). A two-sided test at 5% with 80% power needs
a true effect of about 2.8 standard errors, so the smallest detectable rank IC is
about 2.8 / sqrt(k(n-1)).

*Empirically.* The same question run through the actual engine on synthetic panels
with a known planted effect, counting how often it reaches a verdict of suggestive
or better. The two should agree, and where they do not the empirical number is the
one to believe, because it includes the engine's own conservatism, its block
bootstrap, and its multiple-testing adjustment.

The answer is uncomfortable, and it has a practical consequence worth acting on
today: **archive monthly, not quarterly.**

Measured through the real engine on 150-name panels, 16 trials per cell:

    rebalances   typical signal (IC 0.04)   strong signal (IC 0.06)
             4                          0%                       0%
             8                          0%                       0%
            12                         44%                      69%
            20                         50%                      88%
            40                         88%                     100%

At four and eight rebalances the detection rate is zero by construction: the
engine's verdict tiers refuse to say more than "weak" below twelve independent
periods, whatever the interval does. The confidence interval excluded zero in 38%
to 69% of those runs, and the engine still declined to call it, which is the
behaviour you want from a tool that will otherwise be quoted.

Quarterly archiving reaches twelve rebalances in three years and forty in ten.
Monthly archiving reaches twelve in one year and forty in three and a bit. The
fundamentals only move quarterly, but the score does not: prices, drawdowns and
estimate revisions all move continuously, and they carry most of the score's
realised variance. Monthly snapshots with a one-month forward return are
independent observations, so they count fully.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

__all__ = ["detectable_ic", "quarters_needed", "PowerRow", "power_table", "REFERENCE_EFFECTS",
           "MEASURED_POWER", "VERDICT_FLOOR_PERIODS", "archiving_cadence_advice"]

MEASURED_POWER = {
    # rebalances -> {planted rank IC -> detection rate}, 16 trials each through
    # backtest.run_backtest on synthetic panels of 150 names. Recorded rather than
    # asserted; tests/test_power.py re-measures a cell and fails if it drifts.
    4: {0.04: 0.00, 0.06: 0.00},
    8: {0.04: 0.00, 0.06: 0.00},
    12: {0.04: 0.44, 0.06: 0.69},
    20: {0.04: 0.50, 0.06: 0.88},
    40: {0.04: 0.88, 0.06: 1.00},
}

VERDICT_FLOOR_PERIODS = 12
"""Below this many independent periods the engine will not say more than "weak".

It is a policy, not a statistic, and it is why the measured detection rate at four
and eight rebalances is exactly zero while the interval excluded zero in a third to
two thirds of those same runs.
"""

Z_ALPHA = 1.959963985  # two-sided 5%
Z_POWER = 0.8416212336  # 80%
NEEDED_SIGMAS = Z_ALPHA + Z_POWER  # about 2.80

REFERENCE_EFFECTS: Dict[str, float] = {
    "a strong published cross-sectional signal": 0.06,
    "a typical published signal": 0.04,
    "a weak but real signal": 0.02,
    "nothing": 0.0,
}
"""Rank ICs to size the question against.

Published cross-sectional equity signals cluster between 0.02 and 0.06. Anything
much above that, sustained, is usually a bug: ``backtest.SUSPICIOUS_IC`` flags it.
"""


def se_of_mean_ic(n_names: int, k_rebalances: int) -> Optional[float]:
    """Standard error of the mean rank IC, under the null.

    The per-date standard error of a rank correlation on ``n`` pairs is about
    1/sqrt(n-1); averaging ``k`` independent dates divides it by sqrt(k). Note the
    word independent: overlapping holding periods do not count as separate
    observations, which is why the backtest engine reports an effective count
    alongside the raw one.
    """
    if n_names < 3 or k_rebalances < 1:
        return None
    return 1.0 / math.sqrt(k_rebalances * (n_names - 1))


def detectable_ic(n_names: int, k_rebalances: int) -> Optional[float]:
    """The smallest true rank IC detectable at 5% with 80% power."""
    se = se_of_mean_ic(n_names, k_rebalances)
    return None if se is None else NEEDED_SIGMAS * se


def quarters_needed(true_ic: float, n_names: int = 150, cap: int = 200) -> Optional[int]:
    """Quarterly rebalances needed before an effect of this size becomes visible."""
    if true_ic <= 0:
        return None
    for k in range(1, cap + 1):
        d = detectable_ic(n_names, k)
        if d is not None and d <= abs(true_ic):
            return k
    return None


@dataclass(frozen=True)
class PowerRow:
    quarters: int
    years: float
    n_names: int
    detectable_ic: float
    detects: Dict[str, bool]
    empirical_detection_rate: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "quarters": self.quarters,
            "years": round(self.years, 2),
            "n_names": self.n_names,
            "detectable_ic": round(self.detectable_ic, 4),
            "detects": self.detects,
            "empirical_detection_rate": (
                None if self.empirical_detection_rate is None else round(self.empirical_detection_rate, 3)
            ),
        }


def power_table(n_names: int = 150, quarters: Sequence[int] = (1, 2, 4, 8, 12, 20, 40, 80)) -> List[PowerRow]:
    out: List[PowerRow] = []
    for k in quarters:
        d = detectable_ic(n_names, k)
        if d is None:
            continue
        out.append(PowerRow(
            quarters=k,
            years=k / 4.0,
            n_names=n_names,
            detectable_ic=d,
            detects={name: (ic >= d) for name, ic in REFERENCE_EFFECTS.items() if ic > 0},
        ))
    return out


def archiving_cadence_advice(n_names: int = 150) -> Dict[str, object]:
    """What to do about it, in the terms the decision is actually made in."""
    per_year = {"quarterly": 4, "monthly": 12}
    out: Dict[str, object] = {}
    for cadence, k_per_year in per_year.items():
        rows = {}
        for label, ic in REFERENCE_EFFECTS.items():
            if ic <= 0:
                continue
            k = quarters_needed(ic, n_names=n_names)
            rows[label] = None if k is None else round(k / k_per_year, 1)
        out[cadence] = {
            "snapshots_per_year": k_per_year,
            "years_to_first_verdict": round(VERDICT_FLOOR_PERIODS / k_per_year, 1),
            "years_to_detect": rows,
        }
    out["recommendation"] = (
        "Archive monthly rather than quarterly. The engine will not return a verdict above 'weak' "
        f"until {VERDICT_FLOOR_PERIODS} independent periods, which is three years of quarterly "
        "snapshots and one year of monthly ones. Fundamentals only move quarterly, but the score "
        "does not: prices, drawdowns and estimate revisions move continuously and carry most of its "
        "realised variance. Pair monthly snapshots with a one-month forward return so the "
        "observations stay independent; a one-month snapshot with a twelve-month holding period is "
        "one observation wearing twelve hats."
    )
    out["caveat"] = (
        "All of this assumes the score has a constant true effect and that the names are "
        "independent of each other, and neither is true. Stocks in the same sector move together, "
        "which makes the effective cross-sectional sample smaller than 150 and every number here "
        "optimistic."
    )
    return out


def measure_empirically(quarters: int, *, true_alpha: float, n_names: int = 150,
                        trials: int = 30, base_seed: int = 4242) -> float:
    """Run the real engine on panels with a planted effect and count the hits.

    Slower than the formula and more trustworthy, because it inherits everything the
    engine actually does: the block bootstrap, the minimum names per date, the
    verdict thresholds and the multiple-testing widening.
    """
    from .backtest import run_backtest
    from .synthetic import PanelSpec, make_panel

    hits = 0
    for i in range(trials):
        panel, _ = make_panel(PanelSpec(
            n_dates=quarters, n_names=n_names, alpha=true_alpha, seed=base_seed + i, months_between=3))
        r = run_backtest(panel, label="power", variant="power", horizon_days=63,
                         rebalance_spacing_days=63)
        if r.verdict in ("suggestive", "supported"):
            hits += 1
    return hits / trials
