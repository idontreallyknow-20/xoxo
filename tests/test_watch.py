"""an.watch: the daily scan, on planted prices, a committed filing index and a committed feed.

What is tested is the rules, not the market: a close under the journal's level is
a trigger alert, a fifteen percent week is the README's event rule, a new 8-K
with item 2.02 is an earnings release, the state file makes yesterday's news
disappear from today's list, and every hole is None.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from an import edgar, journal, prices, watch
from an.http import FixtureTransport
from an.store import Cache, FetchError

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
AS_OF = dt.date(2026, 9, 4)

JOURNAL = """# Decision journal

## 2026-09-04 SYSTEM  Inception
Price at call: n/a
Thesis: x
Wrong if: x
Target size: $0
Conviction: n/a
Bucket: n/a

## 2026-08-01 KLAC  Recommendation: Buy in October
Price at call: 172.94
Thesis: x
Wrong if: December quarter guide under $3.6 billion, or a close under $130.
Target size: $5,000 (5%)
Conviction: 4
Bucket: cyclical turn

## 2026-08-01 ADSK  Recommendation: Buy now
Price at call: 237.52
Thesis: x
Wrong if: Billings growth under 5% twice, or a close under $185.
Target size: $6,000 (6%)
Conviction: 4
Bucket: compounder

## 2026-08-20 ADSK  Recommendation: Watch
Price at call: 240.00
Thesis: later entry wins.
Wrong if: A close under $190.
Target size: $0
Conviction: 3
Bucket: compounder

## 2026-08-01 AMAT NVR  Recommendation: Watch or Pass
Price at call: 435.91 / 6373.95
Thesis: x
Wrong if: n/a
Target size: $0
Conviction: 2
Bucket: n/a
"""


def entries():
    return journal.parse(JOURNAL)


def panel(**cols):
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=8)
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


# -- names ---------------------------------------------------------------------

def test_the_latest_call_per_ticker_wins_and_held_names_are_added():
    names = watch.names_to_watch(entries(), held=["ADSK", "MSFT"], held_triggers={"MSFT": 400.0})
    by = {n.ticker: n for n in names}
    assert set(by) == {"KLAC", "ADSK", "AMAT", "NVR", "MSFT"}
    assert by["ADSK"].trigger == 190.0 and by["ADSK"].held and by["ADSK"].kind == "watch"
    assert by["KLAC"].trigger == 130.0 and not by["KLAC"].held
    assert by["AMAT"].trigger is None and by["AMAT"].kind in ("watch", "pass")
    assert by["MSFT"].kind == "held" and by["MSFT"].trigger == 400.0 and by["MSFT"].price_at_call is None
    assert [n.ticker for n in names][:2] == ["ADSK", "MSFT"], "held names sort first"


# -- prices --------------------------------------------------------------------

def test_price_read_is_mechanical_and_every_hole_is_none():
    p = panel(KLAC=[150, 150, 150, 150, 150, 148, 140, 128])
    r = watch.price_read(p, "KLAC", as_of=AS_OF, price_at_call=172.94, trigger=130.0)
    assert r.last_close == 128 and r.last_date == "2026-09-04"
    assert r.chg_1d == pytest.approx(128 / 140 - 1)
    assert r.chg_5d == pytest.approx(128 / 150 - 1)
    assert r.since_call == pytest.approx(128 / 172.94 - 1)
    assert r.to_trigger == pytest.approx(128 / 130 - 1) and r.breached is True
    assert r.sessions == 8 and r.stale_days == 0
    empty = watch.price_read(p, "NOPE", as_of=AS_OF)
    assert empty.last_close is None and empty.breached is None and empty.sessions == 0
    none = watch.price_read(None, "KLAC", as_of=AS_OF)
    assert none.to_json()["chg_1d"] is None


def test_a_close_after_the_as_of_date_is_not_read():
    p = panel(KLAC=[100] * 8)
    p.loc[pd.Timestamp("2026-09-08")] = 50.0
    r = watch.price_read(p, "KLAC", as_of=AS_OF, trigger=90.0)
    assert r.last_close == 100 and r.breached is False


def test_a_short_series_gives_no_weekly_change_rather_than_a_wrong_one():
    idx = pd.bdate_range(end=pd.Timestamp(AS_OF), periods=3)
    p = pd.DataFrame({"X": [100, 101, 102]}, index=idx)
    r = watch.price_read(p, "X", as_of=AS_OF)
    assert r.chg_1d is not None and r.chg_5d is None


# -- rules ---------------------------------------------------------------------

def name(**kw):
    base = dict(ticker="KLAC", action="Buy in October", kind="buy_later", date="2026-08-01", price_at_call=172.94,
                wrong_if="a close under $130", trigger=130.0, held=False)
    base.update(kw)
    return watch.WatchName(**base)


def test_trigger_breach_is_a_rule_alert_and_near_is_a_line():
    p = panel(KLAC=[150, 150, 150, 150, 150, 150, 150, 125])
    a = watch.alerts_for(name(), watch.price_read(p, "KLAC", as_of=AS_OF, trigger=130.0), [])
    kinds = {x.kind: x for x in a}
    assert kinds["trigger"].severity == 3 and "$130.00" in kinds["trigger"].text and "so far" in kinds["trigger"].text
    assert "drop_week" in kinds and "move_day" in kinds
    p2 = panel(KLAC=[150] * 7 + [134])
    a2 = watch.alerts_for(name(), watch.price_read(p2, "KLAC", as_of=AS_OF, trigger=130.0), [])
    assert [x.kind for x in a2 if x.kind in ("trigger", "near_trigger")] == ["near_trigger"]
    assert "3.1% above" in [x for x in a2 if x.kind == "near_trigger"][0].text


def test_no_trigger_means_no_trigger_alert_not_a_zero_trigger():
    p = panel(AMAT=[100] * 7 + [1])
    a = watch.alerts_for(name(ticker="AMAT", trigger=None, price_at_call=None),
                         watch.price_read(p, "AMAT", as_of=AS_OF), [])
    assert {x.kind for x in a} == {"drop_week", "move_day"}


def test_a_quiet_week_gives_no_alerts():
    p = panel(KLAC=[150, 151, 150, 152, 151, 150, 151, 152])
    assert watch.alerts_for(name(), watch.price_read(p, "KLAC", as_of=AS_OF, trigger=130.0), []) == []


@pytest.fixture
def client(tmp_path):
    t = FixtureTransport()
    t.add_file("https://www.sec.gov/files/company_tickers.json", FIXTURES / "company_tickers.json")
    t.add_file("https://data.sec.gov/submissions/CIK0000320193.json", FIXTURES / "submissions_aapl.json")
    return edgar.EdgarClient(transport=t, cache=Cache(tmp_path / "edgar", default_ttl=60), user_agent="t t@example.com")


def test_filings_since_reads_items_and_stops_at_the_date(client):
    got, note = watch.filings_since(client, "AAPL", dt.date(2026, 7, 1))
    assert note is None
    forms = [(f.form, f.filing_date) for f in got]
    assert ("8-K", "2026-07-31") in forms and ("10-Q", "2026-08-01") in forms
    assert all(f.filing_date > "2026-07-01" for f in got)
    ek = [f for f in got if f.is_earnings][0]
    assert ek.items == ["2.02", "9.01"] and "earnings release" in ek.item_text
    assert ek.url and ek.url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    later, _ = watch.filings_since(client, "AAPL", dt.date(2026, 8, 15))
    assert later == []


def test_a_name_without_a_cik_says_so_instead_of_reporting_nothing(client):
    got, note = watch.filings_since(client, "DPM.TO", dt.date(2026, 1, 1))
    assert got == [] and note and "no CIK" in note


def test_an_earnings_8k_is_a_rule_alert_and_a_form_4_pile_is_one_line(client):
    got, _ = watch.filings_since(client, "AAPL", dt.date(2026, 7, 1))
    fours = [watch.FilingNote("AAPL", "4", "2026-08-0%d" % i, f"acc-{i}", [], None, None, False) for i in (3, 2, 1)]
    a = watch.alerts_for(name(ticker="AAPL", trigger=None), watch.price_read(None, "AAPL", as_of=AS_OF), got + fours)
    kinds = [x.kind for x in a]
    assert kinds[0] == "earnings_8k" and "guidance_diff.py AAPL" in a[0].text
    assert kinds.count("filing") >= 2
    f4 = [x for x in a if x.rule == "new Form 4"]
    assert len(f4) == 1 and f4[0].value == 3.0 and "3 Form 4" in f4[0].text


# -- headlines -----------------------------------------------------------------

def test_rss_is_parsed_and_the_junk_items_are_handled():
    items = watch.parse_rss((FIXTURES / "rss_aapl.xml").read_bytes(), "AAPL")
    assert [h.title for h in items] == [
        "Example Corp reports fiscal third quarter results",
        "Analyst day set for September & a new product line is expected",
        "Whitespace in the title",
    ]
    assert items[0].published == "2026-07-31T20:31:00+00:00"
    assert items[1].published == "2026-07-30T13:05:00+00:00"
    assert items[2].published is None and items[2].guid == "https://example.com/news/ws"
    with pytest.raises(FetchError):
        watch.parse_rss(b"<html>not a feed", "AAPL")


def test_the_reader_caches_and_records_that_it_was_not_live(tmp_path):
    t = FixtureTransport().add_file(watch.RSS_TEMPLATE.format(ticker="AAPL"), FIXTURES / "rss_aapl.xml")
    r = watch.RssReader(t, cache=Cache(tmp_path / "rss", default_ttl=600))
    assert len(r.headlines("AAPL")) == 3
    assert len(r.headlines("AAPL")) == 3
    assert len(t.calls) == 1, "second read served from cache"
    blob = json.loads(next((tmp_path / "rss").glob("*.json")).read_text())
    assert blob["meta"]["live"] is False and blob["meta"]["transport"] == "FixtureTransport"
    with pytest.raises(FetchError):
        r.headlines("NOFEED")


# -- state ---------------------------------------------------------------------

def test_state_hides_what_was_already_reported(tmp_path):
    items = watch.parse_rss((FIXTURES / "rss_aapl.xml").read_bytes(), "AAPL")
    notes = [watch.FilingNote("AAPL", "8-K", "2026-07-31", "0001-26-1", ["2.02"], None, None, True)]
    s = watch.WatchState.load(tmp_path / "state.json")
    assert s.last_scan is None and len(s.new_headlines(items)) == 3 and len(s.new_filings(notes)) == 1
    s.remember(notes, items, when="2026-09-04T18:00:00")
    s.save(tmp_path / "state.json")
    s2 = watch.WatchState.load(tmp_path / "state.json")
    assert s2.n_scans == 1 and s2.last_scan.startswith("2026-09-04")
    assert s2.new_headlines(items) == [] and s2.new_filings(notes) == []
    extra = watch.Headline("AAPL", "New", None, None, "guid-new")
    assert s2.new_headlines(items + [extra]) == [extra]


# -- the report ----------------------------------------------------------------

def test_the_report_carries_provenance_rules_and_a_sorted_alert_list(client, tmp_path):
    p = panel(KLAC=[150] * 7 + [128], ADSK=[240] * 8, SPY=[500] * 8, QQQ=[400] * 8)
    names = watch.names_to_watch(entries())
    reads = {n.ticker: watch.price_read(p, n.ticker, as_of=AS_OF, price_at_call=n.price_at_call, trigger=n.trigger)
             for n in names}
    got, _ = watch.filings_since(client, "AAPL", dt.date(2026, 7, 1))
    blob = watch.build_report(names, reads, {"KLAC": got}, {}, ["a note"], as_of=AS_OF,
                              sources={"prices": {"live": False, "detail": "test"}}, built_at="X")
    assert blob["status"] == "SCANNED" and blob["is_real"]
    assert blob["alerts"][0]["severity"] == 3
    assert blob["n_rule_alerts"] >= 2
    assert "a note" in blob["limitations"] and blob["rules"]
    klac = [r for r in blob["names"] if r["ticker"] == "KLAC"][0]
    assert klac["price"]["breached"] is True and klac["max_severity"] == 3
    amat = [r for r in blob["names"] if r["ticker"] == "AMAT"][0]
    assert amat["price"]["last_close"] is None and amat["alerts"] == []
    assert json.dumps(blob)  # serialisable, no NaN
    assert "NaN" not in json.dumps(blob)


def test_the_not_run_report_names_the_command():
    blob = watch.not_run_report(watch.names_to_watch(entries()), built_at="X")
    assert blob["status"] == "NOT RUN" and not blob["is_real"] and blob["n_watched"] == 4
    assert any("scan.py --live" in s for s in blob["why_not_run"])


def test_the_committed_file_is_honest():
    blob = json.loads((ROOT / "dashboard" / "watch.json").read_text())
    assert blob["status"] == "NOT RUN", "watch.json claims a scan ran; this checkout cannot have run one"
    assert blob["names"] == [] and blob["alerts"] == []
    assert all(s["live"] is False for s in blob["sources"].values())


def test_the_language_guard_passes_over_every_alert_text():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_language_guard import check

    p = panel(KLAC=[150] * 7 + [120])
    a = watch.alerts_for(name(), watch.price_read(p, "KLAC", as_of=AS_OF, trigger=130.0), [
        watch.FilingNote("KLAC", "8-K", "2026-09-01", "a", ["2.02", "9.01"], None, None, True),
        watch.FilingNote("KLAC", "10-Q", "2026-09-02", "b", [], None, None, False),
        watch.FilingNote("KLAC", "SC 13G", "2026-09-02", "c", [], None, None, False),
        watch.FilingNote("KLAC", "8-K", "2026-09-03", "d", ["5.02"], None, None, False),
        watch.FilingNote("KLAC", "4", "2026-09-03", "e", [], None, None, False),
    ])
    assert len(a) >= 6
    bad = check({"alerts": [x.to_json() for x in a], "rules": watch.rules_text()}, "watch")
    assert not bad, "\n".join(bad)


def test_the_cli_fixture_mode_never_writes_under_dashboard(tmp_path):
    out = tmp_path / "wf.json"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "scan.py"), "--fixture", "--as-of", "2026-09-04",
                        "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    blob = json.loads(out.read_text())
    assert blob["status"] == "SYNTHETIC" and blob["limitations"][0].startswith("SYNTHETIC")
    assert blob["names"][0]["ticker"] == "AAPL" and len(blob["names"][0]["headlines"]) == 3
    assert "FIXTURE" in r.stdout


def test_the_cli_dry_run_prints_the_plan_and_sends_nothing():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "scan.py"), "--dry-run"],
                       capture_output=True, text=True, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "never made these requests" in r.stdout
    assert "feeds.finance.yahoo.com" in r.stdout and "data.sec.gov" in r.stdout
    assert "token=" not in r.stdout
