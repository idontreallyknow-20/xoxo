"""Claude's paper book: derived from book.md and the closes, never kept by hand."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from an import book, journal, quotes
from tests.test_language_guard import check as language_check

ROOT = Path(__file__).resolve().parent.parent
QFIX = ROOT / "tests" / "fixtures" / "quotes"


def entry(date, ticker, action="Buy", price=100.0, target="$10,000 (10%)", wrong="a close under $80.", horizon=None, stop=None):
    return journal.JournalEntry(date=date, ticker=ticker, title=f"Recommendation: {action}", price_at_call=price,
                                thesis="test", wrong_if=wrong, target_size=target, conviction=3, bucket="compounder",
                                horizon_days=horizon, stop=stop)


@pytest.fixture(scope="module")
def closes():
    return quotes.load(QFIX).closes


def test_fixture_quotes_load_with_benchmarks(closes):
    assert {"SPY", "QQQ", "VFV.TO", "AAPL"} <= set(closes.columns)
    assert closes.index[0] == pd.Timestamp("2017-06-01") and len(closes) == 85


def test_buy_fills_at_the_next_close_and_is_marked_at_the_last(closes):
    b = book.build_book([entry("2017-06-05", "AAPL")], closes)
    [p] = b.positions
    assert p.status == "open" and p.fill_date == "2017-06-06"
    assert p.entry == pytest.approx(float(closes.loc["2017-06-06", "AAPL"]))
    assert p.shares == int(10_000 // p.entry) and p.last_date == "2017-09-29"
    assert b.equity == pytest.approx(b.cash + p.shares * p.last)
    assert b.first_fill == "2017-06-06" and b.benchmarks["SPY"] is not None
    assert p.spy_same_window is not None and p.excess_vs_spy == pytest.approx(p.ret - p.spy_same_window)


def test_a_call_after_the_last_close_is_pending_and_no_prices_means_everything_pending(closes):
    b = book.build_book([entry("2017-09-29", "AAPL")], closes)
    assert b.positions[0].status == "pending" and b.equity == 100_000.0
    none = book.build_book([entry("2017-06-05", "AAPL")], None)
    assert none.positions[0].status == "pending" and none.as_of is None and none.limitations


def test_the_position_cap_and_the_monthly_pace_and_the_cash_floor(closes):
    big = book.build_book([entry("2017-06-05", "AAPL", target="$30,000 (30%)")], closes)
    [p] = big.positions
    assert p.cost <= 100_000 * 0.12 + 1 and any("capped at 12%" in f for f in big.flags)
    pace = book.build_book([entry("2017-06-05", t, target="$11,000") for t in ("AAPL", "MSFT", "JPM")], closes)
    assert [q.status for q in pace.positions] == ["open", "open", "refused"]
    assert "25%" in pace.positions[2].refused_because
    floor = book.build_book([entry(d, t, target="$11,000") for d, t in
                             (("2017-06-05", "AAPL"), ("2017-06-05", "MSFT"), ("2017-07-05", "JPM"), ("2017-07-05", "XOM"),
                              ("2017-08-01", "KO"), ("2017-08-01", "PG"), ("2017-09-01", "BA"), ("2017-09-01", "CAT"))], closes)
    refused = [q for q in floor.positions if q.status == "refused"]
    assert refused and all("25%" in q.refused_because or "20%" in q.refused_because for q in refused)


def test_sell_exits_at_the_next_close_and_a_sell_without_a_position_is_refused(closes):
    b = book.build_book([entry("2017-06-05", "MSFT"), entry("2017-08-15", "MSFT", action="Sell")], closes)
    p = b.positions[0]
    assert p.status == "closed" and p.exit_date == "2017-08-16" and p.exit_reason == "sold"
    assert p.exit == pytest.approx(float(closes.loc["2017-08-16", "MSFT"]))
    assert b.cash == pytest.approx(100_000 - p.shares * p.entry + p.shares * p.exit)
    bad = book.build_book([entry("2017-08-15", "JPM", action="Sell")], closes)
    assert bad.positions[0].status == "refused" and "no open position" in bad.positions[0].refused_because


def test_a_swing_call_is_sized_by_risk_and_exits_on_its_closing_stop(closes):
    b = book.build_book([entry("2017-06-06", "NFLX", action="Buy (swing)", target="$1,000 at risk", horizon=10, stop=155.0)],
                        closes)
    [p] = b.positions
    assert p.kind == "swing" and p.status == "closed" and p.exit_reason == "stop"
    risk = p.entry - 155.0
    assert p.shares == min(int(1000 // risk), int(5000 // p.entry))
    # the first close strictly below the stop, then the exit at the following close
    s = closes["NFLX"]
    hit = s[(s.index > pd.Timestamp(p.fill_date)) & (s < 155.0)].index[0]
    assert pd.Timestamp(p.exit_date) == s.index[s.index.get_loc(hit) + 1]


def test_a_swing_horizon_exit_and_the_five_open_cap(closes):
    names = ("AAPL", "MSFT", "JPM", "XOM", "KO", "PG")
    calls = [entry("2017-06-06", t, action="Buy (swing)", target="$1,000 at risk", horizon=10, stop=1.0) for t in names]
    b = book.build_book(calls, closes, sectors={t: t for t in names})
    statuses = [p.status for p in b.positions]
    assert statuses.count("refused") == 1 and "5 swing positions" in b.positions[-1].refused_because
    done = [p for p in b.positions if p.status == "closed"]
    assert done and all(p.exit_reason == "horizon" for p in done)
    assert all(pd.Timestamp(p.exit_date) == closes.index[closes.index.get_loc(pd.Timestamp(p.fill_date)) + 9] for p in done)


def test_a_falsifier_crossing_is_flagged_not_sold(closes):
    lo = float(closes["AAPL"].min())
    b = book.build_book([entry("2017-06-05", "AAPL", wrong=f"a close under ${lo + 1:.2f}.")], closes)
    [p] = b.positions
    assert p.status == "open" and p.falsifier_breached is True and p.distance_to_trigger is not None


def test_book_json_is_complete_and_clean(closes):
    b = book.build_book([entry("2017-06-05", "AAPL"), entry("2017-06-05", "MSFT")], closes,
                        sectors={"AAPL": "Technology", "MSFT": "Technology"})
    j = b.to_json()
    for k in ("equity", "cash", "ret_since_start", "benchmarks_since_start", "excess_since_start", "open", "closed",
              "curve", "max_drawdown", "rule_checks", "limitations", "disclaimer"):
        assert k in j, k
    assert j["rule_checks"] and any("sector" in c["rule"] for c in j["rule_checks"])
    assert not language_check(j, "book")


def test_book_refuses_to_exist_without_limitations():
    with pytest.raises(ValueError):
        book.Book(None, 1.0, 1.0, [], [], {}, [], [])


def test_the_repo_book_md_parses_and_every_call_has_a_falsifier_and_a_size():
    entries = [e for e in journal.load(ROOT / "book.md") if not e.is_system]
    assert entries, "book.md has no calls"
    for e in entries:
        assert e.wrong_if and e.wrong_if.lower() not in ("n/a", "none"), e.ticker
        assert e.thesis, e.ticker
        if book.classify(e.action) == "buy":
            assert (e.target_usd or 0) > 0 or e.is_swing, e.ticker
        if e.is_swing:
            assert e.stop is not None and e.horizon_days, e.ticker


def test_book_md_is_append_only():
    """An entry that was ever committed must still be there, unchanged: the record is the point."""
    r = subprocess.run(["git", "show", "HEAD:book.md"], capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        pytest.skip("book.md not committed yet")
    old = [e for e in journal.parse(r.stdout) if not e.is_system]
    now = {(e.date, e.ticker, e.title): e for e in journal.load(ROOT / "book.md") if not e.is_system}
    for e in old:
        assert (e.date, e.ticker, e.title) in now, f"{e.date} {e.ticker} was removed from book.md"
        assert now[(e.date, e.ticker, e.title)] == e, f"{e.date} {e.ticker} was edited in book.md"


def test_cli_marks_from_a_quotes_directory_and_says_not_marked_without_one(tmp_path):
    cli = ROOT / "scripts" / "build_book.py"
    j = tmp_path / "b.md"
    j.write_text("# t\n\n## 2017-06-05 AAPL  Recommendation: Buy\nPrice at call: 153.93\nThesis: t.\nWrong if: a close under $140.\n"
                 "Target size: $10,000 (10%)\nConviction: 3\nBucket: compounder\n", encoding="utf-8")
    out = tmp_path / "book.json"
    r = subprocess.run([sys.executable, str(cli), "--journal", str(j), "--quotes", str(QFIX), "--out", str(out),
                        "--as-of", "2017-09-29"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    blob = json.loads(out.read_text())
    assert blob["status"] == "MARKED" and blob["is_real"] is False and blob["n_open"] == 1
    r = subprocess.run([sys.executable, str(cli), "--journal", str(j), "--quotes", str(tmp_path / "nothing"), "--out", str(out)],
                       capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "DESK_QUOTES": ""})
    assert r.returncode == 0, r.stderr[-500:]
    blob = json.loads(out.read_text())
    assert blob["status"] == "NOT MARKED" and blob["why_not_marked"] and blob["n_pending"] == 1
