"""Shared paths and settings. Edit here, not in the individual scripts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_DIR = ROOT / "universe"
RESEARCH_DIR = ROOT / "research"
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
FUND_CACHE = CACHE_DIR / "fundamentals"
PRICE_CACHE = CACHE_DIR / "prices"
DASHBOARD_DIR = ROOT / "dashboard"
PORTFOLIO_DIR = ROOT / "portfolio"

for d in (UNIVERSE_DIR, RESEARCH_DIR, FUND_CACHE, PRICE_CACHE, DASHBOARD_DIR, PORTFOLIO_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Universe filters (Step 1)
MIN_MARKET_CAP_USD = 2_000_000_000
MIN_DOLLAR_VOLUME_USD = 10_000_000       # avg daily volume in dollars
MIN_YEARS_LISTED = 3
ALLOWED_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "TOR"}   # Nasdaq tiers, NYSE, TSX. NEO (CDRs) and OTC excluded.
EXCLUDED_COUNTRIES = {"China", "Hong Kong"}               # Chinese ADRs are out
SPAC_WORDS = ("acquisition corp", "acquisition co", "spac", "blank check")

# Cache freshness in days
FUNDAMENTALS_MAX_AGE_DAYS = 30
PRICES_MAX_AGE_DAYS = 1

# Concurrency for pulls. Keep low. Yahoo rate limits aggressively.
WORKERS = 2

BENCHMARKS = ["SPY", "QQQ", "VFV.TO"]
STARTING_CASH_USD = 100_000.0
INCEPTION_DATE = "2026-09-04"
