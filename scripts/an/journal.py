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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import paths

__all__ = ["JournalEntry", "parse", "load", "by_ticker"]

_ENTRY = re.compile(r"^## (\d{4}-\d{2}-\d{2}) (\S+)\s*(.*?)$\n(.*?)(?=^## |\Z)", re.M | re.S)


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
    return v or None


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
    out: List[JournalEntry] = []
    for m in _ENTRY.finditer(text):
        body = m.group(4)
        conv = _num(_field(body, "Conviction"))
        out.append(
            JournalEntry(
                date=m.group(1),
                ticker=m.group(2).upper(),
                title=m.group(3).strip(),
                price_at_call=_num(_field(body, "Price at call")),
                thesis=_field(body, "Thesis"),
                wrong_if=_field(body, "Wrong if"),
                target_size=_field(body, "Target size"),
                conviction=int(conv) if conv is not None and 1 <= conv <= 5 else None,
                bucket=_field(body, "Bucket"),
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
