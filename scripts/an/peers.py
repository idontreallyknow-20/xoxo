"""Valuation against peers, and against what the quality justifies.

WHY THIS EXISTS
---------------
The pipeline's only valuation lens is a company against its own past: forward P/E
today versus the median at four fiscal year ends. That comparison has two holes
the pages already admit to, and this module addresses both.

*A four-point median is thin.* Four year-ends is not a distribution. One unusual
year moves it a long way, and a company whose business changed has an own-history
median that describes a different company.

*Own history says nothing about whether the old multiple was deserved.* A stock at
half its historical multiple is cheap only if the historical multiple was right. A
software company that used to grow 30% and now grows 8% should trade at half its
old multiple, and "60% below its own median" is then a description of the
derating, not an argument against it.

WHAT THIS ADDS
--------------
Two things, both computed across the 150 rather than down one company's history.

*Where it sits among its peers.* A percentile of forward P/E, EV/EBITDA and price
to sales inside a peer set, with the peer set named so it can be argued with. The
set is the industry when there are enough names in it and the sector otherwise,
because standardising against four peers manufactures a number.

*Whether the cheapness is deserved.* Inside the peer set, compare where a name
ranks on price against where it ranks on the things that justify a price: return
on invested capital, free cash flow margin, revenue growth, margin stability. A
name in the cheapest quartile on multiple and the best quartile on quality is
interesting. A name in the cheapest quartile on both multiple and quality is
probably priced correctly, and the own-history comparison would have called it
cheap either way.

This is a gap measure, not a forecast. It says the market is pricing this company
below where its measured characteristics sit among its peers, which is a question
worth asking, not an answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .local import TickerRecord
from .stats import median, rank_percentile

__all__ = ["PeerSet", "PeerValuation", "build_peer_valuations", "MIN_PEERS", "WIDE_GAP"]

MIN_PEERS = 6
"""Below this many peers, an industry percentile is arithmetic rather than evidence."""

# What a reader is being told a price should reflect. Each is (label, extractor,
# higher_is_better). Kept short on purpose: a "quality" measure built from twelve
# inputs stops being interpretable, and the point here is that a person can argue
# with it.
QUALITY_INPUTS = (
    ("return on invested capital", lambda r: r.fundamentals.roic_avg, True),
    ("free cash flow margin", lambda r: r.fundamentals.fcf_margin_avg, True),
    ("revenue growth", lambda r: r.fundamentals.rev_cagr, True),
    ("gross margin stability", lambda r: r.fundamentals.gm_std, False),
)

PRICE_INPUTS = (
    ("forward P/E", lambda r: r.valuation.forward_pe if r.valuation else None),
    ("EV/EBITDA", lambda r: r.valuation.ev_ebitda if r.valuation else None),
)
# Price to sales is deliberately absent. The screen output has no such column: what
# looks like one, ``price_ps``, is a second price produced by a merge suffix
# collision in price_screen.py, and reading it as a multiple gives Adobe a
# price-to-sales of 280x.


@dataclass(frozen=True)
class PeerSet:
    basis: str  # "industry" | "sector" | "whole list"
    label: str
    tickers: List[str]

    @property
    def n(self) -> int:
        return len(self.tickers)

    @property
    def caveat(self) -> str:
        if self.basis == "industry":
            return f"Compared against {self.n - 1} other names in {self.label}."
        if self.basis == "sector":
            return (f"Not enough names in its industry to compare against, so this is against "
                    f"{self.n - 1} others in {self.label}. A sector holds businesses that are not "
                    "really comparable, so read the percentile loosely.")
        return (f"Neither its industry nor its sector had enough names, so this is against the whole "
                f"list of {self.n - 1} others, which mixes businesses that have nothing to do with "
                "each other. Treat it as a sanity check, not a comparison.")


@dataclass(frozen=True)
class PeerValuation:
    ticker: str
    peer_set: PeerSet
    price_percentiles: Dict[str, Optional[float]]
    quality_percentiles: Dict[str, Optional[float]]
    price_rank: Optional[float]
    quality_rank: Optional[float]
    gap: Optional[float]
    verdict: str
    reasoning: str
    cheapest_peers: List[Tuple[str, Optional[float]]] = field(default_factory=list)
    dearest_peers: List[Tuple[str, Optional[float]]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "ticker": self.ticker,
            "peer_set": {"basis": self.peer_set.basis, "label": self.peer_set.label,
                         "n": self.peer_set.n, "tickers": self.peer_set.tickers,
                         "caveat": self.peer_set.caveat},
            "price_percentiles": {k: (None if v is None else round(v, 4))
                                  for k, v in self.price_percentiles.items()},
            "quality_percentiles": {k: (None if v is None else round(v, 4))
                                    for k, v in self.quality_percentiles.items()},
            "price_rank": None if self.price_rank is None else round(self.price_rank, 4),
            "quality_rank": None if self.quality_rank is None else round(self.quality_rank, 4),
            "gap": None if self.gap is None else round(self.gap, 4),
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "cheapest_peers": [{"ticker": t, "forward_pe": v} for t, v in self.cheapest_peers],
            "dearest_peers": [{"ticker": t, "forward_pe": v} for t, v in self.dearest_peers],
        }


def _peer_set(rec: TickerRecord, records: Sequence[TickerRecord]) -> PeerSet:
    same_industry = [r.ticker for r in records if r.industry and r.industry == rec.industry]
    if len(same_industry) >= MIN_PEERS:
        return PeerSet("industry", rec.industry or "?", sorted(same_industry))
    same_sector = [r.ticker for r in records if r.sector and r.sector == rec.sector]
    if len(same_sector) >= MIN_PEERS:
        return PeerSet("sector", rec.sector or "?", sorted(same_sector))
    return PeerSet("whole list", "the quality top 150", sorted(r.ticker for r in records))


WIDE_GAP = 0.35
"""A gap this wide between where a name is priced and where its quality sits is the
statement worth making, whatever quartile the two happen to fall in.

Without this, a name in the 46th percentile on price and the 88th on quality lands
in "the middle" because neither number crossed a quartile line, and the 42-point
gap between them, which is the whole point, goes unsaid."""


def _verdict(price_rank: Optional[float], quality_rank: Optional[float]) -> Tuple[str, str]:
    """The gap first, then the quadrant. Most of the list is genuinely unremarkable."""
    if price_rank is None or quality_rank is None:
        return ("not comparable",
                "Too few of its peers carry both a multiple and the quality measures for a "
                "comparison to mean anything.")
    gap = quality_rank - price_rank
    cheap = price_rank <= 0.35
    dear = price_rank >= 0.65
    good = quality_rank >= 0.65
    poor = quality_rank <= 0.35

    if cheap and good:
        return ("cheap against peers of better quality",
                "It sits in the cheaper end of its peer set while its measured quality sits in the "
                "better end. That is the combination worth a closer look, and it is also the "
                "combination most often explained by something the numbers do not carry: a "
                "customer loss, a patent cliff, a regulator. The gap is a question, not an answer.")
    if dear and poor:
        return ("expensive against peers of worse quality",
                "It trades in the dearer end of its peer set while its measured quality sits in the "
                "weaker end. That is the pairing that most often ends badly, and it is worth knowing "
                "before the own-history comparison says it is cheap versus its own past bubble.")
    if gap >= WIDE_GAP:
        return ("priced below where its quality sits",
                f"Neither number crosses a quartile line, but they are {gap * 100:.0f} points apart: "
                "the market is pricing it well below where its measured characteristics rank among "
                "its peers. That gap is the observation. Whether it is an opportunity depends "
                "entirely on something outside these numbers.")
    if gap <= -WIDE_GAP:
        return ("priced above where its quality sits",
                f"Neither number crosses a quartile line, but they are {abs(gap) * 100:.0f} points "
                "apart the other way: it is priced well above where its measured characteristics "
                "rank among its peers. Sometimes that is the market seeing something these measures "
                "do not; sometimes it is not.")
    if cheap and poor:
        return ("cheap, and the quality suggests why",
                "It is cheap against its peers and its measured quality is also in the weaker end. "
                "The own-history comparison would call this cheap too, and it would be describing a "
                "derating rather than arguing against one.")
    if dear and good:
        return ("expensive, and the quality supports it",
                "It trades in the dearer end of its peer set and its measured quality is in the "
                "better end. Nothing here says the price is wrong; it says the price is explained.")
    return ("in the middle",
            "Neither its multiple nor its quality separates it from its peers, and the two are "
            "close to each other. Most of the list is here, and that is the honest answer for most "
            "of the list.")


def build_peer_valuations(records: Sequence[TickerRecord]) -> Dict[str, PeerValuation]:
    """Percentiles within each name's peer set. Pure, deterministic, no I/O."""
    recs = [r for r in records if r.valuation is not None]
    by_ticker = {r.ticker: r for r in recs}
    out: Dict[str, PeerValuation] = {}

    for rec in recs:
        ps = _peer_set(rec, recs)
        peers = [by_ticker[t] for t in ps.tickers if t in by_ticker]
        idx = {p.ticker: i for i, p in enumerate(peers)}
        i = idx.get(rec.ticker)
        if i is None:
            continue

        price_pcts: Dict[str, Optional[float]] = {}
        for label, get in PRICE_INPUTS:
            vals = [get(p) for p in peers]
            # A negative or zero multiple is not "cheap", it is not a multiple.
            vals = [None if (v is None or v <= 0) else v for v in vals]
            pcts = rank_percentile(vals)  # ascending: 0 is the cheapest
            price_pcts[label] = pcts[i]

        quality_pcts: Dict[str, Optional[float]] = {}
        for label, get, higher_is_better in QUALITY_INPUTS:
            vals = [get(p) for p in peers]
            pcts = rank_percentile(vals, ascending=higher_is_better)
            quality_pcts[label] = pcts[i]

        price_rank = median([v for v in price_pcts.values() if v is not None])
        quality_rank = median([v for v in quality_pcts.values() if v is not None])
        gap = None if (price_rank is None or quality_rank is None) else quality_rank - price_rank

        verdict, reasoning = _verdict(price_rank, quality_rank)

        priced = [(p.ticker, p.valuation.forward_pe) for p in peers
                  if p.valuation and p.valuation.forward_pe and p.valuation.forward_pe > 0]
        priced.sort(key=lambda kv: kv[1])
        out[rec.ticker] = PeerValuation(
            ticker=rec.ticker,
            peer_set=ps,
            price_percentiles=price_pcts,
            quality_percentiles=quality_pcts,
            price_rank=price_rank,
            quality_rank=quality_rank,
            gap=gap,
            verdict=verdict,
            reasoning=reasoning,
            cheapest_peers=priced[:4],
            dearest_peers=priced[-4:][::-1],
        )
    return out
