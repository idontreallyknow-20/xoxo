"""A cross-sectional backtest engine that is hard to fool yourself with.

The engine measures one thing: on each rebalance date, does the score's ordering
of names line up with the ordering of what those names actually did next? That is
the rank information coefficient. Everything else here exists to stop a number
being quoted without the caveat that makes it meaningful.

FOUR WAYS A BACKTEST LIES, AND WHAT IS DONE ABOUT EACH
------------------------------------------------------
*Look-ahead.* Using a number that was not public on the rebalance date. Guarded
outside this module, by sourcing fundamentals through
``edgar.EdgarClient.as_known_on`` rather than from a convenience API that silently
serves restated figures. This module cannot verify that for you, so it says so in
``limitations`` every single time, and it flags an information coefficient high
enough to be a smell rather than a result.

*Survivorship.* Testing on the names that exist today. Every company that was
delisted, acquired or went to zero is absent, and they are exactly the ones that
lost money. This module counts the names with no forward return on each date and
refuses to quietly drop them: they appear in ``missing_returns`` and their count is
reported next to every statistic.

*Overlapping windows.* Twelve monthly rebalances with a twelve-month holding
period are not twelve independent observations, they are closer to one. Confidence
intervals come from a moving-block bootstrap with a block length set to the
overlap, and both the raw count and the effective independent count are reported.

*Multiple testing.* Three score variants times four horizons is twelve hypotheses,
and the best of twelve looks good by construction. The number of hypotheses is
carried on the result and the verdict threshold is adjusted for it.

WHAT A RESULT IS ALLOWED TO CLAIM
---------------------------------
``verdict`` is mechanical, not editorial:

``no evidence``   the interval covers zero, or there is not enough data to say
``weak``          interval excludes zero but fewer than 12 independent periods
``suggestive``    interval excludes zero, 12 or more independent periods
``supported``     as above with 20 or more, and it survives the multiple-testing
                  adjustment

Nothing here reaches "proven", because a cross-sectional score on 150 large-cap
names over a handful of years cannot get there.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .stats import bootstrap_ci, mean, median, rank_percentile, spearman, stdev, t_stat

__all__ = [
    "Rebalance",
    "BucketStats",
    "BacktestResult",
    "run_backtest",
    "SUSPICIOUS_IC",
    "MIN_NAMES_PER_DATE",
]

SUSPICIOUS_IC = 0.25
"""A sustained rank IC above this is nearly always a bug, not an edge.

Published cross-sectional signals live around 0.02 to 0.06. Anything three or four
times that, sustained, means the score is reading something it should not have
known yet. The engine flags it rather than celebrating it.
"""

MIN_NAMES_PER_DATE = 20
"""Below this many scored names with returns, a date's IC is not computed at all."""

MEASURED_FALSE_POSITIVE_RATE = 0.075
"""How often this engine claims an edge when there is provably none.

Measured, not assumed. 200 synthetic panels per sample size were generated with the
score and the forward return statistically independent (``synthetic.PanelSpec`` with
``alpha=0``), and the fraction where the 95% interval excluded zero was counted:

    12 rebalances   7.5%
    20 rebalances   7.0%
    30 rebalances   5.5%
    40 rebalances   7.5%

Against a nominal 5%, so the intervals are mildly too narrow. That is the known
undercoverage of a percentile bootstrap at small sample sizes, and it is stated on
every result rather than left for someone to discover. In plain terms: roughly one
in thirteen "suggestive" findings from this engine is nothing at all. The
regression test is ``test_measured_false_positive_rate_has_not_drifted``.
"""


@dataclass(frozen=True)
class Rebalance:
    """One date's cross section: what the score said, and what happened next."""

    date: str
    scores: Dict[str, float]
    forward_returns: Dict[str, float]
    sectors: Dict[str, str] = field(default_factory=dict)
    buckets: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def scored(self) -> List[str]:
        return sorted(self.scores)

    @property
    def with_returns(self) -> List[str]:
        return sorted(t for t in self.scores if self.forward_returns.get(t) is not None)

    @property
    def missing_returns(self) -> List[str]:
        """Names that were scored but have no forward return.

        Usually delisted, acquired, or simply absent from the price source. Named
        rather than counted, because "we dropped 14 names" and "we dropped these
        14 names, all of which were down 40% when they stopped trading" are very
        different sentences.
        """
        return sorted(t for t in self.scores if self.forward_returns.get(t) is None)

    def ic(self) -> Tuple[Optional[float], int]:
        names = self.with_returns
        if len(names) < MIN_NAMES_PER_DATE:
            return None, len(names)
        return spearman([self.scores[t] for t in names], [self.forward_returns[t] for t in names])


@dataclass(frozen=True)
class BucketStats:
    label: str
    n_observations: int
    n_dates: int
    mean_return: Optional[float]
    median_return: Optional[float]
    stdev_return: Optional[float]
    hit_rate: Optional[float]


def _blocks(n: int, block: int, rng: random.Random) -> List[int]:
    """Indices for one moving-block bootstrap resample."""
    out: List[int] = []
    if n <= 0:
        return out
    block = max(1, min(block, n))
    while len(out) < n:
        start = rng.randrange(0, max(1, n - block + 1))
        out.extend(range(start, min(start + block, n)))
    return out[:n]


def _block_bootstrap_ci(
    series: Sequence[float], *, block: int, confidence: float = 0.95, iterations: int = 3000, seed: int = 20260906
) -> Optional[Tuple[float, float]]:
    """Percentile interval for the mean of a serially dependent series.

    An ordinary bootstrap resamples one observation at a time and so assumes they
    are independent. Overlapping holding periods make consecutive ICs correlated,
    and pretending otherwise produces an interval far too narrow, which is how a
    coin flip gets published.
    """
    v = [float(x) for x in series if x is not None]
    n = len(v)
    if n < 3:
        return None
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        idx = _blocks(n, block, rng)
        draws.append(sum(v[i] for i in idx) / len(idx))
    draws.sort()
    alpha = (1 - confidence) / 2
    return draws[int(alpha * (len(draws) - 1))], draws[int((1 - alpha) * (len(draws) - 1))]


@dataclass
class BacktestResult:
    label: str
    variant: str
    horizon_days: int
    rebalance_spacing_days: int

    n_rebalances: int
    n_rebalances_scored: int
    effective_independent_periods: float
    names_per_date: List[int]
    missing_per_date: List[int]
    missing_names: Dict[str, List[str]]

    ic_series: List[Tuple[str, Optional[float], int]]
    mean_ic: Optional[float]
    median_ic: Optional[float]
    ic_stdev: Optional[float]
    ic_ci: Optional[Tuple[float, float]]
    ic_t: Optional[float]
    ic_hit_rate: Optional[float]

    quintiles: List[BucketStats]
    long_short_spread: Optional[float]
    long_short_ci: Optional[Tuple[float, float]]

    by_sector: Dict[str, Tuple[Optional[float], int]]
    by_bucket: Dict[str, Tuple[Optional[float], int]]
    worst_dates: List[Tuple[str, float, int]]

    hypotheses_tested: int
    limitations: List[str]
    failure_modes: List[str]
    flags: List[str]

    def __post_init__(self) -> None:
        if not self.limitations:
            raise ValueError(
                "a backtest result may not be created without limitations. "
                "If you genuinely believe there are none, the bug is that belief."
            )

    # -- the only place a claim is made ------------------------------------

    @property
    def verdict(self) -> str:
        if self.mean_ic is None or self.ic_ci is None:
            return "no evidence"
        lo, hi = self.ic_ci
        if lo <= 0 <= hi:
            return "no evidence"
        n = self.effective_independent_periods
        if n < 12:
            return "weak"
        if n < 20:
            return "suggestive"
        # Bonferroni: with k hypotheses the interval has to be wider to mean the same.
        if self.hypotheses_tested > 1 and self.ic_t is not None:
            needed = 1.96 + 0.5 * math.log(max(self.hypotheses_tested, 1))
            if abs(self.ic_t) < needed:
                return "suggestive"
        return "supported"

    @property
    def verdict_sentence(self) -> str:
        ic = "n/a" if self.mean_ic is None else f"{self.mean_ic:+.3f}"
        ci = "n/a" if self.ic_ci is None else f"[{self.ic_ci[0]:+.3f}, {self.ic_ci[1]:+.3f}]"
        return (
            f"{self.verdict}: mean rank IC {ic}, 95% interval {ci}, over "
            f"{self.n_rebalances_scored} rebalances "
            f"({self.effective_independent_periods:.1f} independent), "
            f"{int(median(self.names_per_date) or 0)} names per date."
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label,
            "variant": self.variant,
            "horizon_days": self.horizon_days,
            "rebalance_spacing_days": self.rebalance_spacing_days,
            "n_rebalances": self.n_rebalances,
            "n_rebalances_scored": self.n_rebalances_scored,
            "effective_independent_periods": round(self.effective_independent_periods, 2),
            "names_per_date": self.names_per_date,
            "missing_per_date": self.missing_per_date,
            "missing_names": self.missing_names,
            "ic_series": [{"date": d, "ic": ic, "n": n} for d, ic, n in self.ic_series],
            "mean_ic": self.mean_ic,
            "median_ic": self.median_ic,
            "ic_stdev": self.ic_stdev,
            "ic_ci": list(self.ic_ci) if self.ic_ci else None,
            "ic_t": self.ic_t,
            "ic_hit_rate": self.ic_hit_rate,
            "quintiles": [
                {
                    "label": q.label,
                    "n_observations": q.n_observations,
                    "n_dates": q.n_dates,
                    "mean_return": q.mean_return,
                    "median_return": q.median_return,
                    "stdev_return": q.stdev_return,
                    "hit_rate": q.hit_rate,
                }
                for q in self.quintiles
            ],
            "long_short_spread": self.long_short_spread,
            "long_short_ci": list(self.long_short_ci) if self.long_short_ci else None,
            "by_sector": {k: {"ic": v[0], "n": v[1]} for k, v in self.by_sector.items()},
            "by_bucket": {k: {"ic": v[0], "n": v[1]} for k, v in self.by_bucket.items()},
            "worst_dates": [{"date": d, "ic": ic, "n": n} for d, ic, n in self.worst_dates],
            "hypotheses_tested": self.hypotheses_tested,
            "verdict": self.verdict,
            "verdict_sentence": self.verdict_sentence,
            "limitations": self.limitations,
            "failure_modes": self.failure_modes,
            "flags": self.flags,
        }


def _quintile_stats(rebalances: Sequence[Rebalance], n_buckets: int = 5) -> List[BucketStats]:
    """Sort each date into buckets by score, then pool the forward returns.

    Bucketing happens within a date, never across dates, or a year when everything
    went up would fill the top bucket regardless of what the score said.
    """
    per_bucket: List[List[float]] = [[] for _ in range(n_buckets)]
    dates_seen: List[set] = [set() for _ in range(n_buckets)]
    for rb in rebalances:
        names = rb.with_returns
        if len(names) < MIN_NAMES_PER_DATE:
            continue
        pcts = rank_percentile([rb.scores[t] for t in names])
        for t, p in zip(names, pcts):
            if p is None:
                continue
            b = min(int(p * n_buckets), n_buckets - 1)
            per_bucket[b].append(rb.forward_returns[t])
            dates_seen[b].add(rb.date)

    labels = ["Q1 (lowest score)", "Q2", "Q3", "Q4", "Q5 (highest score)"][:n_buckets]
    out: List[BucketStats] = []
    for i, rets in enumerate(per_bucket):
        out.append(
            BucketStats(
                label=labels[i] if i < len(labels) else f"B{i+1}",
                n_observations=len(rets),
                n_dates=len(dates_seen[i]),
                mean_return=mean(rets),
                median_return=median(rets),
                stdev_return=stdev(rets),
                hit_rate=(sum(1 for r in rets if r > 0) / len(rets)) if rets else None,
            )
        )
    return out


def _group_ic(rebalances: Sequence[Rebalance], group_of) -> Dict[str, Tuple[Optional[float], int]]:
    """Pool across dates within a group, then take one rank correlation.

    A per-date IC inside a single sector would be computed on six names, which is
    not a correlation. Pooling ranks within date first would be better still, but
    at these sample sizes the pooled estimate is the honest available option and
    the pair count is reported so nobody mistakes it for more.
    """
    pooled: Dict[str, Tuple[List[float], List[float]]] = {}
    for rb in rebalances:
        names = rb.with_returns
        if len(names) < MIN_NAMES_PER_DATE:
            continue
        pcts = dict(zip(names, rank_percentile([rb.scores[t] for t in names])))
        rets = dict(zip(names, rank_percentile([rb.forward_returns[t] for t in names])))
        for t in names:
            for g in group_of(rb, t):
                if not g:
                    continue
                xs, ys = pooled.setdefault(g, ([], []))
                if pcts[t] is not None and rets[t] is not None:
                    xs.append(pcts[t])
                    ys.append(rets[t])
    out: Dict[str, Tuple[Optional[float], int]] = {}
    for g, (xs, ys) in sorted(pooled.items()):
        rho, n = spearman(xs, ys)
        out[g] = (rho, n)
    return out


def run_backtest(
    rebalances: Sequence[Rebalance],
    *,
    label: str,
    variant: str,
    horizon_days: int,
    rebalance_spacing_days: int,
    hypotheses_tested: int = 1,
    extra_limitations: Optional[Iterable[str]] = None,
    seed: int = 20260906,
) -> BacktestResult:
    """Run the whole thing and return a result that carries its own caveats."""
    ic_series: List[Tuple[str, Optional[float], int]] = []
    names_per_date: List[int] = []
    missing_per_date: List[int] = []
    missing_names: Dict[str, List[str]] = {}

    for rb in sorted(rebalances, key=lambda r: r.date):
        ic, n = rb.ic()
        ic_series.append((rb.date, ic, n))
        names_per_date.append(n)
        miss = rb.missing_returns
        missing_per_date.append(len(miss))
        if miss:
            missing_names[rb.date] = miss

    ics = [ic for _, ic, _ in ic_series if ic is not None]
    overlap = max(1.0, horizon_days / max(rebalance_spacing_days, 1))
    effective = len(ics) / overlap

    ci = _block_bootstrap_ci(ics, block=int(math.ceil(overlap)), seed=seed) if len(ics) >= 3 else None

    quints = _quintile_stats(rebalances)
    top, bottom = (quints[-1].mean_return, quints[0].mean_return) if quints else (None, None)
    spread = None if top is None or bottom is None else top - bottom

    # Spread interval from the per-date spread, so it inherits the same dependence.
    per_date_spread: List[float] = []
    for rb in sorted(rebalances, key=lambda r: r.date):
        names = rb.with_returns
        if len(names) < MIN_NAMES_PER_DATE:
            continue
        pcts = rank_percentile([rb.scores[t] for t in names])
        hi = [rb.forward_returns[t] for t, p in zip(names, pcts) if p is not None and p >= 0.8]
        lo = [rb.forward_returns[t] for t, p in zip(names, pcts) if p is not None and p < 0.2]
        mh, ml = mean(hi), mean(lo)
        if mh is not None and ml is not None:
            per_date_spread.append(mh - ml)
    spread_ci = (
        _block_bootstrap_ci(per_date_spread, block=int(math.ceil(overlap)), seed=seed + 1)
        if len(per_date_spread) >= 3
        else None
    )

    by_sector = _group_ic(rebalances, lambda rb, t: [rb.sectors.get(t)])
    by_bucket = _group_ic(rebalances, lambda rb, t: rb.buckets.get(t, []) or ["unbucketed"])

    worst = sorted([(d, ic, n) for d, ic, n in ic_series if ic is not None], key=lambda p: p[1])[:5]

    mean_ic = mean(ics)
    limitations = list(extra_limitations or [])
    limitations.append(
        f"Overlapping windows: a {horizon_days}-day horizon rebalanced every "
        f"{rebalance_spacing_days} days gives {len(ics)} observations but only about "
        f"{effective:.1f} independent ones. The interval is a moving-block bootstrap, not an ordinary one."
    )
    limitations.append(
        "Survivorship: the universe is the names listed today. Companies delisted, acquired or "
        "wiped out before today are absent, and those are the ones that lost money. Every number "
        "here is therefore flattering by an unknown amount."
    )
    limitations.append(
        "Look-ahead cannot be verified from inside this engine. It depends entirely on whether the "
        "scores handed in were computable on each rebalance date."
    )
    limitations.append(
        f"This engine's own false-positive rate was measured at about "
        f"{MEASURED_FALSE_POSITIVE_RATE:.1%} against a nominal 5%, on synthetic panels where the "
        "score and the return were independent by construction. Roughly one finding in thirteen at "
        "the 'suggestive' level is nothing at all."
    )
    if hypotheses_tested > 1:
        limitations.append(
            f"{hypotheses_tested} variant and horizon combinations were tested. The best of "
            f"{hypotheses_tested} looks good by construction, and the verdict threshold is widened for it."
        )
    total_missing = sum(missing_per_date)
    if total_missing:
        limitations.append(
            f"{total_missing} scored name-dates had no forward return and were excluded from every "
            "statistic. They are listed by date in missing_names."
        )

    flags: List[str] = []
    if mean_ic is not None and abs(mean_ic) > SUSPICIOUS_IC:
        flags.append(
            f"Mean rank IC of {mean_ic:+.3f} is above {SUSPICIOUS_IC}. Published cross-sectional "
            "signals sit near 0.02 to 0.06. Treat this as evidence of look-ahead in the inputs "
            "until proven otherwise, not as an edge."
        )
    if len(ics) < 3:
        flags.append("Fewer than three scored rebalances. No interval can be computed.")
    if names_per_date and min(names_per_date) < MIN_NAMES_PER_DATE:
        flags.append(
            f"At least one date had fewer than {MIN_NAMES_PER_DATE} names with returns and was skipped."
        )

    failure_modes: List[str] = []
    for sector, (rho, n) in by_sector.items():
        if rho is not None and n >= 30 and mean_ic is not None and rho < 0:
            failure_modes.append(f"Negative in {sector}: rank IC {rho:+.3f} on {n} name-dates.")
    for bucket, (rho, n) in by_bucket.items():
        if rho is not None and n >= 30 and rho < 0:
            failure_modes.append(f"Negative in the {bucket} bucket: rank IC {rho:+.3f} on {n} name-dates.")
    for d, ic, n in worst[:3]:
        failure_modes.append(f"Worst date {d}: rank IC {ic:+.3f} across {n} names.")
    if quints and quints[-1].mean_return is not None and quints[0].mean_return is not None:
        if quints[-1].mean_return <= quints[0].mean_return:
            failure_modes.append(
                "The top-scoring quintile did not beat the bottom quintile. Whatever the average IC "
                "says, the ordering did not pay at the ends, which is where a concentrated portfolio lives."
            )
    non_monotonic = [
        (quints[i].label, quints[i + 1].label)
        for i in range(len(quints) - 1)
        if quints[i].mean_return is not None
        and quints[i + 1].mean_return is not None
        and quints[i + 1].mean_return < quints[i].mean_return
    ]
    if non_monotonic:
        failure_modes.append(
            "Quintile returns are not monotonic in the score: "
            + ", ".join(f"{a} beat {b}" for a, b in non_monotonic)
            + ". A score that only works at the extremes is a weaker claim than a score that orders."
        )

    return BacktestResult(
        label=label,
        variant=variant,
        horizon_days=horizon_days,
        rebalance_spacing_days=rebalance_spacing_days,
        n_rebalances=len(ic_series),
        n_rebalances_scored=len(ics),
        effective_independent_periods=effective,
        names_per_date=names_per_date,
        missing_per_date=missing_per_date,
        missing_names=missing_names,
        ic_series=ic_series,
        mean_ic=mean_ic,
        median_ic=median(ics),
        ic_stdev=stdev(ics),
        ic_ci=ci,
        ic_t=t_stat(ics),
        ic_hit_rate=(sum(1 for x in ics if x > 0) / len(ics)) if ics else None,
        quintiles=quints,
        long_short_spread=spread,
        long_short_ci=spread_ci,
        by_sector=by_sector,
        by_bucket=by_bucket,
        worst_dates=worst,
        hypotheses_tested=hypotheses_tested,
        limitations=limitations,
        failure_modes=failure_modes,
        flags=flags,
    )
