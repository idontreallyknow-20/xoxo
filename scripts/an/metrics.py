"""Turn a series of fiscal-year figures into something a page can show honestly.

Two ideas here.

*A trend is a claim and needs a size.* "Revenue is growing" is not information.
"Revenue compounded 8.9% a year over the three intervals between FY2023 and
FY2026" is. Every :class:`Trend` therefore carries the first and last value, the
number of intervals it was computed over, and the label that says so.

*Four fiscal years is three intervals.* The source gives four annual statements,
which the README already flags. A CAGR over four points is a three-year CAGR, and
calling it a four-year growth rate overstates it by a third. :attr:`Trend.periods`
is the interval count and :attr:`Trend.span_label` prints it, so the page cannot
quietly claim more than the data supports.

Direction is classified with a deliberate dead zone. A margin that moved 20 basis
points in three years did not "improve", it was flat, and saying otherwise turns
noise into narrative.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["Trend", "trend", "series_from_rows", "cagr", "yoy", "direction_of", "FLAT_BAND"]

FLAT_BAND = 0.05
"""Relative change below this counts as flat.

A 5% relative move over three years is inside the noise of restatements,
acquisitions and accounting changes at this level of aggregation. Anything under
it is reported as flat rather than dressed up as a trend.
"""


def cagr(first: Optional[float], last: Optional[float], periods: int) -> Optional[float]:
    """Compound annual growth. None when it is not defined rather than a fabricated number.

    A CAGR from a negative or zero starting value is meaningless: there is no real
    root. Plenty of screens paper over that with an absolute value and produce
    nonsense like "free cash flow grew 240% a year" for a company that went from
    minus 50 to plus 10.
    """
    if first is None or last is None or periods < 1:
        return None
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / periods) - 1.0


def yoy(series: Sequence[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = [None]
    for prev, cur in zip(series, series[1:]):
        if prev is None or cur is None or prev == 0:
            out.append(None)
        else:
            out.append(cur / prev - 1.0)
    return out


def direction_of(first: Optional[float], last: Optional[float], *, band: float = FLAT_BAND,
                 absolute: bool = False) -> str:
    """up, down or flat, with a dead zone so noise does not become narrative.

    ``absolute`` compares the raw difference against the band instead of the
    relative change, which is the right test for something already expressed as a
    rate: a gross margin going from 60.0% to 61.5% moved 1.5 points, and dividing
    by 60 to call that a 2.5% change buries it.
    """
    if first is None or last is None:
        return "unknown"
    delta = last - first
    if absolute:
        return "flat" if abs(delta) < band else ("up" if delta > 0 else "down")
    if first == 0:
        return "flat" if delta == 0 else ("up" if delta > 0 else "down")
    rel = delta / abs(first)
    return "flat" if abs(rel) < band else ("up" if rel > 0 else "down")


@dataclass(frozen=True)
class Trend:
    key: str
    label: str
    unit: str  # "currency_bn" | "percent" | "count_m" | "ratio" | "x"
    values: List[Optional[float]]
    periods_labels: List[str]
    source: str
    as_of: Optional[str] = None
    higher_is_better: Optional[bool] = True

    @property
    def observed(self) -> List[Tuple[str, float]]:
        return [(p, v) for p, v in zip(self.periods_labels, self.values) if v is not None]

    @property
    def first(self) -> Optional[float]:
        obs = self.observed
        return obs[0][1] if obs else None

    @property
    def last(self) -> Optional[float]:
        obs = self.observed
        return obs[-1][1] if obs else None

    @property
    def periods(self) -> int:
        """Elapsed intervals between the first and last observation.

        Counted from the fiscal-year labels, not from how many points survived. Four
        fiscal years is three intervals; but four points with FY2023 missing spans
        four years, and dividing by three would annualise the growth over the wrong
        window. That is the exact error this module's docstring says it exists to
        prevent, and the first version made it.

        Falls back to the observation count when the labels cannot be parsed, which
        is the old behaviour and is right for a series whose periods are not years.
        """
        obs = self.observed
        if len(obs) < 2:
            return 0
        years = [self._year_of(label) for label, _ in obs]
        if years[0] is not None and years[-1] is not None and years[-1] > years[0]:
            return years[-1] - years[0]
        return len(obs) - 1

    @property
    def has_gap(self) -> bool:
        """True when a period is missing from the middle of the series."""
        obs = self.observed
        if len(obs) < 2:
            return False
        years = [self._year_of(label) for label, _ in obs]
        if any(y is None for y in years):
            return False
        return (years[-1] - years[0]) != (len(obs) - 1)

    @staticmethod
    def _year_of(label: str) -> Optional[int]:
        m = re.search(r"(\d{4})", str(label))
        return int(m.group(1)) if m else None

    @property
    def span_label(self) -> str:
        obs = self.observed
        if not obs:
            return "no data"
        if len(obs) == 1:
            return obs[0][0]
        return f"{obs[0][0]} to {obs[-1][0]}, {self.periods} interval{'s' if self.periods != 1 else ''}"

    @property
    def change(self) -> Optional[float]:
        if self.first is None or self.last is None:
            return None
        return self.last - self.first

    @property
    def change_pct(self) -> Optional[float]:
        """Relative change, or None when the ratio would be meaningless.

        A percentage change is only defined when the series stays on one side of
        zero. Net debt does not: nine of the sixteen research notes start the window
        in net cash, and ``last / first - 1`` on a negative base comes out with the
        wrong sign and a meaningless magnitude. Alphabet went from $84bn of net cash
        to $16bn of net debt, a hundred-billion-dollar deterioration, and the naive
        formula reported it as "-118.8%".

        :attr:`cagr` already refused negative bases; the page fell back to this
        property precisely when it did, which routed around the guard. So the guard
        lives here too, and the card prints the absolute move instead.
        """
        if self.first is None or self.last is None or self.first == 0:
            return None
        if self.first < 0 or self.last < 0:
            return None
        return self.last / self.first - 1.0

    @property
    def crosses_zero(self) -> bool:
        """Whether the series changes sign, which is why no percentage is offered."""
        if self.first is None or self.last is None:
            return False
        return (self.first < 0) != (self.last < 0)

    @property
    def cagr(self) -> Optional[float]:
        if self.unit == "percent":
            return None  # a rate does not compound; the change in points is the story
        return cagr(self.first, self.last, self.periods)

    @property
    def span_note(self) -> Optional[str]:
        """Said out loud when the series is not what the reader will assume."""
        if self.has_gap:
            return (f"A period is missing from the middle: {len(self.observed)} observations across "
                    f"{self.periods} years. The annual rate is computed over the elapsed years, not "
                    "the number of points.")
        return None

    @property
    def direction(self) -> str:
        return direction_of(self.first, self.last, absolute=(self.unit == "percent"),
                            band=0.01 if self.unit == "percent" else FLAT_BAND)

    @property
    def reads_well(self) -> Optional[bool]:
        """Whether the direction is good news, given what the metric is."""
        if self.higher_is_better is None or self.direction in ("flat", "unknown"):
            return None
        return (self.direction == "up") == self.higher_is_better

    @property
    def yoy(self) -> List[Optional[float]]:
        return yoy(self.values)

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "values": self.values,
            "periods_labels": self.periods_labels,
            "yoy": self.yoy,
            "first": self.first,
            "last": self.last,
            "change": self.change,
            "change_pct": self.change_pct,
            "crosses_zero": self.crosses_zero,
            "has_gap": self.has_gap,
            "span_note": self.span_note,
            "cagr": self.cagr,
            "periods": self.periods,
            "span_label": self.span_label,
            "direction": self.direction,
            "reads_well": self.reads_well,
            "higher_is_better": self.higher_is_better,
            "source": self.source,
            "as_of": self.as_of,
        }


def trend(key: str, label: str, unit: str, rows: Sequence[object], attr: str, source: str,
          *, as_of: Optional[str] = None, higher_is_better: Optional[bool] = True,
          period_attr: str = "fiscal_year") -> Trend:
    return Trend(
        key=key,
        label=label,
        unit=unit,
        values=[getattr(r, attr, None) for r in rows],
        periods_labels=[str(getattr(r, period_attr, "?")) for r in rows],
        source=source,
        as_of=as_of,
        higher_is_better=higher_is_better,
    )


def series_from_rows(rows: Sequence[object], source: str, *, as_of: Optional[str] = None) -> List[Trend]:
    """The standard six trends from a parsed research-note financial table."""
    spec = [
        ("revenue", "Revenue", "currency_bn", "revenue_bn", True),
        ("gross_margin", "Gross margin", "percent", "gross_margin", True),
        ("operating_margin", "Operating margin", "percent", "operating_margin", True),
        ("fcf", "Free cash flow", "currency_bn", "fcf_bn", True),
        ("diluted_shares", "Diluted shares", "count_m", "diluted_shares_m", False),
        ("net_debt", "Net debt", "currency_bn", "net_debt_bn", False),
    ]
    return [
        trend(k, label, unit, rows, attr, source, as_of=as_of, higher_is_better=hib)
        for k, label, unit, attr, hib in spec
    ]
