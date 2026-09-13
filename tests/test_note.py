"""an.note: the short morning email, as data and as words, on small hand-written inputs."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from an import journal, note, quotes

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_language_guard import check  # noqa: E402

TODAY = dt.date(2026, 9, 14)


def entry(ticker, action, wrong_if="or a close under $100.", target="$8,000 (8%)", conviction=4):
    return journal.JournalEntry(date="2026-09-04", ticker=ticker, title=f"Recommendation: {action}", price_at_call=120.0,
                                thesis="A thesis.", wrong_if=wrong_if, target_size=target, conviction=conviction,
                                bucket="compounder")


def price(last, chg_1d=0.01, breached=False):
    return {"last_close": last, "last_date": "2026-09-11", "chg_1d": chg_1d, "chg_5d": 0.02, "since_call": -0.05,
            "breached": breached}


def inputs(*, alerts=(), breached_book=False, marked=True):
    watch = {"status": "SCANNED", "as_of": "2026-09-11",
             "names": [{"ticker": "AAA", "price": price(110.0, 0.031)}, {"ticker": "BBB", "price": price(320.0, -0.045)},
                       {"ticker": "CCC", "price": price(90.0, -0.01, breached=True)}, {"ticker": "DDD", "price": price(150.0)}],
             "alerts": list(alerts),
             "benchmarks": {"SPY": {"last_close": 762.4, "last_date": "2026-09-11", "chg_1d": -0.0046, "chg_5d": 0.0008},
                            "QQQ": {"last_close": 716.3, "last_date": "2026-09-11", "chg_1d": -0.0029, "chg_5d": 0.0123}}}
    book = {"status": "MARKED" if marked else "NOT MARKED", "equity": 99637.0, "ret_since_start": -0.0036, "cash": 76871.0,
            "first_fill": "2026-09-08", "as_of": "2026-09-11", "quotes_age_sessions": 1, "n_pending": 0 if marked else 3,
            "benchmarks_since_start": {"SPY": -0.0046},
            "open": [{"ticker": "AAA", "shares": 44, "entry": 100.0, "last": 110.0, "ret": 0.10, "trigger": 80.0, "kind": "long",
                      "falsifier_breached": breached_book}],
            "long": [{"ticker": "AAA", "call_date": "2026-09-07"}], "swing": [], "closed": []}
    return {"watch": watch, "book": book, "tests": {"passed": 10, "failed": 0, "errors": 0}}


ENTRIES = [entry("AAA", "Buy now"), entry("BBB", "Buy on pullback to $250 to $290"), entry("CCC", "Buy now"),
           entry("DDD", "Buy in October"), entry("EEE", "Buy after September 10 earnings"), entry("FFF", "Watch or Pass")]


def test_the_note_sorts_calls_into_now_waiting_and_out():
    n = note.build_note(inputs(), today=TODAY, entries=ENTRIES)
    assert [x["ticker"] for x in n["buy_now"]] == ["AAA"]
    assert n["buy_now"][0]["held"] and n["buy_now"][0]["target_usd"] == 8000.0
    assert [x["ticker"] for x in n["waiting"]] == ["BBB", "DDD", "EEE"]
    assert "above its $250 to $290 buy zone" in n["waiting"][0]["why"]
    assert "October" in n["waiting"][1]["why"] and "report has printed" in n["waiting"][2]["why"]
    assert [x["ticker"] for x in n["out"]] == ["CCC"], "a breached falsifier takes a name off the list"
    assert "FFF" not in json.dumps(n), "a pass is not a buy"


def test_a_pullback_zone_counts_as_a_buy_inside_it_and_october_arrives():
    n = note.build_note(inputs(), today=dt.date(2026, 10, 1), entries=[entry("BBB", "Buy on pullback to $300 to $340"),
                                                                        entry("DDD", "Buy in October")])
    assert [x["ticker"] for x in n["buy_now"]] == ["BBB", "DDD"]
    assert "inside the $300 to $340 buy zone" in n["buy_now"][0]["why"]


def test_the_market_paragraph_prefers_the_quotes_file_and_carries_the_year():
    panel = quotes.load(ROOT / "tests" / "fixtures" / "quotes")
    bench = note.benchmarks_from_panel(panel)
    assert bench and "SPY" in bench and bench["SPY"]["last_date"] == "2017-09-29"
    assert bench["SPY"]["ytd"] is None, "the fixture starts mid-year, so no year-to-date is invented"
    n = note.build_note(inputs(), today=TODAY, entries=ENTRIES, bench_from_quotes=bench)
    assert n["market"]["close_date"] == "2017-09-29"
    assert [b["ticker"] for b in n["market"]["benchmarks"]][:2] == ["SPY", "QQQ"]
    idx = pd.date_range("2025-12-30", periods=3, freq="B")
    p2 = quotes.QuotePanel(pd.DataFrame({"SPY": [100.0, 110.0, 121.0]}, index=idx), {}, ROOT)
    b2 = note.benchmarks_from_panel(p2)
    # the year starts from the 31 December close (110), not the first print in the file (100)
    assert abs(b2["SPY"]["ytd"] - 0.10) < 1e-9 and abs(b2["SPY"]["chg_1d"] - 0.10) < 1e-9


def test_the_words_say_what_the_data_says():
    n = note.build_note(inputs(), today=TODAY, entries=ENTRIES)
    text = note.render_text(n)
    assert text.startswith("Morning note, Monday 14 September 2026")
    assert "The S&P 500 (SPY) closed at $762.40 on Friday, down 0.5% on the day" in text
    assert "The Nasdaq 100 (QQQ) was down 0.3% on the day" in text
    assert "$99,637, down 0.4% since I started on 8 September. SPY was down 0.5% over the same stretch" in text
    assert "AAA: 44 shares at $100.00, now $110.00, +10.0%. Wrong if it closes under $80." in text
    assert "Nothing is under the level I said would prove me wrong." in text
    assert "1. AAA at $110.00, the note calls it a buy now. $8,000. Already in my book." in text
    assert "Waiting: BBB (above its $250 to $290 buy zone, now $320.00)" in text
    assert "Off the list until I re-read the note: CCC (closed under its own wrong-if level of $100)" in text
    assert "AAA +3.1%, BBB -4.5%" in text, "the day's moves among the watched names"
    assert "not personalised financial advice" in text
    assert "What crossed a rule" not in text and "TESTS" not in note.subject_for(n)
    html = note.render_html(n)
    assert "<script" not in html and "<table" not in html and "S&amp;P 500" in html
    assert note.subject_for(n) == "Morning note, Mon 14 Sep: book -0.4%, 1 name in a buy zone"


def test_a_breach_and_an_alert_change_the_subject_and_add_a_section():
    alert = {"ticker": "BBB", "severity": 3, "text": "BBB is -16.6% over five sessions.", "rule": "README event driven"}
    n = note.build_note(inputs(alerts=[alert]), today=TODAY, entries=ENTRIES)
    assert note.subject_for(n) == "Morning note, Mon 14 Sep: BBB crossed a rule"
    assert "WHAT CROSSED A RULE" in note.render_text(n) and "BBB is -16.6%" in note.render_text(n)
    n = note.build_note(inputs(breached_book=True), today=TODAY, entries=ENTRIES)
    assert note.subject_for(n) == "Morning note, Mon 14 Sep: AAA under its wrong-if level"
    assert "AAA closed under the level I said would prove me wrong. I decide today" in note.render_text(n)


def test_no_book_no_scan_reads_honestly():
    n = note.build_note({"watch": None, "book": None}, today=TODAY, entries=ENTRIES)
    text = note.render_text(n)
    assert "No scan ran, so I have no closes" in text and "Not marked this morning (no book file)" in text
    assert "No closes were available this morning." in text
    assert note.subject_for(n) == "Morning note, Mon 14 Sep"
    n = note.build_note(inputs(marked=False), today=TODAY, entries=ENTRIES)
    assert "Not marked this morning (not marked): 3 calls waiting" in note.render_text(n)


def test_failed_tests_turn_the_subject_red():
    i = inputs()
    i["tests"] = {"passed": 9, "failed": 1, "errors": 0}
    n = note.build_note(i, today=TODAY, entries=ENTRIES)
    assert note.subject_for(n).startswith("TESTS RED, ")
    assert "1 of the desk's own tests failed" in note.render_text(n)


def test_the_language_guard_passes_over_the_note_and_its_words():
    for i in (inputs(), inputs(breached_book=True), {"watch": None, "book": None}):
        n = note.build_note(i, today=TODAY, entries=ENTRIES)
        bad = check(n, "note") + check({"text": note.render_text(n), "html": note.render_html(n)}, "note")
        assert not bad, "\n".join(bad)


def test_the_note_never_uses_an_em_dash():
    src = (ROOT / "scripts" / "an" / "note.py").read_text(encoding="utf-8")
    assert "—" not in src


def test_the_cli_writes_the_note_and_its_text_beside_it(tmp_path):
    out = tmp_path / "latest.html"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_email.py"), "--edition", "note", "--html-out", str(out),
                        "--date", "2026-09-14", "--inputs", str(ROOT / "tests" / "fixtures" / "desk_not_run")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists() and Path(str(out) + ".subject.txt").read_text().startswith("Morning note, Mon 14 Sep")
    assert Path(str(out) + ".txt").read_text().startswith("Morning note, Monday 14 September 2026")
