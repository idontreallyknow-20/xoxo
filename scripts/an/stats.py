"""Small statistics helpers, written out rather than imported.

scipy is not installed and is not worth adding for six functions. Everything here
is pure Python over lists of ``Optional[float]``, because the whole point of the
data layer is that missing stays missing, and numpy would happily turn a ``None``
into a ``nan`` and a ``nan`` into a silent mis-sort.
"""
from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "mean",
    "stdev",
    "median",
    "percentile",
    "winsorize",
    "mad",
    "zscores",
    "rank_percentile",
    "spearman",
    "bootstrap_ci",
    "t_stat",
]

Num = Optional[float]


def _clean(xs: Iterable[Num]) -> List[float]:
    return [float(x) for x in xs if x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))]


def mean(xs: Iterable[Num]) -> Optional[float]:
    v = _clean(xs)
    return sum(v) / len(v) if v else None


def stdev(xs: Iterable[Num], *, sample: bool = True) -> Optional[float]:
    v = _clean(xs)
    n = len(v)
    if n < (2 if sample else 1):
        return None
    m = sum(v) / n
    ss = sum((x - m) ** 2 for x in v)
    return math.sqrt(ss / (n - 1 if sample else n))


def median(xs: Iterable[Num]) -> Optional[float]:
    v = sorted(_clean(xs))
    if not v:
        return None
    n = len(v)
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2


def percentile(xs: Iterable[Num], q: float) -> Optional[float]:
    """Linear interpolation between order statistics, the numpy default."""
    v = sorted(_clean(xs))
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    q = min(max(q, 0.0), 1.0)
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    frac = pos - lo
    return v[lo] * (1 - frac) + v[hi] * frac


def winsorize(xs: Sequence[Num], lower: float = 0.01, upper: float = 0.99) -> List[Num]:
    """Clip the tails at the given percentiles. ``None`` stays ``None``.

    Deliberately gentle. Clipping hard enough to always catch a lone outlier means
    clipping by position, and clipping by position destroys a distribution with a
    large point mass: twenty-eight names on the same value plus one high and one
    low, and the one-from-each-end rule wipes out the only variation there was.
    Outlier resistance is handled in :func:`zscores` by using a robust scale
    instead, which does not have that failure mode.
    """
    lo, hi = percentile(xs, lower), percentile(xs, upper)
    if lo is None or hi is None:
        return list(xs)
    out: List[Num] = []
    for x in xs:
        if x is None:
            out.append(None)
        else:
            out.append(min(max(float(x), lo), hi))
    return out


def mad(xs: Iterable[Num], *, scaled: bool = True) -> Optional[float]:
    """Median absolute deviation. Scaled by 1.4826 so it estimates sigma on normal data."""
    v = _clean(xs)
    if len(v) < 2:
        return None
    med = median(v)
    d = median([abs(x - med) for x in v])
    if d is None:
        return None
    return d * 1.4826 if scaled else d


def zscores(
    xs: Sequence[Num],
    *,
    winsor: Optional[Tuple[float, float]] = (0.01, 0.99),
    robust: bool = True,
    clip: Optional[float] = 4.0,
) -> List[Num]:
    """Standardise, keeping ``None`` as ``None``.

    Robust by default: centred on the median and scaled by the median absolute
    deviation rather than the mean and standard deviation. Cross-sectional
    financial data has fat tails, and one misparsed multiple of 100,000 in a set of
    thirty will own the standard deviation completely, compressing every real name
    to a z of roughly zero. The median and the MAD do not notice it.

    Falls back to the mean and standard deviation when the MAD is zero, which
    happens when more than half the sample sits on one value. Falls back again to
    all-zeros when there is no variation at all, which is the honest answer rather
    than a division by zero.

    The final clip bounds any remaining outlier's contribution to a few standard
    deviations, so a bad data point can tilt a ranking but cannot decide it.
    """
    vals = winsorize(xs, *winsor) if winsor else list(xs)
    if mean(vals) is None:
        return [None] * len(xs)

    if robust:
        centre = median(vals)
        scale = mad(vals)
    else:
        centre, scale = mean(vals), stdev(vals)
    if not scale:
        centre, scale = mean(vals), stdev(vals)
    if not scale:
        return [None if x is None else 0.0 for x in vals]

    out: List[Num] = []
    for x in vals:
        if x is None:
            out.append(None)
            continue
        z = (float(x) - centre) / scale
        if clip is not None:
            z = min(max(z, -clip), clip)
        out.append(z)
    return out


def rank_percentile(xs: Sequence[Num], *, ascending: bool = True) -> List[Num]:
    """Percentile rank in [0, 1], averaging ties. ``None`` stays ``None``."""
    idx = [(i, float(x)) for i, x in enumerate(xs) if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not idx:
        return [None] * len(xs)
    idx.sort(key=lambda p: p[1], reverse=not ascending)
    out: List[Num] = [None] * len(xs)
    n = len(idx)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and idx[j + 1][1] == idx[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1) if n > 1 else 0.5
        for k in range(i, j + 1):
            out[idx[k][0]] = pct
        i = j + 1
    return out


def spearman(xs: Sequence[Num], ys: Sequence[Num]) -> Tuple[Optional[float], int]:
    """Rank correlation and the pair count it was computed on.

    The count comes back with the number on purpose. A rank IC of 0.30 on eleven
    names is noise wearing a suit, and the only way to stop that being quoted as a
    result is to make the sample size impossible to drop.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    rx = rank_percentile([p[0] for p in pairs])
    ry = rank_percentile([p[1] for p in pairs])
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None, n
    return num / (dx * dy), n


def bootstrap_ci(
    xs: Sequence[float],
    *,
    confidence: float = 0.95,
    iterations: int = 2000,
    seed: int = 20260906,
    statistic=None,
) -> Optional[Tuple[float, float]]:
    """Percentile bootstrap interval, deterministic under a fixed seed.

    Seeded rather than random so a reported interval is reproducible: a confidence
    interval that changes every time you rerun the script is not evidence.
    """
    v = _clean(xs)
    if len(v) < 3:
        return None
    stat = statistic or (lambda s: sum(s) / len(s))
    rng = random.Random(seed)
    n = len(v)
    draws = []
    for _ in range(iterations):
        sample = [v[rng.randrange(n)] for _ in range(n)]
        draws.append(stat(sample))
    draws.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = draws[int(alpha * (len(draws) - 1))]
    hi = draws[int((1 - alpha) * (len(draws) - 1))]
    return lo, hi


def t_stat(xs: Sequence[float]) -> Optional[float]:
    """One-sample t against zero. Reported alongside the CI, never instead of it."""
    v = _clean(xs)
    if len(v) < 2:
        return None
    m = mean(v)
    s = stdev(v)
    if not s:
        return None
    return m / (s / math.sqrt(len(v)))
