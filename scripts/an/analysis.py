"""Assemble everything known about one ticker into the record the page renders.

The page renders this object and nothing else. That is deliberate: if a number is
not in here with a source attached, it does not appear on screen, and there is one
place to look when something is wrong.

Three levels of depth, and the record says which one it is:

``deep``        a hand-written research note exists, so there is a business
                description, a four-year statement table, what management said on
                the last two calls, a bear case and named thesis killers.
``screen``      the name survived the quality screen, so there are fundamentals,
                valuation against its own history, and estimate revisions, but
                nobody has read a filing.
``universe``    scored and nothing else.

The ``gaps`` list is a first-class field rather than a footnote. Every record says
what is not known about it, because the difference between "no transcript exists
for free" and "there is no transcript" matters when you are deciding how much
weight to put on a page.

Narrative that requires reading rather than parsing (what guidance changed, what
management stepped around) is not invented here. It is merged in from
``dashboard/analysis/_narrative/<TICKER>.json`` when that file exists, and every
claim in it carries a quote from the research note it came from. When it does not
exist, the section says so instead of guessing.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import journal as journal_mod
from . import local, metrics, paths, peers, research_md, score

__all__ = ["build_record", "build_all", "DEPTHS"]

DEPTHS = ("deep", "screen", "universe")

SOURCE_SCREEN = "Yahoo Finance via yfinance, pulled 2026-09-04"
SOURCE_NOTE = "hand-written research note in research/"


def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 6)


def _identity(rec: local.TickerRecord, note: Optional[research_md.ResearchNote]) -> Dict[str, Any]:
    return {
        "ticker": rec.ticker,
        "company": (note.company if note else rec.name) or rec.ticker,
        "sector": rec.sector,
        "industry": rec.industry,
        "country": rec.country,
        "exchange": rec.exchange,
        "currency": rec.currency,
        "price": rec.price,
        "market_cap_usd": rec.market_cap_usd,
        "dollar_volume_usd": rec.dollar_volume_usd,
        "source": SOURCE_SCREEN,
        "as_of": rec.pulled,
    }


def _fundamentals_block(rec: local.TickerRecord) -> Dict[str, Any]:
    f = rec.fundamentals
    return {
        "window": f.years_label,
        "n_years": f.n_years,
        "note": "Four fiscal years is all the free source provides, so every growth figure here "
                "is computed over three intervals, not five years.",
        "rows": [
            {"key": "roic_avg", "label": "Return on invested capital, average", "value": _pct(f.roic_avg),
             "unit": "percent", "higher_is_better": True},
            {"key": "roic_latest", "label": "Return on invested capital, latest year", "value": _pct(f.roic_latest),
             "unit": "percent", "higher_is_better": True},
            {"key": "fcf_margin_avg", "label": "Free cash flow margin, average", "value": _pct(f.fcf_margin_avg),
             "unit": "percent", "higher_is_better": True},
            {"key": "rev_cagr", "label": "Revenue CAGR", "value": _pct(f.rev_cagr),
             "unit": "percent", "higher_is_better": True},
            {"key": "gm_avg", "label": "Gross margin, average", "value": _pct(f.gm_avg),
             "unit": "percent", "higher_is_better": True},
            {"key": "gm_std", "label": "Gross margin, standard deviation", "value": _pct(f.gm_std),
             "unit": "percent", "higher_is_better": False},
            {"key": "nd_to_ebitda", "label": "Net debt to EBITDA", "value": f.nd_to_ebitda,
             "unit": "x", "higher_is_better": False,
             "absent_means": "net cash, or EBITDA not meaningful" if f.nd_to_ebitda is None else None},
            {"key": "share_change", "label": "Diluted share count change", "value": _pct(f.share_change),
             "unit": "percent", "higher_is_better": False},
        ],
        "net_cash": f.net_cash,
        "fcf_positive_years": f.fcf_positive_years,
        "fcf_years": f.fcf_years,
        "source": SOURCE_SCREEN,
        "as_of": rec.pulled,
    }


def _valuation_block(rec: local.TickerRecord, note: Optional[research_md.ResearchNote],
                     peer: Optional["peers.PeerValuation"] = None) -> Dict[str, Any]:
    v = rec.valuation
    if v is None:
        return {"available": False,
                "why": "This name did not reach the price screen, which only runs on the quality top 150."}

    n_hist = v.n_hist_years or 0
    multiples = [
        {"key": "forward_pe", "label": "Forward P/E", "value": v.forward_pe,
         "own_median": v.median_pe_hist, "vs_median": v.pe_vs_median,
         "basis": "forward vs trailing", "like_for_like": False,
         "basis_note": (
             "The two sides are not the same multiple. The left is a forward P/E, the Street's "
             "estimate of next year's earnings. The median on the right is trailing: the price at "
             "each fiscal year end divided by that year's reported diluted EPS. Where earnings are "
             "expected to grow, forward is mechanically lower than trailing, so this column reads "
             "cheap by construction."
         )},
        {"key": "ev_ebitda", "label": "EV/EBITDA", "value": v.ev_ebitda,
         "own_median": v.median_ev_ebitda_hist, "vs_median": v.ev_vs_median,
         "basis": "trailing vs trailing", "like_for_like": True,
         "basis_note": "Both sides are trailing, so this comparison is like for like."},
    ]
    block: Dict[str, Any] = {
        "available": True,
        "multiples": multiples,
        "own_history": {
            "usable": v.has_own_history,
            "n_year_ends": v.n_hist_years,
            "caveat": (
                f"The median is taken at {n_hist} fiscal year end{'s' if n_hist != 1 else ''}, so it "
                f"is a {n_hist}-point median. That is a thin basis for 'cheap against its own "
                "history' and it says nothing about whether the old multiple was deserved."
                if v.has_own_history else
                (v.multiples_note or "No usable own-history multiples for this name.")
            ),
            "basis_warning": (
                "Read the P/E row with care. Across the 138 names that carry both comparisons, 85% "
                "print a negative 'vs median' on forward P/E with a median of -29%, against 51% and "
                "-0.6% on EV/EBITDA where both sides are trailing. That gap is the basis mismatch, "
                "not 138 companies being cheap. It is also correlated with growth: faster-growing "
                "names look cheaper here (rank correlation -0.28 against revenue CAGR, versus -0.16 "
                "for EV/EBITDA), because their forward earnings are further above their trailing "
                "ones. The score weights the like-for-like EV/EBITDA comparison more heavily for "
                "exactly this reason."
            ),
        },
        "drawdown": {
            "from_52w_high": v.dd_52w,
            "from_all_time_high": v.dd_ath,
            "high_52w": v.high_52w,
            "all_time_high": v.ath,
            "caveat": "The all-time high is the highest closing price; the 52-week high is an "
                      "intraday high. They are not on the same basis, and the drawdown from the "
                      "all-time high is therefore understated.",
        },
        "estimates": {
            "eps_fy0": v.eps_fy0_now,
            "eps_fy1": v.eps_fy1_now,
            "fy1_change_30d": v.eps_fy1_chg_30d,
            "fy1_change_90d": v.eps_fy1_chg_90d,
            "fy0_change_30d": v.eps_fy0_chg_30d,
            "fy0_change_90d": v.eps_fy0_chg_90d,
            "analysts_up_30d": v.rev_up30_fy1,
            "analysts_down_30d": v.rev_down30_fy1,
            "net_breadth": v.net_revisions_fy1,
        },
        "price_basis": {
            "displayed": rec.price,
            "used_for_derived_figures": v.price_from_price_screen,
            "note": (
                "The drawdowns and the multiple-versus-median above were computed inside the price "
                "screen from its own last close, which is not quite the price shown at the top of "
                "this page: the two come from different passes of the pipeline and differ by a "
                "fraction of a percent. Neither is wrong; they are just not the same number."
            ),
        },
        "short_pct_float": v.short_pct_float,
        "insider_pct": v.insider_pct,
        "next_earnings": v.next_earnings,
        "buckets": v.buckets,
        "source": SOURCE_SCREEN,
        "as_of": rec.pulled,
    }
    if peer is not None:
        block["peers"] = peer.to_dict()
        block["peers"]["why"] = (
            "The own-history comparison above says nothing about whether the old multiple was "
            "deserved. This asks a different question: where does this name sit among its peers on "
            "price, and where does it sit among them on the things a price is supposed to reflect."
        )
    if note:
        block["written_view"] = {"text": note.section("Valuation"), "source": SOURCE_NOTE}
    return block


def _what_changed(rec: local.TickerRecord, note: Optional[research_md.ResearchNote],
                  narrative: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What moved since last quarter. Mechanical parts first, read parts merged in."""
    v = rec.valuation
    mechanical: List[Dict[str, Any]] = []
    if v:
        if v.eps_fy1_chg_30d is not None and v.eps_fy1_chg_90d is not None:
            last_60 = v.eps_fy1_chg_90d - v.eps_fy1_chg_30d
            mechanical.append({
                "label": "Next-year EPS consensus",
                "detail": (
                    f"moved {v.eps_fy1_chg_90d:+.1%} over 90 days, of which {v.eps_fy1_chg_30d:+.1%} "
                    f"came in the last 30. The 60 days before that accounted for {last_60:+.1%}."
                ),
                "direction": "up" if v.eps_fy1_chg_90d > 0 else ("down" if v.eps_fy1_chg_90d < 0 else "flat"),
                "accelerating": (
                    None if v.eps_fy1_chg_90d == 0 else
                    (v.eps_fy1_chg_30d * 3 > v.eps_fy1_chg_90d) if v.eps_fy1_chg_90d > 0 else
                    (v.eps_fy1_chg_30d * 3 < v.eps_fy1_chg_90d)
                ),
                "source": SOURCE_SCREEN,
            })
        if v.rev_up30_fy1 is not None or v.rev_down30_fy1 is not None:
            up, down = int(v.rev_up30_fy1 or 0), int(v.rev_down30_fy1 or 0)
            mechanical.append({
                "label": "Analyst breadth, 30 days",
                "detail": f"{up} raised next-year estimates, {down} cut them."
                          + ("" if up + down else " Nobody moved."),
                "direction": "up" if up > down else ("down" if down > up else "flat"),
                "source": SOURCE_SCREEN,
            })
        if v.dd_52w is not None:
            mechanical.append({
                "label": "Price against its year",
                "detail": f"{v.dd_52w:+.1%} from the 52-week high"
                          + (f", {v.dd_ath:+.1%} from the highest close it has ever made."
                             if v.dd_ath is not None else "."),
                # Deliberately neutral. A drawdown is a price fact, not a verdict, and
                # the cyclical-turn bucket exists precisely because a 44% fall can be
                # the reason to buy. Colouring it red would settle that question before
                # the reader had read anything, which is the same reason the valuation
                # multiples on this page are never coloured either.
                "direction": "flat",
                "source": SOURCE_SCREEN,
            })
        if v.next_earnings:
            mechanical.append({
                "label": "Next report",
                "detail": f"{v.next_earnings}. Everything on this page is dated {rec.pulled}, so the "
                          "revisions above are stale the moment that lands.",
                "direction": "flat",
                "source": SOURCE_SCREEN,
            })

    out: Dict[str, Any] = {"mechanical": mechanical}

    if note:
        out["earnings_text"] = {"text": note.earnings, "source": SOURCE_NOTE}

    if narrative:
        out["read"] = narrative
    else:
        out["read"] = {
            "available": False,
            "why": (
                "Guidance changes, tone, and what management stepped around cannot be parsed out of "
                "a screen. They need someone to read the 8-K exhibit and the call. "
                + ("The research note for this name covers the last two reports, and its text is "
                   "above." if note else
                   "No research note has been written for this name.")
            ),
        }
    return out


def _thesis(note: Optional[research_md.ResearchNote], entries) -> Dict[str, Any]:
    if not note:
        return {"available": False,
                "why": "No research note has been written for this name, so there is no thesis to state."}
    latest = entries[0] if entries else None
    return {
        "available": True,
        "why_on_the_list": note.section("Why it is on the list"),
        "business_quality": note.section("Business quality"),
        "verdict": note.section("Verdict"),
        "action": note.verdict_action,
        "conviction": note.conviction,
        "logged": (
            {"date": latest.date, "thesis": latest.thesis, "wrong_if": latest.wrong_if,
             "target_size": latest.target_size, "conviction": latest.conviction,
             "price_at_call": latest.price_at_call, "bucket": latest.bucket}
            if latest else None
        ),
        "source": SOURCE_NOTE,
    }


def _risks(note: Optional[research_md.ResearchNote], entries) -> Dict[str, Any]:
    if not note:
        return {"available": False,
                "why": "No research note has been written for this name. The screen cannot tell you "
                       "what would break a thesis it never formed."}
    trigger = note.price_trigger
    return {
        "available": True,
        "bear_case": note.section("The bear case"),
        "killers": note.thesis_killers,
        "price_trigger": trigger,
        "price_trigger_note": (
            None if trigger is not None else
            "No price trigger is named in the note. Two of the sixteen notes say so outright; "
            "the rest name one. An absent trigger is not a trigger of zero."
        ),
        "wrong_if": [{"date": e.date, "text": e.wrong_if} for e in entries if e.wrong_if],
        "source": SOURCE_NOTE,
    }


def _gaps(rec: local.TickerRecord, note: Optional[research_md.ResearchNote],
          narrative: Optional[Dict[str, Any]]) -> List[str]:
    gaps: List[str] = []
    v = rec.valuation
    gaps.append(
        "Four fiscal years of annual statements, not five or ten. Every growth figure is over three "
        "intervals and every own-history median is a four-point median."
    )
    if not note:
        gaps.append("No research note. Nobody has read a filing for this name.")
    if v is None:
        gaps.append("No valuation data: this name is outside the quality top 150 the price screen runs on.")
    elif not v.has_own_history:
        gaps.append(
            "No own-history multiples. " + (v.multiples_note or "The screen could not compute them.")
        )
    if not narrative:
        gaps.append(
            "No earnings-call transcript. Free full transcripts are not reliably available; see "
            "NOTES.md. The closest free substitute is the 8-K Exhibit 99.1 press release on EDGAR, "
            "which has the guidance language but not the analyst questions."
        )
    if rec.fundamentals.gm_avg is None:
        gaps.append("No gross margin line. This company does not report a cost of revenue.")
    if v and v.short_pct_float is None:
        gaps.append("No short interest figure.")
    gaps.append(
        "Everything here is dated " + (rec.pulled or "the last pipeline run")
        + ". Prices, estimates and drawdowns move daily; nothing on this page does."
    )
    return gaps


def _sources(note: Optional[research_md.ResearchNote]) -> List[Dict[str, Any]]:
    out = [{"title": "Screen and statement data, Yahoo Finance via yfinance", "url": None,
            "read_date": "2026-09-04", "kind": "data"}]
    if note:
        for s in note.sources:
            out.append({"title": s.title, "url": s.url, "read_date": s.read_date,
                        "domain": s.domain, "kind": "reading"})
    return out


def _narrative_for(ticker: str, narrative_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    if narrative_dir is None:
        narrative_dir = paths.ANALYSIS_DIR / "_narrative"
    p = Path(narrative_dir) / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_record(
    rec: local.TickerRecord,
    *,
    note: Optional[research_md.ResearchNote] = None,
    scores: Optional[Dict[str, Dict[str, score.ScoreBreakdown]]] = None,
    peer: Optional["peers.PeerValuation"] = None,
    entries=None,
    narrative_dir: Optional[Path] = None,
    built_at: Optional[str] = None,
) -> Dict[str, Any]:
    entries = entries or []
    narrative = _narrative_for(rec.ticker, narrative_dir)
    depth = "deep" if note else ("screen" if rec.in_top_150 else "universe")

    trends = (
        [t.to_dict() for t in metrics.series_from_rows(note.financials, SOURCE_NOTE, as_of=rec.pulled)]
        if note else []
    )

    score_block: Dict[str, Any] = {"available": False}
    if scores:
        per_variant = {}
        for variant, table in scores.items():
            b = table.get(rec.ticker)
            if not b:
                continue
            per_variant[variant] = {
                "score": round(b.score, 4),
                "percentile": b.display,
                "coverage": round(b.coverage, 4),
                "thinly_evidenced": b.thinly_evidenced,
                "missing": b.missing,
                "contributions": {k: round(v, 4) for k, v in b.contributions.items()},
                "top_contributions": [{"key": k, "value": round(v, 4)} for k, v in b.top_contributions(5)],
            }
        if per_variant:
            score_block = {
                "available": True,
                "variants": per_variant,
                "universe": "the quality top 150 as of 2026-09-04",
                "caveat": "A percentile against 150 names on one date. It has not been backtested; "
                          "see the positioning page for why not.",
            }

    return {
        "schema": 1,
        "built_at": built_at or dt.datetime.now().replace(microsecond=0).isoformat(),
        "depth": depth,
        "identity": _identity(rec, note),
        "business": (
            {"available": True, "text": note.section("What the company does"), "source": SOURCE_NOTE}
            if note else
            {"available": False,
             "why": "No research note. The screen knows the industry classification and nothing about "
                    "what the company actually sells.",
             "industry": rec.industry}
        ),
        "headline": (note.stats_line if note else None),
        "fundamentals": _fundamentals_block(rec),
        "trends": trends,
        "what_changed": _what_changed(rec, note, narrative),
        "thesis": _thesis(note, entries),
        "risks": _risks(note, entries),
        "valuation": _valuation_block(rec, note, peer),
        "score": score_block,
        "journal": [
            {"date": e.date, "action": e.action, "price_at_call": e.price_at_call, "thesis": e.thesis,
             "wrong_if": e.wrong_if, "target_size": e.target_size, "conviction": e.conviction,
             "bucket": e.bucket}
            for e in entries
        ],
        "sources": _sources(note),
        "gaps": _gaps(rec, note, narrative),
        "disclaimer": "Research and analysis from public data, not personalised financial advice.",
    }


def build_all(*, narrative_dir: Optional[Path] = None, built_at: Optional[str] = None
              ) -> Dict[str, Dict[str, Any]]:
    """Every name worth a page: the 16 with notes plus the whole quality top 150."""
    universe = local.load_universe()
    notes = research_md.load_all()
    entries = journal_mod.by_ticker()
    top150 = [r for r in universe.values() if r.in_top_150]
    scores = {v: score.score_universe(top150, variant=v) for v in score.VARIANTS}
    peer_vals = peers.build_peer_valuations(top150)

    wanted = sorted({r.ticker for r in top150} | set(notes))
    built_at = built_at or dt.datetime.now().replace(microsecond=0).isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    for t in wanted:
        rec = universe.get(t)
        if rec is None:
            continue
        out[t] = build_record(
            rec, note=notes.get(t), scores=scores, peer=peer_vals.get(t),
            entries=entries.get(t, []), narrative_dir=narrative_dir, built_at=built_at,
        )
    return out
