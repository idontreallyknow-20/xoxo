"""A/B: named rule arms on the same panel, against a matched random control and SPY.

The arithmetic that keeps this honest:

* **Same panel, same window, same mechanics** for every arm. The only thing that differs
  between arms is the rule.
* **A matched random control.** For every rule arm, twenty replicates of the same number of
  entries drawn uniformly from the eligible universe in the same window, with the same horizon
  and the same stop distance, traded through the same mechanics. A rule that cannot beat the
  95th percentile of that null has not shown anything, whatever its own interval says. Most
  "edges" die here, and that is the lesson worth the most.
* **Train and held-out windows, fixed before any run.** Parameters are explored on the train
  window only. The held-out window is run once per pre-registered arm, and the guard refuses a
  second run without a flag that is itself written into the result.
* **Every arm ever run counts.** ``hypotheses_tested`` is read from the results directory, not
  from whoever is asking, and it widens the interval every arm needs to clear.

Nothing here connects to anything. Every number is a price return on 2013 to 2018 closes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import paths, stats
from .paper import Arm, ArmResult, CostModel, Features, Rules, block_ci, run_arm, verdict_for

__all__ = [
    "SPLITS", "HORIZONS", "CONTROL_REPLICATES", "LAB_DIR", "RESULTS_DIR", "LAB_MD", "default_arms", "arm_by_id",
    "NullDistribution", "random_control", "PairedDiff", "paired_difference", "AbResult", "run_ab", "Preregistration",
    "load_preregistration", "list_results", "hypotheses_from_results", "write_result", "build_report", "not_run_report",
    "DISCLAIMER",
]

SPLITS: Dict[str, Tuple[str, str]] = {"train": ("2013-02-08", "2016-06-30"), "test": ("2016-07-01", "2018-02-07")}
HORIZONS: Tuple[int, ...] = (5, 10, 20)
CONTROL_REPLICATES = 20
LAB_DIR = paths.ROOT / "lab"
RESULTS_DIR = LAB_DIR / "results"
LAB_MD = LAB_DIR / "LAB.md"
PAPER_JSON = paths.DASHBOARD_DIR / "paper.json"
DISCLAIMER = "Research and analysis from public data, not personalised financial advice."


def default_arms() -> List[Arm]:
    """The pre-declared grid. Each entry is one hypothesis."""
    out: List[Arm] = []
    for band in (0.02, 0.03, 0.04):
        for h in HORIZONS:
            out.append(Arm(f"pullback_b{int(band * 100)}_h{h}", "pullback", h, {"band": band, "max_dd": -0.15}))
    for stop in (0.06, 0.08, 0.10):
        for h in HORIZONS:
            out.append(Arm(f"breakout_s{int(stop * 100)}_h{h}", "breakout_price_only", h, {"stop_pct": stop}))
    for gap in (0.05, 0.08):
        for h in HORIZONS:
            out.append(Arm(f"pead_proxy_g{int(gap * 100)}_h{h}", "pead_proxy", h, {"gap": gap, "vol_mult": 2.0}))
    out.append(Arm("spy_hold", "spy_hold", 0))
    return out


def arm_by_id(arm_id: str, extra: Iterable[Arm] = ()) -> Optional[Arm]:
    for a in list(extra) + default_arms():
        if a.id == arm_id:
            return a
    # ad hoc ids from the loop: kind_p<param>_h<h>[_filter+filter], parsed leniently
    m = re.match(r"^(pullback|breakout|pead_proxy)_([a-z])(\d+)_h(\d+)(?:_([a-z0-9+]+))?$", arm_id)
    if not m:
        return None
    kind, key, val, h = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    filters = [x for x in (m.group(5) or "").split("+") if x]
    from .paper import FILTERS
    if any(x not in FILTERS for x in filters):
        return None
    extra = {"filters": filters} if filters else {}
    if kind == "pullback" and key == "b":
        return Arm(arm_id, "pullback", h, {"band": val / 100.0, "max_dd": -0.15, **extra})
    if kind == "breakout" and key == "s":
        return Arm(arm_id, "breakout_price_only", h, {"stop_pct": val / 100.0, **extra})
    if kind == "pead_proxy" and key == "g":
        return Arm(arm_id, "pead_proxy", h, {"gap": val / 100.0, "vol_mult": 2.0, **extra})
    return None


# ---------------------------------------------------------------------------
# the control
# ---------------------------------------------------------------------------

@dataclass
class NullDistribution:
    arm_id: str
    n_replicates: int
    n_per_replicate: int
    means: List[float]

    @property
    def p05(self) -> Optional[float]:
        return stats.percentile(self.means, 0.05)

    @property
    def p50(self) -> Optional[float]:
        return stats.percentile(self.means, 0.50)

    @property
    def p95(self) -> Optional[float]:
        return stats.percentile(self.means, 0.95)

    def percentile_of(self, x: Optional[float]) -> Optional[float]:
        if x is None or not self.means:
            return None
        return sum(1 for m in self.means if m <= x) / len(self.means)

    def to_json(self) -> Dict[str, Any]:
        r4 = lambda v: None if v is None else round(v, 4)  # noqa: E731
        return {"arm_id": self.arm_id, "n_replicates": self.n_replicates, "n_per_replicate": self.n_per_replicate,
                "p05": r4(self.p05), "p50": r4(self.p50), "p95": r4(self.p95), "means": [r4(m) for m in self.means]}


def random_control(f: Features, *, like: ArmResult, start: Optional[str], end: Optional[str], rules: Rules,
                   costs: CostModel, replicates: int = CONTROL_REPLICATES, seed: int = 20260906) -> NullDistribution:
    base = sum(ord(ch) for ch in like.arm.id) * 7919 + seed
    means: List[float] = []
    for r in range(replicates):
        ctrl = run_arm(f, Arm(f"random_like_{like.arm.id}", "random", like.arm.horizon), start=start, end=end,
                       rules=rules, costs=costs, sectors={}, match=like, seed=base + r, with_ledger=False)
        m = ctrl.mean_excess_vs_spy
        if m is not None:
            means.append(m)
    return NullDistribution(like.arm.id, replicates, like.n_events, means)


# ---------------------------------------------------------------------------
# paired comparison
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PairedDiff:
    a: str
    b: str
    n_buckets: int
    diff_mean: Optional[float]
    diff_ci: Optional[Tuple[float, float]]
    t: Optional[float]

    def to_json(self) -> Dict[str, Any]:
        r4 = lambda v: None if v is None else round(v, 4)  # noqa: E731
        return {"a": self.a, "b": self.b, "n_buckets": self.n_buckets, "diff_mean": r4(self.diff_mean),
                "diff_ci": None if self.diff_ci is None else [r4(self.diff_ci[0]), r4(self.diff_ci[1])], "t": r4(self.t)}


def paired_difference(a: ArmResult, b: ArmResult, *, block: Optional[int] = None) -> PairedDiff:
    """Mean excess of ``a`` minus ``b`` by month bucket, inner-joined, block-bootstrapped."""
    ba = {m: v for m, v, _ in a.buckets if v is not None}
    bb = {m: v for m, v, _ in b.buckets if v is not None}
    common = sorted(set(ba) & set(bb))
    diffs = [ba[m] - bb[m] for m in common]
    blk = block or max(a.block, b.block)
    return PairedDiff(a.arm.id, b.arm.id, len(diffs), stats.mean(diffs), block_ci(diffs, block=blk), stats.t_stat(diffs))


# ---------------------------------------------------------------------------
# a run
# ---------------------------------------------------------------------------

def limitations_for(*, split: str, data: Mapping[str, Any], rules: Rules, costs: CostModel,
                    excluded: Sequence[str]) -> List[str]:
    mem = (data.get("membership") or {})
    adj = data.get("dataset_verdict") or "unknown"
    return [
        "Survivorship: the panel holds the 505 names in the S&P 500 as of February 2018; names that left the index "
        f"before then have no prices here. The membership filter keeps a name out until it was actually added "
        f"({mem.get('members_on_first_session')} of {mem.get('n_tickers')} were members on the first session, "
        f"{mem.get('members_on_last_session')} on the last), but it cannot add the departed back.",
        "One regime: February 2013 to February 2018 was one bull market with two shallow corrections. A rule that "
        "worked here has been tested in one weather.",
        "pead_proxy is a gap-on-volume proxy, not post-earnings drift: no earnings dates exist in this history. "
        "breakout is price-only: no estimate revisions exist in this history. The guidance setup cannot be tested at all.",
        "Every return is a price return with no dividends, on the stocks and on SPY alike; excess figures are unbiased by "
        "that, absolute figures are understated by roughly the yield.",
        f"Costs are assumed: {costs.commission_bps:g} bps commission and {costs.slippage_bps:g} bps slippage each way. "
        "Fills are the next session's close after a signal; stops are closing levels exited at the following close.",
        f"Dataset adjustment status from the audit: {adj}. "
        + (f"Suspected data artefacts excluded from the eligible universe: {', '.join(excluded)}." if excluded
           else "No names were excluded for data reasons."),
        "Sector labels come from the 2026 quality screen, not from 2013; names not in that screen count as one "
        "'unknown' sector and the two-per-sector cap applies to it.",
        ("Everything chosen on the train window is in-sample. An arm's own interval says nothing until it is run on the "
         "held-out window, once, as pre-registered." if split == "train" else
         "The held-out window is about eighteen months of one bull market; a survivor here is a candidate for logging "
         "live under swing.md's thirty-call floor, not a finding."),
    ]


@dataclass
class AbResult:
    split: str
    window: Tuple[str, str]
    arms: List[ArmResult]
    nulls: Dict[str, NullDistribution]
    pairs: List[PairedDiff]
    hypotheses_tested: int
    limitations: List[str]
    data: Dict[str, Any] = field(default_factory=dict)
    rules: Rules = field(default_factory=Rules)
    costs: CostModel = field(default_factory=CostModel)
    forced: bool = False
    flags: List[str] = field(default_factory=list)
    run_at: str = ""

    def __post_init__(self) -> None:
        if not self.limitations:
            raise ValueError("an A/B result may not be created without limitations")

    def survives(self, r: ArmResult) -> Tuple[bool, str]:
        """The pre-registered decision rule, applied to one arm."""
        if r.arm.kind in ("random", "spy_hold"):
            return False, "not a rule arm"
        v = verdict_for(r.mean_excess_vs_spy, r.excess_ci, r.excess_t, n_effective=r.n_effective,
                        hypotheses_tested=self.hypotheses_tested)
        null = self.nulls.get(r.arm.id)
        p95 = None if null is None else null.p95
        ci_ok = r.excess_ci is not None and r.excess_ci[0] > 0
        null_ok = p95 is not None and r.mean_excess_vs_spy is not None and r.mean_excess_vs_spy > p95
        ok = v in ("suggestive", "supported") and ci_ok and null_ok
        why = (f"verdict {v}; interval {'above' if ci_ok else 'touches or crosses'} zero; "
               f"mean excess {'above' if null_ok else 'not above'} the random control's p95")
        return ok, why

    @property
    def survivors(self) -> List[str]:
        return [r.arm.id for r in self.arms if self.survives(r)[0]]

    @property
    def verdict(self) -> str:
        if self.split != "test":
            return "in-sample only"
        prereg = [r for r in self.arms if r.arm.preregistered]
        if not prereg:
            return "no pre-registered arm was run"
        s = [r.arm.id for r in prereg if self.survives(r)[0]]
        if s:
            return "survives out of sample against the matched random control: " + ", ".join(s)
        return "no edge found at these horizons on this data"

    @property
    def verdict_sentence(self) -> str:
        if self.split != "test":
            n = len([r for r in self.arms if r.arm.kind not in ("random", "spy_hold")])
            return (f"In-sample only: {n} rule arms on {self.window[0]} to {self.window[1]}, "
                    f"{self.hypotheses_tested} hypotheses counted so far. Nothing here is a finding.")
        parts = []
        for r in self.arms:
            if not r.arm.preregistered:
                continue
            ok, why = self.survives(r)
            ci = "n/a" if r.excess_ci is None else f"[{r.excess_ci[0]:+.4f}, {r.excess_ci[1]:+.4f}]"
            m = "n/a" if r.mean_excess_vs_spy is None else f"{r.mean_excess_vs_spy:+.4f}"
            null = self.nulls.get(r.arm.id)
            p95 = "n/a" if null is None or null.p95 is None else f"{null.p95:+.4f}"
            parts.append(f"{r.arm.id}: mean excess vs SPY {m} per trade, 95% interval {ci}, n={r.n_trades}, "
                         f"random control p95 {p95}; {'survives' if ok else 'does not survive'} ({why})")
        return f"{self.verdict}. " + " ".join(parts) + f" Hypotheses counted: {self.hypotheses_tested}."

    def to_json(self) -> Dict[str, Any]:
        arms = []
        for r in sorted(self.arms, key=lambda x: x.arm.id):
            j = r.to_json()
            j["hypotheses_tested"] = self.hypotheses_tested
            j["verdict"] = verdict_for(r.mean_excess_vs_spy, r.excess_ci, r.excess_t, n_effective=r.n_effective,
                                       hypotheses_tested=self.hypotheses_tested)
            null = self.nulls.get(r.arm.id)
            j["null"] = None if null is None else {**null.to_json(), "percentile_of_arm": null.percentile_of(r.mean_excess_vs_spy)}
            ok, why = self.survives(r)
            j["decision_rule"] = {"survives": ok, "why": why}
            arms.append(j)
        return {
            "run_at": self.run_at, "split": self.split, "window": list(self.window), "data": dict(self.data),
            "rules": self.rules.to_json(), "costs": self.costs.to_json(), "arms": arms,
            "pairs": [p.to_json() for p in sorted(self.pairs, key=lambda p: (p.a, p.b))],
            "hypotheses_tested": self.hypotheses_tested, "survivors": self.survivors, "verdict": self.verdict,
            "verdict_sentence": self.verdict_sentence, "forced": self.forced, "flags": list(self.flags),
            "limitations": list(self.limitations),
        }


def run_ab(f: Features, arms: Sequence[Arm], *, split: str, rules: Rules, costs: CostModel, sectors: Mapping[str, str],
           data: Mapping[str, Any], hypotheses_tested: int, control_replicates: int = CONTROL_REPLICATES,
           seed: int = 20260906, window: Optional[Tuple[str, str]] = None, forced: bool = False,
           run_at: Optional[str] = None) -> AbResult:
    start, end = window or SPLITS[split]
    results: List[ArmResult] = []
    nulls: Dict[str, NullDistribution] = {}
    for arm in arms:
        r = run_arm(f, arm, start=start, end=end, rules=rules, costs=costs, sectors=sectors,
                    hypotheses_tested=hypotheses_tested, seed=seed)
        results.append(r)
        if arm.kind not in ("random", "spy_hold") and r.n_events > 0 and control_replicates > 0:
            nulls[arm.id] = random_control(f, like=r, start=start, end=end, rules=rules, costs=costs,
                                           replicates=control_replicates, seed=seed)
    pairs: List[PairedDiff] = []
    by_kind: Dict[str, List[ArmResult]] = {}
    for r in results:
        by_kind.setdefault(r.arm.kind, []).append(r)
    for kind, group in by_kind.items():
        if kind in ("random", "spy_hold"):
            continue
        group = sorted(group, key=lambda x: x.arm.id)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if group[i].arm.horizon == group[j].arm.horizon:
                    pairs.append(paired_difference(group[i], group[j]))
    lim = limitations_for(split=split, data=data, rules=rules, costs=costs, excluded=list(f.excluded))
    flags: List[str] = []
    for r in results:
        if r.mean_excess_vs_spy is not None and abs(r.mean_excess_vs_spy) > 0.05:
            flags.append(f"{r.arm.id}: mean excess per trade over 5% at a days-to-weeks horizon; check for look-ahead")
    return AbResult(split, (start, end), results, nulls, pairs, hypotheses_tested, lim, dict(data), rules, costs, forced,
                    flags, run_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat())


# ---------------------------------------------------------------------------
# pre-registration and the results directory
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preregistration:
    oos_window: Optional[Tuple[str, str]]
    arm_ids: Tuple[str, ...]
    budget: Optional[int]
    written: Optional[str]


def load_preregistration(path: Optional[Path] = None) -> Preregistration:
    """Read the pre-registration block out of lab/LAB.md. Absent fields are None, never guessed."""
    p = path or LAB_MD
    if not p.exists():
        return Preregistration(None, (), None, None)
    text = p.read_text(encoding="utf-8")
    m = re.search(r"^## Pre-registration.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return Preregistration(None, (), None, None)
    block = m.group(1)
    win = re.search(r"OOS window:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", block)
    arms = re.search(r"Pre-registered arms:\s*(.*)$", block, re.M)
    ids: Tuple[str, ...] = ()
    if arms:
        ids = tuple(a.strip() for a in re.split(r"[,\s]+", arms.group(1).strip())
                    if re.match(r"^[a-z_]+_[a-z]\d+_h\d+(?:_[a-z0-9+]+)?$", a.strip()))
    budget = re.search(r"Budget:\s*(\d+)", block)
    written = re.search(r"written\s+(\d{4}-\d{2}-\d{2})", m.group(0))
    return Preregistration((win.group(1), win.group(2)) if win else None, ids,
                           int(budget.group(1)) if budget else None, written.group(1) if written else None)


def list_results(directory: Optional[Path] = None) -> List[Path]:
    d = directory or RESULTS_DIR
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def _read(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def hypotheses_from_results(directory: Optional[Path] = None, *, extra: Iterable[str] = ()) -> int:
    """Every distinct rule arm ever run on the train window, plus the ones about to run."""
    ids = set(extra)
    for p in list_results(directory):
        blob = _read(p)
        if not blob or blob.get("split") != "train":
            continue
        for a in blob.get("arms") or []:
            kind = (a.get("arm") or {}).get("kind")
            if kind not in ("random", "spy_hold"):
                ids.add((a.get("arm") or {}).get("id"))
    ids.discard(None)
    return len(ids)


def write_result(result: AbResult, *, directory: Optional[Path] = None, slug: str = "") -> Path:
    d = directory or RESULTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = result.run_at.replace(":", "-").replace("+00:00", "Z")
    name = f"{stamp}_{result.split}{'_' + slug if slug else ''}.json"
    p = d / name
    p.write_text(json.dumps(result.to_json(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# the dashboard file
# ---------------------------------------------------------------------------

def _latest(split: str, directory: Optional[Path]) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    best: Optional[Dict[str, Any]] = None
    best_p: Optional[Path] = None
    for p in list_results(directory):
        blob = _read(p)
        if blob and blob.get("split") == split and blob.get("data", {}).get("scope") != "fixture":
            best, best_p = blob, p
    return best, best_p


def build_report(*, directory: Optional[Path] = None, built_at: Optional[str] = None,
                 lab_md: Optional[Path] = None) -> Dict[str, Any]:
    """Render dashboard/paper.json from the committed results. Never reads the price cache."""
    train, train_p = _latest("train", directory)
    test, test_p = _latest("test", directory)
    stamp = built_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    if train is None and test is None:
        return not_run_report(built_at=stamp, why="no result under lab/results/; run scripts/paper_trade.py --live first")
    pre = load_preregistration(lab_md)
    src = test or train
    n_train_files = len([p for p in list_results(directory) if (_read(p) or {}).get("split") == "train"])
    status = "RUN"
    is_real = bool(src and src.get("data", {}).get("scope") == "full")
    return {
        "built_at": stamp, "status": status, "is_real": is_real, "data_scope": (src or {}).get("data", {}).get("scope"),
        "what_this_is": ("The swing rules in swing.md replayed on real 2013-2018 closes with fake money, against a matched "
                         "random control and SPY. A measurement of the rules, not a recommendation of anything."),
        "data": (src or {}).get("data"), "rules": (src or {}).get("rules"), "costs": (src or {}).get("costs"),
        "split": {k: list(v) for k, v in SPLITS.items()},
        "train": None if train is None else {"file": train_p.name if train_p else None, **{k: train.get(k) for k in (
            "run_at", "window", "arms", "pairs", "hypotheses_tested", "survivors", "verdict", "verdict_sentence", "flags")}},
        "test": None if test is None else {"file": test_p.name if test_p else None, **{k: test.get(k) for k in (
            "run_at", "window", "arms", "pairs", "hypotheses_tested", "survivors", "verdict", "verdict_sentence", "forced",
            "flags")}},
        "arms_not_testable": {
            "guidance": "no historical 8-K guidance source is reachable, so the guidance-raise setup has no arm",
            "pead": "no earnings dates exist in this history; pead_proxy is a gap-on-volume proxy and is labelled so",
        },
        "preregistration": {"oos_window": None if pre.oos_window is None else list(pre.oos_window),
                            "arm_ids": list(pre.arm_ids), "budget": pre.budget, "written": pre.written,
                            "oos_window_matches_code": pre.oos_window == SPLITS["test"] if pre.oos_window else None},
        "lab": {"log": "lab/LAB.md", "train_runs": n_train_files, "results": [p.name for p in list_results(directory)]},
        "verdict": (test or {}).get("verdict") or "held-out window not run yet",
        "verdict_sentence": (test or {}).get("verdict_sentence") or (train or {}).get("verdict_sentence"),
        "limitations": (src or {}).get("limitations") or ["no run has been recorded"],
        "disclaimer": DISCLAIMER,
    }


def not_run_report(*, built_at: Optional[str] = None, why: str) -> Dict[str, Any]:
    return {
        "built_at": built_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "status": "NOT RUN", "is_real": False, "data_scope": None, "why_not_run": why,
        "what_this_is": ("The swing rules in swing.md replayed on real closes with fake money, against a matched random "
                         "control and SPY. A measurement of the rules, not a recommendation of anything."),
        "split": {k: list(v) for k, v in SPLITS.items()}, "train": None, "test": None,
        "verdict": "not run", "verdict_sentence": "Nothing has been measured.",
        "limitations": ["No paper-trading run has been recorded under lab/results/."],
        "disclaimer": DISCLAIMER,
    }
