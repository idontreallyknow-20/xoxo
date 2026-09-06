"""Parse ``journal.md`` into entries.

The decision log is append-only and the dashboard already parses it, but
``build_dashboard.py`` does that inline and pulls yfinance in at import time. This
is the same grammar, standalone, so the analysis layer can read a ticker's own
history of calls without dragging the network in.

Grammar, from the header of ``journal.md`` itself::

    ## YYYY-MM-DD TICKER  Recommendation: <action>
    Price at call: 123.45
    Thesis: ...
    Wrong if: ...
    Target size: $X (Y% of capital)
    Conviction: 1 to 5
    Bucket: compounder / cyclical turn
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import paths

__all__ = ["JournalEntry", "parse", "load", "by_ticker"]

_ENTRY = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) ([A-Z0-9.\-]+(?:\s+[A-Z0-9.\-]+)*?)\s{2,}(.*?)$\n(.*?)(?=^## |\Z)",
    re.M | re.S)
"""Date, one or more tickers, then the title after two or more spaces.

The original pattern captured a single ``\S+`` as the ticker, which silently
dropped four of the five names in::

    ## 2026-09-04 META NOW ACN AMAT NVR  Recommendation: Watch or Pass

NOW, ACN, AMAT and NVR were logged calls that no longer existed as far as anything
downstream was concerned. The template separates the ticker field from the title
with two spaces, which is what makes a multi-ticker heading parseable at all.
"""

_ABSENT = {"n/a", "na", "none", "-", "--", "tbd", "?"}
"""Placeholders that mean a field was not filled in.

``journal.md`` writes a literal "n/a" for a Watch-or-Pass entry with no falsifier.
That string is truthy in both Python and JavaScript, so a fallback chain of
``logged.wrong_if || killers[0] || "no falsifier recorded"`` stopped at it and the
page rendered a standing call whose falsifier was the word "n/a"."""


def _split_for(raw: Optional[str], tickers: List[str], ticker: str) -> Optional[float]:
    """A multi-ticker heading writes its prices as "610.68 / 145.59 / 193.12".

    Take the one that lines up with this ticker, and take nothing when the counts
    do not match rather than handing back the first name's price for all five.
    """
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split("/")]
    if len(tickers) > 1:
        if len(parts) != len(tickers):
            return None
        raw = parts[tickers.index(ticker)]
    return _num(raw)


@dataclass(frozen=True)
class JournalEntry:
    date: str
    ticker: str
    title: str
    price_at_call: Optional[float]
    thesis: Optional[str]
    wrong_if: Optional[str]
    target_size: Optional[str]
    conviction: Optional[int]
    bucket: Optional[str]
    shared_with: List[str] = field(default_factory=list)
    """Other tickers logged in the same heading, when one entry covered several."""

    @property
    def is_shared(self) -> bool:
        return bool(self.shared_with)

    @property
    def is_system(self) -> bool:
        return self.ticker.upper() == "SYSTEM"

    @property
    def action(self) -> Optional[str]:
        m = re.search(r"Recommendation:\s*(.+)$", self.title)
        return m.group(1).strip() if m else (self.title.strip() or None)

    @property
    def target_usd(self) -> Optional[float]:
        if not self.target_size:
            return None
        m = re.search(r"\$([\d,]+)", self.target_size)
        return float(m.group(1).replace(",", "")) if m else None


def _field(body: str, name: str) -> Optional[str]:
    m = re.search(r"^" + re.escape(name) + r":\s*(.*)$", body, re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if not v or v.lower().strip(". ") in _ABSENT:
        return None
    return v


def _num(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    m = re.search(r"-?[\d,]+(?:\.\d+)?", v)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse(text: str) -> List[JournalEntry]:
    """One entry per ticker. A heading naming five names produces five entries."""
    out: List[JournalEntry] = []
    for m in _ENTRY.finditer(text):
        body = m.group(4)
        conv = _num(_field(body, "Conviction"))
        tickers = [t for t in m.group(2).split() if t]
        for ticker in tickers:
            out.append(
                JournalEntry(
                    date=m.group(1),
                    ticker=ticker.upper(),
                    title=m.group(3).strip(),
                    price_at_call=_split_for(_field(body, "Price at call"), tickers, ticker),
                    thesis=_field(body, "Thesis"),
                    wrong_if=_field(body, "Wrong if"),
                    target_size=_field(body, "Target size"),
                    conviction=int(conv) if conv is not None and 1 <= conv <= 5 else None,
                    bucket=_field(body, "Bucket"),
                    shared_with=[t.upper() for t in tickers if t.upper() != ticker.upper()],
                )
            )
    return out


def load(path: Optional[Path] = None) -> List[JournalEntry]:
    p = Path(path) if path else paths.JOURNAL_MD
    if not p.exists():
        return []
    return parse(p.read_text(encoding="utf-8"))


def by_ticker(entries: Optional[List[JournalEntry]] = None) -> Dict[str, List[JournalEntry]]:
    """Newest first, system entries excluded."""
    entries = load() if entries is None else entries
    out: Dict[str, List[JournalEntry]] = {}
    for e in entries:
        if e.is_system:
            continue
        out.setdefault(e.ticker, []).append(e)
    for v in out.values():
        v.sort(key=lambda e: e.date, reverse=True)
    return out
