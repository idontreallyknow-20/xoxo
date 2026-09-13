"""Quotes through git: the files, the seam into PriceClient, the universe, the age."""
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from an import prices, quotes

ROOT = Path(__file__).resolve().parent.parent
QFIX = ROOT / "tests" / "fixtures" / "quotes"


def test_fixture_loads_and_describes_itself():
    p = quotes.load(QFIX)
    assert p is not None and p.first == dt.date(2017, 6, 1) and p.last == dt.date(2017, 9, 29)
    assert "15 names" in p.describe() and "fixture" in p.describe()
    assert quotes.load(QFIX / "missing") is None


def test_write_then_load_round_trips(tmp_path):
    p = quotes.load(QFIX)
    m = quotes.write(p.closes, tmp_path, source="test", missing=["ZZZ"], requested=16, pulled_at="2020-01-01T00:00:00+00:00")
    assert m["n_tickers"] == 15 and m["missing"] == ["ZZZ"] and m["last"] == "2017-09-29"
    back = quotes.load(tmp_path)
    pd.testing.assert_frame_equal(back.closes, p.closes)
    latest = pd.read_csv(tmp_path / "latest.csv")
    assert list(latest.columns) == ["ticker", "close", "date"] and len(latest) == 15


def test_desk_quotes_env_turns_the_default_client_into_a_file_reader(monkeypatch, tmp_path):
    from an.store import Cache
    monkeypatch.setenv(prices.ENV_QUOTES, str(QFIX))
    assert isinstance(prices.default_downloader(), prices.PanelDownloader)
    client = prices.PriceClient(cache=Cache(tmp_path))
    closes = client.daily_closes(["AAPL", "SPY"], "2017-07-01", "2017-07-31")
    assert list(closes.columns) == ["AAPL", "SPY"] and len(closes) == 20
    assert all(v == "live" for v in client.last_source.values())
    monkeypatch.delenv(prices.ENV_QUOTES)
    assert isinstance(prices.default_downloader(), prices.YFinanceDownloader)


def test_age_in_sessions():
    p = quotes.load(QFIX)
    assert quotes.age_sessions(p, dt.date(2017, 9, 29)) == 0
    assert quotes.age_sessions(p, dt.date(2017, 10, 2)) == 1     # Monday after a Friday close
    assert quotes.age_sessions(p, dt.date(2017, 10, 6)) == 5


def test_ticker_universe_carries_the_benchmarks_and_both_journals():
    names = quotes.ticker_universe()
    assert set(quotes.BENCHMARKS) <= set(names)
    assert "BKNG" in names and len(names) > 100
    assert names == sorted(names) and all(n == n.upper() for n in names)


def test_fetch_branch_returns_none_when_the_branch_is_absent(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert quotes.fetch_branch(tmp_path, branch="no-such-branch", dest=tmp_path / "q") is None


def test_fetch_cli_dry_run_lists_the_universe():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_quotes.py"), "--dry-run", "--tickers", "AAPL,spy"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "AAPL SPY" in r.stdout and "closes.csv" in r.stdout


def test_workflow_file_targets_the_quotes_branch():
    text = (ROOT / ".github" / "workflows" / "quotes.yml").read_text()
    assert "fetch_quotes.py" in text and "git push origin quotes" in text and "contents: write" in text
    assert "1-5" in text, "weekdays only"


def test_a_partial_last_session_is_set_aside_and_named():
    p = quotes.load(QFIX)
    thin = p.closes.copy()
    extra = thin.iloc[[-1]].copy()
    extra.index = [extra.index[-1] + pd.Timedelta(days=1)]
    extra.iloc[0, 2:] = float("nan")          # two of fifteen names have a close
    trimmed, dropped = quotes.trim_partial_tail(pd.concat([thin, extra]))
    assert dropped == [extra.index[0].date().isoformat()] and len(trimmed) == len(thin)
    full, none = quotes.trim_partial_tail(thin)
    assert none == [] and len(full) == len(thin)


def test_a_row_dated_the_day_of_a_mid_session_pull_is_set_aside(tmp_path):
    p = quotes.load(QFIX)
    last = p.closes.index[-1].date().isoformat()
    quotes.write(p.closes, tmp_path, source="test", pulled_at=f"{last}T14:33:00+00:00")
    during = quotes.load(tmp_path)
    assert during.last == p.closes.index[-2].date()
    assert during.manifest["intraday_session_set_aside"] == last and "pulled during the session" in during.describe()
    quotes.write(p.closes, tmp_path, source="test", pulled_at=f"{last}T22:30:00+00:00")
    after = quotes.load(tmp_path)
    assert after.last == p.closes.index[-1].date() and "intraday_session_set_aside" not in after.manifest


def test_fetch_pulls_in_batches_and_retries_a_throttled_one(monkeypatch):
    import fetch_quotes
    from an.store import Cache
    p = quotes.load(QFIX)
    names = list(p.closes.columns)
    calls = []

    class Throttled:
        """Empty for the first call of the second batch, whole otherwise."""
        def __init__(self):
            self.n = 0
        def download(self, tickers, start, end):
            self.n += 1
            calls.append(list(tickers))
            if list(tickers)[0] == names[5] and self.n == 2:
                return pd.DataFrame()
            return prices.PanelDownloader(p.closes).download(tickers, start, end)

    naps = []
    client = prices.PriceClient(downloader=Throttled(), cache=Cache(pytest.importorskip("tempfile").mkdtemp()))
    closes, missing = fetch_quotes.pull_in_batches(client, names, p.first, p.last, batch=5, pause=1.0, sleep=naps.append)
    assert [len(c) for c in calls] == [5, 5, 5, 5] and missing == []
    assert sorted(closes.columns) == sorted(names) and len(closes) == len(p.closes)
    assert naps == [1.0, 10.0, 1.0]
