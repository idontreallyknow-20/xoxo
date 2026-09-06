import pytest

from an import research_md as R
from an import paths


@pytest.fixture(scope="module")
def notes():
    return R.load_all()


def test_all_sixteen_parse(notes):
    assert len(notes) == 16
    assert set(notes) == {
        "ACN", "ADBE", "ADSK", "AMAT", "BKNG", "CPRT", "GOOGL", "IDXX",
        "ISRG", "KLAC", "LRCX", "META", "MSFT", "NOW", "NVR", "REGN",
    }


def test_every_required_section_is_non_empty(notes):
    for t, n in notes.items():
        for s in R.REQUIRED_SECTIONS:
            assert n.section(s).strip(), f"{t} has empty section {s!r}"
        assert n.earnings.strip(), f"{t} has no earnings section"


def test_financial_tables_have_at_least_three_years(notes):
    for t, n in notes.items():
        assert len(n.financials) >= 3, t
        for row in n.financials:
            assert row.fiscal_year.startswith("FY") or row.fiscal_year.isdigit()


def test_klac_table_matches_the_file(notes):
    rows = notes["KLAC"].financials
    assert [r.fiscal_year for r in rows] == ["FY2023", "FY2024", "FY2025", "FY2026"]
    assert rows[-1].revenue_bn == 13.58
    assert rows[-1].gross_margin == pytest.approx(0.613)
    assert rows[-1].diluted_shares_m == 1320
    assert rows[-1].net_debt_bn == 4.24
    assert rows[0].period_end == "2023-06-30"


def test_headline_stats_are_extracted(notes):
    h = notes["KLAC"].headline
    assert h.quality_rank == 6
    assert h.buckets == ["compounder", "cyclical turn"]
    assert h.price == 172.94 and h.currency == "USD" and h.as_of == "2026-09-04"
    assert h.forward_pe == 25.9 and h.median_pe == 35.0
    assert h.ev_ebitda == 37.5  # was dropped by a greedy regex eating the sentence's full stop
    assert h.dd_ath_pct == -43.0
    assert h.next_earnings == "2026-10-28"


def test_headline_agrees_with_the_csv_where_both_exist(notes):
    """The notes were written from the same screen, so a disagreement means one drifted."""
    from an import local

    priced = local.load_price_screen()
    checked = 0
    for t, n in notes.items():
        v = priced.get(t)
        if not v or n.headline.forward_pe is None or v.forward_pe is None:
            continue
        assert abs(n.headline.forward_pe - v.forward_pe) < 0.15, f"{t} forward P/E drifted"
        checked += 1
    assert checked >= 12


def test_sources_parse_with_urls_and_dates(notes):
    for t, n in notes.items():
        assert len(n.sources) >= 2, t
        assert any(s.url for s in n.sources), t
        linked = [s for s in n.sources if s.url]
        assert all(s.url.startswith("http") for s in linked), t


def test_verdict_conviction_and_price_trigger(notes):
    k = notes["KLAC"]
    assert k.conviction == 4
    assert k.price_trigger == 130.0
    assert k.verdict_action.lower().startswith("buy now")
    have_conviction = sum(1 for n in notes.values() if n.conviction is not None)
    assert have_conviction >= 14


def test_missing_price_triggers_are_real_absences_not_parse_failures():
    """Three notes genuinely name no trigger. AMAT says so outright. The page must
    show 'not set' rather than invent one, so the parser has to report None here."""
    notes = R.load_all()
    missing = sorted(t for t, n in notes.items() if n.price_trigger is None)
    assert missing == ["ACN", "AMAT", "NVR"]
    assert "not set" in notes["AMAT"].section("Key risks and thesis killers")


def test_thesis_killers_are_specific(notes):
    for t, n in notes.items():
        killers = n.thesis_killers
        assert killers, t
        assert all(len(k) > 12 for k in killers), t


def test_parser_refuses_a_half_written_note():
    """A note missing sections must raise, not render as a finished page."""
    with pytest.raises(R.ParseError):
        R.parse("# ABC  A Company\n\nstats\n\n## What the company does\nthing\n")


def test_parser_refuses_a_bad_title():
    with pytest.raises(R.ParseError):
        R.parse("no title here\n")


def test_template_is_skipped(notes):
    assert "TICKER" not in notes
    assert (paths.RESEARCH_DIR / "_TEMPLATE.md").exists()


def test_a_short_table_row_raises_rather_than_shifting_every_column():
    """Padding a short row to seven cells and then indexing positionally does not
    fail, it silently re-reads every later column as the wrong metric: the gross
    margin becomes the operating margin, the FCF becomes the share count."""
    good = (paths.RESEARCH_DIR / "KLAC.md").read_text(encoding="utf-8")
    broken = good.replace(
        "| FY2026 (2026-06-30) | 13.58 | 61.3% | 41.7% | 3.77 | 1,320 | 4.24 |",
        "| FY2026 (2026-06-30) | 13.58 | 41.7% | 3.77 | 1,320 | 4.24 |")
    with pytest.raises(R.ParseError, match="6 cells"):
        R.parse(broken)


def test_a_note_whose_filename_and_heading_disagree_raises(tmp_path):
    """A copy of KLAC.md saved as KLAC-old.md parses, keys itself as KLAC and
    overwrites the real one, last write wins, nothing said."""
    src = (paths.RESEARCH_DIR / "KLAC.md").read_text(encoding="utf-8")
    (tmp_path / "KLAC.md").write_text(src, encoding="utf-8")
    (tmp_path / "KLAC-old.md").write_text(src, encoding="utf-8")
    with pytest.raises(R.ParseError, match="filename and the heading must agree"):
        R.load_all(tmp_path)


def test_one_malformed_note_does_not_take_the_others_down(tmp_path):
    for t in ("KLAC", "BKNG"):
        (tmp_path / f"{t}.md").write_text(
            (paths.RESEARCH_DIR / f"{t}.md").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "BAD.md").write_text("# BAD  Broken Co\n\nstats\n\n## What the company does\nx\n",
                                     encoding="utf-8")
    with pytest.raises(R.ParseError):
        R.load_all(tmp_path)
    notes, errors = R.load_all_lenient(tmp_path)
    assert set(notes) == {"KLAC", "BKNG"}
    assert set(errors) == {"BAD"}
    assert all(isinstance(n, R.ResearchNote) for n in notes.values())


def test_the_lenient_loader_returns_a_pair_not_a_magic_key():
    """An earlier version put the failures under an "_errors" key inside the notes
    dict, so any caller iterating it would eventually find a string where a
    ResearchNote was promised."""
    notes, errors = R.load_all_lenient()
    assert "_errors" not in notes
    assert errors == {}
    assert all(isinstance(n, R.ResearchNote) for n in notes.values())
