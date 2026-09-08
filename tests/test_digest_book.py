"""The two editions and three sections the desk's routine added to the digest: book, memo, tests, close."""
import datetime as dt
import json
from pathlib import Path

import pytest

from an import digest, mail
from tests.test_language_guard import check

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 9, 8)


def inputs(**over):
    base = digest.load_inputs(ROOT / "dashboard")
    base.update(over)
    return base


def marked_book():
    return {"status": "MARKED", "equity": 101_234.5, "starting_cash": 100_000.0, "cash": 76_000.0, "ret_since_start": 0.0123,
            "first_fill": "2026-09-08", "as_of": "2026-09-12", "quotes_age_sessions": 1, "n_open": 2, "n_pending": 0,
            "benchmarks_since_start": {"SPY": 0.004, "QQQ": 0.006, "VFV.TO": 0.004},
            "excess_since_start": {"SPY": 0.0083, "QQQ": 0.0063, "VFV.TO": 0.0083},
            "open": [{"ticker": "BKNG", "kind": "long", "shares": 41, "entry": 195.0, "last": 201.0, "ret": 0.0308,
                      "excess_vs_spy": 0.0268, "distance_to_trigger": 0.34, "falsifier_breached": False, "stop_breached": None},
                     {"ticker": "REGN", "kind": "long", "shares": 9, "entry": 843.0, "last": 830.0, "ret": -0.0154,
                      "excess_vs_spy": -0.0194, "distance_to_trigger": 0.277, "falsifier_breached": False, "stop_breached": None}],
            "closed": [], "refused": [], "flags": ["MSFT: target $8,000 filled at $511.20"],
            "long": [{"ticker": "MSFT", "kind": "long", "call_date": TODAY.isoformat(), "status": "open", "target_usd": 8000.0,
                      "conviction": 4, "thesis": "Azure grew 43%.", "wrong_if": "a close under $400.", "refused_because": None}],
            "swing": [], "rule_checks": [], "max_drawdown": -0.01}


def test_the_close_edition_exists_and_carries_the_book_but_not_the_memo():
    assert "close" in digest.EDITIONS
    d = digest.build_digest(inputs(book=marked_book()), today=TODAY, edition="close")
    assert d["book"]["status"] == "MARKED" and d["memo"] is None
    assert digest.subject_for(d).startswith("Desk close, Tue 8 Sep")
    html = digest.render_html(d)
    assert "My book" in html and "The memo, sized" not in html and "BKNG" in html and "+3.1%" in html


def test_the_morning_edition_carries_book_memo_and_the_days_calls():
    d = digest.build_digest(inputs(book=marked_book()), today=TODAY, edition="morning")
    assert d["book"]["line"].startswith("Equity $101,234")
    assert "1 session old" in d["book"]["line"]
    assert [c["ticker"] for c in d["book"]["calls_today"]] == ["MSFT"]
    assert d["memo"] and d["memo"]["rows"] and d["memo"]["rows"][0]["ticker"] == "BKNG"
    assert all(r["wrong_if"] for r in d["memo"]["rows"])
    html = digest.render_html(d)
    for needle in ("My book", "The memo, sized for $100,000", "Wrong if:", "Azure grew 43%", "sized at", "You decide",
                   "read and left out"):
        assert needle in html, needle
    assert "<script" not in html
    assert "MSFT" in mail.text_from_html(html)


def test_not_marked_and_no_book_file_are_said_plainly():
    d = digest.build_digest(inputs(book={"status": "NOT MARKED", "n_pending": 3}), today=TODAY)
    assert d["book"]["line"].startswith("NOT MARKED: 3 calls pending")
    d2 = digest.build_digest(inputs(book=None), today=TODAY)
    assert d2["book"]["status"] == "NO BOOK FILE"
    mid = digest.build_digest(inputs(book=marked_book()), today=TODAY, edition="midday")
    assert mid["book"] is None and mid["memo"] is None


def test_a_red_suite_turns_the_subject_red_and_lists_the_failures():
    tests = {"passed": 990, "failed": 2, "errors": 0, "seconds": 300.0, "ran_at": "2026-09-08T10:55:00+00:00",
             "failed_tests": ["tests/test_x.py::test_a", "tests/test_y.py::test_b"]}
    d = digest.build_digest(inputs(tests=tests), today=TODAY)
    assert digest.subject_for(d).startswith("TESTS RED, Desk morning")
    html = digest.render_html(d)
    assert "990 tests passed, 2 failed" in html and "tests/test_x.py::test_a" in html
    green = digest.build_digest(inputs(tests={**tests, "failed": 0, "failed_tests": []}), today=TODAY)
    assert not digest.subject_for(green).startswith("TESTS RED")


def test_the_language_guard_passes_over_the_new_sections():
    for ed in ("morning", "close"):
        d = digest.build_digest(inputs(book=marked_book()), today=TODAY, edition=ed)
        bad = check(d, ed)
        assert not bad, "\n".join(bad)


def test_the_committed_book_json_renders():
    blob = json.loads((ROOT / "dashboard" / "book.json").read_text())
    d = digest.build_digest(inputs(book=blob), today=TODAY)
    assert d["book"]["status"] in ("MARKED", "NOT MARKED")
    assert not check(d, "book-digest")
