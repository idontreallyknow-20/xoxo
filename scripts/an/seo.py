"""Everything a page needs in its <head> to be found, previewed and attributed.

One place for the site's canonical origin, the author, the Open Graph card and the
JSON-LD, so the 150 generated ticker pages, the hand-written front page and the
404 page cannot drift apart. ``pages.py`` calls :func:`head`; the three hand-written
pages carry the same output pasted in, and ``tests/test_seo.py`` checks that every
page has it. :func:`write_sitemap` and :func:`write_robots` are called from
``build_analysis.py`` so the sitemap always lists the pages that exist.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Iterable, List, Optional

from . import paths

__all__ = ["SITE", "HUB", "AUTHOR", "PERSON", "head", "json_ld", "sitemap_urls",
           "write_sitemap", "write_robots"]

SITE = "https://xoxo-tau-umber.vercel.app"
HUB = "https://josephleung-site.vercel.app"
AUTHOR = "Joseph Leung"
SITE_NAME = "Desk"
OG_IMAGE = SITE + "/og.png"

# The same @id the hub site uses for its Person node, so search engines merge the
# two descriptions into one entity instead of guessing that they are the same man.
PERSON_ID = HUB + "/#joseph"
PERSON = {
    "@type": "Person",
    "@id": PERSON_ID,
    "name": AUTHOR,
    "alternateName": ["Joseph Wah Sing Leung", "Wah Sing (Joseph) Leung"],
    "url": HUB,
    "description": "Joseph Leung is a founder, a retired national-level chess player and a "
                   "calisthenics athlete from Richmond Hill, Ontario.",
    "homeLocation": {"@type": "Place", "name": "Richmond Hill, Ontario, Canada"},
    "sameAs": [
        HUB,
        "https://www.linkedin.com/in/joseph-leung-21b3473bb/",
        "https://github.com/idontreallyknow-20",
        "https://dailybriefhq.com",
        "https://dailybriefhq.com/about",
        "https://nerfchess.com",
        "https://ratings.fide.com/profile/2636654",
        "https://www.chess.ca/en/ratings/p/?id=167606",
        "https://www.chess.com/member/squeakycrab",
        "https://lichess.org/@/BigTrustedCrabby",
        "https://lichess.org/@/UltraAddict2010",
    ],
}

FONTS = (
    '<link rel="preload" href="{root}assets/fonts/bricolage-grotesque-latin.woff2" as="font" '
    'type="font/woff2" crossorigin>\n'
    '<link rel="preload" href="{root}assets/fonts/ibm-plex-mono-400-latin.woff2" as="font" '
    'type="font/woff2" crossorigin>'
)


def json_ld(url: str, name: str, description: str, *, home: bool = False) -> str:
    """Person, WebSite and this WebPage as one graph. The site's creator is the Person."""
    website = {
        "@type": "WebSite",
        "@id": SITE + "/#website",
        "url": SITE + "/",
        "name": SITE_NAME,
        "alternateName": "Desk, a stock research dashboard by Joseph Leung",
        "description": "A stock research dashboard built by Joseph Leung: a quality and price "
                       "screen of US and Canadian companies, a decision journal, and a "
                       "scorecard against the index.",
        "inLanguage": "en-CA",
        "creator": {"@id": PERSON_ID},
        "author": {"@id": PERSON_ID},
        "publisher": {"@id": PERSON_ID},
    }
    page = {
        "@type": "WebPage",
        "@id": url + "#webpage",
        "url": url,
        "name": name,
        "description": description,
        "isPartOf": {"@id": SITE + "/#website"},
        "author": {"@id": PERSON_ID},
        "inLanguage": "en-CA",
    }
    graph = [PERSON, website, page]
    if home:
        graph.append({
            "@type": "WebApplication",
            "@id": SITE + "/#app",
            "name": SITE_NAME,
            "url": SITE + "/",
            "applicationCategory": "FinanceApplication",
            "operatingSystem": "Any (web browser)",
            "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CAD"},
            "creator": {"@id": PERSON_ID},
        })
    body = json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=False, separators=(",", ":"))
    return '<script type="application/ld+json">' + body.replace("</", "<\\/") + "</script>"


def _attr(s: str) -> str:
    return html.escape(s, quote=False).replace('"', "&quot;")


def head(*, path: str, title: str, description: str, root: str, home: bool = False,
         noindex: bool = False) -> str:
    """The SEO block for one page. ``path`` is the page's URL path, e.g. ``/analyze/KLAC/``."""
    url = SITE + path
    t, d = _attr(title), _attr(description)
    lines = [
        f'<meta name="description" content="{d}">',
        f'<meta name="author" content="{AUTHOR}">',
        '<meta name="theme-color" content="#131311">',
        f'<meta name="robots" content="{"noindex" if noindex else "index, follow, max-image-preview:large"}">',
    ]
    if not noindex:
        lines.append(f'<link rel="canonical" href="{url}">')
    lines += [
        f'<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">',
        f'<link rel="icon" href="{root}favicon.ico" sizes="32x32">',
        f'<link rel="apple-touch-icon" href="{root}apple-touch-icon.png">',
        f'<link rel="author" href="{HUB}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{OG_IMAGE}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="Desk, a stock research dashboard by Joseph Leung">',
        '<meta property="og:locale" content="en_CA">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{OG_IMAGE}">',
        FONTS.format(root=root),
    ]
    if not noindex:
        lines.append(json_ld(url, title, description, home=home))
    return "\n".join(lines)


def sitemap_urls(tickers: Iterable[str]) -> List[str]:
    fixed = ["/", "/analyze/", "/analyze/compare/", "/positioning/", "/paper/"]
    return [SITE + p for p in fixed] + [SITE + f"/analyze/{t}/" for t in sorted(tickers)]


def write_sitemap(tickers: Iterable[str], lastmod: Optional[str] = None) -> Path:
    """``lastmod`` is the data snapshot date, not today, so a rebuild of the same
    snapshot writes the same bytes."""
    mod = f"<lastmod>{html.escape(lastmod)}</lastmod>" if lastmod else ""
    rows = "".join(f"  <url><loc>{html.escape(u)}</loc>{mod}</url>\n" for u in sitemap_urls(tickers))
    p = paths.DASHBOARD_DIR / "sitemap.xml"
    p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 + rows + "</urlset>\n", encoding="utf-8")
    return p


def write_robots() -> Path:
    p = paths.DASHBOARD_DIR / "robots.txt"
    p.write_text("User-agent: *\nAllow: /\n\nSitemap: " + SITE + "/sitemap.xml\n", encoding="utf-8")
    return p
