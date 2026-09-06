"""What can honestly be said about a score from a single snapshot.

There is exactly one dated cross section in this repo (2026-09-04) and no price
history, so a backtest is impossible: nothing here observes what happened next.
That is stated plainly rather than worked around.

What a single cross section *can* answer is structural, and those questions are
worth asking before any historical test:

*Am I double counting?* Forward P/E against its own median and EV/EBITDA against
its own median are two ways of asking one question. If they correlate at 0.9 then
the 21% of weight they share is really 21% on one idea, and the score is more
concentrated than its weights table suggests.

*How many independent bets is this really?* The effective number of signals, from
the eigenvalues of the component correlation matrix. A score with thirteen
components and an effective rank of three is a three-factor model wearing a
costume.

*What is actually driving the ranking?* The share of realised score variance each
component contributes, which is not the same as its nominal weight: a component
with a small weight and a wide spread can matter more than a large weight on
something that barely varies.

*Where does the score disagree with the process that produced the data?* If the
new score and the existing quality screen rank names very differently, one of them
is wrong about something, and that is worth knowing before either is trusted.

None of this is evidence that the score predicts returns. It cannot be. It is
evidence about what the score is.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .local import TickerRecord
from .score import ScoreBreakdown, components_for
from .stats import mean, spearman, stdev

__all__ = ["ComponentPair", "ScoreDiagnostics", "diagnose"]


@dataclass(frozen=True)
class ComponentPair:
    a: str
    b: str
    rho: float
    n: int
    combined_weight: float

    @property
    def redundant(self) -> bool:
        return abs(self.rho) >= 0.75


@dataclass(frozen=True)
class ScoreDiagnostics:
    variant: str
    n_names: int
    component_correlations: List[ComponentPair]
    redundant_pairs: List[ComponentPair]
    effective_signal_count: Optional[float]
    variance_share: Dict[str, float]
    nominal_weight: Dict[str, float]
    coverage_distribution: Dict[str, float]
    thinly_evidenced: List[str]
    agreement_with_quality_screen: Optional[float]
    biggest_disagreements: List[Dict[str, object]]
    notes: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "variant": self.variant,
            "n_names": self.n_names,
            "component_correlations": [
                {"a": p.a, "b": p.b, "rho": round(p.rho, 4), "n": p.n,
                 "combined_weight": round(p.combined_weight, 4), "redundant": p.redundant}
                for p in self.component_correlations
            ],
            "redundant_pairs": [
                {"a": p.a, "b": p.b, "rho": round(p.rho, 4), "combined_weight": round(p.combined_weight, 4)}
                for p in self.redundant_pairs
            ],
            "effective_signal_count": (
                None if self.effective_signal_count is None else round(self.effective_signal_count, 2)
            ),
            "variance_share": {k: round(v, 4) for k, v in self.variance_share.items()},
            "nominal_weight": {k: round(v, 4) for k, v in self.nominal_weight.items()},
            "coverage_distribution": {k: round(v, 3) for k, v in self.coverage_distribution.items()},
            "thinly_evidenced": self.thinly_evidenced,
            "agreement_with_quality_screen": (
                None if self.agreement_with_quality_screen is None else round(self.agreement_with_quality_screen, 4)
            ),
            "biggest_disagreements": self.biggest_disagreements,
            "notes": self.notes,
        }


def _eigenvalues_symmetric(m: List[List[float]], iterations: int = 400) -> List[float]:
    """Eigenvalues of a small symmetric matrix by the unshifted QR algorithm.

    numpy is available, but this runs on a matrix of at most fourteen rows and
    keeping it here means the diagnostics module has no array dependency and its
    behaviour is inspectable in one screen.
    """
    n = len(m)
    if n == 0:
        return []
    a = [row[:] for row in m]
    for _ in range(iterations):
        q, r = _qr(a)
        a = _matmul(r, q)
    return sorted((a[i][i] for i in range(n)), reverse=True)


def _qr(a: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
    """Gram-Schmidt. Fine at this size; not for anything large or ill-conditioned."""
    n = len(a)
    cols = [[a[i][j] for i in range(n)] for j in range(n)]
    q_cols: List[List[float]] = []
    r = [[0.0] * n for _ in range(n)]
    for j in range(n):
        v = cols[j][:]
        for i, qi in enumerate(q_cols):
            proj = sum(qi[k] * cols[j][k] for k in range(n))
            r[i][j] = proj
            for k in range(n):
                v[k] -= proj * qi[k]
        norm = math.sqrt(sum(x * x for x in v))
        r[j][j] = norm
        q_cols.append([x / norm for x in v] if norm > 1e-12 else [0.0] * n)
    q = [[q_cols[j][i] for j in range(n)] for i in range(n)]
    return q, r


def _matmul(x: List[List[float]], y: List[List[float]]) -> List[List[float]]:
    n = len(x)
    return [[sum(x[i][k] * y[k][j] for k in range(n)) for j in range(n)] for i in range(n)]


def diagnose(
    records: Sequence[TickerRecord],
    scores: Dict[str, ScoreBreakdown],
    *,
    variant: str = "quality_value",
) -> ScoreDiagnostics:
    comps = components_for(variant)
    keys = [c.key for c in comps]
    total_weight = sum(c.weight for c in comps)
    weight_of = {c.key: c.weight / total_weight for c in comps}

    tickers = [r.ticker for r in records if r.ticker in scores]
    zcols: Dict[str, List[Optional[float]]] = {k: [scores[t].z.get(k) for t in tickers] for k in keys}

    # Pairwise rank correlation between components.
    pairs: List[ComponentPair] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            rho, n = spearman(zcols[a], zcols[b])
            if rho is not None:
                pairs.append(ComponentPair(a, b, rho, n, weight_of[a] + weight_of[b]))
    pairs.sort(key=lambda p: -abs(p.rho))
    redundant = [p for p in pairs if p.redundant]

    # Effective number of independent signals, from the eigenvalues of the
    # correlation matrix. exp(entropy of the normalised eigenvalues) is the usual
    # participation-ratio style measure and it degrades gracefully.
    corr = [[1.0 if a == b else 0.0 for b in keys] for a in keys]
    lookup = {(p.a, p.b): p.rho for p in pairs}
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i == j:
                continue
            rho = lookup.get((a, b), lookup.get((b, a)))
            corr[i][j] = rho if rho is not None else 0.0
    eff: Optional[float] = None
    try:
        eig = [max(e, 0.0) for e in _eigenvalues_symmetric(corr)]
        s = sum(eig)
        if s > 0:
            p = [e / s for e in eig if e / s > 1e-12]
            entropy = -sum(x * math.log(x) for x in p)
            eff = math.exp(entropy)
    except (ValueError, ZeroDivisionError):
        eff = None

    # Share of realised score variance, as a covariance decomposition.
    #
    # The first version divided each component's own variance by the sum of those
    # variances. That denominator is not the variance of the score: the score is a
    # sum of correlated components, so Var(total) = sum of variances plus twice the
    # covariances, and on this universe the two differ by a third. It also could
    # never go negative, when a component that moves against the rest genuinely
    # subtracts from the spread. Cov(component, total) / Var(total) is the standard
    # decomposition for a sum: it sums to exactly one and it can be negative.
    totals = [scores[t].score for t in tickers]
    mean_total = mean(totals) or 0.0
    var_total = (stdev(totals) or 0.0) ** 2
    variance_share: Dict[str, float] = {}
    if var_total > 0 and len(tickers) > 1:
        n = len(tickers)
        for k in keys:
            col = [scores[t].contributions.get(k, 0.0) for t in tickers]
            mk = mean(col) or 0.0
            cov = sum((c - mk) * (tt - mean_total) for c, tt in zip(col, totals)) / (n - 1)
            variance_share[k] = cov / var_total
    else:
        variance_share = {k: 0.0 for k in keys}

    covs = [scores[t].coverage for t in tickers]
    coverage_distribution = {
        "min": min(covs) if covs else 0.0,
        "p10": sorted(covs)[max(0, int(0.10 * (len(covs) - 1)))] if covs else 0.0,
        "median": sorted(covs)[len(covs) // 2] if covs else 0.0,
        "mean": mean(covs) or 0.0,
        "max": max(covs) if covs else 0.0,
    }
    thin = sorted(t for t in tickers if scores[t].thinly_evidenced)

    # How much does this reorder the existing quality screen?
    q = [r.quality.total for r in records if r.ticker in scores]
    s_new = [scores[r.ticker].score for r in records if r.ticker in scores]
    agreement, _ = spearman(q, s_new)

    disagreements: List[Dict[str, object]] = []
    ranked_old = {t: i for i, t in enumerate(sorted(tickers, key=lambda t: -(next(
        (r.quality.total or 0.0) for r in records if r.ticker == t))))}
    ranked_new = {t: i for i, t in enumerate(sorted(tickers, key=lambda t: -scores[t].score))}
    for t in tickers:
        gap = ranked_old[t] - ranked_new[t]
        disagreements.append({"ticker": t, "quality_rank": ranked_old[t] + 1,
                              "score_rank": ranked_new[t] + 1, "moved": gap})
    disagreements.sort(key=lambda d: -abs(d["moved"]))
    disagreements = disagreements[:12]

    notes: List[str] = [
        "This is a single cross section dated 2026-09-04. Nothing here observes what happened next, "
        "so none of it is evidence that the score predicts returns. It is evidence about what the "
        "score is made of.",
    ]
    if redundant:
        worst = redundant[0]
        notes.append(
            f"{len(redundant)} component pair(s) correlate above 0.75. The worst is {worst.a} and "
            f"{worst.b} at {worst.rho:+.2f}, which together carry "
            f"{worst.combined_weight:.0%} of the weight on what is largely one idea."
        )
    if eff is not None and eff < len(keys) * 0.6:
        notes.append(
            f"Thirteen named components behave like about {eff:.1f} independent ones. The weights "
            "table overstates how diversified the score is."
        )
    negative = sorted((v, k) for k, v in variance_share.items() if v < -0.01)
    if negative:
        v, k = negative[0]
        notes.append(
            f"{k} contributes {v:.0%} of realised score variance, which is negative: it moves "
            "against the rest of the score often enough to narrow the spread rather than widen it. "
            "That is possible because this is a covariance decomposition, not a sum of variances."
        )
    over = sorted(((variance_share[k] - weight_of[k], k) for k in keys), reverse=True)
    if over and over[0][0] > 0.08:
        d, k = over[0]
        notes.append(
            f"{k} drives {variance_share[k]:.0%} of realised score variance on a nominal weight of "
            f"{weight_of[k]:.0%}. It spreads names out more than its weight implies."
        )
    if agreement is not None:
        notes.append(
            f"Rank agreement with the existing quality screen is {agreement:+.2f}. "
            + (
                "The two mostly agree, so the new score is a refinement rather than a different view."
                if agreement > 0.6
                else "The two disagree substantially. At least one of them is wrong about something."
            )
        )
    if thin:
        notes.append(
            f"{len(thin)} name(s) are ranked on less than {int(0.60*100)}% of the score's weight. "
            "Their position is not really a ranking."
        )

    return ScoreDiagnostics(
        variant=variant,
        n_names=len(tickers),
        component_correlations=pairs[:20],
        redundant_pairs=redundant,
        effective_signal_count=eff,
        variance_share=variance_share,
        nominal_weight=weight_of,
        coverage_distribution=coverage_distribution,
        thinly_evidenced=thin,
        agreement_with_quality_screen=agreement,
        biggest_disagreements=disagreements,
        notes=notes,
    )
