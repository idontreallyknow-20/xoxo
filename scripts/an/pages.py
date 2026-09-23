"""Generate the static page shells for /analyze/<TICKER>/ and /analyze/.

There is no router here and no framework. ``scripts/serve.py`` runs
``http.server.SimpleHTTPRequestHandler`` rooted at ``dashboard/``, which serves
``index.html`` out of a directory, so writing ``dashboard/analyze/KLAC/index.html``
makes the URL ``/analyze/KLAC/`` work with no server change at all. The same layout
works on ``python -m http.server``, on Vercel and on any other static host, which
is why it was chosen over a query string.

Each shell is about a kilobyte: it sets the ticker, pulls in the shared stylesheet
and the two scripts, and gets out of the way. The page's content comes from
``analysis/<TICKER>.json`` at load time, so a rebuild of the data does not require
regenerating 150 HTML files.
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import paths, seo

__all__ = ["write_ticker_pages", "write_analyze_index", "write_compare_page", "write_positioning_page", "SHELL"]

SHELL = """<!doctype html>
<html lang="en" data-theme="night">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{head}
<link rel="stylesheet" href="{root}assets/desk.css">
</head>
<body>
<div class="page"><p class="loading" role="status">loading</p></div>
<noscript><div style="padding:24px 40px;font-family:sans-serif">
This page renders from {data}. Without JavaScript, read that file directly.
</div></noscript>
<script>{setup}</script>
<script src="{root}assets/motion.js"></script>
<script src="{root}assets/desk-common.js"></script>
<script src="{root}assets/{script}"></script>
</body>
</html>
"""


def _shell(*, title: str, description: str, path: str, root: str, script: str, setup: str,
           data: str) -> str:
    return SHELL.format(
        title=html.escape(title),
        head=seo.head(path=path, title=title, description=description, root=root),
        root=root, script=script, setup=setup, data=html.escape(data),
    )


def write_ticker_pages(tickers: Iterable[str], index: Optional[Dict] = None) -> List[Path]:
    out: List[Path] = []
    names = {}
    if index:
        names = {t["ticker"]: t.get("company") or t["ticker"] for t in index.get("tickers", [])}
    for t in sorted(tickers):
        d = paths.ANALYZE_PAGES_DIR / t
        d.mkdir(parents=True, exist_ok=True)
        company = names.get(t, t)
        p = d / "index.html"
        p.write_text(
            _shell(
                title=f"{company} ({t}) stock analysis | Desk by Joseph Leung",
                description=f"{company} ({t}): quality score, valuation against its own history, "
                            "the standing call and what would prove it wrong. From Desk, Joseph "
                            "Leung's stock research dashboard. Research, not financial advice.",
                path=f"/analyze/{t}/",
                root="../../",
                script="analyze.js",
                setup=f'window.DESK_TICKER = "{t}";',
                data=f"analysis/{t}.json",
            ),
            encoding="utf-8",
        )
        out.append(p)
    return out


def write_analyze_index() -> Path:
    paths.ANALYZE_PAGES_DIR.mkdir(parents=True, exist_ok=True)
    p = paths.ANALYZE_PAGES_DIR / "index.html"
    p.write_text(
        _shell(
            title="Analyse the quality top 150 | Desk by Joseph Leung",
            description="The 150 highest-quality US and Canadian companies from Joseph Leung's "
                        "stock screen, each with its own analysis page: score, valuation, "
                        "drawdown and next report date.",
            path="/analyze/",
            root="../",
            script="analyze-index.js",
            setup="window.DESK_INDEX = true;",
            data="analysis/index.json",
        ),
        encoding="utf-8",
    )
    return p


def write_positioning_page() -> Path:
    paths.POSITIONING_DIR.mkdir(parents=True, exist_ok=True)
    p = paths.POSITIONING_DIR / "index.html"
    p.write_text(
        _shell(
            title="Positioning memo | Desk by Joseph Leung",
            description="The scoring model behind Desk, what validating it would take, and a "
                        "ranked memo of candidate positions. Research, not financial advice.",
            path="/positioning/",
            root="../",
            script="positioning.js",
            setup="window.DESK_POSITIONING = true;",
            data="positioning.json",
        ),
        encoding="utf-8",
    )
    return p


def write_compare_page() -> Path:
    """``/analyze/compare/?t=KLAC,BKNG``: the selection is a query string, so the page is one shell."""
    d = paths.ANALYZE_PAGES_DIR / "compare"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "index.html"
    p.write_text(
        _shell(
            title="Compare stocks side by side | Desk by Joseph Leung",
            description="Up to four analysed names side by side: the call, the falsifier, four "
                        "fiscal years, valuation and the score. Research, not financial advice.",
            path="/analyze/compare/",
            root="../../",
            script="compare.js",
            setup="window.DESK_COMPARE = true;",
            data="analysis/<TICKER>.json",
        ),
        encoding="utf-8",
    )
    return p

