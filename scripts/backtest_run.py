#!/usr/bin/env python3
"""Run the score's validation and write dashboard/backtest.json.

    python scripts/backtest_run.py                 # whatever can honestly be run here
    python scripts/backtest_run.py --calibrate     # engine calibration on synthetic panels
    python scripts/backtest_run.py --live          # the real historical test, needs network
    python scripts/backtest_run.py --live --years 6 --horizon 63

WHY THE DEFAULT IS NOT A BACKTEST
---------------------------------
This repo contains one dated cross section, 2026-09-04, and no price history.
A cross-sectional score is validated by asking, on many past dates, whether its
ordering predicted what came next. With one date there is no "next". No amount of
cleverness fixes that, so the default run does the three things that are honest
without history:

1. calibrates the engine on synthetic panels where the answer is known
2. reports the structure of the score from the one cross section that exists
3. writes a backtest.json whose status says NOT RUN, with the exact command to run

``--live`` does the real thing and needs two sources: daily prices from yfinance
and, for the fundamental components, values dated by their SEC filing date so a
rebalance only sees what was public. Even then, read the limitations it prints.
The universe is the names listed today, so every number it produces is flattered
by every company that failed and left.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import backtest as B  # noqa: E402
from an import dera_fundamentals, diagnostics, local, paths, power, score  # noqa: E402
from an.store import Offline, is_offline  # noqa: E402
from an.synthetic import PanelSpec, make_panel  # noqa: E402

HORIZONS = (21, 63, 126, 252)

NOT_RUN_EXPLANATION = [
    "No historical backtest has been run, because this repository contains exactly one dated "
    "cross section (2026-09-04) and no price history. A cross-sectional score is validated by "
    "asking, on many past dates, whether its ordering of names predicted what those names did "
    "next. With a single date there is no next.",
    "What is reported instead: the engine's calibration against synthetic panels where the true "
    "answer was planted by hand, and the structure of the score itself measured on the one cross "
    "section that exists. Neither is evidence that the score predicts returns.",
    "To run the real thing: python scripts/backtest_run.py --live, on a machine with network "
    "access. It needs daily prices from yfinance and fundamentals dated by their SEC filing date "
    "so that each rebalance only sees what was public on the day.",
    "Even a successful live run inherits survivorship bias that cannot be removed with free data. "
    "The universe is the 1,505 names that are listed and screenable today. Every company that was "
    "delisted, acquired or wiped out between the start of the test and now is absent, and those "
    "are disproportionately the ones that lost money. {survivorship}",
    "The single highest-value fix is not a cleverer backtest, it is archiving a dated snapshot on "
    "every pipeline run (scripts/snapshot.py). A snapshot holds only what the pipeline could see "
    "that day, so a panel built from several has no look-ahead and no survivorship problem.",
    "How long that takes is answered rather than guessed, under how_long_until_this_can_say_anything "
    "below. The short version: archive MONTHLY, not quarterly. This engine will not return a "
    "verdict above 'weak' below twelve independent periods, which is three years of quarterly "
    "snapshots and one year of monthly ones, and detecting a typical published signal takes eight "
    "years quarterly against under three monthly.",
]


def survivorship_block(years: int) -> Dict[str, object]:
    """The survivorship hole as the build can honestly state it.

    Measured from the Alpha Vantage cache when a real pull exists there; otherwise
    NOT MEASURED, with the command. The synthetic calibration panels do not get
    the measured number: they plant their own attrition and say so.
    """
    from an import listing_status

    m = listing_status.measured_attrition(years)
    if m is None:
        return {
            "status": "NOT MEASURED",
            "statement": B.SURVIVORSHIP_UNMEASURED,
            "how_to_measure": "export ALPHAVANTAGE_KEY=... then python scripts/listing_status.py --fetch "
                              "(two requests), then rebuild. The limitation quotes the measured rate from then on.",
            "synthetic_engine_assumes_per_year": round(listing_status.SYNTHETIC_ATTRITION_PER_YEAR, 4),
        }
    return {
        "status": "MEASURED",
        "statement": B.survivorship_limitation(m),
        "measurement": m.to_dict(),
    }


def not_run_explanation(surv: Dict[str, object]) -> List[str]:
    return [p.format(survivorship=surv["statement"]) if "{survivorship}" in p else p for p in NOT_RUN_EXPLANATION]


def calibrate() -> Dict[str, object]:
    """Prove the engine works by running it on panels whose answer is known."""
    cases = [
        ("planted alpha, realistic size", PanelSpec(n_dates=40, n_names=150, alpha=0.03, seed=1),
         "The score genuinely predicts returns by a small, realistic amount. The engine should find it."),
        ("no relationship at all", PanelSpec(n_dates=40, n_names=150, alpha=0.0, seed=3),
         "The score and the return are independent by construction. The engine must report no evidence."),
        ("negative signal", PanelSpec(n_dates=40, n_names=150, alpha=-0.03, seed=2),
         "The score predicts returns backwards. The engine should report a negative IC, not a weak positive one."),
        ("look-ahead contamination", PanelSpec(n_dates=24, n_names=120, alpha=0.01, contaminate=True, seed=7),
         "The score was computed from the answer. The engine must flag it rather than celebrate it."),
        ("names that stop trading", PanelSpec(n_dates=20, n_names=150, alpha=0.02, delist_per_date=3, seed=9),
         "Companies leave the panel at a loss. The engine must show the universe shrinking."),
    ]
    out = []
    for label, spec, expectation in cases:
        panel, truth = make_panel(spec)
        r = B.run_backtest(panel, label=label, variant="synthetic", horizon_days=63,
                           rebalance_spacing_days=63)
        expected_to_find = spec.alpha != 0 and not spec.contaminate
        out.append({
            "case": label,
            "expectation": expectation,
            "expected_to_find_something": expected_to_find,
            "behaved_as_expected": (
                (r.verdict in ("suggestive", "supported")) == expected_to_find
                if not spec.contaminate else bool(r.flags)
            ),
            # The engine refuses to construct a result without limitations, and then
            # none of them reached this file. The whole enforcement was invisible to
            # anyone reading the output.
            "limitations": r.limitations,
            "planted_alpha": spec.alpha,
            "expected_rank_ic": round(truth["approx_rank_ic"], 4),
            "measured_rank_ic": None if r.mean_ic is None else round(r.mean_ic, 4),
            "ci": [round(x, 4) for x in r.ic_ci] if r.ic_ci else None,
            "verdict": r.verdict,
            "verdict_sentence": r.verdict_sentence,
            "flags": r.flags,
            "names_first_date": r.names_per_date[0] if r.names_per_date else 0,
            "names_last_date": r.names_per_date[-1] if r.names_per_date else 0,
        })
    return {
        "cases": out,
        "measured_false_positive_rate": B.MEASURED_FALSE_POSITIVE_RATE,
        "note": "These are synthetic panels, not this portfolio. They test the measuring "
                "instrument, not the score.",
    }


def structure() -> Dict[str, object]:
    """What can be said about the score from the one cross section that exists."""
    universe, _ = dera_fundamentals.universe_with_basis()
    recs = [r for r in universe.values() if r.in_top_150]
    out: Dict[str, object] = {}
    for variant in score.VARIANTS:
        s = score.score_universe(recs, variant=variant)
        d = diagnostics.diagnose(recs, s, variant=variant)
        out[variant] = d.to_dict()
    return out


def variant_disagreement() -> Dict[str, object]:
    """How differently the three variants rank the same names.

    If momentum and reversal produce nearly the same ordering, the contested price
    component is not doing anything and the argument is moot. If they produce very
    different orderings, the choice matters a great deal and there is no evidence
    here to make it with.
    """
    from an.stats import spearman

    universe, _ = dera_fundamentals.universe_with_basis()
    recs = [r for r in universe.values() if r.in_top_150]
    scored = {v: score.score_universe(recs, variant=v) for v in score.VARIANTS}
    tickers = sorted(scored["quality_value"])
    pairs = {}
    for i, a in enumerate(score.VARIANTS):
        for b in score.VARIANTS[i + 1:]:
            rho, n = spearman([scored[a][t].score for t in tickers], [scored[b][t].score for t in tickers])
            pairs[f"{a} vs {b}"] = {"rank_correlation": None if rho is None else round(rho, 4), "n": n}

    top10 = {v: [t for t, _ in sorted(scored[v].items(), key=lambda kv: -kv[1].score)[:10]] for v in score.VARIANTS}
    shared = set(top10["with_momentum"]) & set(top10["with_reversal"])
    return {
        "rank_correlations": pairs,
        "top_ten": top10,
        "top_ten_overlap_momentum_vs_reversal": sorted(shared),
        "note": (
            f"The momentum and reversal variants share {len(shared)} of their top ten. "
            "The two take opposite views of a drawdown on purpose, and nothing in this repository "
            "can say which is right for these names. Until a live backtest runs, the base "
            "quality_value score is the one to use, because it does not take the bet."
        ),
    }


def run_live(years: int, horizons, spacing_days: int) -> Dict[str, object]:
    """The real historical test. Requires network access."""
    if is_offline():
        raise Offline("DESK_OFFLINE is set")
    try:
        from an import prices  # noqa: F401
    except ImportError as e:
        return {"status": "unavailable", "reason": f"price module missing: {e}"}
    return {
        "status": "not implemented in this environment",
        "reason": (
            "The live path needs point-in-time fundamentals, which means one EDGAR companyfacts "
            "pull per name and a filing-date lookup per rebalance. That was written "
            "(an.edgar.as_known_on) but never run, because this container cannot reach sec.gov. "
            "Wiring it to real prices is the first task in the morning, and it is spelled out in "
            "PLAN.md under X2."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="run the real historical test (needs network)")
    ap.add_argument("--calibrate", action="store_true", help="only run engine calibration")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=63)
    ap.add_argument("--spacing", type=int, default=63)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true", help="deprecated no-op")
    a = ap.parse_args()

    paths.ensure_dirs()
    built_at = dt.datetime.now().replace(microsecond=0).isoformat()

    surv = survivorship_block(a.years)
    payload: Dict[str, object] = {
        "built_at": built_at,
        "snapshot_date": "2026-09-04",
        "status": "NOT RUN",
        "why_not_run": not_run_explanation(surv),
        "survivorship": surv,
        "engine_calibration": calibrate(),
    }

    payload["how_long_until_this_can_say_anything"] = {
        "question": (
            "If snapshot archiving starts today, how many rebalances until a result could mean "
            "anything, and what size of edge would still be invisible when it got there?"
        ),
        "smallest_detectable_rank_ic": [r.to_dict() for r in power.power_table()],
        "reference_effects": power.REFERENCE_EFFECTS,
        "measured_detection_rate": {
            str(k): {str(ic): rate for ic, rate in v.items()}
            for k, v in power.MEASURED_POWER.items()
        },
        "measured_how": (
            "Sixteen synthetic 150-name panels per cell, run through this same engine with an "
            "effect planted at the stated rank IC, counting how often the verdict reached "
            "suggestive or better."
        ),
        "verdict_floor_periods": power.VERDICT_FLOOR_PERIODS,
        "why_zero_below_the_floor": (
            "At four and eight rebalances the detection rate is exactly zero by policy, not by "
            "statistics: the engine refuses to say more than 'weak' below twelve independent "
            "periods. In those same runs the interval excluded zero between 38% and 69% of the "
            "time, and the engine still declined to call it."
        ),
        "cadence": power.archiving_cadence_advice(),
    }

    if not a.calibrate:
        payload["score_structure"] = structure()
        payload["variant_disagreement"] = variant_disagreement()

    if a.live:
        live = run_live(a.years, [a.horizon], a.spacing)
        payload["live"] = live
        if live.get("status") == "ok":
            payload["status"] = "RUN"

    out = Path(a.out) if a.out else paths.BACKTEST_JSON
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")

    print(f"wrote {out}")
    print(f"status: {payload['status']}")
    print(f"survivorship: {surv['status']}")
    adv = payload["how_long_until_this_can_say_anything"]["cadence"]
    for cad in ("quarterly", "monthly"):
        d = adv[cad]
        print(f"  archiving {cad:<10} first verdict in {d['years_to_first_verdict']}y, "
              f"typical signal in {d['years_to_detect']['a typical published signal']}y")
    for c in payload["engine_calibration"]["cases"]:
        print(f"  calibration  {c['case']:<32} ic={c['measured_rank_ic']}  {c['verdict']}")
    if "variant_disagreement" in payload:
        for k, v in payload["variant_disagreement"]["rank_correlations"].items():
            print(f"  variants     {k:<44} rho={v['rank_correlation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
