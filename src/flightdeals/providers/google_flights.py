"""Google Flights provider.

Builds the same ``?tfs`` protobuf query the website uses (via `fast-flights`),
fetches the search page, and reads the itineraries straight out of the embedded
JSON payload.

Why a custom parser instead of `fast_flights.parse`: the page carries two result
lists -- the "best" shortlist and the "other flights" list -- and the library
only returns the first. For a deal engine the cheapest option matters more than
Google's idea of "best", so this reads both and hands back the union.

No API key is needed, which also means no service guarantee: be gentle with
request volume (see `scanner.RateLimiter`) and expect the payload shape to shift
one day. `PAYLOAD_SECTIONS` is the single place to adjust if it does.
"""

from __future__ import annotations

import json
from datetime import datetime

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

from ..models import Itinerary, Leg
from .base import NoFlightsFound, ProviderError, SearchRequest

#: Indices of the itinerary lists inside the page payload: "other", then "best".
PAYLOAD_SECTIONS = (2, 3)


def _extract_json(blob: str) -> str:
    """Pull the complete JSON array that follows ``data:`` out of the page script.

    Splitting on ``data:`` and then trimming the last comma is the usual shortcut,
    and it breaks whenever the trailing arguments differ (``sideChannel`` present
    or not, nested commas inside strings). Instead, walk from the opening bracket
    and stop when it closes, skipping over string literals so that brackets and
    commas inside them cannot throw the count off.
    """
    marker = blob.find("data:")
    if marker == -1:
        raise ProviderError("no data: marker in page payload")
    start = blob.find("[", marker)
    if start == -1:
        raise ProviderError("no JSON array after data: marker")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(blob)):
        char = blob[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return blob[start : index + 1]
    raise ProviderError("unterminated JSON array in page payload")


def _time(value: list[int | None] | None) -> tuple[int, int]:
    """Google omits zero components: ``[8]`` is 08:00 and ``[None, 31]`` is 00:31."""
    padded = [*(value or []), None, None]
    return (padded[0] or 0, padded[1] or 0)


def _datetime(day: list[int] | None, clock: list[int | None] | None) -> datetime | None:
    if not day or len(day) < 3:
        return None
    hour, minute = _time(clock)
    try:
        return datetime(day[0], day[1], day[2], hour, minute)
    except (TypeError, ValueError):
        return None


class GoogleFlightsProvider:
    """Fetch live fares from Google Flights' public search page."""

    name = "google-flights"

    def __init__(self, *, proxy: str | None = None, language: str = "en") -> None:
        self.proxy = proxy
        self.language = language

    # -- public API ---------------------------------------------------------
    def search(self, request: SearchRequest) -> list[Itinerary]:
        query = self._query(request)
        try:
            html = fetch_flights_html(query, proxy=self.proxy)
        except Exception as exc:  # network / TLS / impersonation failures
            raise ProviderError(f"fetch failed for {request.label()}: {exc}") from exc
        return self._parse(html, request)

    def search_url(self, request: SearchRequest) -> str:
        """A human-clickable link to the same search, for reports."""
        return self._query(request).url()

    # -- internals ----------------------------------------------------------
    def _query(self, request: SearchRequest):
        flights = [
            FlightQuery(
                date=request.depart_date.isoformat(),
                from_airport=request.origin,
                to_airport=request.destination,
                max_stops=request.max_stops,
                max_duration_minutes=request.max_duration_minutes,
                airlines=list(request.airlines) or None,
            )
        ]
        if request.return_date:
            flights.append(
                FlightQuery(
                    date=request.return_date.isoformat(),
                    from_airport=request.destination,
                    to_airport=request.origin,
                    max_stops=request.max_stops,
                    max_duration_minutes=request.max_duration_minutes,
                    airlines=list(request.airlines) or None,
                )
            )
        return create_query(
            flights=flights,
            seat=request.seat,
            trip=request.trip,
            passengers=Passengers(adults=request.adults, children=request.children),
            currency=request.currency,
            language=self.language,
            hide_separate_and_self_transfer=request.hide_self_transfer,
            carry_on_bags=request.carry_on_bags,
            checked_bags=request.checked_bags,
        )

    def _parse(self, html: str, request: SearchRequest) -> list[Itinerary]:
        parser = LexborHTMLParser(html)
        script = parser.css_first(r"script.ds\:1")
        if script is None:
            raise ProviderError(
                f"no result payload in page for {request.label()} "
                "(rate limited, consent wall, or page layout changed)"
            )
        blob = script.text()
        if "errorHasStatus" in blob:
            raise NoFlightsFound(f"no flights for {request.label()}")
        try:
            payload = json.loads(_extract_json(blob))
        except ValueError as exc:
            raise ProviderError(f"unreadable payload for {request.label()}: {exc}") from exc

        itineraries: list[Itinerary] = []
        for section in PAYLOAD_SECTIONS:
            for entry in self._section(payload, section):
                parsed = self._itinerary(entry, request)
                if parsed is not None:
                    itineraries.append(parsed)

        if not itineraries:
            raise NoFlightsFound(f"no flights for {request.label()}")
        return self._dedupe(itineraries)

    @staticmethod
    def _section(payload, index: int) -> list:
        try:
            return payload[index][0] or []
        except (IndexError, KeyError, TypeError):
            return []

    def _itinerary(self, entry, request: SearchRequest) -> Itinerary | None:
        try:
            flight, price_block = entry[0], entry[1]
            price = float(price_block[0][1])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
        if price <= 0:
            return None

        airlines = [a for a in (flight[1] or []) if a]
        legs: list[Leg] = []
        for seg in flight[2] or []:
            legs.append(
                Leg(
                    from_code=seg[3],
                    to_code=seg[6],
                    depart=_datetime(seg[20], seg[8]),
                    arrive=_datetime(seg[21], seg[10]),
                    duration_min=seg[11],
                    aircraft=seg[17] or "",
                )
            )

        carbon = typical = None
        try:
            extras = flight[22]
            carbon, typical = extras[7], extras[8]
        except (IndexError, KeyError, TypeError):
            pass

        return Itinerary(
            origin=request.origin,
            destination=request.destination,
            depart_date=request.depart_date,
            return_date=request.return_date,
            price=price,
            currency=request.currency,
            airlines=airlines,
            legs=legs,
            carbon_g=carbon,
            typical_carbon_g=typical,
            source=self.name,
        )

    @staticmethod
    def _dedupe(itineraries: list[Itinerary]) -> list[Itinerary]:
        """The two page sections can overlap; keep one row per price+routing."""
        seen: set[tuple] = set()
        unique: list[Itinerary] = []
        for it in sorted(itineraries, key=lambda i: i.price):
            fingerprint = (
                it.price,
                tuple(it.airlines),
                tuple((leg.from_code, leg.to_code, leg.depart) for leg in it.legs),
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(it)
        return unique


__all__ = ["PAYLOAD_SECTIONS", "GoogleFlightsProvider"]
