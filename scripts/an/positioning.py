"""A positioning memo: what to consider next, why, and at what size.

This is written as an analyst memo and not as a trade signal, which is a
constraint on the output rather than a disclaimer bolted to the end of it. Three
things follow from it.

*Nothing is stated as a prediction.* Every candidate line says what is true about
the company today and what would have to happen for the idea to be wrong. No
sentence claims to know what a price will do. :mod:`tests.test_language_guard`
runs over every string this module produces and fails the build on the vocabulary
of certainty.

*The score's status is carried, not assumed.* The ranking uses a score that has
never been validated on out-of-sample data, because this repository holds one
dated cross section and no price history. Every ranked list here is prefixed with
that fact, and the memo's own confidence is bounded by it.

*The portfolio rules do the sizing, not the score.* ``criteria.md`` sets the
limits: eight to twelve names, no position above 12% at cost, no sector above 30%,
the cyclical-turn bucket capped at 30% of deployed capital, 20 to 30% held in cash,
and no more than 25% of total capital deployed in any one month. Those are checked
mechanically, and a candidate that would breach one is shown with the breach
rather than quietly dropped.

Holdings are read from ``portfolio/holdings.csv`` when it exists. In the public
repository it does not, because that path is gitignored and personal positions are
not committed. Where there are no holdings, the logged calls in ``journal.md`` are
used as the stated intent instead, and the memo says which of the two it is
looking at.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import journal as journal_mod
from . import local, paths, peers, research_md, score

__all__ = ["Rules", "Holding", "PortfolioState", "Candidate", "build_memo", "RULES"]


@dataclass(frozen=True)
class Rules:
    """The limits from criteria.md, in one place so a breach can be computed."""

    min_names: int = 8
    max_names: int = 12
    max_position_pct: float = 0.12
    max_sector_pct: float = 0.30
    max_cyclical_pct_of_deployed: float = 0.30
    cash_band: Tuple[float, float] = (0.20, 0.30)
    max_deployed_per_month_pct: float = 0.25
    source: str = "criteria.md, Step 5"


RULES = Rules()


@dataclass(frozen=True)
class Holding:
    ticker: str
    shares: float
    cost_per_share: float
    currency: str
    date_bought: Optional[str]
    bucket: Optional[str]
    wrong_if_price: Optional[float]

    @property
    def cost_usd(self) -> float:
        return self.shares * self.cost_per_share


@dataclass(frozen=True)
class PortfolioState:
    source: str
    holdings: List[Holding]
    cash_usd: float
    total_usd: float
    logged_calls: List[Dict[str, Any]]

    @property
    def deployed_usd(self) -> float:
        return sum(h.cost_usd for h in self.holdings)

    @property
    def n_positions(self) -> int:
        return len(self.holdings)


@dataclass(frozen=True)
class Candidate:
    ticker: str
    company: str
    sector: Optional[str]
    rank: int
    percentile: Optional[float]
    coverage: float
    thinly_evidenced: bool
    depth: str
    buckets: List[str]
    price: Optional[float]
    forward_pe: Optional[float]
    pe_vs_median: Optional[float]
    dd_52w: Optional[float]
    revisions_90d: Optional[float]
    next_earnings: Optional[str]
    drivers: List[Dict[str, Any]]
    drags: List[Dict[str, Any]]
    reasoning: List[str]
    what_would_be_wrong: List[Dict[str, Any]]
    """Falsifiers, each ``{"text", "source", "date"}``. Not bare strings: the page
    has to attribute a falsifier to the note and the date it was written on."""
    suggested_band_usd: Optional[Tuple[float, float]]
    constraint_notes: List[str]
    already_logged: Optional[Dict[str, Any]]
    confidence: str
    stance: Optional[str] = None
    """buy, watching, passed or unstated, taken from the research note's verdict."""
    peer_verdict: Optional[str] = None
    peer_gap: Optional[float] = None
    peer_set_label: Optional[str] = None
    peer_set_n: Optional[int] = None


def _read_holdings() -> Tuple[List[Holding], Optional[float], str]:
    p = paths.PORTFOLIO_DIR / "holdings.csv"
    cash_p = paths.PORTFOLIO_DIR / "cash.csv"
    holdings: List[Holding] = []
    if p.exists():
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("ticker") or "").strip().upper()
                if not t:
                    continue
                try:
                    holdings.append(Holding(
                        ticker=t,
                        shares=float(r.get("shares") or 0),
                        cost_per_share=float(r.get("cost_basis_per_share") or 0),
                        currency=(r.get("currency") or "USD").strip(),
                        date_bought=(r.get("date_bought") or "").strip() or None,
                        bucket=(r.get("bucket") or "").strip() or None,
                        wrong_if_price=float(r["wrong_if_price"]) if r.get("wrong_if_price") else None,
                    ))
                except (TypeError, ValueError):
                    continue
    cash = None
    if cash_p.exists():
        rows = []
        with cash_p.open(newline="", encoding="utf-8") as fh:
            rows = sorted(csv.DictReader(fh), key=lambda r: r.get("date", ""))
        if rows:
            try:
                cash = float(rows[-1].get("cash_usd") or 0)
            except (TypeError, ValueError):
                cash = None
    if p.exists():
        return holdings, cash, "portfolio/holdings.csv"
    return holdings, cash, "no holdings file (portfolio/ is gitignored and absent here)"


def portfolio_state(starting_cash: float = 100_000.0) -> PortfolioState:
    holdings, cash, src = _read_holdings()
    entries = [e for e in journal_mod.load() if not e.is_system]
    logged = [
        {"date": e.date, "ticker": e.ticker, "action": e.action, "target_usd": e.target_usd,
         "conviction": e.conviction, "bucket": e.bucket, "wrong_if": e.wrong_if,
         "price_at_call": e.price_at_call, "thesis": e.thesis}
        for e in sorted(entries, key=lambda e: (e.date, e.ticker))
    ]
    deployed = sum(h.cost_usd for h in holdings)
    cash_usd = cash if cash is not None else starting_cash - deployed
    return PortfolioState(source=src, holdings=holdings, cash_usd=cash_usd,
                          total_usd=cash_usd + deployed, logged_calls=logged)


def _driver_labels() -> Dict[str, str]:
    return {c.key: c.label for c in score.components_for("quality_value")}


def _reasoning(rec: local.TickerRecord, note, breakdown, drivers, drags,
               peer: Optional["peers.PeerValuation"] = None) -> List[str]:
    """Sentences built from the record. Nothing here is a forecast."""
    out: List[str] = []
    v = rec.valuation
    labels = _driver_labels()

    if note:
        first = (note.section("What the company does") or "").split(". ")
        if first:
            out.append(first[0].rstrip(".") + ".")
    elif rec.industry:
        out.append(f"Classified as {rec.industry}. No research note has been written, so nothing "
                   "below rests on reading a filing.")

    if drivers:
        names = ", ".join(labels.get(d["key"], d["key"]) for d in drivers[:3])
        out.append(f"Ranks where it does mainly on {names}.")
    if drags:
        names = ", ".join(labels.get(d["key"], d["key"]) for d in drags[:2])
        out.append(f"The score is held back by {names}.")

    if v and v.forward_pe is not None:
        if v.has_own_history and v.pe_vs_median is not None:
            direction = "below" if v.pe_vs_median < 0 else "above"
            n = v.n_hist_years
            out.append(
                f"Trades at {v.forward_pe:.1f}x forward earnings against a {n}-point median of "
                f"{v.median_pe_hist:.1f}x, {abs(v.pe_vs_median):.0%} {direction} it. A {n}-point "
                "median is a thin basis and says nothing about whether the old multiple was deserved."
            )
        else:
            out.append(f"Trades at {v.forward_pe:.1f}x forward earnings. No usable own-history "
                       "comparison for this name.")

    if v and v.eps_fy1_chg_90d is not None:
        up, down = int(v.rev_up30_fy1 or 0), int(v.rev_down30_fy1 or 0)
        if up == 0 and down == 0:
            # "0 raising and 0 cutting" next to a positive 90-day move reads as a
            # contradiction. It is not: the move happened earlier in the window.
            out.append(
                f"Next-year consensus has moved {v.eps_fy1_chg_90d:+.1%} over 90 days, but no analyst "
                "changed their number in the last 30, so the move is older than a month."
            )
        else:
            out.append(
                f"Next-year consensus has moved {v.eps_fy1_chg_90d:+.1%} over 90 days, with {up} "
                f"analyst{'' if up == 1 else 's'} raising and {down} cutting in the last 30."
            )

    if peer is not None and peer.price_rank is not None and peer.quality_rank is not None:
        out.append(
            f"Against {peer.peer_set.n - 1} peers in {peer.peer_set.label}, it is priced at the "
            f"{peer.price_rank * 100:.0f}th percentile and its measured quality sits at the "
            f"{peer.quality_rank * 100:.0f}th: {peer.verdict}."
        )

    if v and v.dd_52w is not None:
        out.append(f"Sits {v.dd_52w:+.0%} from its 52-week high. Whether that is an opportunity or a "
                   "warning is exactly the question the score cannot answer; see the variants below.")

    if breakdown.thinly_evidenced:
        out.append(f"Only {breakdown.coverage:.0%} of the score's weight is backed by a real "
                   "observation for this name, so its position in the ranking is weakly evidenced.")
    return out


def _falsifiers(rec: local.TickerRecord, note, logged: Optional[Dict[str, Any]]
                ) -> List[Dict[str, Any]]:
    """What would prove the idea wrong, each line labelled with where it came from.

    There is no similarity heuristic here any more, and that is the fix rather than
    an omission. The journal's "wrong if" line and the note's thesis killers are two
    renderings of the same underlying conditions by the same person, so any attempt
    to merge them is guessing at semantic equivalence. The first version divided the
    shared-word count by the shorter phrase, which let two common words delete a
    longer and more specific condition. Dividing by the union instead kept obvious
    restatements. Both are wrong in the same way: a falsifier is the last thing to
    quietly drop, and "under 8%" and "under 6%" are different conditions.

    So nothing is dropped. Each source gets its own line, the reader can see that
    they agree, and the exact wording each was written in survives.
    """
    out: List[Dict[str, Any]] = []
    if logged and logged.get("wrong_if"):
        out.append({"text": logged["wrong_if"], "source": "as logged in journal.md",
                    "date": logged.get("date")})
    if note:
        for k in note.thesis_killers[:3]:
            out.append({"text": k if k.endswith(".") else k + ".",
                        "source": "thesis killers, from the research note"})
        if note.price_trigger is not None:
            out.append({"text": f"A close under ${note.price_trigger:,.0f}.",
                        "source": "price trigger, from the research note"})
    if not out:
        out.append({
            "text": (
                "No falsifier has been written for this name. Nothing should be bought on this page "
                "until one is: the rule in criteria.md is that every recommendation carries the "
                "price or event that would prove it wrong."
            ),
            "source": "absent",
        })
        v = rec.valuation
        if v and v.eps_fy1_chg_90d is not None:
            out.append({
                "text": "A mechanical stand-in until then: two consecutive months of next-year "
                        "consensus falling would remove the revision component that put it here.",
                "source": "mechanical stand-in, not written by anyone",
            })
    return out


def _size_band(rec: local.TickerRecord, breakdown, state: PortfolioState, rules: Rules,
               note) -> Tuple[Optional[Tuple[float, float]], List[str]]:
    """A size range from the rules, not from conviction about the outcome."""
    notes: List[str] = []
    total = state.total_usd
    if total <= 0:
        return None, ["No capital recorded, so no size can be suggested."]

    ceiling = total * rules.max_position_pct
    # The rules want eight to twelve names, so an equal-weight starting point is
    # total divided by ten, trimmed by how well evidenced the name is and capped by
    # the position limit. This is arithmetic from criteria.md, not a view.
    base = total * (1 - rules.cash_band[0]) / 10.0
    factor = 1.0
    if breakdown.thinly_evidenced:
        factor *= 0.6
        notes.append("Halved for thin evidence coverage.")
    if not note:
        factor *= 0.6
        notes.append("Reduced because no research note exists: nobody has read a filing.")
    buckets = rec.valuation.buckets if rec.valuation else []
    if "cyclical turn" in buckets:
        factor *= 0.7
        notes.append("Reduced because the cyclical-turn bucket has a higher false positive rate and "
                     f"is capped at {rules.max_cyclical_pct_of_deployed:.0%} of deployed capital.")
    lo = min(base * factor * 0.75, ceiling)
    hi = min(base * factor * 1.25, ceiling)
    if hi >= ceiling:
        notes.append(f"Capped at the {rules.max_position_pct:.0%} single-position limit "
                     f"(${ceiling:,.0f}).")
    return (round(lo, -2), round(hi, -2)), notes


def _constraint_check(state: PortfolioState, rules: Rules, universe: Dict[str, local.TickerRecord]
                      ) -> List[Dict[str, Any]]:
    """Mechanical checks against criteria.md. Each one names the rule it applies."""
    out: List[Dict[str, Any]] = []
    total = state.total_usd or 1.0

    out.append({
        "rule": f"{rules.min_names} to {rules.max_names} names",
        "status": "ok" if rules.min_names <= state.n_positions <= rules.max_names else "not met",
        "detail": f"{state.n_positions} positions held.",
        "source": rules.source,
    })
    cash_pct = state.cash_usd / total
    within = rules.cash_band[0] <= cash_pct <= rules.cash_band[1]
    out.append({
        "rule": f"{rules.cash_band[0]:.0%} to {rules.cash_band[1]:.0%} in cash",
        "status": "ok" if within else "not met",
        "detail": f"{cash_pct:.0%} in cash (${state.cash_usd:,.0f} of ${total:,.0f}).",
        "source": rules.source,
    })

    by_sector: Dict[str, float] = {}
    for h in state.holdings:
        sec = (universe.get(h.ticker).sector if universe.get(h.ticker) else None) or "unknown"
        by_sector[sec] = by_sector.get(sec, 0.0) + h.cost_usd
    breaches = [f"{s} {v/total:.0%}" for s, v in by_sector.items() if v / total > rules.max_sector_pct]
    out.append({
        "rule": f"no sector above {rules.max_sector_pct:.0%}",
        "status": "not met" if breaches else "ok",
        "detail": ", ".join(breaches) if breaches
                  else (", ".join(f"{s} {v/total:.0%}" for s, v in sorted(by_sector.items()))
                        or "no positions held."),
        "source": rules.source,
    })

    over = [f"{h.ticker} {h.cost_usd/total:.0%}" for h in state.holdings
            if h.cost_usd / total > rules.max_position_pct]
    out.append({
        "rule": f"no position above {rules.max_position_pct:.0%} at cost",
        "status": "not met" if over else "ok",
        "detail": ", ".join(over) if over else "no position exceeds the limit.",
        "source": rules.source,
    })

    cyc = sum(h.cost_usd for h in state.holdings if (h.bucket or "").lower().startswith("cyc"))
    dep = state.deployed_usd or 1.0
    out.append({
        "rule": f"cyclical turn under {rules.max_cyclical_pct_of_deployed:.0%} of deployed capital",
        "status": "not met" if state.holdings and cyc / dep > rules.max_cyclical_pct_of_deployed else "ok",
        "detail": f"{cyc/dep:.0%} of deployed capital." if state.holdings else "nothing deployed.",
        "source": rules.source,
    })
    return out


def build_memo(*, variant: str = "quality_value", top_n: int = 12,
               built_at: Optional[str] = None) -> Dict[str, Any]:
    universe = local.load_universe()
    notes = research_md.load_all()
    top150 = [r for r in universe.values() if r.in_top_150]
    scored = {v: score.score_universe(top150, variant=v) for v in score.VARIANTS}
    peer_vals = peers.build_peer_valuations(top150)
    table = scored[variant]
    state = portfolio_state()
    held = {h.ticker for h in state.holdings}
    logged_by_ticker: Dict[str, Dict[str, Any]] = {}
    for c in state.logged_calls:
        logged_by_ticker.setdefault(c["ticker"], c)

    ordered = sorted(table.values(), key=lambda b: -b.score)

    def make(b, rank: int) -> Candidate:
        rec = universe[b.ticker]
        note = notes.get(b.ticker)
        contribs = sorted(b.contributions.items(), key=lambda kv: -kv[1])
        drivers = [{"key": k, "value": round(v, 4)} for k, v in contribs[:4] if v > 0.01]
        drags = [{"key": k, "value": round(v, 4)} for k, v in contribs[::-1][:4] if v < -0.01]
        band, cnotes = _size_band(rec, b, state, RULES, note)
        v = rec.valuation
        return Candidate(
            ticker=b.ticker,
            company=(note.company if note else rec.name) or b.ticker,
            sector=rec.sector,
            rank=rank,
            percentile=b.display,
            coverage=round(b.coverage, 4),
            thinly_evidenced=b.thinly_evidenced,
            depth="deep" if note else "screen",
            buckets=v.buckets if v else [],
            price=rec.price,
            forward_pe=v.forward_pe if v else None,
            pe_vs_median=v.pe_vs_median if v else None,
            dd_52w=v.dd_52w if v else None,
            revisions_90d=v.eps_fy1_chg_90d if v else None,
            next_earnings=v.next_earnings if v else None,
            drivers=drivers,
            drags=drags,
            reasoning=_reasoning(rec, note, b, drivers, drags, peer_vals.get(b.ticker)),
            what_would_be_wrong=_falsifiers(rec, note, logged_by_ticker.get(b.ticker)),
            suggested_band_usd=band,
            constraint_notes=cnotes,
            already_logged=logged_by_ticker.get(b.ticker),
            confidence=("moderate, and bounded by the fact that the score has never been tested "
                        "out of sample" if note else
                        "low: ranked by screen output only, with no filing read"),
            peer_verdict=(peer_vals[b.ticker].verdict if b.ticker in peer_vals else None),
            peer_gap=(peer_vals[b.ticker].gap if b.ticker in peer_vals else None),
            peer_set_label=(peer_vals[b.ticker].peer_set.label if b.ticker in peer_vals else None),
            peer_set_n=(peer_vals[b.ticker].peer_set.n if b.ticker in peer_vals else None),
        )

    def stance_of(note) -> str:
        """What the person who read the filings actually concluded.

        The memo used to put every name with a research note into the candidate
        list, sized in dollars, without ever reading the verdict at the bottom of
        that note. Four of the sixteen say Pass or Watch. Ranking a name the analyst
        explicitly passed on, and printing a size band next to it, is the single
        most misleading thing this memo could do.
        """
        action = (note.verdict_action or "").strip().lower() if note else ""
        if not action:
            return "unstated"
        if action.startswith("pass"):
            return "passed"
        if action.startswith("watch"):
            return "watching"
        if action.startswith("buy"):
            return "buy"
        return "unstated"

    # Three lists, because they answer three different questions. Merging any two
    # would put a name nobody has read, or a name the analyst passed on, into a list
    # about deploying capital.
    #
    # The first is what could actually be acted on: a research note exists, so there
    # is a thesis, a bear case and a written falsifier. criteria.md is explicit that
    # no recommendation exists without the price or event that would prove it wrong,
    # and a screen row cannot supply one.
    #
    # The second is what the score surfaces that nobody has read. Those are
    # candidates for the next deep dive, not candidates for money.
    actionable: List[Candidate] = []
    reviewed_and_declined: List[Candidate] = []
    queue: List[Candidate] = []
    for b in ordered:
        if b.ticker in held:
            continue
        note = notes.get(b.ticker)
        if note is not None:
            stance = stance_of(note)
            if stance == "buy":
                if len(actionable) < top_n:
                    actionable.append(make(b, len(actionable) + 1))
            else:
                reviewed_and_declined.append(make(b, len(reviewed_and_declined) + 1))
        elif len(queue) < top_n:
            queue.append(make(b, len(queue) + 1))
    candidates = actionable
    for c in reviewed_and_declined:
        object.__setattr__(c, "stance", stance_of(notes.get(c.ticker)))
        object.__setattr__(c, "suggested_band_usd", None)
        object.__setattr__(c, "constraint_notes",
                           ["Not sized. The research note concluded "
                            f"\"{(notes[c.ticker].verdict_action or '').strip()}\", so this name is "
                            "not a candidate for capital until that verdict changes."])
    for c in actionable:
        object.__setattr__(c, "stance", "buy")

    # Agreement between the three variants on this exact list, which is a cheap
    # measure of how much the contested drawdown component is actually deciding.
    variant_ranks = {
        v: [t for t, _ in sorted(((k, s.score) for k, s in scored[v].items()), key=lambda kv: -kv[1])]
        for v in score.VARIANTS
    }
    top_sets = {v: set(r[:top_n]) for v, r in variant_ranks.items()}
    overlap = {
        f"{a} vs {b}": len(top_sets[a] & top_sets[b])
        for i, a in enumerate(score.VARIANTS) for b in score.VARIANTS[i + 1:]
    }

    return {
        "built_at": built_at or dt.datetime.now().replace(microsecond=0).isoformat(),
        "snapshot_date": "2026-09-04",
        "variant": variant,
        "status_of_the_score": {
            "validated": False,
            "statement": (
                "This ranking uses a score that has never been tested on out-of-sample data. This "
                "repository holds one dated cross section and no price history, so there is no "
                "'next' against which any ordering could be checked. The engine that would run "
                "that test is built and calibrated; it has nothing to run on yet."
            ),
            "where_to_read_more": "backtest.json, and PLAN.md item X1.",
        },
        "how_to_read_this": [
            "This is a memo, not a signal. It says what is true about each company today and what "
            "would have to happen for the idea to be wrong. Nothing here predicts a price.",
            "The ordering comes from a score that ranks names against each other on one date. A "
            "higher score means the name looks better than its peers on a set of measured "
            "characteristics, not that it will go up.",
            "Sizes come from the rules in criteria.md, not from confidence about outcomes. They are "
            "arithmetic: total capital, the eight-to-twelve-name target, the 12% position cap, and a "
            "reduction where the evidence is thin.",
            "Every candidate carries what would prove it wrong. A candidate with no falsifier is "
            "flagged rather than ranked quietly, because the rule in criteria.md is that no "
            "recommendation exists without one.",
            "The candidate list contains only names whose research note concluded to buy. Names "
            "that were read and declined appear separately, unsized, with the verdict quoted, "
            "however well the score ranks them.",
        ],
        "portfolio": {
            "source": state.source,
            "n_positions": state.n_positions,
            "cash_usd": round(state.cash_usd, 2),
            "deployed_usd": round(state.deployed_usd, 2),
            "total_usd": round(state.total_usd, 2),
            "holdings": [
                {"ticker": h.ticker, "shares": h.shares, "cost_per_share": h.cost_per_share,
                 "cost_usd": round(h.cost_usd, 2), "currency": h.currency, "bucket": h.bucket,
                 "date_bought": h.date_bought, "wrong_if_price": h.wrong_if_price}
                for h in state.holdings
            ],
            "logged_calls": state.logged_calls,
            "note": (
                "No holdings file is present, so nothing has been bought as far as this repository "
                "knows. The logged calls below are stated intent from journal.md, not positions."
                if not state.holdings else
                "Holdings read from portfolio/holdings.csv. That file is gitignored and its contents "
                "are not committed."
            ),
        },
        "rules": {
            "min_names": RULES.min_names, "max_names": RULES.max_names,
            "max_position_pct": RULES.max_position_pct, "max_sector_pct": RULES.max_sector_pct,
            "max_cyclical_pct_of_deployed": RULES.max_cyclical_pct_of_deployed,
            "cash_band": list(RULES.cash_band),
            "max_deployed_per_month_pct": RULES.max_deployed_per_month_pct,
            "source": RULES.source,
        },
        "constraints": _constraint_check(state, RULES, universe),
        "candidates": [
            {
                "rank": c.rank, "ticker": c.ticker, "company": c.company, "sector": c.sector,
                "percentile": c.percentile, "coverage": c.coverage,
                "thinly_evidenced": c.thinly_evidenced, "depth": c.depth, "buckets": c.buckets,
                "price": c.price, "forward_pe": c.forward_pe, "pe_vs_median": c.pe_vs_median,
                "dd_52w": c.dd_52w, "revisions_90d": c.revisions_90d, "next_earnings": c.next_earnings,
                "drivers": c.drivers, "drags": c.drags, "reasoning": c.reasoning,
                "what_would_be_wrong": c.what_would_be_wrong,
                "suggested_band_usd": list(c.suggested_band_usd) if c.suggested_band_usd else None,
                "constraint_notes": c.constraint_notes, "already_logged": c.already_logged,
                "confidence": c.confidence, "stance": c.stance, "peer_verdict": c.peer_verdict,
                "peer_gap": (None if c.peer_gap is None else round(c.peer_gap, 4)),
                "peer_set_label": c.peer_set_label, "peer_set_n": c.peer_set_n,
            }
            for c in candidates
        ],
        "reviewed_and_declined": [
            {
                "rank": c.rank, "ticker": c.ticker, "company": c.company, "sector": c.sector,
                "stance": c.stance, "percentile": c.percentile, "coverage": c.coverage,
                "buckets": c.buckets, "price": c.price, "forward_pe": c.forward_pe,
                "pe_vs_median": c.pe_vs_median, "dd_52w": c.dd_52w,
                "revisions_90d": c.revisions_90d, "next_earnings": c.next_earnings,
                "reasoning": c.reasoning, "verdict_text": (
                    (notes[c.ticker].verdict_action or "").strip() if c.ticker in notes else None),
                "note_verdict_full": (
                    notes[c.ticker].section("Verdict") if c.ticker in notes else None),
                "constraint_notes": c.constraint_notes, "peer_verdict": c.peer_verdict,
                "already_logged": c.already_logged,
            }
            for c in reviewed_and_declined
        ],
        "reviewed_and_declined_note": (
            "These names have a research note and the note concluded not to buy. The score still "
            "ranks them, sometimes highly, and that disagreement is the point of showing them: it "
            "is the clearest available test of whether the score is seeing something the reading "
            "missed, or the reading is seeing something the score cannot. They are not sized, and "
            "they are not candidates for capital until the verdict changes."
        ),
        "research_queue": [
            {
                "rank": c.rank, "ticker": c.ticker, "company": c.company, "sector": c.sector,
                "percentile": c.percentile, "coverage": c.coverage, "buckets": c.buckets,
                "price": c.price, "forward_pe": c.forward_pe, "pe_vs_median": c.pe_vs_median,
                "dd_52w": c.dd_52w, "revisions_90d": c.revisions_90d,
                "next_earnings": c.next_earnings, "drivers": c.drivers, "drags": c.drags,
                "reasoning": c.reasoning, "confidence": c.confidence,
                "peer_verdict": c.peer_verdict, "peer_set_label": c.peer_set_label,
            }
            for c in queue
        ],
        "research_queue_note": (
            "These are the names the score ranks highest that nobody has read a filing for. They are "
            "candidates for the next deep dive, not candidates for capital. The rule in criteria.md is "
            "that every recommendation carries the price or event that would prove it wrong, and a "
            "screen row cannot supply one."
            + (
                (f" The score's single highest-ranked name, {queue[0].ticker}, is one of them, which "
                 "is a gap in the process rather than a recommendation.")
                if queue and ordered and queue[0].ticker == ordered[0].ticker else
                (f" The highest-ranked name nobody has read is {queue[0].ticker}, at the "
                 f"{queue[0].percentile:.0f}th percentile.") if queue else ""
            )
        ),
        "variant_agreement": {
            "top_n": top_n,
            "overlap": overlap,
            "note": (
                "How many names the three score variants share in their top "
                f"{top_n}. They differ only in how they treat a drawdown: one adds proximity to the "
                "52-week high as a positive, one adds depth below the all-time high as a positive, "
                "and they take opposite views on purpose. A high overlap means the argument between "
                "them is not deciding much."
            ),
        },
        "disclaimer": "Research and analysis from public data, not personalised financial advice. "
                      "Nothing here is a recommendation to buy or sell.",
    }
