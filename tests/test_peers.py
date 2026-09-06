import pytest

from an import local, peers


@pytest.fixture(scope="module")
def pv():
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    return peers.build_peer_valuations(recs), {r.ticker: r for r in recs}


def test_every_priced_name_gets_a_peer_set(pv):
    table, recs = pv
    assert len(table) == 150


def test_peer_sets_prefer_industry_then_sector(pv):
    table, recs = pv
    for t, v in table.items():
        assert v.peer_set.basis in ("industry", "sector", "whole list")
        assert v.peer_set.n >= peers.MIN_PEERS or v.peer_set.basis == "whole list"
        assert t in v.peer_set.tickers
        if v.peer_set.basis == "industry":
            assert all(recs[p].industry == recs[t].industry for p in v.peer_set.tickers)
        if v.peer_set.basis == "sector":
            assert all(recs[p].sector == recs[t].sector for p in v.peer_set.tickers)


def test_a_thin_industry_falls_back_and_says_so(pv):
    table, recs = pv
    fell_back = [v for v in table.values() if v.peer_set.basis != "industry"]
    assert fell_back
    for v in fell_back:
        assert "not really comparable" in v.peer_set.caveat or "nothing to do with each other" in v.peer_set.caveat


def test_the_peer_list_is_named_so_it_can_be_argued_with(pv):
    table, _ = pv
    v = table["ADBE"]
    assert len(v.peer_set.tickers) >= peers.MIN_PEERS
    assert "ADBE" in v.peer_set.tickers
    assert v.peer_set.label


def test_percentiles_are_bounded(pv):
    table, _ = pv
    for t, v in table.items():
        for d in (v.price_percentiles, v.quality_percentiles):
            for k, x in d.items():
                assert x is None or 0.0 <= x <= 1.0, f"{t} {k} = {x}"


def test_a_negative_multiple_is_not_treated_as_cheap():
    """A company with negative earnings has no P/E. Ranking it as the cheapest name
    in its sector is how a screen ends up long a loss-maker."""
    from an.local import Fundamentals, TickerRecord, Valuation

    recs = [TickerRecord(ticker=f"P{i}", sector="Technology", industry="Software - Application",
                         fundamentals=Fundamentals(roic_avg=0.1 + i * 0.01, fcf_margin_avg=0.1,
                                                   rev_cagr=0.1, gm_std=0.02),
                         valuation=Valuation(forward_pe=20 + i, ev_ebitda=15 + i))
            for i in range(8)]
    recs.append(TickerRecord(ticker="LOSS", sector="Technology", industry="Software - Application",
                             fundamentals=Fundamentals(roic_avg=0.05, fcf_margin_avg=-0.2,
                                                       rev_cagr=0.1, gm_std=0.02),
                             valuation=Valuation(forward_pe=-8.0, ev_ebitda=-4.0)))
    table = peers.build_peer_valuations(recs)
    assert table["LOSS"].price_percentiles["forward P/E"] is None
    assert table["LOSS"].price_rank is None
    assert table["LOSS"].verdict == "not comparable"


def test_adobe_is_cheap_against_better_peers(pv):
    """Adobe is in the cheapest decile of application software on forward P/E and the
    better end on return on capital. That is the quadrant this module exists to find."""
    v = pv[0]["ADBE"]
    assert v.peer_set.basis == "industry"
    assert v.price_rank < 0.2
    assert v.quality_rank > 0.6
    assert v.verdict == "cheap against peers of better quality"
    assert "question, not an answer" in v.reasoning


def test_a_wide_gap_is_reported_even_inside_the_quartiles(pv):
    """Booking sits at the 46th percentile on price and the 88th on quality. Without
    the gap rule that lands in "the middle" and the 42-point gap goes unsaid."""
    v = pv[0]["BKNG"]
    assert v.gap >= peers.WIDE_GAP
    assert v.verdict == "priced below where its quality sits"
    assert "points apart" in v.reasoning


def test_most_of_the_list_is_unremarkable_and_says_so(pv):
    table, _ = pv
    middle = [v for v in table.values() if v.verdict == "in the middle"]
    assert len(middle) > len(table) * 0.4
    assert "honest answer for most of the list" in middle[0].reasoning


def test_every_verdict_has_reasoning(pv):
    table, _ = pv
    for t, v in table.items():
        assert v.verdict and len(v.reasoning) > 60, t


def test_gap_is_quality_minus_price(pv):
    table, _ = pv
    for t, v in table.items():
        if v.price_rank is not None and v.quality_rank is not None:
            assert v.gap == pytest.approx(v.quality_rank - v.price_rank)


def test_cheapest_and_dearest_peers_are_listed(pv):
    table, _ = pv
    v = table["ADBE"]
    assert v.cheapest_peers and v.dearest_peers
    assert v.cheapest_peers[0][1] <= v.dearest_peers[0][1]


def test_price_to_sales_is_not_among_the_inputs():
    """The screen has no price-to-sales column. What looks like one, price_ps, is a
    second price from a merge suffix collision, and reading it as a multiple gives
    Adobe a price-to-sales of 280x."""
    labels = [lab for lab, _ in peers.PRICE_INPUTS]
    assert "price to sales" not in labels
    assert labels == ["forward P/E", "EV/EBITDA"]


def test_the_second_price_column_is_named_for_what_it_is():
    v = local.load_price_screen()["ADBE"]
    assert hasattr(v, "price_from_price_screen")
    assert not hasattr(v, "price_sales")
    rec = local.load_universe()["ADBE"]
    assert abs(v.price_from_price_screen - rec.price) / rec.price < 0.05


def test_to_dict_is_serialisable(pv):
    import json

    table, _ = pv
    blob = json.dumps({t: v.to_dict() for t, v in table.items()})
    assert "peer_set" in blob and "caveat" in blob


def test_deterministic(pv):
    recs = [r for r in local.load_universe().values() if r.in_top_150]
    a = peers.build_peer_valuations(recs)
    b = peers.build_peer_valuations(recs)
    assert {t: v.gap for t, v in a.items()} == {t: v.gap for t, v in b.items()}
