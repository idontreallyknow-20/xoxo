from pathlib import Path

from an import paths


def test_root_is_the_repo():
    assert (paths.ROOT / "criteria.md").exists()
    assert (paths.ROOT / "dashboard" / "index.html").exists()


def test_source_csvs_exist():
    for p in (paths.QUALITY_SCORES, paths.PRICE_SCREEN, paths.UNIVERSE_CSV):
        assert p.exists(), p


def test_import_does_not_create_directories(tmp_path, monkeypatch):
    """Importing the module must not touch the filesystem."""
    import importlib

    monkeypatch.setenv("DESK_ROOT", str(tmp_path))
    mod = importlib.reload(paths)
    try:
        assert not any(Path(d).exists() for d in mod.WRITABLE)
        mod.ensure_dirs()
        assert all(Path(d).exists() for d in mod.WRITABLE)
    finally:
        monkeypatch.delenv("DESK_ROOT", raising=False)
        importlib.reload(paths)
