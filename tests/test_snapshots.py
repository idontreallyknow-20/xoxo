import json
import shutil

import pytest

from an import paths, snapshots


@pytest.fixture
def archive_root(tmp_path):
    return tmp_path / "snapshots"


def test_archives_the_current_pull(archive_root):
    s = snapshots.archive(root=archive_root)
    assert s.date == "2026-09-04"
    assert s.n_scored == 1505 and s.n_priced == 150
    for name in ("quality_scores_latest.csv", "price_screen_latest.csv", "manifest.json", "scores.json"):
        assert (s.directory / name).exists(), name


def test_the_date_comes_from_the_data_not_the_clock(archive_root):
    """A snapshot is stamped with the day the data describes. Otherwise re-running
    the archiver tomorrow would create a second directory holding the same pull
    under a date that pull knows nothing about."""
    s = snapshots.archive(root=archive_root)
    assert s.date == "2026-09-04"
    manifest = json.loads((s.directory / "manifest.json").read_text())
    assert manifest["date"] == "2026-09-04"
    assert manifest["archived_at"] != manifest["date"]


def test_re_archiving_the_same_pull_is_a_no_op(archive_root):
    a = snapshots.archive(root=archive_root)
    stamp = a.manifest["archived_at"]
    b = snapshots.archive(root=archive_root)
    assert b.manifest["archived_at"] == stamp
    assert len(list(archive_root.iterdir())) == 1


def test_force_re_archives(archive_root):
    snapshots.archive(root=archive_root)
    b = snapshots.archive(root=archive_root, force=True)
    assert b.files


def test_checksums_are_recorded(archive_root):
    s = snapshots.archive(root=archive_root)
    assert all(len(v) == 16 for v in s.files.values())
    assert s.files["quality_scores_latest.csv"] != s.files["price_screen_latest.csv"]


def test_the_universe_is_recorded_as_it_was(archive_root):
    """The whole point: a company present today and gone next quarter stays in this
    list, and its later absence is the survivorship datum."""
    s = snapshots.archive(root=archive_root)
    assert len(s.manifest["tickers_scored"]) == 1505
    assert len(s.manifest["tickers_priced"]) == 150
    assert "KLAC" in s.manifest["tickers_priced"]


def test_scores_are_stored_alongside_the_inputs(archive_root):
    """Recomputing later from archived CSVs would only agree if score.py never
    changed, and score.py will change."""
    s = snapshots.archive(root=archive_root)
    blob = json.loads((s.directory / "scores.json").read_text())
    assert set(blob["variants"]) == {"quality_value", "with_momentum", "with_reversal"}
    assert len(blob["variants"]["quality_value"]) == 150
    assert blob["variants"]["quality_value"]["KLAC"]["percentile"] is not None


def test_one_snapshot_is_not_a_panel(archive_root):
    snapshots.archive(root=archive_root)
    with pytest.raises(snapshots.NotEnoughHistory, match="at least two dates"):
        snapshots.load_panel(root=archive_root)


def test_zero_snapshots_is_not_a_panel(tmp_path):
    with pytest.raises(snapshots.NotEnoughHistory):
        snapshots.load_panel(root=tmp_path / "nothing")


def test_two_snapshots_make_a_panel(archive_root):
    snapshots.archive(root=archive_root)
    later = archive_root / "2026-12-04"
    shutil.copytree(archive_root / "2026-09-04", later)
    m = json.loads((later / "manifest.json").read_text())
    m["date"] = "2026-12-04"
    m["tickers_priced"] = [t for t in m["tickers_priced"] if t != "KLAC"]  # a name leaves
    (later / "manifest.json").write_text(json.dumps(m))

    panel = snapshots.load_panel(root=archive_root)
    assert len(panel) == 2
    assert [d for d, _ in panel] == ["2026-09-04", "2026-12-04"]
    assert len(panel[0][1]) == 150


def test_the_coverage_report_names_who_left(archive_root):
    snapshots.archive(root=archive_root)
    later = archive_root / "2026-12-04"
    shutil.copytree(archive_root / "2026-09-04", later)
    m = json.loads((later / "manifest.json").read_text())
    m["date"] = "2026-12-04"
    m["tickers_priced"] = [t for t in m["tickers_priced"] if t != "KLAC"] + ["NEWCO"]
    (later / "manifest.json").write_text(json.dumps(m))

    rep = snapshots.coverage_report(root=archive_root)
    assert rep["n_snapshots"] == 2
    assert rep["usable_forward_windows"] == 1
    assert rep["names_that_left"] == {"KLAC": "2026-12-04"}
    assert rep["names_that_entered"] == {"NEWCO": "2026-12-04"}
    assert "very little below about a dozen" in rep["status"]


def test_report_on_an_empty_archive(tmp_path):
    rep = snapshots.coverage_report(root=tmp_path / "nothing")
    assert rep["n_snapshots"] == 0
    assert rep["usable_forward_windows"] == 0


def test_the_first_snapshot_is_committed():
    """The one that already exists is free, taken from data already in the repo.
    If it is missing, the archive has no starting point."""
    d = paths.SNAPSHOT_DIR / "2026-09-04"
    assert d.exists(), "run python scripts/snapshot.py"
    assert (d / "manifest.json").exists()
