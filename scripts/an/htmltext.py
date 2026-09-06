"""Strip an SEC filing document down to readable text.

Filings are HTML with inline XBRL, so a naive tag strip leaves a soup of hidden
``ix:`` elements and style blocks. This removes the non-content elements first,
turns block-level tags into line breaks so paragraphs survive, then unescapes.
Deliberately dependency-free: it runs wherever Python does.
"""
from __future__ import annotations

import html
import re

__all__ = ["to_text"]

_DROP = re.compile(r"<(script|style|head|ix:header)\b.*?</\1\s*>", re.I | re.S)
_BLOCK = re.compile(r"</?(p|div|br|tr|table|h[1-6]|li|ul|ol|section)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\xa0]+")
_BLANKS = re.compile(r"\n{3,}")


def to_text(raw: str) -> str:
    s = _DROP.sub(" ", raw)
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.splitlines())
    return _BLANKS.sub("\n\n", s).strip()
