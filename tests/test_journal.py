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


def test_every_buy_has_a_falsifier():
    """A buy logged without a "wrong if" is not a call, it is a hope.

    A decision to stay away is different: there is nothing to be wrong about yet,
    and the one Watch-or-Pass entry in the log says "n/a" for exactly that reason.
    The rule applies to the calls that put money at risk."""
    for t, entries in journal.by_ticker().items():
        for e in entries:
            if (e.action or "").lower().startswith("buy"):
                assert e.wrong_if, f"{t} {e.date} is a buy with no 'Wrong if'"


def test_the_only_calls_without_a_falsifier_are_the_ones_staying_away():
    without = [(t, e.action) for t, es in journal.by_ticker().items() for e in es if not e.wrong_if]
    assert {t for t, _ in without} == {"META", "NOW", "ACN", "AMAT", "NVR"}
    assert all("watch" in (a or "").lower() or "pass" in (a or "").lower() for _, a in without)


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


def test_a_heading_naming_five_tickers_produces_five_entries():
    """journal.md line 123 is "## 2026-09-04 META NOW ACN AMAT NVR  Recommendation:
    Watch or Pass". The old pattern captured a single \\S+ and silently dropped four
    logged calls."""
    by = journal.by_ticker()
    for t in ("META", "NOW", "ACN", "AMAT", "NVR"):
        assert t in by, t
        e = by[t][0]
        assert e.action == "Watch or Pass"
        assert e.is_shared
        assert set(e.shared_with) == {"META", "NOW", "ACN", "AMAT", "NVR"} - {t}


def test_each_ticker_in_a_shared_heading_gets_its_own_price():
    """The heading writes "610.68 / 145.59 / 193.12 / 435.91 / 6373.95". Handing the
    first name's price to all five would misprice four of them by up to 10x."""
    by = journal.by_ticker()
    assert by["META"][0].price_at_call == 610.68
    assert by["NOW"][0].price_at_call == 145.59
    assert by["ACN"][0].price_at_call == 193.12
    assert by["AMAT"][0].price_at_call == 435.91
    assert by["NVR"][0].price_at_call == 6373.95


def test_a_mismatched_price_list_yields_nothing_rather_than_the_wrong_number():
    entries = journal.parse(
        "## 2026-01-01 AAA BBB CCC  Recommendation: Watch\n"
        "Price at call: 10.0 / 20.0\n"
        "Thesis: x.\n"
        "Wrong if: y.\n")
    assert len(entries) == 3
    assert all(e.price_at_call is None for e in entries)


def test_a_placeholder_field_reads_as_absent():
    """journal.md writes a literal "n/a" for an entry with no falsifier. That string
    is truthy in Python and in JavaScript, so a fallback chain stops at it and the
    page renders "n/a" as the thing that would prove the idea wrong."""
    by = journal.by_ticker()
    assert by["META"][0].wrong_if is None
    entries = journal.parse(
        "## 2026-01-01 AAA  Recommendation: Watch\nWrong if: n/a\nBucket: -\nThesis: real.\n")
    assert entries[0].wrong_if is None
    assert entries[0].bucket is None
    assert entries[0].thesis == "real."


def test_single_ticker_headings_still_parse_and_are_not_shared():
    b = journal.by_ticker()["BKNG"][0]
    assert b.shared_with == [] and b.is_shared is False
    assert b.price_at_call == 195.13
