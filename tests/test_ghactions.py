"""an.ghactions and scripts/refresh_quotes.py: the dispatch, the wait, the session arithmetic. No network."""
import datetime as dt
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

from an import ghactions

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import refresh_quotes  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, status, body=b""):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_dispatch_posts_the_ref_with_the_token_and_reads_204():
    seen = []

    def opener(req):
        seen.append(req)
        return FakeResponse(204)

    assert ghactions.dispatch("o/r", "quotes.yml", "main", auth="tok", opener=opener)
    req = seen[0]
    assert req.full_url == "https://api.github.com/repos/o/r/actions/workflows/quotes.yml/dispatches"
    assert req.get_method() == "POST" and json.loads(req.data) == {"ref": "main"}
    assert req.get_header("Authorization") == "Bearer tok"
    assert not ghactions.dispatch("o/r", "quotes.yml", "main", opener=lambda r: FakeResponse(403))

    def refused(req):
        raise urllib.error.URLError("no route")

    assert not ghactions.dispatch("o/r", "quotes.yml", "main", opener=refused)


def test_branch_head_and_wait_for_a_new_one():
    heads = iter(["aaa", "aaa", "bbb"])
    opener = lambda req: FakeResponse(200, json.dumps({"sha": next(heads)}).encode())  # noqa: E731
    assert ghactions.branch_head("o/r", "quotes", opener=opener) == "aaa"
    slept = []
    assert ghactions.wait_for_new_head("o/r", "quotes", "aaa", opener=opener, sleep=slept.append, poll=5.0,
                                       clock=lambda: 0.0) == "bbb"
    assert slept == [5.0]


def test_wait_gives_up_on_the_clock():
    opener = lambda req: FakeResponse(200, b'{"sha": "aaa"}')  # noqa: E731
    ticks = iter([0.0, 100.0, 700.0])
    assert ghactions.wait_for_new_head("o/r", "quotes", "aaa", opener=opener, sleep=lambda s: None, timeout=600.0,
                                       clock=lambda: next(ticks)) is None


def test_repo_slug_reads_origin(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert ghactions.repo_slug(tmp_path) is None
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", "https://github.com/some-one/a-repo.git"], check=True)
    assert ghactions.repo_slug(tmp_path) == "some-one/a-repo"
    assert ghactions.repo_slug(ROOT) is not None


def test_last_completed_session_arithmetic():
    f = refresh_quotes.last_completed_session
    assert f(dt.datetime(2026, 9, 14, 11, 0)) == dt.date(2026, 9, 11), "Monday morning wants Friday's close"
    assert f(dt.datetime(2026, 9, 14, 21, 30)) == dt.date(2026, 9, 14), "Monday evening wants Monday's"
    assert f(dt.datetime(2026, 9, 13, 12, 0)) == dt.date(2026, 9, 11), "Sunday wants Friday's"
    assert f(dt.datetime(2026, 9, 15, 20, 59)) == dt.date(2026, 9, 14), "before the close, yesterday's"


def test_the_cli_reports_without_dispatching_when_told(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_quotes.quotes, "fetch_branch", lambda **kw: None)
    assert refresh_quotes.main(["--no-dispatch", "--now", "2026-09-14T11:00:00"]) == 1
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setattr(refresh_quotes.ghactions, "repo_slug", lambda *a, **k: "o/r")
    monkeypatch.setattr(refresh_quotes.ghactions, "branch_head", lambda *a, **k: "aaa")
    monkeypatch.setattr(refresh_quotes.ghactions, "dispatch", lambda *a, **k: False)
    assert refresh_quotes.main(["--now", "2026-09-14T11:00:00"]) == 1


def test_wait_for_session_polls_until_the_branch_carries_it():
    class P:
        def __init__(self, last):
            self.last = last

        def describe(self):
            return f"panel to {self.last}"

    panels = iter([None, P(dt.date(2026, 9, 11)), P(dt.date(2026, 9, 14))])
    slept = []
    got = refresh_quotes.wait_for_session(dt.date(2026, 9, 14), timeout=600, poll=7, fetch=lambda: next(panels),
                                          sleep=slept.append, clock=lambda: 0.0)
    assert got is not None and got.last == dt.date(2026, 9, 14) and slept == [7, 7]
    ticks = iter([0.0, 700.0])
    assert refresh_quotes.wait_for_session(dt.date(2026, 9, 14), timeout=600, poll=7, fetch=lambda: None,
                                           sleep=lambda s: None, clock=lambda: next(ticks)) is None
    assert refresh_quotes.main(["--wait", "--timeout", "0", "--now", "2026-09-14T22:00:00"]) in (0, 1)
