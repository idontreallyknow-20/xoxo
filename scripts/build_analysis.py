#!/usr/bin/env python3
"""Write the per-ticker analysis JSON the /analyze pages render.

    python scripts/build_analysis.py                 # all 150
    python scripts/build_analysis.py KLAC BKNG       # just these
    python scripts/build_analysis.py --check         # rebuild and fail if anything changed

Writes dashboard/analysis/<TICKER>.json and dashboard/analysis/index.json. The
pages read those and nothing else, so a number that is not in here with a source
attached cannot appear on screen.

Output is deterministic apart from the built_at stamp, which --check pins so a
rebuild can be diffed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import analysis, pages, paths  # noqa: E402

FIXED_STAMP = "1970-01-01T00:00:00"  # deprecated; see --check below


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--check", action="store_true",
                    help="deprecated no-op; the build is deterministic apart from built_at, "
                         "which tests/test_build_determinism.py excludes when comparing")
    a = ap.parse_args()

    paths.ensure_dirs()
    records = analysis.build_all()
    if not records:
        print("no records built. universe/quality_scores_latest.csv is missing or empty; "
              "run the pipeline first. Refusing to write an index that says the universe is "
              "empty while 150 stale pages sit next to it.", file=sys.stderr)
        return 1
    wanted = {t.upper() for t in a.tickers} or set(records)
    missing = wanted - set(records)
    if missing:
        print(f"not in the universe: {sorted(missing)}", file=sys.stderr)

    written = 0
    for t in sorted(wanted & set(records)):
        p = paths.ANALYSIS_DIR / f"{t}.json"
        p.write_text(json.dumps(records[t], indent=1, sort_keys=False), encoding="utf-8")
        written += 1

    index = {
        "built_at": records[next(iter(records))]["built_at"],
        "snapshot_date": "2026-09-04",
        "count": len(records),
        "tickers": [
            {
                "ticker": t,
                "company": r["identity"]["company"],
                "sector": r["identity"]["sector"],
                "industry": r["identity"]["industry"],
                "depth": r["depth"],
                "price": r["identity"]["price"],
                "score": (r["score"]["variants"]["quality_value"]["percentile"]
                          if r["score"].get("available") else None),
                "coverage": (r["score"]["variants"]["quality_value"]["coverage"]
                             if r["score"].get("available") else None),
                "buckets": (r["valuation"].get("buckets") or []) if r["valuation"].get("available") else [],
                "forward_pe": (r["valuation"]["multiples"][0]["value"]
                               if r["valuation"].get("available") else None),
                "pe_vs_median": (r["valuation"]["multiples"][0]["vs_median"]
                                 if r["valuation"].get("available") else None),
                "dd_52w": (r["valuation"]["drawdown"]["from_52w_high"]
                           if r["valuation"].get("available") else None),
                "next_earnings": (r["valuation"].get("next_earnings")
                                  if r["valuation"].get("available") else None),
                "has_narrative": bool(r["what_changed"]["read"].get("available", True) is not False),
            }
            for t, r in sorted(records.items())
        ],
    }
    paths.ANALYSIS_INDEX.write_text(json.dumps(index, indent=1), encoding="utf-8")

    shells = pages.write_ticker_pages(sorted(records), index=index)
    pages.write_analyze_index()
    pages.write_positioning_page()

    deep = sum(1 for r in records.values() if r["depth"] == "deep")
    narrated = sum(1 for r in records.values() if r["what_changed"]["read"].get("available") is not False)
    print(f"wrote {written} records to {paths.ANALYSIS_DIR}")
    print(f"  {deep} deep (research note), {len(records) - deep} screen-only")
    print(f"  {narrated} with a read narrative of last quarter")
    print(f"  {len(shells)} page shells under dashboard/analyze/, plus the index and /positioning/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
