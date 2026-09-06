import json
import time

import pytest

from an.store import Cache, FetchError, Offline, cache_key, is_offline


def test_cache_key_is_stable_and_safe():
    assert cache_key("AAPL", "companyfacts") == "AAPL_companyfacts"
    assert cache_key("a b/c") == "a-b-c"
    long = cache_key("x" * 300)
    assert len(long) <= 97 and "/" not in long
    assert cache_key("x" * 300) == long  # deterministic


def test_write_then_read_roundtrip(tmp_path):
    c = Cache(tmp_path)
    c.write("k", {"a": 1})
    e = c.read("k")
    assert e is not None and e.payload == {"a": 1}
    assert e.age_seconds < 5


def test_read_missing_and_corrupt(tmp_path):
    c = Cache(tmp_path)
    assert c.read("nope") is None
    c.dir.mkdir(parents=True, exist_ok=True)
    c.path_for("bad").write_text("{not json", encoding="utf-8")
    assert c.read("bad") is None


def test_writes_are_atomic_no_tmp_left(tmp_path):
    c = Cache(tmp_path)
    c.write("k", {"a": 1})
    assert list(tmp_path.glob("*.tmp")) == []


def test_fresh_entry_skips_fetch(tmp_path):
    c = Cache(tmp_path, default_ttl=3600)
    c.write("k", "cached")
    calls = []

    def fetch():
        calls.append(1)
        return "fresh"

    assert c.get_or_fetch("k", fetch) == "cached"
    assert calls == []


def test_stale_entry_triggers_fetch(tmp_path):
    c = Cache(tmp_path, default_ttl=0.0)
    c.write("k", "cached")
    time.sleep(0.01)
    assert c.get_or_fetch("k", lambda: "fresh") == "fresh"
    assert c.read("k").payload == "fresh"


def test_fetch_failure_falls_back_to_stale(tmp_path):
    c = Cache(tmp_path, default_ttl=0.0)
    c.write("k", "old")

    def boom():
        raise FetchError("nope", status=503, url="http://x")

    assert c.get_or_fetch("k", boom) == "old"


def test_fetch_failure_without_stale_raises(tmp_path):
    c = Cache(tmp_path)

    def boom():
        raise FetchError("nope")

    with pytest.raises(FetchError):
        c.get_or_fetch("missing", boom)


def test_stale_fallback_can_be_disabled(tmp_path):
    c = Cache(tmp_path, default_ttl=0.0)
    c.write("k", "old")
    with pytest.raises(FetchError):
        c.get_or_fetch("k", lambda: (_ for _ in ()).throw(FetchError("x")), allow_stale_on_error=False)


def test_offline_flag(monkeypatch):
    monkeypatch.delenv("DESK_OFFLINE", raising=False)
    assert is_offline() is False
    monkeypatch.setenv("DESK_OFFLINE", "1")
    assert is_offline() is True
    monkeypatch.setenv("DESK_OFFLINE", "0")
    assert is_offline() is False


def test_offline_is_a_runtime_error():
    assert issubclass(Offline, RuntimeError)


def test_stats(tmp_path):
    c = Cache(tmp_path)
    assert c.stats()["files"] == 0
    c.write("a", [1, 2, 3])
    c.write("b", [1])
    s = c.stats()
    assert s["files"] == 2 and s["bytes"] > 0


def test_meta_is_recorded(tmp_path):
    c = Cache(tmp_path)
    p = c.write("k", 1, meta={"url": "http://x", "source": "test"})
    blob = json.loads(p.read_text())
    assert blob["meta"]["source"] == "test"
