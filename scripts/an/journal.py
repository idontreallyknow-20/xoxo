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

_ENTRY = re.compile(r"^## (\d{4}-\d{2}-\d{2}) +(\S.*?)$\n(.*?)(?=^## |\Z)", re.M | re.S)
"""Date, then the rest of the heading line, then the body up to the next heading.

The original pattern captured a single ``\S+`` as the ticker, which silently
dropped four of the five names in::

    ## 2026-09-04 META NOW ACN AMAT NVR  Recommendation: Watch or Pass

NOW, ACN, AMAT and NVR were logged calls that no longer existed as far as anything
downstream was concerned. The obvious repair, requiring the template's two-space
separator between the ticker field and the title, traded that bug for a worse one:
a heading typed with one space matched nothing, and ``parse`` returned ``[]`` for
the whole file rather than one bad entry. ``journal.md`` is append-only and typed
by hand, so the parser splits the heading in :func:`_split_heading` instead, where
it can fall back rather than fail.
"""

_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)*$")
"""A ticker is all caps, starts with a letter, and may carry a suffix: ``DPM.TO``."""


def _split_heading(rest: str) -> tuple[List[str], str]:
    """Heading text after the date, split into tickers and title.

    The template writes two or more spaces between the two fields, and when that
    separator is present it decides the split outright: a title may legitimately
    open with an all-caps word, and the separator is the only thing that can tell
    ``NVR  RECAP of the quarter`` from ``NVR RECAP``.

    Without it, take ticker-shaped tokens from the left and stop at the first that
    is not one. ``Recommendation:`` stops it because of the colon and the lower
    case. This recovers a heading typed with one space, which is the whole point;
    it can still mis-split a one-space heading whose title starts in caps, and
    that is the acceptable half of the trade.
    """
    if "  " in rest:
        head, _, title = rest.partition("  ")
        return [t for t in head.split() if t], title.strip()
    tokens = rest.split()
    n = 0
    while n < len(tokens) and _TICKER.match(tokens[n]):
        n += 1
    if not n:
        return [], rest.strip()
    return tokens[:n], " ".join(tokens[n:]).strip()

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
        body = m.group(3)
        conv = _num(_field(body, "Conviction"))
        tickers, title = _split_heading(m.group(2))
        for ticker in tickers:
            out.append(
                JournalEntry(
                    date=m.group(1),
                    ticker=ticker.upper(),
                    title=title,
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
