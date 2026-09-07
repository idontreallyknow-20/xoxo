"""Guidance language, pulled mechanically out of an 8-K Exhibit 99.1 and diffed against last quarter's.

WHY A PRESS RELEASE AND NOT A TRANSCRIPT
----------------------------------------
NOTES.md section 4 establishes that there is no free, legal, automatable source
of full earnings-call transcripts. The 8-K Item 2.02 press release, filed as
Exhibit 99.1, is the closest free substitute: it carries the prepared numbers
and the guidance language, and none of the analyst questions. ``edgar.py``
already resolves an 8-K to that exhibit and strips it to text. This module reads
the text.

WHAT "MECHANICALLY" MEANS HERE
------------------------------
A sentence is a guidance sentence when it contains a forward-looking word
(``expect``, ``anticipate``, ``outlook``, ``guidance``, ``forecast``, ``guide``,
``target``) and is not safe-harbour boilerplate. Inside such a sentence, each
mention of a metric the module knows (revenue, EPS, gross margin, operating
margin, operating expenses, tax rate, capital expenditures, free cash flow,
operating income) starts a clause that runs to the next metric mention, and the
first number, range or "approximately" figure in the clause is that metric's
guide. The period is the nearest period phrase before the clause in the same
sentence ("for the fourth quarter of fiscal 2026", "for fiscal 2026", "September
quarter"), else the first one after it, else unspecified. The verbatim sentence
travels with every item so the reader can check the machine's reading against
the words.

Two releases are then matched on (metric, period). The same period in both is
raised, lowered, narrowed, widened or reiterated on the midpoint and the width;
a period only in the newer release is introduced; a fiscal-year period only in
the older one is *not repeated*, which is not the same as withdrawn; a quarter
only in the older one has lapsed, because it is normally the quarter the new
release reports.

WHAT IT CANNOT DO, AND SAYS
---------------------------
It does not read tone. A guide given in words without a number ("modest growth")
is invisible to it and is listed as such rather than dropped silently when the
sentence carries a metric word. It does not know that "the September quarter" and
"the fourth quarter of fiscal 2026" are the same period at a September year-end
filer unless both phrasings appear, so a company that switches phrasing between
releases will show one lapsed and one introduced where a reader would see one
reiterated. Those are the failure modes of a regular expression, and the output
labels itself ``mechanical`` so nobody mistakes it for the read narrative that
the sixteen research notes carry.

WHETHER THIS HAS RUN FOR REAL
-----------------------------
No. It has never seen a live exhibit. It is tested on two hand-built releases for
a fictional filer under ``tests/fixtures/ex991_prior.htm`` and
``ex991_current.htm``, written so that every classification the differ knows
appears at least once, with the expected answers worked out by hand first.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths

__all__ = [
    "GuidanceItem",
    "GuidanceDiffItem",
    "GuidanceDiff",
    "extract",
    "diff",
    "split_sentences",
    "METRICS",
    "not_run_block",
    "load_diff",
]

FORWARD = re.compile(r"\b(expects?|expected|expecting|anticipates?|anticipated|outlook|guidance|guided?|guiding|"
                     r"forecasts?|forecasting|targets?|targeting|projects?|projected)\b", re.I)
BOILERPLATE = re.compile(r"forward-looking statements?|safe harbor|private securities litigation reform act|"
                         r"undue reliance|risks and uncertainties", re.I)

# Metric keywords, most specific first so "non-GAAP diluted earnings per share"
# is one metric and not "earnings" plus something else.
METRICS: Tuple[Tuple[str, str], ...] = (
    ("gross_margin", r"gross margins?"),
    ("operating_margin", r"operating margins?"),
    ("operating_expenses", r"operating expenses?|opex"),
    ("operating_income", r"operating income|income from operations"),
    ("tax_rate", r"(?:effective )?tax rate"),
    ("capex", r"capital expenditures?|capex"),
    ("free_cash_flow", r"free cash flow"),
    ("eps", r"(?:diluted |net )?(?:earnings|income|loss) per (?:diluted )?share|\bEPS\b"),
    ("revenue", r"revenues?|net sales|total sales|\bsales\b"),
)
_METRIC_RE = re.compile("|".join(f"(?P<{k}>{p})" for k, p in METRICS), re.I)
_LABEL = {
    "revenue": "revenue", "eps": "EPS", "gross_margin": "gross margin", "operating_margin": "operating margin",
    "operating_expenses": "operating expenses", "operating_income": "operating income", "tax_rate": "tax rate",
    "capex": "capital expenditures", "free_cash_flow": "free cash flow",
}
_PERCENT_METRICS = {"gross_margin", "operating_margin", "tax_rate"}

_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_PERIOD = re.compile(
    r"(?P<qword>first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter(?: of)?(?: fiscal(?: year)?| FY)?[ ]?(?P<qyear>\d{4})?"
    r"|(?P<q>Q[1-4])[ ]?(?:FY)?[ ]?(?P<qyear2>\d{2,4})?"
    r"|(?:full[- ]year|fiscal(?: year)?|FY)[ ]?(?P<fyyear>\d{4})"
    r"|(?P<fullyear>full year|full fiscal year|fiscal year)(?![ ]?\d)"
    r"|(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)[ ]quarter",
    re.I,
)

_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"
_UNIT = r"(?:\s*(?:billion|million|thousand|percent|%|bps|basis points))?"
_RANGE = re.compile(
    rf"(?:between\s+)?(?P<cur1>\$|€|£)?(?P<a>{_NUM})(?P<u1>{_UNIT})\s*(?:to|and|-|–|—)\s*(?P<cur2>\$|€|£)?(?P<b>{_NUM})(?P<u2>{_UNIT})",
    re.I,
)
_PLUSMINUS = re.compile(rf"(?P<cur>\$|€|£)?(?P<a>{_NUM})(?P<u1>{_UNIT})\s*(?:plus or minus|±|\+/-)\s*\$?(?P<b>{_NUM})(?P<u2>{_UNIT})", re.I)
_POINT = re.compile(rf"(?:approximately|about|roughly|around|of|at|be|near|~)?\s*(?P<cur>\$|€|£)?(?P<a>{_NUM})(?P<u>{_UNIT})", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_ABBREV = re.compile(r"\b(Inc|Corp|Ltd|Co|vs|approx|U\.S|No)\.\s")


def split_sentences(text: str) -> List[str]:
    """Sentences, with the common abbreviations protected so "Inc. reported" does not split."""
    flat = re.sub(r"\s+", " ", text)
    protected = _ABBREV.sub(lambda m: m.group(0).replace(".", "․"), flat)
    return [s.replace("․", ".").strip() for s in _SENT.split(protected) if s.strip()]


def _scale(unit: str) -> Tuple[float, str]:
    u = (unit or "").strip().lower()
    if u == "billion":
        return 1e9, "USD"
    if u == "million":
        return 1e6, "USD"
    if u == "thousand":
        return 1e3, "USD"
    if u in ("percent", "%"):
        return 1.0, "percent"
    if u in ("bps", "basis points"):
        return 0.01, "percent"
    return 1.0, ""


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _clean(x: Optional[float]) -> Optional[float]:
    """4.1 billion is 4,100,000,000, not 4,099,999,999.9999995."""
    return None if x is None else round(x, 6)


@dataclass(frozen=True)
class GuidanceItem:
    metric: str
    period: str
    low: Optional[float]
    high: Optional[float]
    unit: str  # "USD" | "percent" | "" (a bare number, EPS typically)
    kind: str  # "range" | "point" | "qualitative"
    clause: str
    sentence: str

    @property
    def label(self) -> str:
        return _LABEL.get(self.metric, self.metric)

    @property
    def mid(self) -> Optional[float]:
        if self.low is None or self.high is None:
            return None
        return (self.low + self.high) / 2.0

    @property
    def width(self) -> Optional[float]:
        if self.low is None or self.high is None:
            return None
        return self.high - self.low

    def describe(self) -> str:
        if self.kind == "qualitative" or self.low is None:
            return "no number given"
        return _fmt_range(self.low, self.high, self.unit, self.metric)

    def to_dict(self) -> Dict[str, Any]:
        return {"metric": self.metric, "label": self.label, "period": self.period, "low": self.low, "high": self.high,
                "unit": self.unit, "kind": self.kind, "as_stated": self.describe(), "clause": self.clause,
                "sentence": self.sentence}


def _fmt_num(x: float, unit: str, metric: str) -> str:
    if unit == "percent":
        return f"{x:g}%"
    if unit == "USD":
        if abs(x) >= 1e9:
            return f"${x / 1e9:g} billion"
        if abs(x) >= 1e6:
            return f"${x / 1e6:g} million"
        return f"${x:,.2f}"
    if metric == "eps":
        return f"${x:.2f}"
    return f"{x:g}"


def _fmt_range(lo: float, hi: Optional[float], unit: str, metric: str) -> str:
    if hi is None or hi == lo:
        return _fmt_num(lo, unit, metric)
    return f"{_fmt_num(lo, unit, metric)} to {_fmt_num(hi, unit, metric)}"


def _norm_period(m: "re.Match[str]", default_year: Optional[int]) -> str:
    if m.group("qword"):
        q = _ORD[m.group("qword").lower()]
        y = m.group("qyear")
        return f"Q{q} FY{y}" if y else f"Q{q}" + (f" FY{default_year}" if default_year else "")
    if m.group("q"):
        y = m.group("qyear2")
        if y and len(y) == 2:
            y = "20" + y
        return f"{m.group('q').upper()} FY{y}" if y else m.group("q").upper()
    if m.group("fyyear"):
        return f"FY{m.group('fyyear')}"
    if m.group("fullyear"):
        return f"FY{default_year}" if default_year else "full year"
    if m.group("month"):
        return f"{m.group('month').capitalize()} quarter"
    return "unspecified"


def _periods_in(sentence: str, default_year: Optional[int]) -> List[Tuple[int, str]]:
    return [(m.start(), _norm_period(m, default_year)) for m in _PERIOD.finditer(sentence)]


def _parse_value(clause: str, metric: str) -> Tuple[Optional[float], Optional[float], str, str]:
    """(low, high, unit, kind) from the first figure in the clause."""
    m = _RANGE.search(clause)
    pm = _PLUSMINUS.search(clause)
    if pm and (not m or pm.start() <= m.start()):
        k, u = _scale(pm.group("u1") or pm.group("u2"))
        centre = _to_float(pm.group("a")) * k
        k2, _ = _scale(pm.group("u2") or pm.group("u1"))
        half = _to_float(pm.group("b")) * k2
        unit = u or ("USD" if pm.group("cur") else "")
        return centre - half, centre + half, unit, "range"
    if m:
        k1, u1 = _scale(m.group("u1") or m.group("u2"))
        k2, u2 = _scale(m.group("u2") or m.group("u1"))
        a, b = _to_float(m.group("a")) * k1, _to_float(m.group("b")) * k2
        unit = u1 or u2 or ("USD" if (m.group("cur1") or m.group("cur2")) else "")
        return min(a, b), max(a, b), unit, "range"
    for p in _POINT.finditer(clause):
        k, u = _scale(p.group("u"))
        x = _to_float(p.group("a")) * k
        unit = u or ("USD" if p.group("cur") else "")
        # "in fiscal 2027" and "by 2030" are dates, not guides: a bare four-digit
        # number in the calendar's range with no unit and no currency is skipped.
        if not unit and 1990 <= x <= 2100 and "." not in p.group("a") and "," not in p.group("a"):
            continue
        if metric in _PERCENT_METRICS and not unit:
            unit = "percent"
        return x, x, unit, "point"
    return None, None, "", "qualitative"


def extract(text: str, *, default_year: Optional[int] = None) -> List[GuidanceItem]:
    """Every (metric, period, figure) a forward-looking sentence in ``text`` states."""
    out: List[GuidanceItem] = []
    seen: set = set()
    for sentence in split_sentences(text):
        if not FORWARD.search(sentence) or BOILERPLATE.search(sentence):
            continue
        periods = _periods_in(sentence, default_year)
        hits = list(_METRIC_RE.finditer(sentence))
        if not hits:
            continue
        for i, h in enumerate(hits):
            metric = h.lastgroup or "other"
            end = hits[i + 1].start() if i + 1 < len(hits) else len(sentence)
            clause = sentence[h.start():end].strip(" ,;")
            # A prior-period comparison inside the clause ("up from its prior
            # outlook of $4.0 billion") must not become the guide: cut the clause
            # at the first such phrase before reading the number.
            cut = re.search(r"\b(up from|down from|compared (?:to|with)|versus|vs\.?|from (?:its |the )?prior)\b", clause, re.I)
            head = clause[:cut.start()] if cut else clause
            low, high, unit, kind = _parse_value(head, metric)
            low, high = _clean(low), _clean(high)
            before = [p for pos, p in periods if pos <= h.start()]
            after = [p for pos, p in periods if pos > h.start()]
            period = before[-1] if before else (after[0] if after else "unspecified")
            key = (metric, period, low, high)
            if key in seen:
                continue
            seen.add(key)
            out.append(GuidanceItem(metric, period, low, high, unit, kind, clause, sentence))
    return out


# -- the diff ----------------------------------------------------------------------


@dataclass(frozen=True)
class GuidanceDiffItem:
    metric: str
    period: str
    change: str  # raised | lowered | narrowed | widened | reiterated | introduced | not repeated | lapsed | changed | not comparable
    current: Optional[GuidanceItem]
    prior: Optional[GuidanceItem]
    detail: str

    @property
    def label(self) -> str:
        return _LABEL.get(self.metric, self.metric)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "what": f"{self.period} {self.label}",
            "metric": self.metric, "period": self.period, "change": self.change, "detail": self.detail,
            "quote": self.current.sentence if self.current else None,
            "prior_quote": self.prior.sentence if self.prior else None,
            "current": self.current.to_dict() if self.current else None,
            "prior": self.prior.to_dict() if self.prior else None,
            "mechanical": True,
        }


@dataclass
class GuidanceDiff:
    items: List[GuidanceDiffItem]
    current_count: int
    prior_count: int
    not_determinable: List[str] = field(default_factory=list)

    def by_change(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for it in self.items:
            out[it.change] = out.get(it.change, 0) + 1
        return dict(sorted(out.items()))


_ORDER = {"raised": 0, "lowered": 1, "narrowed": 2, "widened": 3, "changed": 4, "not comparable": 5,
          "reiterated": 6, "introduced": 7, "not repeated": 8, "lapsed": 9}


def _is_quarter(period: str) -> bool:
    return period.startswith("Q") or period.endswith(" quarter")


def _compare(cur: GuidanceItem, pri: GuidanceItem) -> Tuple[str, str]:
    if cur.kind == "qualitative" or pri.kind == "qualitative":
        return "not comparable", (f"Stated as {cur.describe()} now and {pri.describe()} last quarter; at least one "
                                  "side has no figure to compare.")
    if cur.unit != pri.unit:
        return "not comparable", f"The two releases state it in different units ({cur.unit or 'bare'} vs {pri.unit or 'bare'})."
    cm, pm = cur.mid, pri.mid
    assert cm is not None and pm is not None
    tol = 0.0005 * max(abs(pm), 1e-9) if cur.unit != "percent" else 0.05
    if abs(cm - pm) <= tol:
        cw, pw = cur.width or 0.0, pri.width or 0.0
        if abs(cw - pw) <= tol:
            return "reiterated", f"Same figure both times: {cur.describe()}."
        return ("narrowed" if cw < pw else "widened"), (
            f"Same midpoint, {'tighter' if cw < pw else 'wider'} range: {pri.describe()} last quarter, {cur.describe()} now.")
    direction = "raised" if cm > pm else "lowered"
    if cur.unit == "percent":
        move = f"{cm - pm:+.1f} points"
    else:
        move = f"{(cm / pm - 1):+.1%}" if pm else "from zero"
    return direction, f"Midpoint {move}: {pri.describe()} last quarter, {cur.describe()} now."


def diff(current: Sequence[GuidanceItem], prior: Sequence[GuidanceItem]) -> GuidanceDiff:
    """Match on (metric, period) and classify. Every item carries both verbatim sentences."""
    cur = {(g.metric, g.period): g for g in current}
    pri = {(g.metric, g.period): g for g in prior}
    items: List[GuidanceDiffItem] = []
    nd: List[str] = []
    for key, g in cur.items():
        p = pri.get(key)
        if p is None:
            items.append(GuidanceDiffItem(g.metric, g.period, "introduced", g, None,
                                          f"Guided for the first time in this release: {g.describe()}."))
            continue
        change, detail = _compare(g, p)
        items.append(GuidanceDiffItem(g.metric, g.period, change, g, p, detail))
    for key, p in pri.items():
        if key in cur:
            continue
        if _is_quarter(p.period):
            items.append(GuidanceDiffItem(p.metric, p.period, "lapsed", None, p,
                                          f"Guided last quarter at {p.describe()}; that period is normally the one this "
                                          "release reports, so its absence is expected rather than a withdrawal."))
        else:
            items.append(GuidanceDiffItem(p.metric, p.period, "not repeated", None, p,
                                          f"Guided last quarter at {p.describe()} and not mentioned with a figure in this "
                                          "release. Not repeated is not the same as withdrawn; read the release."))
    for g in list(current) + list(prior):
        if g.kind == "qualitative":
            nd.append(f"A forward-looking sentence names {g.label} for {g.period} without a figure the extractor "
                      f"could read: “{g.sentence}”")
    items.sort(key=lambda it: (_ORDER.get(it.change, 99), it.period, it.metric))
    return GuidanceDiff(items=items, current_count=len(current), prior_count=len(prior), not_determinable=nd)


# -- the record block ----------------------------------------------------------------

CAVEAT = (
    "Mechanical. Two press releases, sentences with a forward-looking word and a figure, matched on metric "
    "and period. It does not read tone, it cannot see a guide given without a number, and a filer that "
    "changes how it names a period between releases shows one lapsed and one introduced where a reader "
    "would see one reiterated. The verbatim sentence sits under every row so the machine's reading can be checked."
)


def not_run_block(ticker: str, why: str) -> Dict[str, Any]:
    return {
        "available": False,
        "status": "NOT RUN",
        "why": why,
        "command": f"SEC_USER_AGENT=\"Name email\" python scripts/guidance_diff.py {ticker}",
        "caveat": CAVEAT,
    }


def build_block(ticker: str, *, current_meta: Dict[str, Any], prior_meta: Dict[str, Any],
                current_text: str, prior_text: str, default_year: Optional[int] = None,
                built_at: Optional[str] = None) -> Dict[str, Any]:
    cur = extract(current_text, default_year=default_year)
    pri = extract(prior_text, default_year=default_year)
    d = diff(cur, pri)
    return {
        "available": True,
        "status": "mechanical",
        "ticker": ticker,
        "built_at": built_at or dt.datetime.now().replace(microsecond=0).isoformat(),
        "current": current_meta,
        "prior": prior_meta,
        "items": [it.to_dict() for it in d.items],
        "summary": d.by_change(),
        "extracted": {"current": d.current_count, "prior": d.prior_count},
        "not_determinable": d.not_determinable,
        "caveat": CAVEAT,
        "source": "8-K Exhibit 99.1 press releases on SEC EDGAR, read mechanically",
    }


def load_diff(ticker: str, directory: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The committed diff for a ticker, written by scripts/guidance_diff.py, or None."""
    d = Path(directory) if directory is not None else paths.ANALYSIS_DIR / "_guidance"
    p = d / f"{ticker.upper()}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
