#!/usr/bin/env python3
"""Write dashboard/scorecard.json and dashboard/positioning.json.

    python scripts/build_positioning.py
    python scripts/build_positioning.py --check     # fixed timestamp, byte-comparable

scorecard.json is the score over the quality top 150 with every component's
contribution, so the page can show why a name ranks where it does rather than
only that it does.

positioning.json is the memo. It reads holdings from portfolio/holdings.csv when
that exists; that path is gitignored, so in the public repository the memo falls
back to the logged calls in journal.md and says which it is looking at. Personal
positions are never written into the committed JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import diagnostics, local, paths, positioning, score  # noqa: E402

FIXED_STAMP = "1970-01-01T00:00:00"  # deprecated; see --check below


def scorecard(built_at: str) -> dict:
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    out = {
        "built_at": built_at,
        "snapshot_date": "2026-09-04",
        "universe": "the quality top 150 from universe/quality_top150.csv",
        "n_names": len(recs),
        "variants": {},
        "weights": {v: score.weights_table(v) for v in score.VARIANTS},
        "min_coverage": score.MIN_COVERAGE,
        "caveat": (
            "A percentile against 150 names on one date. The score has never been tested on "
            "out-of-sample data because this repository holds one dated cross section and no price "
            "history. See backtest.json."
        ),
    }
    for variant in score.VARIANTS:
        table = score.score_universe(recs, variant=variant)
        by_ticker = {r.ticker: r for r in recs}
        rows = []
        for b in sorted(table.values(), key=lambda x: -x.score):
            r = by_ticker[b.ticker]
            v = r.valuation
            rows.append({
                "ticker": b.ticker, "company": r.name, "sector": r.sector, "industry": r.industry,
                "score": round(b.score, 4), "percentile": b.display,
                "coverage": round(b.coverage, 4), "thinly_evidenced": b.thinly_evidenced,
                "missing": b.missing,
                "contributions": {k: round(x, 4) for k, x in b.contributions.items()},
                "quality_screen_score": r.quality.total, "quality_screen_rank": r.quality.rank,
                "price": r.price, "forward_pe": v.forward_pe if v else None,
                "pe_vs_median": v.pe_vs_median if v else None,
                "dd_52w": v.dd_52w if v else None,
                "revisions_90d": v.eps_fy1_chg_90d if v else None,
                "buckets": v.buckets if v else [],
                "next_earnings": v.next_earnings if v else None,
            })
        out["variants"][variant] = {
            "rows": rows,
            "diagnostics": diagnostics.diagnose(recs, table, variant=variant).to_dict(),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="deprecated no-op")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()

    paths.ensure_dirs()
    stamp = None
    import datetime as dt

    built = stamp or dt.datetime.now().replace(microsecond=0).isoformat()

    sc = scorecard(built)
    paths.SCORECARD_JSON.write_text(json.dumps(sc, indent=1), encoding="utf-8")

    memo = positioning.build_memo(top_n=a.top, built_at=built)
    paths.POSITIONING_JSON.write_text(json.dumps(memo, indent=1), encoding="utf-8")

    print(f"wrote {paths.SCORECARD_JSON.name}: {sc['n_names']} names, {len(sc['variants'])} variants")
    print(f"wrote {paths.POSITIONING_JSON.name}: {len(memo['candidates'])} actionable, "
          f"{len(memo['research_queue'])} in the research queue")
    print(f"  holdings source: {memo['portfolio']['source']}")
    for c in memo["constraints"]:
        print(f"  {c['status']:<8} {c['rule']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
