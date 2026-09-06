"""Paths for the analysis layer.

Deliberately independent of ``scripts/config.py``: that module creates directories
as a side effect of import, which makes it unpleasant to unit test against. The two
agree on ``ROOT`` and nothing else.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("DESK_ROOT", Path(__file__).resolve().parent.parent.parent))

UNIVERSE_DIR = ROOT / "universe"
RESEARCH_DIR = ROOT / "research"
PORTFOLIO_DIR = ROOT / "portfolio"
DASHBOARD_DIR = ROOT / "dashboard"

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
EDGAR_CACHE = CACHE_DIR / "edgar"
FINNHUB_CACHE = CACHE_DIR / "finnhub"
PRICE_CACHE = CACHE_DIR / "prices"
SNAPSHOT_DIR = UNIVERSE_DIR / "snapshots"

ANALYSIS_DIR = DASHBOARD_DIR / "analysis"
ASSETS_DIR = DASHBOARD_DIR / "assets"
ANALYZE_PAGES_DIR = DASHBOARD_DIR / "analyze"
POSITIONING_DIR = DASHBOARD_DIR / "positioning"

JOURNAL_MD = ROOT / "journal.md"
CRITERIA_MD = ROOT / "criteria.md"

QUALITY_SCORES = UNIVERSE_DIR / "quality_scores_latest.csv"
QUALITY_TOP150 = UNIVERSE_DIR / "quality_top150.csv"
PRICE_SCREEN = UNIVERSE_DIR / "price_screen_latest.csv"
UNIVERSE_CSV = UNIVERSE_DIR / "universe_latest.csv"

# Written by the analysis layer.
SCORECARD_JSON = DASHBOARD_DIR / "scorecard.json"
BACKTEST_JSON = DASHBOARD_DIR / "backtest.json"
POSITIONING_JSON = DASHBOARD_DIR / "positioning.json"
ANALYSIS_INDEX = ANALYSIS_DIR / "index.json"

WRITABLE = (
    EDGAR_CACHE,
    FINNHUB_CACHE,
    PRICE_CACHE,
    SNAPSHOT_DIR,
    ANALYSIS_DIR,
    ASSETS_DIR,
    ANALYZE_PAGES_DIR,
    POSITIONING_DIR,
)


def ensure_dirs() -> None:
    """Create the directories the build writes into. Call from build scripts, not on import."""
    for d in WRITABLE:
        d.mkdir(parents=True, exist_ok=True)
