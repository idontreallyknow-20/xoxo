#!/usr/bin/env python3
"""Paper trading and A/B on real history: run an experiment, or render the dashboard file.

    python scripts/paper_trade.py                         # render dashboard/paper.json from lab/results/ (offline, fast)
    python scripts/paper_trade.py --check                 # the same, for build_all --check
    python scripts/paper_trade.py --dry-run               # what --live would read and run, nothing written
    python scripts/paper_trade.py --fixture               # the whole pipeline on the 12-name fixture; refuses dashboard/
    python scripts/paper_trade.py --live --split train    # the train window, every default arm, into lab/results/
    python scripts/paper_trade.py --live --split train --arms pullback_b3_h10,breakout_s8_h10 --slug one_change
    python scripts/paper_trade.py --live --split test     # the held-out window, pre-registered arms only, once

The live runs need data/cache/history/ from scripts/fetch_history.py with the audit gate at
PASS. They write a small summary into lab/results/ (committed) and never a trade log. The
default mode reads only lab/results/, so the build stays offline and deterministic.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from an import ab, history, local, paper, paths  # noqa: E402


def sectors_from_screen() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for t, rec in local.load_quality().items():
            if rec.sector:
                out[t.upper()] = rec.sector
    except (OSError, ValueError):
        pass
    return out


def data_block(manifest: Dict, panel: history.HistoryPanel, f: paper.Features, *, scope: str) -> Dict:
    audit = manifest.get("audit") or {}
    srcs = manifest.get("sources") or {}
    worst = None
    for c in audit.get("cross_checks") or []:
        if worst is None or (c.get("max_abs_rel_diff") or 0) > (worst.get("max_abs_rel_diff") or 0):
            worst = c
    return {
        "scope": scope, "source": (manifest.get("panel") or {}).get("source") or panel.source,
        "sessions": panel.sessions, "tickers": len(panel.tickers), "first": panel.first, "last": panel.last,
        "basis": "price returns; no dividends on either side", "dataset_verdict": audit.get("dataset_verdict"),
        "quality_gate": audit.get("gate"), "worst_cross_check": worst, "membership": audit.get("membership"),
        "excluded_as_suspect": list(f.excluded),
        "eligible_cells": int(f.eligible.to_numpy().sum()),
        "sha256": {k: v.get("sha256") for k, v in srcs.items() if isinstance(v, dict) and v.get("sha256")},
        "fetched_at": manifest.get("fetched_at"),
    }


def parse_arms(spec: Optional[str]) -> List[paper.Arm]:
    if not spec:
        return ab.default_arms()
    out = []
    for raw in spec.split(","):
        a = ab.arm_by_id(raw.strip())
        if a is None:
            raise SystemExit(f"unknown arm id {raw!r}; ids look like pullback_b3_h10, breakout_s8_h10, pead_proxy_g5_h20")
        out.append(a)
    return out


def run_live(a: argparse.Namespace) -> int:
    cached = history.load_cached(Path(a.root))
    if cached is None:
        print(f"no history cache under {a.root}; run scripts/fetch_history.py first", file=sys.stderr)
        return 2
    panel, mem, manifest = cached
    audit = manifest.get("audit") or {}
    if audit.get("gate") != "PASS":
        print(f"audit gate is {audit.get('gate')!r}, refusing to run live; fix the data first", file=sys.stderr)
        return 2
    arms = parse_arms(a.arms)
    pre = ab.load_preregistration()
    forced = False
    if a.split == "test":
        problems = []
        if pre.oos_window != ab.SPLITS["test"]:
            problems.append(f"lab/LAB.md pre-registers OOS window {pre.oos_window}, code has {ab.SPLITS['test']}")
        not_reg = [x.id for x in arms if x.id not in pre.arm_ids and x.kind not in ("spy_hold",)]
        if not_reg:
            problems.append(f"arms not pre-registered in lab/LAB.md: {', '.join(not_reg)}")
        already = []
        for p in ab.list_results():
            blob = json.loads(p.read_text(encoding="utf-8"))
            if blob.get("split") == "test" and blob.get("data", {}).get("scope") == "full":
                already.extend(x["arm"]["id"] for x in blob.get("arms", []))
        rerun = [x.id for x in arms if x.id in already]
        if rerun:
            problems.append(f"already run on the held-out window: {', '.join(rerun)}")
        if problems and not a.force_oos:
            for p in problems:
                print(f"refusing the held-out run: {p}", file=sys.stderr)
            print("pass --force-oos to override; the result will say it was forced", file=sys.stderr)
            return 2
        forced = bool(problems)
        arms = [paper.Arm(x.id, x.kind, x.horizon, dict(x.params), preregistered=x.id in pre.arm_ids) for x in arms]
    f = paper.compute_features(panel, mem, exclude=audit.get("suspect_tickers") or [])
    rule_ids = [x.id for x in arms if x.kind not in ("random", "spy_hold")]
    k = ab.hypotheses_from_results(extra=rule_ids)
    data = data_block(manifest, panel, f, scope="full")
    print(f"  {a.split}: {len(arms)} arms, {k} hypotheses counted, window {ab.SPLITS[a.split]}, "
          f"{data['eligible_cells']:,d} eligible name-days, {data['tickers']} tickers")
    t0 = dt.datetime.now()
    res = ab.run_ab(f, arms, split=a.split, rules=paper.Rules(), costs=paper.CostModel(), sectors=sectors_from_screen(),
                    data=data, hypotheses_tested=k, control_replicates=a.control_replicates, forced=forced)
    print(f"  ran in {(dt.datetime.now() - t0).total_seconds():.0f}s")
    print_summary(res)
    p = ab.write_result(res, slug=a.slug)
    print(f"  wrote {p}")
    return 0


def print_summary(res: ab.AbResult) -> None:
    fmt = lambda v, w=8: f"{'n/a':>{w}s}" if v is None else f"{v:>+{w}.4f}"  # noqa: E731
    print(f"\n  {'arm':24s} {'n':>5s} {'hit':>6s} {'mean xs':>8s} {'ci lo':>8s} {'ci hi':>8s} {'null p95':>9s} "
          f"{'pct':>5s} {'verdict':12s} {'ledger $':>10s} decision")
    for r in sorted(res.arms, key=lambda x: x.arm.id):
        j = r.to_json()
        null = res.nulls.get(r.arm.id)
        ci = j["excess_ci"] or [None, None]
        pct = "n/a" if null is None else f"{null.percentile_of(r.mean_excess_vs_spy) or 0:5.2f}"
        ledger = "n/a" if j["ledger"] is None else f"{j['ledger']['final_equity']:,.0f}"
        ok, _ = res.survives(r)
        print(f"  {r.arm.id:24s} {r.n_trades:5d} {fmt(j['hit_rate'], 6)} {fmt(j['mean_excess_vs_spy'])} {fmt(ci[0])} "
              f"{fmt(ci[1])} {fmt(None if null is None else null.p95, 9)} {pct:>5s} {verdict_of(res, r):12s} "
              f"{ledger:>10s} {'SURVIVES' if ok else ''}")
    print(f"\n  {res.verdict_sentence}")


def verdict_of(res: ab.AbResult, r: paper.ArmResult) -> str:
    return paper.verdict_for(r.mean_excess_vs_spy, r.excess_ci, r.excess_t, n_effective=r.n_effective,
                             hypotheses_tested=res.hypotheses_tested)


def run_fixture(a: argparse.Namespace) -> int:
    panel, mem, meta = history.load_fixture()
    f = paper.compute_features(panel, mem)
    sectors = {t: ("Technology" if t in ("AAPL", "MSFT", "NVDA") else "Consumer Cyclical" if t in ("AMZN", "NFLX")
                   else "other") for t in f.tickers}
    arms = parse_arms(a.arms) if a.arms else [ab.arm_by_id("pullback_b3_h10"), ab.arm_by_id("breakout_s8_h10"),
                                               ab.arm_by_id("pead_proxy_g5_h20"), ab.arm_by_id("spy_hold")]
    data = {"scope": "fixture", "source": "tests/fixtures/history (12 names)", "sessions": panel.sessions,
            "tickers": len(panel.tickers), "first": panel.first, "last": panel.last, "dataset_verdict": "adjusted",
            "quality_gate": "n/a", "membership": {"members_on_first_session": len(panel.tickers),
                                                  "members_on_last_session": len(panel.tickers),
                                                  "n_tickers": len(panel.tickers)}, "excluded_as_suspect": []}
    res = ab.run_ab(f, arms, split="train", window=(panel.first, panel.last), rules=paper.Rules(), costs=paper.CostModel(),
                    sectors=sectors, data=data, hypotheses_tested=len(arms), control_replicates=min(a.control_replicates, 5))
    res.limitations.insert(0, "This is the committed 12-name fixture: it exercises the code and measures nothing.")
    blob = {"built_at": res.run_at, "status": "RUN", "is_real": False, "data_scope": "fixture", **res.to_json(),
            "disclaimer": ab.DISCLAIMER}
    out = Path(a.out) if a.out else history.HISTORY_CACHE / "fixture_paper.json"
    if paths.DASHBOARD_DIR in out.resolve().parents:
        print(f"refusing to write a fixture result under {paths.DASHBOARD_DIR}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print_summary(res)
    print(f"  wrote {out}")
    return 0


def render(a: argparse.Namespace) -> int:
    paths.ensure_dirs()
    report = ab.build_report()
    out = Path(a.out) if a.out else ab.PAPER_JSON
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  {out}: status {report['status']}, is_real {report['is_real']}; {report.get('verdict')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--split", choices=["train", "test"], default="train")
    ap.add_argument("--arms", default=None, help="comma-separated arm ids; default is the whole grid")
    ap.add_argument("--slug", default="", help="a word for the results file name")
    ap.add_argument("--control-replicates", type=int, default=ab.CONTROL_REPLICATES)
    ap.add_argument("--force-oos", action="store_true")
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--root", default=str(history.HISTORY_CACHE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true", help="accepted for build_all --check; renders like the default")
    a = ap.parse_args(argv)

    if a.dry_run:
        arms = parse_arms(a.arms)
        print(f"# dry run. --live --split {a.split} would read {Path(a.root) / 'panel'} and the manifest's audit gate,")
        print(f"#   run {len(arms)} arms over {ab.SPLITS[a.split]} with {a.control_replicates} random-control replicates each,")
        print(f"#   count hypotheses from {ab.RESULTS_DIR} ({ab.hypotheses_from_results()} so far), and write one summary there.")
        print("#   arms: " + ", ".join(x.id for x in arms))
        return 0
    if a.fixture:
        return run_fixture(a)
    if a.live:
        return run_live(a)
    return render(a)


if __name__ == "__main__":
    raise SystemExit(main())
