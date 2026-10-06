"""Provider interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from ..models import Itinerary


class ProviderError(RuntimeError):
    """A provider could not answer; the scanner decides whether to retry."""


class NoFlightsFound(ProviderError):
    """The provider answered, and the answer was 'nothing flies'."""


@dataclass(frozen=True)
class SearchRequest:
    origin: str
    destination: str
    depart_date: date
    return_date: date | None = None
    adults: int = 1
    children: int = 0
    seat: str = "economy"
    currency: str = "AUD"
    max_stops: int | None = None
    max_duration_minutes: int | None = None
    airlines: tuple[str, ...] = ()
    """Restrict results to these marketing carriers (IATA codes). Empty = no filter."""
    hide_self_transfer: bool = False
    """Drop separate-ticket and self-transfer itineraries, where a missed connection
    is the passenger's own problem."""
    carry_on_bags: int = 0
    checked_bags: int = 0
    """Bags to price in, so a headline fare is not compared against one that bundles
    luggage."""

    @property
    def trip(self) -> str:
        return "round-trip" if self.return_date else "one-way"

    def label(self) -> str:
        tail = f" -> {self.return_date}" if self.return_date else ""
        carriers = f" [{'/'.join(self.airlines)}]" if self.airlines else ""
        return f"{self.origin}-{self.destination} {self.depart_date}{tail}{carriers}"


@runtime_checkable
class FareProvider(Protocol):
    name: str

    def search(self, request: SearchRequest) -> list[Itinerary]:
        """Return every itinerary the source offers, cheapest-first is not required."""
        ...
