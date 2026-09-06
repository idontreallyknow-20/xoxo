"""Archive a dated copy of every pipeline run, so a real backtest becomes possible.

THE PROBLEM THIS SOLVES
-----------------------
The backtest question in this project is not hard, it is impossible, and for a
reason no amount of cleverness fixes: there is one dated cross section and no
history. A cross-sectional score is validated by asking, on many past dates,
whether its ordering predicted what came next, and with one date there is no next.

Reconstructing the past from today's data does not fix it either. Today's universe
is the companies that survived, so any historical test built from it is flattered
by every name that was delisted, acquired or wiped out and is therefore absent.
Today's fundamentals are restated, so any score computed from them knows things
that were not public on the rebalance date.

WHAT THIS DOES INSTEAD
----------------------
Archives the screen output as it stood, every run, under
``universe/snapshots/<date>/``. Nothing clever, and that is the point:

*No look-ahead,* because a snapshot contains only what the pipeline could see on
the day it ran.

*No survivorship bias,* because the universe is recorded as it was. A company that
was in the 2026-09 snapshot and is gone by 2027-03 is still in the 2026-09 file,
and its absence later is itself the datum.

*No reconstruction,* because nothing is inferred backwards.

The cost is patience, and ``an.power`` measures exactly how much. The short answer
is **archive monthly, not quarterly**: the backtest engine will not return a
verdict above "weak" below twelve independent periods, which is three years of
quarterly snapshots and one year of monthly ones. The fundamentals only move
quarterly, but the score does not, because prices, drawdowns and estimate revisions
move continuously and carry most of its realised variance.

The first snapshot is free, taken from what is already committed.

``load_panel`` turns the archive into the ``Rebalance`` objects the backtest engine
takes, and refuses to build one from a single date rather than returning something
that looks like a panel and is not.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import local, paths, score

__all__ = ["Snapshot", "archive", "list_snapshots", "load_snapshot", "load_panel", "ARCHIVED_FILES"]

ARCHIVED_FILES = (
    "quality_scores_latest.csv",
    "price_screen_latest.csv",
    "quality_top150.csv",
    "universe_latest.csv",
    "bucket_compounders.csv",
    "bucket_cyclical_turns.csv",
)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Snapshot:
    date: str
    directory: Path
    manifest: Dict[str, object]

    @property
    def files(self) -> Dict[str, str]:
        return dict(self.manifest.get("files", {}))

    @property
    def n_scored(self) -> Optional[int]:
        return self.manifest.get("n_scored")

    @property
    def n_priced(self) -> Optional[int]:
        return self.manifest.get("n_priced")


def archive(date: Optional[str] = None, *, force: bool = False,
            root: Optional[Path] = None) -> Snapshot:
    """Copy the current screen output into a dated directory with a manifest.

    The date comes from the ``pulled`` column rather than from the clock, so a
    snapshot is stamped with the day the data describes and re-archiving the same
    pull is a no-op instead of creating a second, identical directory under
    today's date.
    """
    base = Path(root) if root else paths.SNAPSHOT_DIR
    src = paths.UNIVERSE_DIR

    if date is None:
        recs = local.load_quality()
        dates = {r.pulled for r in recs.values() if r.pulled}
        date = sorted(dates)[-1] if dates else dt.date.today().isoformat()

    out = base / date
    if out.exists() and not force:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        return Snapshot(date=date, directory=out, manifest=manifest)

    out.mkdir(parents=True, exist_ok=True)
    files: Dict[str, str] = {}
    for name in ARCHIVED_FILES:
        p = src / name
        if not p.exists():
            continue
        shutil.copy2(p, out / name)
        files[name] = _sha(p)

    universe = local.load_universe()
    top150 = [r for r in universe.values() if r.in_top_150]
    scores = {v: score.score_universe(top150, variant=v) for v in score.VARIANTS}
    # The score as it stood, stored alongside the inputs. Recomputing it later from
    # archived CSVs would give the same answer only if score.py never changed, and
    # score.py will change.
    (out / "scores.json").write_text(json.dumps({
        "date": date,
        "score_module_version": 1,
        "variants": {
            v: {t: {"score": round(b.score, 6), "percentile": b.display,
                    "coverage": round(b.coverage, 4)} for t, b in table.items()}
            for v, table in scores.items()
        },
    }, indent=1), encoding="utf-8")
    files["scores.json"] = _sha(out / "scores.json")

    manifest = {
        "date": date,
        "archived_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "files": files,
        "n_scored": len(universe),
        "n_priced": len(top150),
        "tickers_scored": sorted(universe),
        "tickers_priced": sorted(r.ticker for r in top150),
        "why": (
            "A point-in-time record of what the pipeline could see on this date. Kept so that a "
            "future backtest can use what was actually known rather than reconstructing the past "
            "from today's survivors and today's restated figures."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return Snapshot(date=date, directory=out, manifest=manifest)


def list_snapshots(root: Optional[Path] = None) -> List[Snapshot]:
    base = Path(root) if root else paths.SNAPSHOT_DIR
    if not base.exists():
        return []
    out: List[Snapshot] = []
    for d in sorted(base.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.exists():
            try:
                out.append(Snapshot(date=d.name, directory=d,
                                    manifest=json.loads(m.read_text(encoding="utf-8"))))
            except json.JSONDecodeError:
                continue
    return out


def load_snapshot(date: str, root: Optional[Path] = None) -> Optional[Snapshot]:
    for s in list_snapshots(root):
        if s.date == date:
            return s
    return None


class NotEnoughHistory(RuntimeError):
    """Raised rather than returning something that looks like a panel and is not."""


def load_panel(*, variant: str = "quality_value", root: Optional[Path] = None):
    """Build the score side of a backtest panel from the archive.

    Returns ``[(date, {ticker: score}, {ticker: sector}, {ticker: [buckets]})]``.
    Forward returns are the caller's job: they need prices, which are not archived
    here because a price series is cheap to refetch and a screen is not.

    Refuses to return anything from fewer than two dates. One snapshot is not a
    panel, and quietly returning a one-element list is how a single cross section
    gets treated as history.
    """
    snaps = list_snapshots(root)
    if len(snaps) < 2:
        raise NotEnoughHistory(
            f"{len(snaps)} snapshot(s) archived. A panel needs at least two dates, and a result "
            "worth quoting needs a dozen. Run scripts/snapshot.py after every pipeline run; the "
            "next useful date is the next quarterly re-underwrite."
        )
    out = []
    for s in snaps:
        p = s.directory / "scores.json"
        if not p.exists():
            continue
        blob = json.loads(p.read_text(encoding="utf-8"))
        table = (blob.get("variants") or {}).get(variant) or {}
        if not table:
            continue
        out.append((s.date, {t: v["score"] for t, v in table.items()}))
    if len(out) < 2:
        raise NotEnoughHistory(f"only {len(out)} snapshot(s) carry scores for variant {variant!r}")
    return out


def coverage_report(root: Optional[Path] = None) -> Dict[str, object]:
    """What the archive can and cannot support yet, in one object the page can show."""
    snaps = list_snapshots(root)
    dates = [s.date for s in snaps]
    entered: Dict[str, str] = {}
    left: Dict[str, str] = {}
    prev: Optional[set] = None
    for s in snaps:
        cur = set(s.manifest.get("tickers_priced", []))
        if prev is not None:
            for t in cur - prev:
                entered.setdefault(t, s.date)
            for t in prev - cur:
                left.setdefault(t, s.date)
        prev = cur
    return {
        "n_snapshots": len(snaps),
        "dates": dates,
        "usable_forward_windows": max(0, len(snaps) - 1),
        "names_that_entered": entered,
        "names_that_left": left,
        "status": (
            "A panel needs at least two dates. There is one."
            if len(snaps) < 2 else
            f"{len(snaps) - 1} forward window(s) available. A rank information coefficient means "
            "very little below about a dozen."
        ),
        "note": (
            "Names that left the priced universe between snapshots are the beginning of an honest "
            "survivorship record: they are in the earlier file and absent from the later one, and "
            "that absence is itself the datum."
        ),
    }
