"""As-reported SEC fundamentals, dated by filing, as an input to the score.

WHY THIS EXISTS
---------------
Every fundamental the score reads comes from ``universe/quality_scores_latest.csv``,
which the original pipeline built from four fiscal years of Yahoo Finance annual
statements. ``criteria.md`` and the README both flag the four years as a known
limitation, and there is a second, quieter one: those statements are *restated*.
Yahoo shows the FY2022 column as the FY2024 10-K reports it, not as the FY2022
10-K did, so nothing computed from them can be dated to the day the market knew
it. A backtest that scores past dates from restated numbers has look-ahead built
into its inputs.

``an.dera`` already reads the SEC's DERA Financial Statement Data Sets: every
numeric fact from every filing, as filed, with the filing date attached. Until
this module existed nothing consumed it. This is the adapter: it turns those
facts into the same aggregates the quality screen computes, on the same
definitions, over a longer window, as they were known on a chosen date, and
merges them into the ``TickerRecord`` the score reads.

WHAT IT DOES NOT DO
-------------------
It does not change a weight, add a component, or touch ``score.py``. The score
has never been validated out of sample, and its three revision components are
already known to be one idea in three coats; feeding the same score better inputs
is an upgrade, retuning it on a diagnostic is not. Everything here is an input.

THE DEFINITIONS, MIRRORED FROM ``scripts/quality_screen.py``
-------------------------------------------------------------
So the two sources can be compared like for like, each aggregate is computed the
way the quality screen computes it, with the substitutions the SEC tags force:

===================  ======================================================
ROIC                 operating income x (1 - tax rate) / invested capital,
                     averaged over the window. Tax rate is income tax over
                     pre-tax income, replaced by 21% when missing, non-positive
                     or above 50%, exactly as the screen does. Invested capital
                     is shareholders' equity plus interest-bearing debt
                     (non-current and current term debt plus commercial
                     paper). Yahoo's "Invested Capital" line may include
                     lease liabilities; that is a documented source of
                     disagreement, not a bug in either side.
FCF margin           (operating cash flow - capital expenditure) / revenue,
                     averaged; positive-year and total-year counts alongside.
Revenue CAGR         first to last fiscal year in the window, annualised over
                     the number of *elapsed* fiscal years between their labels,
                     so a missing middle year does not shorten the exponent.
Net debt / EBITDA    (term debt + commercial paper - cash - current marketable
                     securities) / (operating income + depreciation and
                     amortisation), on the latest year. Net cash is flagged and
                     the ratio left None, the convention ``an.local`` already
                     uses for the CSV. Yahoo's own "Net Debt" line excludes
                     short-term investments, so cash-rich names disagree here
                     by construction.
Share count change   diluted weighted-average shares, last over first minus one.
Gross margin         gross profit over revenue, or revenue minus cost of revenue
                     where the filer tags no gross profit line. Average and
                     population standard deviation, as the screen's ``np.std``.
===================  ======================================================

A fiscal year is labelled by the calendar year its period ends in, which is the
screen's ``fiscal_years`` convention, so the two sources line up by label.

HOW THE TWO SOURCES ARE RECONCILED
----------------------------------
Two windows are computed from the SEC facts: the *long* window (up to ten fiscal
years) and the *same* window (the fiscal years the Yahoo CSV covers). The same
window is compared to the CSV field by field, with a tolerance per field
(:data:`TOLERANCE`). A difference beyond tolerance is a **disagreement**, and a
disagreement is information rather than an error: it is written into the record
with both numbers, listed on the page, counted in the build report, and never
raised. The commonest honest reasons are a restatement, a definitional gap
named above, or a fiscal-year-end change.

The score then reads the SEC figure wherever the long window carries at least
three fiscal years for that group of fields (three is the quality screen's own
floor), and the Yahoo figure otherwise. Which source supplied each field is
recorded in ``Fundamentals.basis_by_field`` and shown on the page. The policy is
a preference for the source that can be dated, not a claim that Yahoo is wrong.

WHETHER THIS HAS RUN FOR REAL
-----------------------------
Not on a downloaded data set. Every build so far has found no quarters under
``data/cache/dera/`` and written the ``NOT RUN`` state, which is what the pages
show. The adapter is exercised on ``tests/fixtures/dera_full``, a hand-built
miniature of Apple's FY2023 and FY2024 10-Ks whose figures are the ones those
filings report, and the tests recompute every aggregate from those figures by
hand before comparing. The first live run is
``SEC_USER_AGENT="Name email" python scripts/fetch_dera.py --since 2016q1`` and
then ``python scripts/fetch_dera.py --basis-report``.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import statistics
from dataclasses import dataclass, field, fields as dc_fields, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import dera, edgar, local, paths
from .http import DryRunTransport
from .local import Fundamentals, TickerRecord
from .metrics import cagr as _cagr
from .store import Cache, FetchError, Offline

__all__ = [
    "WINDOW_YEARS",
    "MIN_YEARS",
    "TOLERANCE",
    "FiscalYear",
    "LongFundamentals",
    "Difference",
    "Reconciliation",
    "Panel",
    "BasisReport",
    "load_panel",
    "ticker_cik_map",
    "fiscal_years",
    "aggregate",
    "fundamentals_for",
    "reconcile",
    "merge_record",
    "apply",
    "universe_with_basis",
    "status_block",
    "basis_comparison",
    "FETCH_COMMAND",
]

WINDOW_YEARS = 10
"""Fiscal years the long window may hold. Ten is what the SEC sets reach back
to for most filers with a 2009 XBRL start; the record says how many it got."""

MIN_YEARS = 3
"""Below this the SEC figure is not used. The quality screen excludes names with
fewer than three years of statements, and the same floor applies here."""

FETCH_COMMAND = 'SEC_USER_AGENT="Your Name you@example.com" python scripts/fetch_dera.py --since 2016q1'

TOLERANCE: Dict[str, float] = {
    "roic_avg": 0.02,
    "roic_trend": 0.02,
    "roic_latest": 0.02,
    "fcf_margin_avg": 0.02,
    "rev_cagr": 0.01,
    "share_change": 0.01,
    "gm_avg": 0.02,
    "gm_std": 0.01,
    "nd_to_ebitda": 0.25,
}
"""Absolute difference beyond which the two sources are said to disagree.

Rates and margins in fraction units (0.02 is two percentage points); the
leverage ratio in turns. Deliberately loose: the point is to surface a
restatement or a definitional gap, not to flag rounding.
"""

# Groups of fields that must come from one source together. Splitting the FCF
# margin from its year counts, or the leverage ratio from its net-cash flag, would
# let a page describe one source's number with the other source's context.
_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("roic_avg", ("roic_avg", "roic_trend", "roic_latest")),
    ("fcf_margin_avg", ("fcf_margin_avg", "fcf_positive_years", "fcf_years")),
    ("rev_cagr", ("rev_cagr",)),
    ("nd_to_ebitda", ("net_debt", "ebitda_latest", "nd_to_ebitda", "net_cash")),
    ("share_change", ("share_change",)),
    ("gm_avg", ("gm_avg", "gm_std")),
)

_METRICS = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
    "operating_cash_flow", "capex", "diluted_shares", "basic_shares", "equity", "cash",
    "short_term_investments", "long_term_debt", "short_term_debt", "commercial_paper",
    "income_tax", "pretax_income", "depreciation_amortization",
)
_FLOWS = {
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
    "operating_cash_flow", "capex", "diluted_shares", "basic_shares", "income_tax",
    "pretax_income", "depreciation_amortization",
}


def _tags_wanted() -> List[str]:
    out: List[str] = []
    for m in _METRICS:
        out += edgar.KEY_TAGS.get(m, []) + edgar.IFRS_TAGS.get(m, [])
    return sorted(set(out))


# -- one fiscal year -------------------------------------------------------------


@dataclass(frozen=True)
class FiscalYear:
    """The lines one fiscal year needs, each as the market knew it on ``known_on``.

    ``filed`` is the latest filing date among the facts used, so it is the date
    after which this row, as shown, was public. ``first_filed`` is when the year's
    revenue first appeared, which is when the year became knowable at all.
    """

    label: str
    end: dt.date
    filed: dt.date
    first_filed: dt.date
    known_on: dt.date
    revenue: Optional[float] = None
    cost_of_revenue: Optional[float] = None
    gross_profit_reported: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None
    diluted_shares: Optional[float] = None
    equity: Optional[float] = None
    cash: Optional[float] = None
    short_term_investments: Optional[float] = None
    long_term_debt: Optional[float] = None
    short_term_debt: Optional[float] = None
    commercial_paper: Optional[float] = None
    income_tax: Optional[float] = None
    pretax_income: Optional[float] = None
    depreciation_amortization: Optional[float] = None
    accessions: Tuple[str, ...] = ()

    @property
    def year(self) -> int:
        return self.end.year

    @property
    def gross_profit(self) -> Optional[float]:
        if self.gross_profit_reported is not None:
            return self.gross_profit_reported
        if self.revenue is not None and self.cost_of_revenue is not None:
            return self.revenue - self.cost_of_revenue
        return None

    @property
    def gross_margin(self) -> Optional[float]:
        gp = self.gross_profit
        return None if gp is None or not self.revenue else gp / self.revenue

    @property
    def fcf(self) -> Optional[float]:
        if self.operating_cash_flow is None:
            return None
        return self.operating_cash_flow - abs(self.capex or 0.0)

    @property
    def fcf_margin(self) -> Optional[float]:
        f = self.fcf
        return None if f is None or not self.revenue else f / self.revenue

    @property
    def tax_rate(self) -> float:
        """The screen's rule: the reported rate, or 21% when it is missing or implausible."""
        if self.income_tax is None or not self.pretax_income:
            return 0.21
        t = self.income_tax / self.pretax_income
        return 0.21 if (t <= 0 or t > 0.5) else t

    @property
    def total_debt(self) -> Optional[float]:
        parts = [self.long_term_debt, self.short_term_debt, self.commercial_paper]
        if all(p is None for p in parts):
            return None
        return sum(p or 0.0 for p in parts)

    @property
    def invested_capital(self) -> Optional[float]:
        if self.equity is None:
            return None
        return self.equity + (self.total_debt or 0.0)

    @property
    def roic(self) -> Optional[float]:
        ic = self.invested_capital
        if self.operating_income is None or not ic or ic <= 0:
            return None
        return self.operating_income * (1.0 - self.tax_rate) / ic

    @property
    def ebitda(self) -> Optional[float]:
        if self.operating_income is None or self.depreciation_amortization is None:
            return None
        return self.operating_income + self.depreciation_amortization

    @property
    def net_debt(self) -> Optional[float]:
        debt = self.total_debt
        if debt is None and self.cash is None:
            return None
        return (debt or 0.0) - (self.cash or 0.0) - (self.short_term_investments or 0.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label, "end": self.end.isoformat(), "filed": self.filed.isoformat(),
            "first_filed": self.first_filed.isoformat(), "known_on": self.known_on.isoformat(),
            "revenue": self.revenue, "gross_margin": self.gross_margin, "operating_income": self.operating_income,
            "net_income": self.net_income, "fcf": self.fcf, "fcf_margin": self.fcf_margin, "roic": self.roic,
            "diluted_shares": self.diluted_shares, "net_debt": self.net_debt, "ebitda": self.ebitda,
            "accessions": list(self.accessions),
        }


def _pick(facts: Sequence[dera.Fact], cik: str, metric: str, end: dt.date, on_date: dt.date) -> Optional[dera.Fact]:
    mine = dera.metric_facts(facts, cik, metric)
    if not mine:
        return None
    return dera.as_known_on(mine, on_date, ddate=end, qtrs=4 if metric in _FLOWS else 0)


def fiscal_years(
    facts: Sequence[dera.Fact],
    cik: str,
    *,
    on_date: dt.date,
    window_years: int = WINDOW_YEARS,
) -> List[FiscalYear]:
    """The fiscal years known on ``on_date``, oldest first, at most ``window_years`` of them.

    A year exists when an annual revenue figure for it had been filed by
    ``on_date``. Every other line is looked up as known on that date, so a
    restatement filed later is not seen and a line the filer never tagged is a
    hole, not a zero.
    """
    cik = edgar.cik_to_str(cik)
    rev = dera.metric_facts(facts, cik, "revenue")
    ends = sorted({f.ddate for f in rev if f.qtrs == 4 and f.filed <= on_date})
    if not ends:
        return []
    # One row per calendar year of period end. A 52/53-week filer whose year end
    # drifts across a calendar boundary would otherwise produce two "FY2016"s.
    by_year: Dict[int, dt.date] = {}
    for e in ends:
        by_year[e.year] = e
    ends = [by_year[y] for y in sorted(by_year)][-window_years:]

    out: List[FiscalYear] = []
    for end in ends:
        first = dera.first_reported(rev, ddate=end, qtrs=4)
        values: Dict[str, Optional[float]] = {}
        filed: List[dt.date] = []
        adshs: List[str] = []
        for m in _METRICS:
            f = _pick(facts, cik, m, end, on_date)
            values[m] = None if f is None else f.value
            if f is not None:
                filed.append(f.filed)
                adshs.append(f.adsh)
        if values["revenue"] is None:
            continue
        out.append(FiscalYear(
            label=f"FY{end.year}",
            end=end,
            filed=max(filed),
            first_filed=first.filed if first else max(filed),
            known_on=on_date,
            revenue=values["revenue"],
            cost_of_revenue=values["cost_of_revenue"],
            gross_profit_reported=values["gross_profit"],
            operating_income=values["operating_income"],
            net_income=values["net_income"],
            operating_cash_flow=values["operating_cash_flow"],
            capex=values["capex"],
            diluted_shares=values["diluted_shares"] if values["diluted_shares"] is not None else values["basic_shares"],
            equity=values["equity"],
            cash=values["cash"],
            short_term_investments=values["short_term_investments"],
            long_term_debt=values["long_term_debt"],
            short_term_debt=values["short_term_debt"],
            commercial_paper=values["commercial_paper"],
            income_tax=values["income_tax"],
            pretax_income=values["pretax_income"],
            depreciation_amortization=values["depreciation_amortization"],
            accessions=tuple(sorted(set(adshs))),
        ))
    return out


# -- the aggregates ----------------------------------------------------------------


def _mean(xs: List[float]) -> Optional[float]:
    return statistics.fmean(xs) if xs else None


def aggregate(years: Sequence[FiscalYear]) -> Fundamentals:
    """The quality screen's aggregates over these years, on the definitions in the module docstring."""
    ys = sorted(years, key=lambda y: y.end)
    labels = [str(y.year) for y in ys]

    roic = [y.roic for y in ys if y.roic is not None]
    fcfm = [y.fcf_margin for y in ys if y.fcf_margin is not None]
    fcf = [y.fcf for y in ys if y.fcf is not None]
    gm = [y.gross_margin for y in ys if y.gross_margin is not None]
    shares = [y.diluted_shares for y in ys if y.diluted_shares]

    rev_first, rev_last = (ys[0].revenue, ys[-1].revenue) if ys else (None, None)
    periods = (ys[-1].year - ys[0].year) if len(ys) >= 2 else 0
    rev_cagr = _cagr(rev_first, rev_last, periods) if periods else None

    last = ys[-1] if ys else None
    nd = last.net_debt if last else None
    ebitda = last.ebitda if last else None
    net_cash = nd is not None and nd <= 0
    nd_to_ebitda: Optional[float]
    if nd is None or net_cash:
        nd_to_ebitda = None
    elif ebitda is not None and ebitda > 0:
        nd_to_ebitda = nd / ebitda
    else:
        nd_to_ebitda = None

    return Fundamentals(
        n_years=len(ys),
        fiscal_years=labels,
        roic_avg=_mean(roic),
        roic_latest=roic[-1] if roic else None,
        roic_trend=(roic[-1] - roic[0]) if len(roic) >= 2 else None,
        fcf_margin_avg=_mean(fcfm),
        fcf_positive_years=float(sum(1 for f in fcf if f > 0)) if fcf else None,
        fcf_years=float(len(fcf)) if fcf else None,
        rev_cagr=rev_cagr,
        net_debt=nd,
        ebitda_latest=ebitda,
        nd_to_ebitda=nd_to_ebitda,
        net_cash=net_cash,
        share_change=(shares[-1] / shares[0] - 1.0) if len(shares) >= 2 and shares[0] > 0 else None,
        gm_avg=_mean(gm),
        gm_std=statistics.pstdev(gm) if len(gm) >= 2 else None,
        basis="sec_dera",
    )


@dataclass(frozen=True)
class LongFundamentals:
    ticker: str
    cik: str
    on_date: dt.date
    years: List[FiscalYear]
    long: Fundamentals
    same_window: Optional[Fundamentals]
    same_window_labels: List[str]

    @property
    def as_reported_through(self) -> Optional[str]:
        return max((y.filed for y in self.years), default=None).isoformat() if self.years else None


def fundamentals_for(
    facts: Sequence[dera.Fact],
    ticker: str,
    cik: str,
    *,
    on_date: dt.date,
    window_years: int = WINDOW_YEARS,
    yahoo_years: Optional[Iterable[str]] = None,
) -> Optional[LongFundamentals]:
    """Long-window and same-window aggregates for one name as known on ``on_date``.

    This is also the primitive a point-in-time backtest needs: call it with each
    rebalance date and the returned aggregates are what a screen run that day
    could have computed from the filings then public.
    """
    years = fiscal_years(facts, cik, on_date=on_date, window_years=window_years)
    if not years:
        return None
    wanted = {str(y)[:4] for y in (yahoo_years or [])}
    same = [y for y in years if str(y.year) in wanted]
    return LongFundamentals(
        ticker=ticker, cik=edgar.cik_to_str(cik), on_date=on_date, years=years,
        long=aggregate(years),
        same_window=aggregate(same) if same else None,
        same_window_labels=[y.label for y in same],
    )


# -- reconciliation ----------------------------------------------------------------


@dataclass(frozen=True)
class Difference:
    field: str
    yahoo: Optional[float]
    sec_same_window: Optional[float]
    sec_long_window: Optional[float]
    tolerance: Optional[float]
    gap: Optional[float]
    agrees: Optional[bool]
    """True within tolerance, False beyond it, None when either side is missing."""

    def to_dict(self) -> Dict[str, Any]:
        r = lambda x: None if x is None else round(x, 6)  # noqa: E731
        return {"field": self.field, "yahoo": r(self.yahoo), "sec_same_window": r(self.sec_same_window),
                "sec_long_window": r(self.sec_long_window), "tolerance": self.tolerance, "gap": r(self.gap),
                "agrees": self.agrees}


@dataclass(frozen=True)
class Reconciliation:
    ticker: str
    cik: str
    yahoo_years: List[str]
    sec_same_window_years: List[str]
    sec_long_window_years: List[str]
    differences: List[Difference]
    net_cash_agrees: Optional[bool]

    @property
    def compared(self) -> List[Difference]:
        return [d for d in self.differences if d.agrees is not None]

    @property
    def disagreements(self) -> List[Difference]:
        return [d for d in self.differences if d.agrees is False]

    @property
    def same_window_complete(self) -> bool:
        return bool(self.yahoo_years) and set(self.yahoo_years) <= set(self.sec_same_window_years)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "yahoo_years": self.yahoo_years,
            "sec_same_window_years": self.sec_same_window_years,
            "sec_long_window_years": self.sec_long_window_years,
            "same_window_complete": self.same_window_complete,
            "n_compared": len(self.compared),
            "n_disagree": len(self.disagreements),
            "disagree_on": [d.field for d in self.disagreements],
            "net_cash_agrees": self.net_cash_agrees,
            "differences": [d.to_dict() for d in self.differences],
        }


def reconcile(rec: TickerRecord, lf: LongFundamentals) -> Reconciliation:
    """Yahoo's four-year figures against the SEC's over the same fiscal years.

    A missing side gives ``agrees=None`` rather than a verdict; a gap beyond
    :data:`TOLERANCE` is a disagreement and is reported, not fixed.
    """
    yf = rec.fundamentals
    same = lf.same_window
    yahoo_years = [str(y)[:4] for y in yf.fiscal_years]
    same_years = [str(y.year) for y in lf.years if y.label in lf.same_window_labels]
    # A verdict needs the same fiscal years on both sides. If the SEC facts stop a
    # year short of Yahoo's window (a data set not yet downloaded, a filing after
    # the on-date), a three-year average against a four-year one is a different
    # window, not a disagreement, and the comparison is shown without a verdict.
    complete = bool(yahoo_years) and set(yahoo_years) <= set(same_years) and same is not None
    out: List[Difference] = []
    for name, tol in TOLERANCE.items():
        a = getattr(yf, name)
        b = getattr(same, name) if same else None
        c = getattr(lf.long, name)
        gap = None if a is None or b is None else b - a
        agrees = None if (gap is None or not complete) else abs(gap) <= tol
        out.append(Difference(name, a, b, c, tol, gap, agrees))
    nc = None if not complete else (bool(yf.net_cash) == bool(same.net_cash))
    return Reconciliation(
        ticker=rec.ticker, cik=lf.cik,
        yahoo_years=yahoo_years,
        sec_same_window_years=same_years,
        sec_long_window_years=[str(y.year) for y in lf.years],
        differences=out, net_cash_agrees=nc,
    )


# -- merging into the record -----------------------------------------------------


def merge_record(rec: TickerRecord, lf: LongFundamentals) -> TickerRecord:
    """The record with SEC aggregates in place of Yahoo's, group by group, where the SEC has enough years.

    ``basis_by_field`` names the source of every group's lead field. The whole
    record is ``sec_dera`` only when every group came from the SEC; otherwise
    ``mixed``; and untouched Yahoo when the long window is under the floor.
    """
    yf = rec.fundamentals
    sec = lf.long
    if (sec.n_years or 0) < MIN_YEARS:
        return rec
    values: Dict[str, Any] = {f.name: getattr(yf, f.name) for f in dc_fields(Fundamentals)}
    by_field: Dict[str, str] = {}
    for lead, members in _GROUPS:
        use_sec = getattr(sec, lead) is not None or (lead == "nd_to_ebitda" and sec.net_cash)
        src = "sec_dera" if use_sec else "yfinance"
        for m in members:
            by_field[m] = src
            if use_sec:
                values[m] = getattr(sec, m)
    srcs = set(by_field.values())
    values["basis"] = "sec_dera" if srcs == {"sec_dera"} else "mixed"
    values["basis_by_field"] = by_field
    values["n_years"] = sec.n_years
    values["fiscal_years"] = list(sec.fiscal_years)
    return replace(rec, fundamentals=Fundamentals(**values))


# -- the panel: cached quarters plus the ticker map ---------------------------------


@dataclass
class Panel:
    """Everything loaded from the DERA cache for a set of tickers, or the reason nothing was."""

    quarters: List[str]
    facts_by_cik: Dict[str, List[dera.Fact]]
    cik_by_ticker: Dict[str, str]
    unmapped: List[str]
    source: str  # "cache" | "fixture" | "none"
    live: Optional[bool]  # True if any quarter came from a real request; None when unknown
    why: str
    on_date: dt.date

    @property
    def has_data(self) -> bool:
        return bool(self.quarters) and bool(self.facts_by_cik)

    def facts_for(self, ticker: str) -> Tuple[Optional[str], List[dera.Fact]]:
        cik = self.cik_by_ticker.get(ticker.upper())
        return cik, (self.facts_by_cik.get(cik, []) if cik else [])


def ticker_cik_map(tickers: Iterable[str], *, table: Optional[Dict[str, Dict[str, Any]]] = None
                   ) -> Tuple[Dict[str, str], List[str], bool]:
    """Ticker to CIK from the EDGAR ticker map already on disk. Never fetches.

    Returns the map, the tickers it could not place, and whether a map was
    available at all. ``fetch_edgar.py`` caches the map on its first run.
    """
    if table is None:
        client = edgar.EdgarClient(transport=DryRunTransport(sink=lambda _: None),
                                   cache=Cache(paths.EDGAR_CACHE, default_ttl=float("inf")))
        try:
            table = client.company_tickers()
        except (Offline, FetchError):
            return {}, sorted({t.upper() for t in tickers}), False
    out: Dict[str, str] = {}
    missing: List[str] = []
    for t in sorted({t.upper() for t in tickers}):
        hit = None
        for cand in (t, t.replace(".", "-"), t.replace("-", "."), t.split(".")[0]):
            if cand in table:
                hit = table[cand]["cik"]
                break
        if hit:
            out[t] = edgar.cik_to_str(hit)
        else:
            missing.append(t)
    return out, missing, True


def _extract_key(sources: Sequence[Tuple[str, Any]], ciks: Sequence[str], tags: Sequence[str]) -> str:
    h = hashlib.sha256()
    for label, src in sources:
        p = Path(src)
        h.update(f"{label}:{p.name}:{p.stat().st_size if p.exists() else 0}\n".encode())
    h.update(",".join(sorted(ciks)).encode())
    h.update(b"\n" + ",".join(tags).encode())
    return h.hexdigest()[:24]


def _fact_to_row(f: dera.Fact) -> Dict[str, Any]:
    return {"cik": f.cik, "tag": f.tag, "version": f.version, "ddate": f.ddate.isoformat(), "qtrs": f.qtrs,
            "unit": f.unit, "value": f.value, "adsh": f.adsh, "filed": f.filed.isoformat(), "form": f.form,
            "fy": f.fiscal_year, "fp": f.fiscal_period}


def _row_to_fact(r: Dict[str, Any]) -> dera.Fact:
    return dera.Fact(cik=r["cik"], tag=r["tag"], version=r["version"], ddate=dt.date.fromisoformat(r["ddate"]),
                     qtrs=int(r["qtrs"]), unit=r["unit"], value=float(r["value"]), adsh=r["adsh"],
                     filed=dt.date.fromisoformat(r["filed"]), form=r["form"], fiscal_year=r.get("fy"),
                     fiscal_period=r.get("fp"))


def load_panel(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Path] = None,
    sources: Optional[Sequence[Tuple[str, Any]]] = None,
    cik_map: Optional[Dict[str, str]] = None,
    on_date: Optional[dt.date] = None,
    extract_cache: bool = True,
) -> Panel:
    """Load the facts these tickers need from every cached quarter, or say why not.

    ``sources`` overrides the cache (the tests pass the fixture directories).
    ``cik_map`` overrides the EDGAR ticker map. With neither override the panel
    reads only what is on disk and never makes a request.

    Streaming a real quarter is slow (a 100 MB ``num.txt`` per quarter), so the
    kept facts are written once to ``<cache_dir>/extract/`` keyed on the zips,
    the CIKs and the tags, and read back on later builds.
    """
    tickers = sorted({t.upper() for t in tickers})
    on_date = on_date or dt.date.today()
    cache_dir = Path(cache_dir) if cache_dir is not None else paths.DERA_CACHE

    if sources is None:
        zips = sorted(cache_dir.glob("*.zip")) if cache_dir.exists() else []
        sources = [(p.stem, p) for p in zips]
        source_kind = "cache"
    else:
        source_kind = "fixture"
    if not sources:
        try:
            shown = cache_dir.relative_to(paths.ROOT)
        except ValueError:
            shown = cache_dir
        return Panel([], {}, {}, tickers, "none", None,
                     f"no DERA quarters under {shown}. Nothing has been downloaded; run {FETCH_COMMAND}",
                     on_date)

    if cik_map is None:
        cik_map, unmapped, had_map = ticker_cik_map(tickers)
        if not had_map:
            return Panel([lbl for lbl, _ in sources], {}, {}, tickers, source_kind, None,
                         "the EDGAR ticker map is not cached, so no ticker can be placed against a CIK; "
                         "run python scripts/fetch_edgar.py --dry-run AAPL once with network to cache it",
                         on_date)
    else:
        cik_map = {t.upper(): edgar.cik_to_str(c) for t, c in cik_map.items() if t.upper() in tickers}
        unmapped = [t for t in tickers if t not in cik_map]

    ciks = sorted(set(cik_map.values()))
    tags = _tags_wanted()
    facts: Optional[List[dera.Fact]] = None
    cache_file: Optional[Path] = None
    if extract_cache and source_kind == "cache":
        cache_file = cache_dir / "extract" / f"{_extract_key(sources, ciks, tags)}.json"
        if cache_file.exists():
            try:
                facts = [_row_to_fact(r) for r in json.loads(cache_file.read_text(encoding="utf-8"))["facts"]]
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                facts = None
    if facts is None:
        q = dera.load_quarters(list(sources), ciks=ciks, tags=tags)
        facts = q.facts
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".json.part")
            tmp.write_text(json.dumps({"quarters": [lbl for lbl, _ in sources], "facts": [_fact_to_row(f) for f in facts]}),
                           encoding="utf-8")
            tmp.replace(cache_file)

    by_cik: Dict[str, List[dera.Fact]] = {}
    for f in facts:
        by_cik.setdefault(f.cik, []).append(f)

    live: Optional[bool] = None
    if source_kind == "cache":
        prov = dera.provenance(cache_dir)
        live = prov.get("ever_made_a_real_request")
    elif source_kind == "fixture":
        live = False

    labels = [lbl for lbl, _ in sources]
    why = (f"{len(labels)} quarter{'s' if len(labels) != 1 else ''} loaded ({labels[0]} to {labels[-1]}); "
           f"{len(by_cik)} of {len(ciks)} mapped names have facts; {len(unmapped)} tickers could not be mapped to a CIK")
    return Panel(labels, by_cik, cik_map, unmapped, source_kind, live, why, on_date)


# -- applying it to the universe -----------------------------------------------------


@dataclass
class BasisReport:
    panel: Panel
    applied: Dict[str, LongFundamentals] = field(default_factory=dict)
    reconciliations: Dict[str, Reconciliation] = field(default_factory=dict)
    skipped_short: Dict[str, int] = field(default_factory=dict)
    """Names the panel had facts for but under MIN_YEARS of them: ticker -> years found."""
    basis_by_ticker: Dict[str, str] = field(default_factory=dict)
    """``sec_dera`` or ``mixed`` for every re-based name."""

    @property
    def in_use(self) -> bool:
        return bool(self.applied)

    @property
    def status(self) -> str:
        return "IN USE" if self.in_use else "NOT RUN"

    def disagreement_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self.reconciliations.values():
            for d in r.disagreements:
                out[d.field] = out.get(d.field, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def apply(universe: Dict[str, TickerRecord], panel: Panel, *, window_years: int = WINDOW_YEARS
          ) -> Tuple[Dict[str, TickerRecord], BasisReport]:
    """Every record re-based on SEC figures where the panel can supply them; the rest untouched."""
    report = BasisReport(panel=panel)
    if not panel.has_data:
        return dict(universe), report
    out: Dict[str, TickerRecord] = {}
    for t, rec in universe.items():
        cik, facts = panel.facts_for(t)
        if not cik or not facts:
            out[t] = rec
            continue
        lf = fundamentals_for(facts, t, cik, on_date=panel.on_date, window_years=window_years,
                              yahoo_years=rec.fundamentals.fiscal_years)
        if lf is None:
            out[t] = rec
            continue
        if (lf.long.n_years or 0) < MIN_YEARS:
            report.skipped_short[t] = lf.long.n_years or 0
            out[t] = rec
            continue
        report.applied[t] = lf
        report.reconciliations[t] = reconcile(rec, lf)
        merged = merge_record(rec, lf)
        report.basis_by_ticker[t] = merged.fundamentals.basis
        out[t] = merged
    return out, report


@lru_cache(maxsize=1)
def _cached_universe() -> Tuple[Dict[str, TickerRecord], BasisReport]:
    base = local.load_universe()
    pulled = sorted({r.pulled for r in base.values() if r.pulled})
    on_date = dt.date.fromisoformat(pulled[-1]) if pulled else dt.date.today()
    panel = load_panel(base.keys(), on_date=on_date)
    return apply(base, panel)


def universe_with_basis() -> Tuple[Dict[str, TickerRecord], BasisReport]:
    """``local.load_universe()`` with SEC fundamentals applied wherever the cache allows.

    Every builder that scores the universe goes through this one function, so the
    scorecard, the analysis pages, the memo, the snapshot and the backtest
    structure all see the same records. The on-date is the CSV's ``pulled`` date:
    the SEC figures are taken as they were known on the day the screen ran, not
    on the day the page was built.
    """
    return _cached_universe()


def status_block(report: BasisReport) -> Dict[str, Any]:
    """The state of this input, for scorecard.json and the pages. Honest when nothing has run."""
    p = report.panel
    block: Dict[str, Any] = {
        "status": report.status,
        "source": "SEC DERA Financial Statement Data Sets, as reported, dated by filing",
        "quarters_loaded": p.quarters,
        "ever_made_a_real_request": p.live,
        "on_date": p.on_date.isoformat(),
        "window_years": WINDOW_YEARS,
        "min_years": MIN_YEARS,
        "names_rebased": len(report.applied),
        "names_too_short": len(report.skipped_short),
        # With nothing loaded every ticker is "unmapped", which would read as a
        # finding about the ticker map rather than about the empty cache.
        "tickers_unmapped": len(p.unmapped) if p.has_data else None,
        "reconciliation_tolerance": TOLERANCE,
        "policy": (
            "The score reads the SEC figure wherever the filings give at least three fiscal years for "
            "that group of measures, and Yahoo's four restated years otherwise. Over the fiscal years "
            "both cover, the two are compared field by field; a gap beyond tolerance is recorded with "
            "both numbers and never treated as an error."
        ),
        "command": FETCH_COMMAND,
    }
    if report.in_use:
        counts = report.disagreement_counts()
        block["why"] = p.why
        block["disagreements_by_field"] = counts
        block["names_with_a_disagreement"] = sum(1 for r in report.reconciliations.values() if r.disagreements)
        block["basis_counts"] = _basis_counts(report)
    else:
        block["why"] = (
            p.why + ". Every fundamental in the score therefore still comes from four restated fiscal "
            "years of Yahoo Finance statements, which criteria.md flags as a known limitation. "
            "Nothing on the pages is estimated in its place."
        )
    return block


def _basis_counts(report: BasisReport) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for b in report.basis_by_ticker.values():
        out[b] = out.get(b, 0) + 1
    return dict(sorted(out.items()))


def basis_comparison(records_sec: Sequence[TickerRecord], records_yahoo: Sequence[TickerRecord], *, variant: str
                     ) -> Dict[str, Any]:
    """How much the basis swap moved the score: the first testable effect of this input.

    Rank correlation between the two bases and the number of names whose
    percentile moved more than ten points. A swap that moves nothing is not
    worth having; one that reorders everything says the four-year window was
    doing a lot of quiet work. Neither is evidence about returns.
    """
    from . import score
    from .stats import spearman

    a = score.score_universe(records_sec, variant=variant)
    b = score.score_universe(records_yahoo, variant=variant)
    common = sorted(set(a) & set(b))
    rho, n = spearman([a[t].score for t in common], [b[t].score for t in common])
    moved = [
        {"ticker": t, "sec_basis": a[t].display, "yahoo_basis": b[t].display,
         "moved": round(a[t].display - b[t].display, 1)}
        for t in common
        if a[t].display is not None and b[t].display is not None and abs(a[t].display - b[t].display) > 10
    ]
    moved.sort(key=lambda m: -abs(m["moved"]))
    return {
        "variant": variant,
        "rank_correlation": None if rho is None else round(rho, 4),
        "n": n,
        "names_moved_more_than_10_points": len(moved),
        "largest_moves": moved[:10],
        "note": "Two bases for the same weights on the same date. This measures sensitivity to the "
                "input, not whether either ordering predicts anything.",
    }
