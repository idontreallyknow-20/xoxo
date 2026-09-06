"""Every quote in a generated narrative must appear verbatim in its research note.

These narratives are the only part of the analysis pages produced by reading rather
than parsing, which makes them the only part that could contain something nobody
wrote. They are rendered on the page inside quotation marks as evidence, so a
paraphrase presented as a quote would be a fabrication with a citation attached.

This check is mechanical on purpose. The generation pipeline already had a model
audit each extraction and a second model repair what the audit caught, and every
one of the sixteen came back from the first audit needing repair. A model checking
another model's quotes is not the same as looking for the string.

Whitespace and typographic punctuation are normalised before comparison, because a
curly apostrophe is not a fabrication.
"""
import json
import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NARRATIVE_DIR = ROOT / "dashboard" / "analysis" / "_narrative"


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("—", "--"), ("–", "-"), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def narratives():
    if not NARRATIVE_DIR.exists():
        return []
    return sorted(NARRATIVE_DIR.glob("*.json"))


def quoted_fields(n):
    for g in n.get("guidance") or []:
        if g.get("quote"):
            yield f"guidance/{g.get('what', '?')}", g["quote"]
    tone = n.get("tone") or {}
    if tone.get("evidence"):
        yield "tone/evidence", tone["evidence"]
    for r in n.get("new_risks") or []:
        if r.get("quote"):
            yield "new_risk", r["quote"]


@pytest.mark.parametrize("path", narratives(), ids=lambda p: p.stem)
def test_every_quote_is_verbatim(path):
    ticker = path.stem
    note = ROOT / "research" / f"{ticker}.md"
    assert note.exists(), f"narrative for {ticker} but no research note"
    src = normalise(note.read_text(encoding="utf-8"))
    n = json.loads(path.read_text(encoding="utf-8"))
    misses = [(where, q) for where, q in quoted_fields(n) if normalise(q) not in src]
    assert not misses, "\n".join(f"{ticker} [{w}] {q[:120]!r}" for w, q in misses)


def test_there_are_quotes_to_check():
    """A guard that passes because it found nothing is not a guard."""
    total = sum(len(list(quoted_fields(json.loads(p.read_text())))) for p in narratives())
    assert total >= 100, f"only {total} quotes across {len(narratives())} narratives"


@pytest.mark.parametrize("path", narratives(), ids=lambda p: p.stem)
def test_dodged_entries_carry_a_confidence_and_reasoning(path):
    """"Management dodged this" is the easiest claim on the page to fabricate and the
    hardest to check, so it is the one that must always show its working."""
    n = json.loads(path.read_text(encoding="utf-8"))
    for d in n.get("dodged") or []:
        assert d.get("topic")
        assert d.get("reasoning") and len(d["reasoning"]) > 40, d.get("topic")
        assert d.get("confidence") in ("low", "medium", "high"), d.get("confidence")


@pytest.mark.parametrize("path", narratives(), ids=lambda p: p.stem)
def test_the_standing_caveat_is_present(path):
    n = json.loads(path.read_text(encoding="utf-8"))
    assert "usually the author compressing" in n["standing_caveat"]
    assert "no transcript was available" in n["standing_caveat"]


@pytest.mark.parametrize("path", narratives(), ids=lambda p: p.stem)
def test_not_determinable_is_specific_not_generic(path):
    """This list is the honest half of the output. A single vague line means nobody
    actually looked for what was missing."""
    n = json.loads(path.read_text(encoding="utf-8"))
    nd = n.get("not_determinable") or []
    assert len(nd) >= 4, f"{path.stem} lists only {len(nd)} gaps"
    assert all(len(x) > 30 for x in nd)


def test_all_sixteen_researched_names_have_one():
    from an import research_md

    have = {p.stem for p in narratives()}
    want = set(research_md.load_all())
    assert have == want, f"missing {sorted(want - have)}"


def test_no_confidence_string_is_short_enough_to_assume_it_is_a_word():
    """The confidence fields are prose: "low, and it is not higher because ...".
    Rendering the whole string in the narrow nowrap column pushed a page to 2,302px
    wide. The renderer splits the word from the reason; this records why."""
    long_ones = 0
    for p in narratives():
        n = json.loads(p.read_text(encoding="utf-8"))
        tone = (n.get("tone") or {}).get("confidence") or ""
        if len(tone) > 20:
            long_ones += 1
    assert long_ones >= 8, "if these became short words, the renderer's split is now dead code"
