"""The sale feed, parsed from a saved copy so the suite never touches the network."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from flightdeals.feeds import ozbargain

FIXTURE = (Path(__file__).parent / "fixtures" / "ozbargain_travel.xml").read_bytes()


def parse(origin="BNE"):
    return ozbargain.parse_feed(FIXTURE, origin=origin)


def test_only_airfare_posts_survive():
    """Hotels, tours and flight-plus-hotel packages share the category and are not fares."""
    titles = [d.title for d in parse()]
    assert len(titles) == 3
    assert any("London Return" in t for t in titles)
    assert any("Tokyo Return" in t for t in titles)
    assert any("Jetstar Return for Free" in t for t in titles)
    assert not any("Ramada" in t for t in titles), "flight+hotel package is not a fare"
    assert not any("Rail Tour" in t for t in titles)
    assert not any("Marriott" in t for t in titles)


def test_the_price_for_the_requested_origin_is_picked_out():
    london = next(d for d in parse() if "London" in d.title)
    assert london.origin_price == 1320.0
    assert next(d for d in parse("MEL") if "London" in d.title).origin_price == 1082.0
    assert next(d for d in parse("SYD") if "London" in d.title).origin_price == 1129.0


def test_a_city_name_works_as_well_as_a_code():
    tokyo = next(d for d in parse() if "Tokyo" in d.title)
    assert tokyo.origin_price == 907.0, "'Brisbane $907' must parse like 'BNE $907'"
    assert next(d for d in parse("PER") if "Tokyo" in d.title).origin_price == 834.0


def test_an_origin_absent_from_the_title_yields_no_price_rather_than_a_wrong_one():
    london = next(d for d in parse("DRW") if "London" in d.title)
    assert london.origin_price is None


def test_a_post_without_per_city_prices_still_parses():
    jetstar = next(d for d in parse() if "Jetstar" in d.title)
    assert jetstar.origin_price is None
    assert jetstar.votes == 201
    assert jetstar.is_expired is False, "no expiry means not expired"


def test_metadata_is_carried_through():
    london = next(d for d in parse() if "London" in d.title)
    assert london.votes == 62
    assert london.url.endswith("/974327")
    assert london.posted.year == 2026 and london.posted.month == 10
    assert "China Southern" in london.airline


def test_a_past_expiry_is_reported_as_expired():
    tokyo = next(d for d in parse() if "Tokyo" in d.title)
    assert tokyo.is_expired is True
    london = next(d for d in parse() if "London" in d.title)
    assert london.is_expired is False


def test_a_malformed_expiry_does_not_raise():
    deal = ozbargain.SaleDeal(title="x", url="", posted=None, expiry="not a date")
    assert deal.is_expired is False


def test_an_unparsable_feed_raises_rather_than_returning_nothing():
    """A truncated download must fail loudly, not look like "no deals today"."""
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        ozbargain.parse_feed(b"<not xml")


def test_expiry_comparison_is_timezone_aware():
    """A naive datetime here would raise on comparison, so pin that it does not."""
    deal = ozbargain.SaleDeal(
        title="x", url="", posted=None, expiry="Sat, 31 Dec 2050 23:59:00 +1100"
    )
    assert deal.is_expired is False
    assert datetime.now(UTC).tzinfo is not None
