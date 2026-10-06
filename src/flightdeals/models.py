"""Core data types shared by providers, the scanner and the reporters."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime


@dataclass(frozen=True)
class Airport:
    code: str
    name: str
    city: str = ""
    country: str = ""
    region: str = ""
    lat: float | None = None
    lon: float | None = None

    def distance_km(self, other: Airport) -> float | None:
        """Great-circle distance, or None if either side lacks coordinates."""
        if None in (self.lat, self.lon, other.lat, other.lon):
            return None
        lat1, lon1, lat2, lon2 = map(
            math.radians, (self.lat, self.lon, other.lat, other.lon)
        )
        dlat, dlon = lat2 - lat1, lon2 - lon1
        h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 2 * 6371.0088 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Leg:
    """One physical flight segment."""

    from_code: str
    to_code: str
    depart: datetime | None
    arrive: datetime | None
    duration_min: int | None
    aircraft: str = ""


@dataclass
class Itinerary:
    """A priced round-trip (or one-way) option returned by a provider."""

    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price: float
    currency: str
    airlines: list[str] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)
    carbon_g: int | None = None
    typical_carbon_g: int | None = None
    source: str = "unknown"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def outbound(self) -> list[Leg]:
        """Legs before the first one that heads back towards the origin."""
        out: list[Leg] = []
        for leg in self.legs:
            out.append(leg)
            if leg.to_code == self.destination:
                break
        return out

    @property
    def inbound(self) -> list[Leg]:
        return self.legs[len(self.outbound) :]

    @property
    def stops_out(self) -> int:
        return max(len(self.outbound) - 1, 0)

    @property
    def stops_back(self) -> int | None:
        return max(len(self.inbound) - 1, 0) if self.inbound else None

    @property
    def duration_out_min(self) -> int | None:
        mins = [leg.duration_min for leg in self.outbound if leg.duration_min]
        return sum(mins) if mins else None

    @property
    def nights(self) -> int | None:
        if self.return_date is None:
            return None
        return (self.return_date - self.depart_date).days

    @property
    def is_nonstop(self) -> bool:
        return self.stops_out == 0 and (self.stops_back in (0, None))

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.origin,
            self.destination,
            self.depart_date.isoformat(),
            self.return_date.isoformat() if self.return_date else "",
        )


@dataclass
class Quote:
    """The cheapest itinerary found for one origin/destination/date combination.

    `alternatives` keeps the rest of what the provider returned so a report can
    show "cheapest, but there is a nonstop for $120 more".
    """

    cheapest: Itinerary
    alternatives: list[Itinerary] = field(default_factory=list)

    @property
    def cheapest_nonstop(self) -> Itinerary | None:
        options = [self.cheapest, *self.alternatives]
        nonstop = [it for it in options if it.is_nonstop]
        return min(nonstop, key=lambda it: it.price) if nonstop else None


@dataclass
class Deal:
    """A quote with its ranking signals attached."""

    quote: Quote
    distance_km: float | None = None
    cents_per_km: float | None = None
    price_score: float | None = None
    cpk_score: float | None = None
    history_score: float | None = None
    median_price: float | None = None
    discount_pct: float | None = None
    observations: int = 0
    deal_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def itinerary(self) -> Itinerary:
        return self.quote.cheapest
