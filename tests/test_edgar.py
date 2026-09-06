import pytest

from an import edgar
from an.http import FixtureTransport
from an.store import Cache, FetchError


@pytest.fixture
def client(tmp_path, load_fixture):
    t = FixtureTransport()
    t.add("https://www.sec.gov/files/company_tickers.json", load_fixture("company_tickers.json"))
    t.add("https://data.sec.gov/submissions/CIK0000320193.json", load_fixture("submissions_aapl.json"))
    t.add(
        "https://data.sec.gov/submissions/CIK0000320193-submissions-001.json",
        load_fixture("submissions_aapl_older.json"),
    )
    t.add("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json", load_fixture("companyfacts_aapl.json"))
    t.add(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/index.json",
        load_fixture("index_8k.json"),
    )
    t.add(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/a8-kex991q3202607.htm",
        load_fixture("ex991.htm"),
    )
    c = edgar.EdgarClient(transport=t, cache=Cache(tmp_path, default_ttl=3600), user_agent="test test@example.com")
    c._fixture = t
    return c


def test_cik_padding():
    assert edgar.cik_to_str(320193) == "0000320193"
    assert edgar.cik_to_str("0000320193") == "0000320193"
    assert edgar.cik_to_str("CIK0000320193") == "0000320193"
    with pytest.raises(ValueError):
        edgar.cik_to_str("nope")


def test_cik_lookup(client):
    assert client.cik_for("AAPL") == "0000320193"
    assert client.cik_for("aapl") == "0000320193"
    assert client.cik_for("ZZZZ") is None


def test_cik_lookup_handles_class_shares(client):
    """Yahoo says BRK-B, other sources say BRK.B, EDGAR says BRK-B."""
    assert client.cik_for("BRK.B") == "0001067983"
    assert client.cik_for("BRK-B") == "0001067983"


def test_filings_parse_columnar_arrays(client):
    f = client.filings("0000320193")
    assert len(f) == 4
    assert f[0].form == "10-Q" and f[0].filing_date == "2026-08-01"
    assert f[0].accession == "0000320193-26-000081"
    assert f[0].accession_nodash == "000032019326000081"
    assert [x.form for x in f] == ["10-Q", "8-K", "10-Q", "10-K"]


def test_filings_filter_by_form(client):
    assert [f.form for f in client.filings("0000320193", forms=["10-K"])] == ["10-K"]
    assert len(client.filings("0000320193", forms=["10-K", "10-Q"])) == 3


def test_older_pages_are_opt_in(client):
    assert len(client.filings("0000320193", limit=99)) == 4
    assert len(client.filings("0000320193", limit=99, include_older=True)) == 5


def test_earnings_8k_is_identified_by_item_202(client):
    eight_ks = [f for f in client.filings("0000320193", forms=["8-K"])]
    assert len(eight_ks) == 1
    assert eight_ks[0].items == ["2.02", "9.01"]
    assert eight_ks[0].is_earnings_8k is True
    tenq = client.filings("0000320193", forms=["10-Q"])[0]
    assert tenq.is_earnings_8k is False


def test_filing_urls(client):
    f = client.filings("0000320193", forms=["8-K"])[0]
    assert f.directory_url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000070"
    assert f.primary_url.endswith("/aapl-20260731.htm")
    assert f.index_json_url.endswith("/index.json")


def test_exhibit_resolution_by_type(client):
    f = client.filings("0000320193", forms=["8-K"])[0]
    ex = client.earnings_exhibits(f)
    assert ex["ex99_1"].endswith("a8-kex991q3202607.htm")
    assert ex["ex99_2"].endswith("ex992slides.htm")


def test_exhibit_resolution_falls_back_to_filename(tmp_path, load_fixture):
    from an.http import FixtureTransport

    t = FixtureTransport()
    t.add("https://data.sec.gov/submissions/CIK0000320193.json", load_fixture("submissions_aapl.json"))
    t.add(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000070/index.json",
        load_fixture("index_8k_untyped.json"),
    )
    c = edgar.EdgarClient(transport=t, cache=Cache(tmp_path), user_agent="t t@e.com")
    f = c.filings("0000320193", forms=["8-K"])[0]
    assert c.earnings_exhibits(f)["ex99_1"].endswith("ex99_1.htm")


def test_document_text_strips_markup(client):
    f = client.filings("0000320193", forms=["8-K"])[0]
    txt = client.document_text(client.earnings_exhibits(f)["ex99_1"])
    assert "Reports Third Quarter Results" in txt
    assert "color:red" not in txt
    assert "<p>" not in txt
    assert "—" in txt or "—" in txt  # &mdash; unescaped
    assert "grow 5 to 7 percent" in txt


def test_facts_carry_the_filed_date(client):
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    assert rev
    assert all(f.filed for f in rev)
    assert {f.unit for f in rev} == {"USD"}


def test_metric_falls_through_tag_preferences(client):
    cf = client.companyfacts("0000320193")
    assert client.metric(cf, "revenue")[0].tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert client.metric(cf, "nonexistent-metric") == []


def test_null_values_are_skipped_not_crashed_on(client):
    cf = client.companyfacts("0000320193")
    assets = client.metric(cf, "assets")
    assert len(assets) == 1
    assert assets[0].value == 364_980_000_000


def test_as_known_on_ignores_later_restatements(client):
    """FY2025 revenue was filed at 400.0bn in Nov 2025 and restated to 401.5bn in Nov 2026.
    A backtest standing in Jan 2026 must see 400.0bn, or it is cheating."""
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    known = client.as_known_on(rev, "2026-01-15", period_end="2025-09-27")
    assert known.value == 400_000_000_000
    assert known.filed == "2025-11-01"

    later = client.as_known_on(rev, "2026-12-01", period_end="2025-09-27")
    assert later.value == 401_500_000_000


def test_as_known_on_returns_none_before_anything_was_filed(client):
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    assert client.as_known_on(rev, "2020-01-01") is None


def test_missing_fixture_raises_rather_than_silently_returning_nothing(client):
    with pytest.raises(FetchError):
        client.submissions("0000000001")


def test_a_concept_the_company_never_tagged_is_none_not_an_error(client):
    """EDGAR answers an untagged concept with a 404, which is the normal answer to a
    normal question: most companies do not use most tags. Turning that into an
    exception makes a tag-fallback chain a pile of try/excepts."""
    assert client.companyconcept("0000320193", "Revenues") is None


def test_fy_and_fp_describe_the_filing_not_the_period(client):
    """The trap. A FY2024 revenue figure appears again as a comparative column in the
    FY2026 10-K carrying fy=2026. Reading fy would date it 2026."""
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    comparative = [f for f in rev if f.fiscal_year == 2026 and f.end == "2024-09-28"]
    assert comparative, "fixture no longer carries the comparative column"
    f = comparative[0]
    assert f.fiscal_period == "FY"
    assert f.end.startswith("2024")
    assert f.covers_a_year is True
    assert 330 <= f.period_days <= 400


def test_period_length_comes_from_start_and_end(client):
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    annual = [f for f in rev if f.covers_a_year]
    quarterly = [f for f in rev if f.covers_a_quarter]
    assert annual and quarterly
    assert all(f.period_days >= 330 for f in annual)
    assert all(60 <= f.period_days <= 120 for f in quarterly)


def test_an_instant_fact_has_no_period(client):
    cf = client.companyfacts("0000320193")
    assets = client.metric(cf, "assets")
    assert assets[0].is_instant is True
    assert assets[0].period_days is None
    assert assets[0].covers_a_year is False


def test_canonical_keeps_one_fact_per_period(client):
    """The same figure recurs under many accession numbers as comparatives pile up.
    frame is the SEC's own pick, and it is free."""
    cf = client.companyfacts("0000320193")
    rev = client.metric(cf, "revenue")
    canon = client.canonical(rev)
    assert len(canon) < len(rev)
    assert all(f.frame for f in canon)
    assert len({f.frame for f in canon}) == len(canon)


def test_annual_series_is_sorted_and_excludes_quarters(client):
    cf = client.companyfacts("0000320193")
    series = client.annual_series(client.metric(cf, "revenue"))
    assert series
    assert all(f.covers_a_year for f in series)
    assert [f.end for f in series] == sorted(f.end for f in series)


def test_a_ten_q_cash_flow_fact_is_cumulative_not_quarterly(client):
    """Q2 operating cash flow covers six months. Treating it as a quarter doubles it."""
    cf = client.companyfacts("0000320193")
    ocf = client.metric(cf, "operating_cash_flow")
    ytd = [f for f in ocf if f.form == "10-Q"]
    assert ytd
    assert ytd[0].period_days > 150, "a Q2 10-Q fact should span about half a year"
    assert ytd[0].covers_a_quarter is False


def test_the_taxonomy_falls_back_to_ifrs_for_a_foreign_filer(tmp_path, load_fixture):
    """A 20-F filer tags under IFRS. Looking only in us-gaap finds nothing at all."""
    from an.http import FixtureTransport

    t = FixtureTransport()
    t.add("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000111.json",
          load_fixture("companyfacts_ifrs.json"))
    c = edgar.EdgarClient(transport=t, cache=Cache(tmp_path), user_agent="t t@e.com")
    cf = c.companyfacts("111")
    assert c.facts_for(cf, "Revenues") == []
    got = c.metric(cf, "revenue")
    assert got and got[0].value == 9_000_000_000


def test_acceptance_datetime_is_captured(client):
    """filingDate is only a date. A filing accepted at 17:35 gets that day's date and
    the market never saw it until the next session."""
    f = client.filings("0000320193")[0]
    assert f.acceptance_datetime == "2026-08-01T17:35:12.000Z"
    assert f.filing_date == "2026-08-01"
    late = [x for x in client.filings("0000320193") if x.acceptance_datetime and
            int(x.acceptance_datetime[11:13]) >= 17]
    assert late, "at least one fixture filing lands after the close"


def test_exhibit_99_2_is_returned_without_claiming_what_it_is(client):
    """Exhibit numbering under Item 601 is not standardised past the 99, so 99.2 is
    slides at some filers and a supplemental pack at others."""
    import inspect

    doc = inspect.getdoc(edgar.EdgarClient.earnings_exhibits)
    assert "not reliably anything" in doc


def test_cache_prevents_a_second_fetch(client):
    client.company_tickers()
    n = len(client._fixture.calls)
    client.company_tickers()
    assert len(client._fixture.calls) == n


def test_user_agent_is_sent(client):
    assert "test@example.com" in client.user_agent


def test_key_tag_map_is_preference_ordered():
    assert edgar.KEY_TAGS["revenue"][0] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert "Revenues" in edgar.KEY_TAGS["revenue"]
    for metric, tags in edgar.KEY_TAGS.items():
        assert tags and len(set(tags)) == len(tags), metric
