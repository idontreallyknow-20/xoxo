from an import journal


def test_entries_parse():
    e = journal.load()
    assert len(e) >= 12
    assert all(x.date.count("-") == 2 for x in e)


def test_system_entries_are_flagged_and_excluded():
    e = journal.load()
    assert sum(1 for x in e if x.is_system) == 2
    assert "SYSTEM" not in journal.by_ticker(e)


def test_bkng_entry():
    b = journal.by_ticker()["BKNG"][0]
    assert b.date == "2026-09-04"
    assert b.price_at_call == 195.13
    assert b.conviction == 4
    assert b.bucket == "compounder"
    assert b.target_usd == 8000
    assert "Record bookings" in b.thesis
    assert "room nights" in b.wrong_if
    assert b.action == "Buy now"


def test_every_pick_has_a_falsifier():
    """A call logged without a 'wrong if' is not a call, it is a hope."""
    for t, entries in journal.by_ticker().items():
        for e in entries:
            assert e.wrong_if, f"{t} {e.date} has no 'Wrong if'"


def test_conviction_is_bounded_or_none():
    for e in journal.load():
        assert e.conviction is None or 1 <= e.conviction <= 5


def test_tickers_overlap_the_research_notes():
    from an import research_md

    logged = set(journal.by_ticker())
    researched = set(research_md.load_all())
    assert logged <= researched, f"logged but never researched: {logged - researched}"


def test_empty_input():
    assert journal.parse("") == []
