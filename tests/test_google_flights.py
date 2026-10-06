"""Parser tests against a synthetic copy of the page payload.

These pin the payload indices the provider depends on. If Google changes the
shape of its embedded JSON, the real fix is in `PAYLOAD_SECTIONS` / `_itinerary`
and these fixtures move with it.
"""

import json
from datetime import date

import pytest

from flightdeals.providers.base import NoFlightsFound, ProviderError, SearchRequest
from flightdeals.providers.google_flights import GoogleFlightsProvider

REQUEST = SearchRequest(
    origin="BNE",
    destination="SIN",
    depart_date=date(2026, 12, 20),
    return_date=date(2027, 1, 4),
    currency="AUD",
)


def segment(frm, to, dep_date, dep_time, dur):
    seg = [None] * 23
    seg[3], seg[4] = frm, f"{frm} Airport"
    seg[5], seg[6] = f"{to} Airport", to
    seg[8] = dep_time
    seg[10] = [(dep_time[0] or 0) + dur // 60, dur % 60]
    seg[11] = dur
    seg[17] = "Boeing 787"
    seg[20] = list(dep_date)
    seg[21] = list(dep_date)
    return seg


def entry(price, airlines, segments):
    flight = [None] * 23
    flight[0] = "SQ"
    flight[1] = airlines
    flight[2] = segments
    extras = [None] * 9
    extras[7], extras[8] = 1_200_000, 1_500_000
    flight[22] = extras
    return [flight, [[None, price]]]


def page(best, other):
    payload = [None, None, [other], [best], None, None, None, [None, [[], []]]]
    return (
        '<html><body><script class="ds:1" nonce="x">'
        'AF_initDataCallback({key: "ds:1", hash: "1", data:'
        + json.dumps(payload)
        + ", sideChannel: {}});</script></body></html>"
    )


def parse(html):
    return GoogleFlightsProvider()._parse(html, REQUEST)


def test_reads_both_best_and_other_sections():
    html = page(
        best=[entry(2047, ["Air Niugini"], [segment("BNE", "SIN", (2026, 12, 20), [9, 25], 190)])],
        other=[entry(1680, ["Scoot"], [segment("BNE", "SIN", (2026, 12, 20), [23, 50], 480)])],
    )
    itineraries = parse(html)
    assert [it.price for it in itineraries] == [1680.0, 2047.0], "cheapest first, both sections"
    assert itineraries[0].airlines == ["Scoot"]


def test_leg_fields_and_round_trip_split():
    out = segment("BNE", "SIN", (2026, 12, 20), [9, 25], 480)
    back = segment("SIN", "BNE", (2027, 1, 4), [20, 5], 455)
    (it,) = parse(page(best=[entry(1500, ["SQ"], [out, back])], other=[]))
    assert it.stops_out == 0 and it.stops_back == 0
    assert it.is_nonstop
    assert it.duration_out_min == 480
    assert it.nights == 15
    assert it.legs[0].depart.hour == 9 and it.legs[0].depart.minute == 25
    assert it.legs[0].aircraft == "Boeing 787"
    assert it.carbon_g == 1_200_000


def test_counts_stops_on_a_connecting_itinerary():
    legs = [
        segment("BNE", "DRW", (2026, 12, 20), [6, 0], 230),
        segment("DRW", "SIN", (2026, 12, 20), [11, 30], 275),
        segment("SIN", "BNE", (2027, 1, 4), [20, 0], 455),
    ]
    (it,) = parse(page(best=[entry(900, ["Jetstar"], legs)], other=[]))
    assert it.stops_out == 1
    assert it.stops_back == 0
    assert not it.is_nonstop


def test_times_with_omitted_components():
    seg = segment("BNE", "SIN", (2026, 12, 20), [None, 31], 300)
    (it,) = parse(page(best=[entry(1000, ["TR"], [seg])], other=[]))
    assert (it.legs[0].depart.hour, it.legs[0].depart.minute) == (0, 31)


def test_duplicate_itineraries_collapse():
    seg = segment("BNE", "SIN", (2026, 12, 20), [9, 25], 480)
    same = entry(1500, ["SQ"], [seg])
    assert len(parse(page(best=[same], other=[same]))) == 1


def test_unpriced_rows_are_skipped_not_fatal():
    broken = [[None] * 23, []]
    good = entry(1200, ["SQ"], [segment("BNE", "SIN", (2026, 12, 20), [9, 0], 480)])
    assert len(parse(page(best=[broken, good], other=[]))) == 1


def test_empty_result_raises_no_flights():
    with pytest.raises(NoFlightsFound):
        parse(page(best=[], other=[]))


def test_missing_payload_script_raises_provider_error():
    with pytest.raises(ProviderError, match="no result payload"):
        parse("<html><body>consent wall</body></html>")


def test_search_url_round_trip_is_wellformed():
    url = GoogleFlightsProvider().search_url(REQUEST)
    assert url.startswith("https://www.google.com/travel/flights/search?tfs=")
    assert "curr=AUD" in url
