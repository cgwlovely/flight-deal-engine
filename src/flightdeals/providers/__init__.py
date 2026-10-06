"""Fare providers.

A provider turns a route + dates into priced itineraries. Everything downstream
(scoring, history, reports) only knows about `Itinerary`, so adding a new source
means implementing `FareProvider.search` and nothing else.
"""

from __future__ import annotations

from .base import FareProvider, ProviderError, SearchRequest
from .google_flights import GoogleFlightsProvider

_REGISTRY: dict[str, type[FareProvider]] = {
    GoogleFlightsProvider.name: GoogleFlightsProvider,
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def build(name: str, **kwargs) -> FareProvider:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ProviderError(
            f"unknown provider {name!r}; available: {', '.join(available())}"
        ) from None
    return cls(**kwargs)


def register(cls: type[FareProvider]) -> type[FareProvider]:
    """Decorator so out-of-tree providers can plug themselves in."""
    _REGISTRY[cls.name] = cls
    return cls


__all__ = [
    "FareProvider",
    "GoogleFlightsProvider",
    "ProviderError",
    "SearchRequest",
    "available",
    "build",
    "register",
]
