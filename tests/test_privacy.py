"""Personal positions and API keys must never reach anything git tracks."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=str(ROOT))
    return [ROOT / f for f in out.stdout.split("\n") if f.strip()]


def test_portfolio_directory_is_ignored():
    # check-ignore needs a path shape it can classify; "portfolio/" only matches a
    # directory, and the directory does not exist in this clone.
    r = subprocess.run(["git", "check-ignore", "-q", "portfolio/holdings.csv"], cwd=str(ROOT))
    assert r.returncode == 0, "portfolio/ must stay gitignored"
    r = subprocess.run(["git", "check-ignore", "-q", "portfolio.md"], cwd=str(ROOT))
    assert r.returncode == 0


def test_no_portfolio_file_is_tracked():
    bad = [p for p in tracked_files() if "portfolio" in str(p.relative_to(ROOT)).lower()]
    assert not bad, bad


def test_generated_json_contains_no_holdings():
    """The memo reads holdings at build time; it must not write them into the
    committed JSON, or a public repo publishes a private position."""
    p = ROOT / "dashboard" / "positioning.json"
    if not p.exists():
        return
    memo = json.loads(p.read_text())
    assert memo["portfolio"]["holdings"] == [], (
        "holdings were written into a committed file. The memo may read them, never publish them.")


def test_no_api_key_shaped_string_in_any_tracked_file():
    """Finnhub keys are 20+ char alphanumeric tokens; so are plenty of innocent
    things, so the pattern is anchored to the contexts a key actually appears in."""
    patterns = [
        re.compile(r"""(?i)\b(?:finnhub|api|secret|access)[_-]?(?:key|token)\s*[=:]\s*['"][A-Za-z0-9_\-]{12,}['"]"""),
        re.compile(r"""(?i)token=([A-Za-z0-9]{20,})"""),
        re.compile(r"""(?i)\bsk-[A-Za-z0-9]{20,}"""),
        re.compile(r"""(?i)\bghp_[A-Za-z0-9]{20,}"""),
    ]
    offenders = []
    for p in tracked_files():
        if not p.exists() or p.suffix in (".png", ".gif", ".jpg", ".ico"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in patterns:
            for m in pat.finditer(text):
                token = m.group(0)
                if "REDACTED" in token or "YOUR" in token.upper() or "example" in token.lower():
                    continue
                offenders.append(f"{p.relative_to(ROOT)}: {token[:60]}")
    assert not offenders, "\n".join(offenders)


def test_the_only_place_a_finnhub_key_comes_from_is_the_environment():
    src = (ROOT / "scripts" / "an" / "finnhub.py")
    if not src.exists():
        return
    text = src.read_text()
    assert "FINNHUB_KEY" in text
    # No literal key assignment anywhere.
    assert not re.search(r"""(?i)key\s*=\s*['"][A-Za-z0-9]{16,}['"]""", text)


def test_generated_pages_do_not_embed_a_key():
    for p in (ROOT / "dashboard").rglob("*"):
        if p.suffix not in (".html", ".js", ".json"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert "FINNHUB_KEY" not in text or "finnhub" in p.name.lower(), p
        assert not re.search(r"token=[A-Za-z0-9]{16,}", text), p
