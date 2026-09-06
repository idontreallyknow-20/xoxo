"""A cross-sectional stock score, and an honest account of what it rests on.

WHAT THIS IS
------------
A score that ranks names against each other on one date. It is not a return
forecast, it is not a probability, and a higher score does not mean a stock will
go up. It means the name looks better than its peers on a set of measurable
characteristics that have some published association with subsequent returns.
Whether that association survives in this universe, at this size, in this decade,
is a question for the backtest, not for this module.

THE COMPONENTS, AND WHY EACH ONE IS HERE
----------------------------------------
Every component is something the existing pipeline already measures. Nothing new
is invented; the contribution is combining them properly and being explicit about
which ones are contested.

*Profitability.* Return on invested capital and free cash flow margin, scored
within sector because a 12% ROIC in utilities and a 12% ROIC in software are not
the same statement.

A note on what this is and is not standing on. Fama and French's five-factor model
uses operating profitability, which is the closer relative of what is measured
here. Novy-Marx (2013) is often cited in this neighbourhood and should not be:
his result is specifically that *gross* profits over assets predicts returns
better than bottom-line measures, which is an argument against the measures used
here rather than for them. Gross margin is loaded and gross profitability is
computable, so adding it is on the backlog; until then the honest statement is
that ROIC and FCF margin are the profitability measures the existing screen
already produces, and they are correlated with the documented one rather than
being it.

*Stability.* The standard deviation of gross margin, inverted. A stable margin is
weak evidence of pricing power. This is the softest component here and it is
weighted accordingly.

*Balance sheet.* Net debt to EBITDA, inverted, with net cash treated as best.
Leverage does not predict returns on its own, but it predicts the size of the
drawdown when a thesis breaks, which is what actually costs money in a 12-name
portfolio. Included as risk control, not as alpha.

*Value.* EV/EBITDA and forward P/E against the company's own four-year median,
rather than against the market. Comparing a company to its own history sidesteps
the sector composition problem that makes raw P/E screens buy banks and sell
software forever. The cost is that four fiscal year-ends is a four-point median,
which is a thin basis for "cheap versus its own history", and twelve of the 150
have no usable history at all because their statements are in a different currency
from their listing.

The EV/EBITDA comparison carries the larger weight of the two, and that is a
correction rather than a preference. The upstream screen builds its historical P/E
as the price at each fiscal year end over that year's reported diluted EPS, which
is trailing, and compares it against a *forward* P/E from the Street. Where
earnings are expected to grow, forward is mechanically below trailing, so the P/E
column reads cheap by construction: across the 136 names carrying both, 85% print
a negative "vs median" on P/E with a median of -29%, against 50% and 0.0% on
EV/EBITDA where both sides are trailing. Worse, the bias tracks growth (rank
correlation -0.28 against revenue CAGR, versus -0.15 for EV/EBITDA), so the P/E
component is partly a second helping of the growth component. Both stay in, because
dropping a signal on a fixable labelling problem is its own error, but the
like-for-like one leads.

*Estimate revisions.* Change in the next fiscal year consensus EPS over 30 and 90
days, plus the breadth of analysts moving up versus down. Post-earnings drift and
revision momentum are among the more replicated effects, and they work at the
one-to-six-month horizon this portfolio actually rebalances on. This is the
component most likely to carry real information here.

*Capital discipline.* Change in diluted share count. Net share issuance is one of
the more robust characteristics in the literature (Daniel and Titman 2006, Pontiff
and Woodgate 2008), and it is cheap and clean to measure.

*Short interest.* Percent of float short, inverted, at a deliberately small weight.

The commonly cited result here is Boehmer, Jones and Zhang, and it is about
something else: they measure daily short-sale *order flow* from exchange data and
find that heavily shorted-*into* stocks underperform. What is available free is the
short interest *level*, a fortnightly snapshot of shares outstanding short, whose
own literature finds a real but considerably weaker effect. Using the level as a
proxy for a flow result is a substitution, not a citation, and it is why this
component carries 0.03 rather than something that would matter.

THE COMPONENT THAT IS DELIBERATELY CONTESTED
--------------------------------------------
Drawdown. The existing screen's cyclical-turn bucket buys names 35% or more below
their all-time high, which is a mean-reversion bet. The academic literature points
the other way over 6 to 12 months: proximity to the 52-week high predicts higher
returns, not lower (George and Hwang 2004), and buying deep drawdowns is closer to
catching a falling knife than to value investing.

Rather than pick a side and hide it, drawdown is not in the base score at all, and
two variants exist so the backtest can arbitrate:

``quality_value``   the base score, no price component
``with_momentum``   adds proximity to the 52-week high, positively signed
``with_reversal``   adds depth of drawdown, positively signed, which is the view
                    the existing cyclical-turn bucket already takes

If the backtest cannot separate them, that is the finding, and the honest response
is to keep the base score and stop pretending the price component adds anything.

MISSING DATA
------------
A missing component contributes zero. Standardisation here is robust, centred on
the median and scaled by the median absolute deviation, so zero is the sample
*median* rather than its mean: a name with gaps is pulled towards the middle of the
distribution, not towards its average. Those differ whenever a component is skewed,
which several are, so the effect of a gap is a small tilt away from the mean rather
than exactly nothing. Saying "the mean" would have been tidier and false.

Every scored name carries ``coverage``: the fraction of weight backed by a real
observation. A name at 0.55 coverage ranked 8th is not really ranked 8th and the
page says so.

WHAT THIS SCORE CANNOT DO
-------------------------
It cannot tell you a company is a fraud, that a patent expires in 2029, that the
CEO is leaving, or that the last quarter's guidance was quietly walked back. Those
live in the filings and the research notes. This is a sorter, not a judgement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .local import TickerRecord
from .stats import rank_percentile, zscores

__all__ = [
    "Component",
    "COMPONENTS",
    "VARIANTS",
    "ScoreBreakdown",
    "score_universe",
    "MIN_COVERAGE",
]

MIN_COVERAGE = 0.60  # below this, a name is ranked but flagged as thinly evidenced


@dataclass(frozen=True)
class Component:
    """One input to the score.

    ``higher_is_better`` says which direction is good. ``sector_neutral`` says the
    metric is compared inside its sector, because the level is a property of the
    industry rather than of the company.
    """

    key: str
    label: str
    weight: float
    extract: object  # Callable[[TickerRecord], Optional[float]]
    higher_is_better: bool = True
    sector_neutral: bool = False
    why: str = ""
    contested: bool = False


def _val(rec: TickerRecord, name: str) -> Optional[float]:
    return getattr(rec.valuation, name, None) if rec.valuation else None


def _nd_to_ebitda(rec: TickerRecord) -> Optional[float]:
    """Leverage, with net cash pinned to the good end rather than left missing.

    A company with net cash has no meaningful net-debt-to-EBITDA. Leaving it None
    would make the best balance sheets in the universe score neutral, which is
    backwards, so they are placed at -1.0x, below any real borrower.
    """
    f = rec.fundamentals
    if f.net_cash:
        return -1.0
    return f.nd_to_ebitda


def _fcf_quality(rec: TickerRecord) -> Optional[float]:
    """FCF margin, penalised when free cash flow was negative in more than one year.

    An average margin of 15% built out of two good years and two bad ones is not
    the same business as 15% every year, and the average alone cannot tell them apart.

    The penalty has to work in both directions. Multiplying by 0.4 shrinks a positive
    margin towards zero, which is a penalty; it also shrinks a *negative* margin
    towards zero, which is a reward, and the companies that burn cash in more than
    one year of four are exactly the ones with a negative average. The first version
    of this function did that, so the worst cash generators in the universe were
    handed the largest improvement. Dividing instead of multiplying below zero makes
    the penalty push the same way on both sides.
    """
    f = rec.fundamentals
    if f.fcf_margin_avg is None:
        return None
    if f.fcf_years and f.fcf_positive_years is not None:
        negatives = f.fcf_years - f.fcf_positive_years
        if negatives > 1:
            m = f.fcf_margin_avg
            return m * 0.4 if m > 0 else m / 0.4
    return f.fcf_margin_avg


def _revision_breadth(rec: TickerRecord) -> Optional[float]:
    """Analysts revising next year up minus down, scaled by the total revising.

    A raw count favours widely covered names. The ratio asks whether the analysts
    who moved agreed with each other, which is the part that carries information.
    """
    v = rec.valuation
    if not v:
        return None
    up, down = v.rev_up30_fy1, v.rev_down30_fy1
    if up is None and down is None:
        return None
    up, down = up or 0.0, down or 0.0
    total = up + down
    if total == 0:
        # Nobody moved. That is an absence of evidence, not evidence of balance, and
        # returning 0.0 fed it to the standardiser as a real observation: the name
        # was counted as fully covered and, because a raw 0.0 sits below the sample
        # median, the manufactured value became a small negative contribution.
        return None
    return (up - down) / total


def _pe_vs_own_history(rec: TickerRecord) -> Optional[float]:
    """Negative means cheaper than its own median. Only trusted with 3+ history points."""
    v = rec.valuation
    if not v or v.pe_vs_median is None:
        return None
    if not v.n_hist_years or v.n_hist_years < 3:
        return None
    return v.pe_vs_median


def _ev_vs_own_history(rec: TickerRecord) -> Optional[float]:
    v = rec.valuation
    if not v or v.ev_vs_median is None:
        return None
    if not v.n_hist_years or v.n_hist_years < 3:
        return None
    return v.ev_vs_median


def _proximity_to_52w_high(rec: TickerRecord) -> Optional[float]:
    """0 at the high, more negative the further below. Higher is better under momentum."""
    return _val(rec, "dd_52w")


def _drawdown_depth(rec: TickerRecord) -> Optional[float]:
    """Depth below the all-time high as a positive number. Higher is better under reversal."""
    dd = _val(rec, "dd_ath")
    return None if dd is None else -dd


COMPONENTS: Tuple[Component, ...] = (
    Component(
        "roic", "return on invested capital", 0.15,
        lambda r: r.fundamentals.roic_avg, True, sector_neutral=True,
        why="Operating profitability is the best-replicated quality characteristic. Sector-neutral because the level is an industry property.",
    ),
    Component(
        "roic_trend", "ROIC direction", 0.04,
        lambda r: r.fundamentals.roic_trend, True, sector_neutral=True,
        why="A rising return on capital is weak evidence the moat is widening rather than eroding.",
    ),
    Component(
        "fcf", "free cash flow margin", 0.11,
        _fcf_quality, True, sector_neutral=True,
        why="Cash conversion, discounted when it was negative in more than one of the four years.",
    ),
    Component(
        "growth", "revenue CAGR", 0.06,
        lambda r: r.fundamentals.rev_cagr, True, sector_neutral=True,
        why="Three-year revenue CAGR. Weak on its own, useful as a filter against melting ice cubes.",
    ),
    Component(
        "margin_stability", "gross margin stability", 0.04,
        lambda r: r.fundamentals.gm_std, False, sector_neutral=True,
        why="Inverted standard deviation of gross margin. Weak evidence of pricing power. The softest component here.",
    ),
    Component(
        "balance", "balance sheet", 0.06,
        _nd_to_ebitda, False,
        why="Net debt to EBITDA, net cash pinned to the good end. Risk control rather than alpha: it predicts drawdown size, not return.",
    ),
    Component(
        "value_ev", "EV/EBITDA vs own median", 0.13,
        _ev_vs_own_history, False,
        why="Cheap against its own four-year median, on an enterprise basis so leverage and cash do "
            "not distort it. Carries the larger of the two value weights because both sides of the "
            "comparison are trailing, which the P/E version cannot say.",
    ),
    Component(
        "value_pe", "forward P/E vs own median", 0.08,
        _pe_vs_own_history, False,
        why="The same idea on earnings, but the screen compares a forward P/E against a trailing "
            "median, which reads cheap by construction and does so more for faster-growing names. "
            "Kept at a reduced weight rather than dropped.",
    ),
    Component(
        "revisions_90d", "next-year EPS revision, 90 days", 0.11,
        lambda r: _val(r, "eps_fy1_chg_90d"), True,
        why="Estimate revision momentum is among the more replicated effects and works at the horizon this portfolio rebalances on.",
    ),
    Component(
        "revisions_30d", "next-year EPS revision, 30 days", 0.08,
        lambda r: _val(r, "eps_fy1_chg_30d"), True,
        why="The same signal, fresher and noisier.",
    ),
    Component(
        "revision_breadth", "analyst agreement", 0.06,
        _revision_breadth, True,
        why="Up minus down over total revising. Asks whether the analysts who moved agreed, rather than how many cover the name.",
    ),
    Component(
        "share_count", "share count change", 0.05,
        lambda r: r.fundamentals.share_change, False,
        why="Net share issuance is one of the more robust characteristics, and it is cheap and clean to measure.",
    ),
    Component(
        "short_interest", "short interest", 0.03,
        lambda r: _val(r, "short_pct_float"), False,
        why="Heavily shorted names underperform on average. Small weight: at this liquidity it is mostly noise.",
    ),
)

_PRICE_COMPONENTS: Dict[str, Component] = {
    "with_momentum": Component(
        "momentum_52w", "proximity to the 52-week high", 0.10,
        _proximity_to_52w_high, True, contested=True,
        why="George and Hwang: nearness to the 52-week high predicts higher returns over 6 to 12 months.",
    ),
    "with_reversal": Component(
        "reversal_ath", "depth below the all-time high", 0.10,
        _drawdown_depth, True, contested=True,
        why="The view the existing cyclical-turn bucket already takes: a deep drawdown is an opportunity. It is the opposite of the momentum variant on purpose.",
    ),
}

VARIANTS: Tuple[str, ...] = ("quality_value", "with_momentum", "with_reversal")


def components_for(variant: str) -> List[Component]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}, expected one of {VARIANTS}")
    comps = list(COMPONENTS)
    extra = _PRICE_COMPONENTS.get(variant)
    if extra is None:
        return comps
    # The price component is added on top, so the two variants are directly
    # comparable to the base and to each other rather than being three different
    # weightings of the same thing.
    return comps + [extra]


@dataclass(frozen=True)
class ScoreBreakdown:
    ticker: str
    variant: str
    score: float
    percentile: Optional[float]
    display: Optional[float]
    coverage: float
    contributions: Dict[str, float]
    z: Dict[str, Optional[float]]
    missing: List[str]

    @property
    def thinly_evidenced(self) -> bool:
        return self.coverage < MIN_COVERAGE

    def top_contributions(self, n: int = 4) -> List[Tuple[str, float]]:
        return sorted(self.contributions.items(), key=lambda kv: -abs(kv[1]))[:n]


def _standardise(
    records: Sequence[TickerRecord], comp: Component
) -> List[Optional[float]]:
    """z-scores for one component, sector-neutral where the component asks for it.

    A sector with fewer than five names is standardised against the whole universe
    instead. Standardising three utilities against each other manufactures a
    plus-or-minus one z-score out of nothing.
    """
    raw = [comp.extract(r) for r in records]
    sign = 1.0 if comp.higher_is_better else -1.0

    if not comp.sector_neutral:
        return [None if z is None else sign * z for z in zscores(raw)]

    groups: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        groups.setdefault(r.sector or "?", []).append(i)

    out: List[Optional[float]] = [None] * len(records)
    small: List[int] = []
    for sector, idxs in groups.items():
        if len(idxs) < 5:
            small.extend(idxs)
            continue
        zs = zscores([raw[i] for i in idxs])
        for i, z in zip(idxs, zs):
            out[i] = None if z is None else sign * z
    if small:
        zs = zscores(raw)  # fall back to the whole universe
        for i in small:
            out[i] = None if zs[i] is None else sign * zs[i]
    return out


def score_universe(
    records: Iterable[TickerRecord],
    *,
    variant: str = "quality_value",
) -> Dict[str, ScoreBreakdown]:
    """Score every record against every other record. Deterministic and pure.

    Ranking is only meaningful inside the group you pass in. Passing the 150 gives
    a score relative to the 150; passing all 1,505 gives a different, equally valid
    number. The caller decides which universe the question is about.
    """
    recs = [r for r in records]
    if not recs:
        return {}
    comps = components_for(variant)
    total_weight = sum(c.weight for c in comps)

    zmat: Dict[str, List[Optional[float]]] = {c.key: _standardise(recs, c) for c in comps}

    raw_scores: List[float] = []
    breakdowns: List[ScoreBreakdown] = []
    for i, rec in enumerate(recs):
        contributions: Dict[str, float] = {}
        zrow: Dict[str, Optional[float]] = {}
        missing: List[str] = []
        covered = 0.0
        total = 0.0
        for c in comps:
            z = zmat[c.key][i]
            zrow[c.key] = z
            if z is None:
                missing.append(c.key)
                contributions[c.key] = 0.0
                continue
            covered += c.weight
            contrib = c.weight * z
            contributions[c.key] = contrib
            total += contrib
        coverage = covered / total_weight if total_weight else 0.0
        raw_scores.append(total)
        breakdowns.append(
            ScoreBreakdown(
                ticker=rec.ticker,
                variant=variant,
                score=total,
                percentile=None,
                display=None,
                coverage=coverage,
                contributions=contributions,
                z=zrow,
                missing=missing,
            )
        )

    pcts = rank_percentile(raw_scores)
    out: Dict[str, ScoreBreakdown] = {}
    for b, p in zip(breakdowns, pcts):
        out[b.ticker] = ScoreBreakdown(
            ticker=b.ticker,
            variant=b.variant,
            score=b.score,
            percentile=p,
            display=None if p is None else round(p * 100, 1),
            coverage=b.coverage,
            contributions=b.contributions,
            z=b.z,
            missing=b.missing,
        )
    return out


def weights_table(variant: str = "quality_value") -> List[Dict[str, object]]:
    """The weights, for rendering on the page. A score you cannot inspect is a black box."""
    comps = components_for(variant)
    total = sum(c.weight for c in comps)
    return [
        {
            "key": c.key,
            "label": c.label,
            "weight": round(c.weight / total, 4),
            "raw_weight": c.weight,
            "direction": "higher is better" if c.higher_is_better else "lower is better",
            "sector_neutral": c.sector_neutral,
            "contested": c.contested,
            "why": c.why,
        }
        for c in comps
    ]
