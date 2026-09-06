import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    p = FIXTURES / name
    if p.suffix == ".json":
        return json.loads(p.read_text())
    return p.read_text()


@pytest.fixture
def fixtures_dir():
    return FIXTURES


@pytest.fixture
def load_fixture():
    return fixture


@pytest.fixture(autouse=True)
def _no_accidental_network(monkeypatch):
    """Any test that reaches urllib fails instead of quietly hanging on a blocked proxy."""
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("a test tried to open a real network connection")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
