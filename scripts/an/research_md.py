"""Parse ``research/TICKER.md`` into structure.

These sixteen files are the most valuable thing in the repo. They are hand-written
deep dives with real per-fiscal-year statement tables, what management said on the
last two calls, a bear case written to talk you out of the position, named thesis
killers with price triggers, and a source list with URLs and read dates. Nothing
generated from an API comes close.

All sixteen follow ``research/_TEMPLATE.md`` exactly, which makes them parseable
rather than merely readable. The parser is strict about that: if a note stops
matching the template, :func:`parse` raises instead of silently returning half a
page, because a half-parsed research note rendered as a finished analysis page is
worse than no page.

The stats line (line 3 of every file) is prose, not a header, so the numbers in it
are pulled out with named regexes and every one of them is optional. Where the
regex misses, the field is ``None`` and the page falls back to the CSV.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import paths

__all__ = ["Source", "FinancialRow", "ResearchNote", "parse", "parse_file", "load_all", "REQUIRED_SECTIONS"]

REQUIRED_SECTIONS = (
    "What the company does",
    "Why it is on the list",
    "Business quality",
    "Financials",
    "Valuation",
    "The bear case",
    "Key risks and thesis killers",
    "Verdict",
    "Sources",
)

# The template calls it "Last two earnings calls"; every written note says "reports".
EARNINGS_SECTION_ALIASES = ("Last two earnings reports", "Last two earnings calls")


@dataclass(frozen=True)
class Source:
    title: str
    url: Optional[str]
    read_date: Optional[str]
    raw: str

    @property
    def domain(self) -> Optional[str]:
        if not self.url:
            return None
        m = re.match(r"https?://(?:www\.)?([^/]+)", self.url)
        return m.group(1) if m else None


@dataclass(frozen=True)
class FinancialRow:
    fiscal_year: str
    period_end: Optional[str]
    revenue_bn: Optional[float]
    gross_margin: Optional[float]
    operating_margin: Optional[float]
    fcf_bn: Optional[float]
    diluted_shares_m: Optional[float]
    net_debt_bn: Optional[float]


@dataclass(frozen=True)
class HeadlineStats:
    """Numbers lifted out of the prose stats line. Every field is best-effort."""

    quality_rank: Optional[int] = None
    quality_score: Optional[float] = None
    buckets: List[str] = field(default_factory=list)
    price: Optional[float] = None
    currency: Optional[str] = None
    as_of: Optional[str] = None
    forward_pe: Optional[float] = None
    median_pe: Optional[float] = None
    pe_vs_median_pct: Optional[float] = None
    ev_ebitda: Optional[float] = None
    dd_52w_pct: Optional[float] = None
    dd_ath_pct: Optional[float] = None
    eps_fy1_chg_90d_pct: Optional[float] = None
    short_pct_float: Optional[float] = None
    next_earnings: Optional[str] = None


@dataclass(frozen=True)
class ResearchNote:
    ticker: str
    company: str
    path: Path
    stats_line: str
    headline: HeadlineStats
    sections: Dict[str, str]
    financials: List[FinancialRow]
    sources: List[Source]

    def section(self, name: str, default: str = "") -> str:
        return self.sections.get(name, default)

    @property
    def earnings(self) -> str:
        for alias in EARNINGS_SECTION_ALIASES:
            if self.sections.get(alias):
                return self.sections[alias]
        return ""

    @property
    def verdict_action(self) -> Optional[str]:
        """'Buy now', 'Buy on pullback to $X', 'Watchlist', 'Pass' — the first sentence."""
        v = self.section("Verdict").strip()
        if not v:
            return None
        return re.split(r"(?<=[a-z0-9)])\.\s", v)[0].strip().rstrip(".")

    @property
    def conviction(self) -> Optional[int]:
        m = re.search(r"[Cc]onviction\s+(\d)\s*(?:of|/)\s*5", self.section("Verdict"))
        return int(m.group(1)) if m else None

    @property
    def price_trigger(self) -> Optional[float]:
        """The 'I was wrong' price named in the risks section."""
        txt = self.section("Key risks and thesis killers")
        m = re.search(r"[Pp]rice trigger[:\s]+\$?([\d,]+(?:\.\d+)?)", txt)
        if m:
            return float(m.group(1).replace(",", ""))
        m = re.search(r"close under \$?([\d,]+(?:\.\d+)?)", txt)
        return float(m.group(1).replace(",", "")) if m else None

    @property
    def thesis_killers(self) -> List[str]:
        """The named, falsifiable conditions, as whole sentences.

        Split on sentence boundaries only. An earlier version also split on commas,
        which turned "A, B, or C" into three fragments and printed "or buybacks
        pausing" as if it were a standalone condition. A falsifier that does not
        parse as a sentence is not one anybody can check.
        """
        txt = self.section("Key risks and thesis killers")
        m = re.search(r"[Tt]hesis killers?[:\s]+(.*?)(?:\s*[Pp]rice trigger|$)", txt, re.S)
        body = m.group(1) if m else txt
        parts = re.split(r"(?<=[a-z0-9%)])\.\s+|;\s+", body)
        return [p.strip(" .") for p in parts if len(p.strip()) > 12]


def _num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip().replace(",", "").replace("%", "").replace("$", "")
    # A number lifted out of prose keeps the sentence's full stop: "EV/EBITDA 37.5."
    s = s.rstrip(".") if s.count(".") > 1 or s.endswith(".") else s
    if s in ("", "-", "n/a", "na", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_stats(line: str) -> HeadlineStats:
    def g(pattern: str, cast=_num, group: int = 1):
        m = re.search(pattern, line)
        return cast(m.group(group)) if m else None

    buckets: List[str] = []
    mb = re.search(r"Bucket:\s*([^.]+)\.", line)
    if mb:
        raw = mb.group(1).lower()
        if "compounder" in raw:
            buckets.append("compounder")
        if "cyclical" in raw:
            buckets.append("cyclical turn")

    return HeadlineStats(
        quality_rank=g(r"[Qq]uality rank (\d+) of \d+", lambda s: int(float(s))),
        quality_score=g(r"score (\d+(?:\.\d+)?)"),
        buckets=buckets,
        price=g(r"Price ([\d,]+(?:\.\d+)?)"),
        currency=g(r"Price [\d,.]+\s+([A-Z]{3})", str),
        as_of=g(r"on (\d{4}-\d{2}-\d{2})", str),
        forward_pe=g(r"Forward P/E ([\d.]+)"),
        median_pe=g(r"median ([\d.]+)"),
        pe_vs_median_pct=g(r"median [\d.]+ \(([-+]?[\d.]+)%\)"),
        ev_ebitda=g(r"EV/EBITDA ([\d.]+)"),
        dd_52w_pct=g(r"([-+]?[\d.]+)% from 52 week high"),
        dd_ath_pct=g(r"([-+]?[\d.]+)% from all time high"),
        eps_fy1_chg_90d_pct=g(r"EPS estimate moved ([-+]?[\d.]+)%"),
        short_pct_float=g(r"[Ss]hort interest ([\d.]+)%"),
        next_earnings=g(r"Next earnings (\d{4}-\d{2}-\d{2})", str),
    )


_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")


def _parse_financials(section: str) -> List[FinancialRow]:
    rows: List[FinancialRow] = []
    for line in section.splitlines():
        m = _TABLE_ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        head = cells[0].lower()
        if head.startswith("fiscal") or head.startswith("period"):
            continue
        fy = re.search(r"(FY\d{4}|\d{4})", cells[0])
        if not fy:
            continue
        end = re.search(r"(\d{4}-\d{2}-\d{2})", cells[0])
        pad = cells + [""] * (7 - len(cells))
        rows.append(
            FinancialRow(
                fiscal_year=fy.group(1),
                period_end=end.group(1) if end else None,
                revenue_bn=_num(pad[1]),
                gross_margin=(_num(pad[2]) / 100 if _num(pad[2]) is not None else None),
                operating_margin=(_num(pad[3]) / 100 if _num(pad[3]) is not None else None),
                fcf_bn=_num(pad[4]),
                diluted_shares_m=_num(pad[5]),
                net_debt_bn=_num(pad[6]),
            )
        )
    return rows


_SOURCE_LINK = re.compile(r"^\s*-\s*\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)(?:,\s*read (?P<date>\d{4}-\d{2}-\d{2}))?")
_SOURCE_PLAIN = re.compile(r"^\s*-\s*(?P<title>[^\[].*?)(?:,\s*pulled (?P<date>\d{4}-\d{2}-\d{2}))?\s*$")


def _parse_sources(section: str) -> List[Source]:
    out: List[Source] = []
    for line in section.splitlines():
        if not line.strip().startswith("-"):
            continue
        m = _SOURCE_LINK.match(line)
        if m:
            out.append(Source(m.group("title").strip(), m.group("url").strip(), m.group("date"), line.strip()))
            continue
        m = _SOURCE_PLAIN.match(line)
        if m and m.group("title").strip():
            out.append(Source(m.group("title").strip(), None, m.group("date"), line.strip()))
    return out


class ParseError(ValueError):
    pass


def parse(text: str, path: Optional[Path] = None) -> ResearchNote:
    path = path or Path("<memory>")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ParseError(f"{path}: first line must be '# TICKER  Company Name'")
    title = lines[0][2:].strip()
    parts = title.split(None, 1)
    ticker = parts[0].strip().upper()
    company = parts[1].strip() if len(parts) > 1 else ticker

    stats_line = ""
    for ln in lines[1:6]:
        if ln.strip() and not ln.startswith("#"):
            stats_line = ln.strip()
            break

    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for ln in lines[1:]:
        if ln.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = ln[3:].strip()
            buf = []
        elif current is not None:
            buf.append(ln)
    if current is not None:
        sections[current] = "\n".join(buf).strip()

    missing = [s for s in REQUIRED_SECTIONS if not sections.get(s)]
    if missing:
        raise ParseError(f"{path}: missing or empty sections {missing}")
    if not any(sections.get(a) for a in EARNINGS_SECTION_ALIASES):
        raise ParseError(f"{path}: no earnings section (looked for {EARNINGS_SECTION_ALIASES})")

    financials = _parse_financials(sections["Financials"])
    if len(financials) < 3:
        raise ParseError(f"{path}: financial table has {len(financials)} year rows, expected at least 3")

    sources = _parse_sources(sections["Sources"])
    if not sources:
        raise ParseError(f"{path}: no sources parsed")

    return ResearchNote(
        ticker=ticker,
        company=company,
        path=path,
        stats_line=stats_line,
        headline=_parse_stats(stats_line),
        sections=sections,
        financials=financials,
        sources=sources,
    )


def parse_file(path: Path) -> ResearchNote:
    return parse(Path(path).read_text(encoding="utf-8"), Path(path))


def load_all(directory: Optional[Path] = None) -> Dict[str, ResearchNote]:
    d = Path(directory) if directory else paths.RESEARCH_DIR
    out: Dict[str, ResearchNote] = {}
    for p in sorted(d.glob("*.md")):
        if p.stem.startswith("_"):
            continue
        note = parse_file(p)
        out[note.ticker] = note
    return out
