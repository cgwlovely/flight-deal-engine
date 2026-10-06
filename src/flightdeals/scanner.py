"""Fan a search out across many destinations and date windows."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from . import catalog
from .history import PriceHistory, window_label
from .models import Deal, Itinerary, Quote
from .providers.base import FareProvider, NoFlightsFound, ProviderError, SearchRequest
from .scoring import Context, score_all


class RateLimiter:
    """Minimum spacing between requests, with jitter.

    The Google Flights provider has no quota to respect, which is exactly why it
    needs one imposed here: a 120-destination scan at full tilt looks like abuse
    and gets the IP throttled halfway through.
    """

    def __init__(self, min_interval: float = 0.8, jitter: float = 0.6) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            target = max(now, self._next_at)
            self._next_at = target + self.min_interval + random.uniform(0, self.jitter)
        delay = target - now
        if delay > 0:
            time.sleep(delay)


@dataclass(frozen=True)
class Window:
    """A departure/return pair with a human name."""

    depart: date
    ret: date | None = None
    name: str = ""

    @property
    def label(self) -> str:
        return window_label(self.depart, self.ret)

    @property
    def nights(self) -> int | None:
        return (self.ret - self.depart).days if self.ret else None

    def describe(self) -> str:
        if self.ret is None:
            return f"{self.name or 'one-way'} ({self.depart})"
        return f"{self.name or 'window'} ({self.depart} -> {self.ret}, {self.nights}n)"


@dataclass
class ScanSpec:
    origin: str
    destinations: list[str]
    windows: list[Window]
    adults: int = 1
    children: int = 0
    seat: str = "economy"
    currency: str = "AUD"
    max_stops: int | None = None
    max_duration_minutes: int | None = None

    def requests(self) -> list[tuple[Window, SearchRequest]]:
        out = []
        for window in self.windows:
            for dest in self.destinations:
                out.append(
                    (
                        window,
                        SearchRequest(
                            origin=self.origin,
                            destination=dest,
                            depart_date=window.depart,
                            return_date=window.ret,
                            adults=self.adults,
                            children=self.children,
                            seat=self.seat,
                            currency=self.currency,
                            max_stops=self.max_stops,
                            max_duration_minutes=self.max_duration_minutes,
                        ),
                    )
                )
        return out


@dataclass
class Failure:
    request: SearchRequest
    reason: str
    no_flights: bool = False


@dataclass
class ScanResult:
    spec: ScanSpec
    started_at: datetime
    finished_at: datetime
    deals_by_window: dict[str, list[Deal]] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)

    @property
    def windows(self) -> list[Window]:
        return self.spec.windows

    def window_by_label(self, label: str) -> Window | None:
        return next((w for w in self.spec.windows if w.label == label), None)

    @property
    def all_deals(self) -> list[Deal]:
        return [d for deals in self.deals_by_window.values() for d in deals]

    def best_per_destination(self) -> list[Deal]:
        """One row per destination: its cheapest window."""
        best: dict[str, Deal] = {}
        for deal in self.all_deals:
            dest = deal.itinerary.destination
            if dest not in best or deal.itinerary.price < best[dest].itinerary.price:
                best[dest] = deal
        return sorted(best.values(), key=lambda d: d.deal_score, reverse=True)

    @property
    def quoted(self) -> int:
        return len(self.all_deals)


def scan(
    spec: ScanSpec,
    provider: FareProvider,
    *,
    history: PriceHistory | None = None,
    workers: int = 4,
    attempts: int = 3,
    limiter: RateLimiter | None = None,
    on_result: Callable[[SearchRequest, Quote | None, str | None], None] | None = None,
) -> ScanResult:
    """Price every destination x window in `spec` and rank the results.

    Scoring happens per window so that a 7-night and a 15-night trip are never
    ranked against each other.
    """
    limiter = limiter or RateLimiter()
    started = datetime.now(UTC)
    jobs = spec.requests()
    quotes: dict[str, list[Quote]] = {w.label: [] for w in spec.windows}
    failures: list[Failure] = []
    lock = threading.Lock()

    def run(job: tuple[Window, SearchRequest]):
        window, request = job
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            limiter.wait()
            try:
                itineraries = provider.search(request)
            except NoFlightsFound as exc:
                return window, request, None, str(exc), True
            except ProviderError as exc:
                last = exc
                if attempt < attempts:
                    time.sleep(1.5 * attempt + random.uniform(0, 1.0))
                continue
            except Exception as exc:  # a provider bug should not kill the scan
                last = exc
                break
            else:
                quote = _to_quote(itineraries)
                return window, request, quote, None, False
        return window, request, None, str(last), False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, job) for job in jobs]
        for future in as_completed(futures):
            window, request, quote, error, no_flights = future.result()
            with lock:
                if quote is not None:
                    quotes[window.label].append(quote)
                else:
                    failures.append(
                        Failure(request=request, reason=error or "unknown", no_flights=no_flights)
                    )
            if on_result is not None:
                on_result(request, quote, error)

    if history is not None:
        for window in spec.windows:
            history.record(
                [q.cheapest for q in quotes[window.label]], window_label=window.label
            )

    deals_by_window = {
        window.label: _rank(quotes[window.label], spec, window, history, started)
        for window in spec.windows
    }
    return ScanResult(
        spec=spec,
        started_at=started,
        finished_at=datetime.now(UTC),
        deals_by_window=deals_by_window,
        failures=failures,
    )


def _to_quote(itineraries: Iterable[Itinerary]) -> Quote:
    ordered = sorted(itineraries, key=lambda it: it.price)
    return Quote(cheapest=ordered[0], alternatives=ordered[1:])


def _rank(
    window_quotes: list[Quote],
    spec: ScanSpec,
    window: Window,
    history: PriceHistory | None,
    started: datetime,
) -> list[Deal]:
    origin = catalog.get(spec.origin)
    pairs: list[tuple[Quote, Context]] = []
    for quote in window_quotes:
        dest = catalog.get(quote.cheapest.destination)
        median = observations = None
        if history is not None:
            median, observations = history.baseline(
                spec.origin,
                quote.cheapest.destination,
                window_label=window.label,
                before=started,
            )
        pairs.append(
            (
                quote,
                Context(
                    distance_km=origin.distance_km(dest),
                    median_price=median,
                    observations=observations or 0,
                ),
            )
        )
    return score_all(pairs)
