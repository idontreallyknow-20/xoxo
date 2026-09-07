"""Load the committed screen output into typed, NaN-free records.

The pipeline writes two CSVs that between them carry everything the existing
screens know: ``quality_scores_latest.csv`` (1,505 scored names, four fiscal years
of aggregates each) and ``price_screen_latest.csv`` (the top 150, plus valuation
against the company's own history, estimate revisions, drawdowns and short
interest). Both are a single snapshot dated in the ``pulled`` column.

Two rules here, both learned from the data rather than assumed.

*Missing is missing.* pandas hands back ``NaN`` for an empty cell and ``NaN``
compares false against everything, which is how a screen quietly starts treating
"no EBITDA" as "bad EBITDA". Every numeric field goes through :func:`_f`, which
returns ``None``, and every consumer downstream has to decide what ``None`` means.

*Own-history multiples are not always available.* Twelve of the 150 have
``n_hist_years == 0`` because their statements are reported in a different
currency from their listing, and the pipeline correctly refuses to compare a
EUR-denominated earnings figure to a USD price. Those names have no
``pe_vs_median``. They are not penalised for it; they are marked.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import paths

__all__ = [
    "Fundamentals",
    "QualityScore",
    "Valuation",
    "TickerRecord",
    "load_quality",
    "load_price_screen",
    "load_universe",
    "SECTORS",
]


def _f(v: Any) -> Optional[float]:
    """Float or None. Empty strings, ``nan`` and infinities all become None."""
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _i(v: Any) -> Optional[int]:
    x = _f(v)
    return None if x is None else int(x)


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def _b(v: Any) -> bool:
    return _s(v) in ("True", "true", "1", "TRUE")


@dataclass(frozen=True)
class Fundamentals:
    """Four-year aggregates from the annual statements. Source: Yahoo Finance via yfinance."""

    n_years: Optional[int] = None
    fiscal_years: List[str] = field(default_factory=list)
    roic_avg: Optional[float] = None
    roic_latest: Optional[float] = None
    roic_trend: Optional[float] = None
    fcf_margin_avg: Optional[float] = None
    fcf_positive_years: Optional[float] = None
    fcf_years: Optional[float] = None
    rev_cagr: Optional[float] = None
    net_debt: Optional[float] = None
    ebitda_latest: Optional[float] = None
    nd_to_ebitda: Optional[float] = None
    net_cash: bool = False
    share_change: Optional[float] = None
    gm_avg: Optional[float] = None
    gm_std: Optional[float] = None
    basis: str = "yfinance"
    """Where these aggregates came from: ``yfinance`` (the committed CSV, four
    restated fiscal years) or ``sec_dera`` (as-reported SEC filings, dated by
    filing, see ``an.dera_fundamentals``). ``mixed`` when a field fell back."""
    basis_by_field: Dict[str, str] = field(default_factory=dict)
    """Per score input, which source supplied it. Empty means all from ``basis``."""

    @property
    def years_label(self) -> str:
        if not self.fiscal_years:
            return "n/a"
        return f"{self.fiscal_years[0]}–{self.fiscal_years[-1]}"


@dataclass(frozen=True)
class QualityScore:
    """The existing 0-100 quality score and its six components. Weights live in criteria.md."""

    total: Optional[float] = None
    rank: Optional[int] = None
    roic: Optional[float] = None
    fcf: Optional[float] = None
    growth: Optional[float] = None
    balance: Optional[float] = None
    shares: Optional[float] = None
    gm_stability: Optional[float] = None

    def components(self) -> Dict[str, Optional[float]]:
        return {
            "return on invested capital": self.roic,
            "free cash flow": self.fcf,
            "revenue growth": self.growth,
            "balance sheet": self.balance,
            "share count": self.shares,
            "gross margin stability": self.gm_stability,
        }


@dataclass(frozen=True)
class Valuation:
    """Price screen output. Only populated for the 150 that survived the quality screen."""

    forward_pe: Optional[float] = None
    ev_ebitda: Optional[float] = None
    price_from_price_screen: Optional[float] = None
    """The ``price_ps`` column, which is a second price, not a price-to-sales ratio.

    ``scripts/price_screen.py:116`` merges with ``suffixes=("", "_ps")``, and both
    frames carry a ``price`` column, so the price screen's own last close lands under
    ``price_ps``. It is within a couple of percent of ``price`` for all 150 names.

    It matters because every derived figure in the row (``dd_52w``, ``dd_ath``,
    ``pe_vs_median``) was computed inside the price screen from *this* price, while
    the ``price`` column the CSV surfaces comes from the quality screen. The two are
    close but not identical.
    """
    median_pe_hist: Optional[float] = None
    median_ev_ebitda_hist: Optional[float] = None
    n_hist_years: Optional[int] = None
    pe_vs_median: Optional[float] = None
    ev_vs_median: Optional[float] = None
    high_52w: Optional[float] = None
    ath: Optional[float] = None
    dd_52w: Optional[float] = None
    dd_ath: Optional[float] = None
    short_pct_float: Optional[float] = None
    insider_pct: Optional[float] = None
    next_earnings: Optional[str] = None
    eps_fy0_now: Optional[float] = None
    eps_fy0_chg_30d: Optional[float] = None
    eps_fy0_chg_90d: Optional[float] = None
    eps_fy1_now: Optional[float] = None
    eps_fy1_chg_30d: Optional[float] = None
    eps_fy1_chg_90d: Optional[float] = None
    rev_up30_fy1: Optional[float] = None
    rev_down30_fy1: Optional[float] = None
    rev_up30_fy0: Optional[float] = None
    rev_down30_fy0: Optional[float] = None
    multiples_note: Optional[str] = None
    bucket_compounder: bool = False
    bucket_cyclical_turn: bool = False

    MIN_HISTORY_POINTS = 3
    """Fewer than three fiscal year ends is not a median, it is two numbers.

    The page and the score used to disagree here: the page called two points a
    usable own-history comparison while ``score.py`` treated anything under three as
    missing. Two names in the committed data sat in that gap, and their pages
    asserted a comparison the score had already refused.
    """

    @property
    def has_own_history(self) -> bool:
        return (bool(self.n_hist_years) and self.n_hist_years >= self.MIN_HISTORY_POINTS
                and self.pe_vs_median is not None)

    @property
    def buckets(self) -> List[str]:
        out = []
        if self.bucket_compounder:
            out.append("compounder")
        if self.bucket_cyclical_turn:
            out.append("cyclical turn")
        return out

    @property
    def net_revisions_fy1(self) -> Optional[float]:
        """Analysts revising next year up minus down, over 30 days. None if neither is known."""
        up, down = self.rev_up30_fy1, self.rev_down30_fy1
        if up is None and down is None:
            return None
        return (up or 0.0) - (down or 0.0)


@dataclass(frozen=True)
class TickerRecord:
    ticker: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None
    price: Optional[float] = None
    market_cap_usd: Optional[float] = None
    dollar_volume_usd: Optional[float] = None
    pulled: Optional[str] = None
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    quality: QualityScore = field(default_factory=QualityScore)
    valuation: Optional[Valuation] = None

    @property
    def in_top_150(self) -> bool:
        return self.valuation is not None


SECTORS = (
    "Technology",
    "Industrials",
    "Healthcare",
    "Consumer Cyclical",
    "Energy",
    "Basic Materials",
    "Real Estate",
    "Consumer Defensive",
    "Utilities",
    "Communication Services",
)


def _rows(path: Path) -> Iterable[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _fundamentals(r: Dict[str, str]) -> Fundamentals:
    years = [y for y in (_s(r.get("fiscal_years")) or "").split(",") if y]
    return Fundamentals(
        n_years=_i(r.get("n_years")),
        fiscal_years=years,
        roic_avg=_f(r.get("roic_avg")),
        roic_latest=_f(r.get("roic_latest")),
        roic_trend=_f(r.get("roic_trend")),
        fcf_margin_avg=_f(r.get("fcf_margin_avg")),
        fcf_positive_years=_f(r.get("fcf_positive_years")),
        fcf_years=_f(r.get("fcf_years")),
        rev_cagr=_f(r.get("rev_cagr")),
        net_debt=_f(r.get("net_debt")),
        ebitda_latest=_f(r.get("ebitda_latest")),
        nd_to_ebitda=_f(r.get("nd_to_ebitda")),
        net_cash=_b(r.get("net_cash")),
        share_change=_f(r.get("share_change")),
        gm_avg=_f(r.get("gm_avg")),
        gm_std=_f(r.get("gm_std")),
    )


def _quality(r: Dict[str, str]) -> QualityScore:
    return QualityScore(
        total=_f(r.get("quality_score")),
        rank=_i(r.get("rank")),
        roic=_f(r.get("score_roic")),
        fcf=_f(r.get("score_fcf")),
        growth=_f(r.get("score_growth")),
        balance=_f(r.get("score_balance")),
        shares=_f(r.get("score_shares")),
        gm_stability=_f(r.get("score_gm_stability")),
    )


def _valuation(r: Dict[str, str]) -> Valuation:
    return Valuation(
        forward_pe=_f(r.get("forward_pe")),
        ev_ebitda=_f(r.get("ev_ebitda")),
        price_from_price_screen=_f(r.get("price_ps")),
        median_pe_hist=_f(r.get("median_pe_hist")),
        median_ev_ebitda_hist=_f(r.get("median_ev_ebitda_hist")),
        n_hist_years=_i(r.get("n_hist_years")),
        pe_vs_median=_f(r.get("pe_vs_median")),
        ev_vs_median=_f(r.get("ev_vs_median")),
        high_52w=_f(r.get("high_52w")),
        ath=_f(r.get("ath")),
        dd_52w=_f(r.get("dd_52w")),
        dd_ath=_f(r.get("dd_ath")),
        short_pct_float=_f(r.get("short_pct_float")),
        insider_pct=_f(r.get("insider_pct")),
        next_earnings=_s(r.get("next_earnings")),
        eps_fy0_now=_f(r.get("eps_fy0_now")),
        eps_fy0_chg_30d=_f(r.get("eps_fy0_chg_30d")),
        eps_fy0_chg_90d=_f(r.get("eps_fy0_chg_90d")),
        eps_fy1_now=_f(r.get("eps_fy1_now")),
        eps_fy1_chg_30d=_f(r.get("eps_fy1_chg_30d")),
        eps_fy1_chg_90d=_f(r.get("eps_fy1_chg_90d")),
        rev_up30_fy1=_f(r.get("rev_up30_fy1")),
        rev_down30_fy1=_f(r.get("rev_down30_fy1")),
        rev_up30_fy0=_f(r.get("rev_up30_fy0")),
        rev_down30_fy0=_f(r.get("rev_down30_fy0")),
        multiples_note=_s(r.get("multiples_note")),
        bucket_compounder=_b(r.get("bucket_compounder")),
        bucket_cyclical_turn=_b(r.get("bucket_cyclical_turn")),
    )


def _record(r: Dict[str, str]) -> TickerRecord:
    return TickerRecord(
        ticker=(_s(r.get("ticker")) or "").upper(),
        name=_s(r.get("name")),
        sector=_s(r.get("sector")),
        industry=_s(r.get("industry")),
        country=_s(r.get("country")),
        exchange=_s(r.get("exchange")),
        currency=_s(r.get("currency")),
        price=_f(r.get("price")),
        market_cap_usd=_f(r.get("market_cap_usd")) or _f(r.get("market_cap")),
        dollar_volume_usd=_f(r.get("dollar_volume_usd")),
        pulled=_s(r.get("pulled")),
        fundamentals=_fundamentals(r),
        quality=_quality(r),
    )


@lru_cache(maxsize=1)
def load_quality(path: Optional[str] = None) -> Dict[str, TickerRecord]:
    p = Path(path) if path else paths.QUALITY_SCORES
    out: Dict[str, TickerRecord] = {}
    for r in _rows(p):
        rec = _record(r)
        if rec.ticker:
            out[rec.ticker] = rec
    return out


@lru_cache(maxsize=1)
def load_price_screen(path: Optional[str] = None) -> Dict[str, Valuation]:
    p = Path(path) if path else paths.PRICE_SCREEN
    out: Dict[str, Valuation] = {}
    for r in _rows(p):
        t = (_s(r.get("ticker")) or "").upper()
        if t:
            out[t] = _valuation(r)
    return out


def load_universe() -> Dict[str, TickerRecord]:
    """Every scored name, with valuation attached for the 150 that have it."""
    quality = load_quality()
    vals = load_price_screen()
    merged: Dict[str, TickerRecord] = {}
    for t, rec in quality.items():
        v = vals.get(t)
        merged[t] = TickerRecord(**{**rec.__dict__, "valuation": v}) if v else rec
    return merged
