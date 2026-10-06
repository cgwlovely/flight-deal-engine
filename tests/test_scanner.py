from datetime import date

from flightdeals.history import PriceHistory
from flightdeals.models import Itinerary, Leg
from flightdeals.providers.base import NoFlightsFound, ProviderError
from flightdeals.scanner import RateLimiter, ScanSpec, Window, scan

WINDOWS = [
    Window(date(2026, 12, 20), date(2027, 1, 4), name="peak"),
    Window(date(2026, 12, 23), date(2026, 12, 30), name="short"),
]
NO_WAIT = RateLimiter(min_interval=0, jitter=0)


class FakeProvider:
    """Prices routes from a table; raises for anything not in it."""

    name = "fake"

    def __init__(self, prices, *, flaky: set[str] | None = None):
        self.prices = prices
        self.flaky = dict.fromkeys(flaky or (), 0)
        self.calls: list[str] = []

    def search(self, request):
        self.calls.append(request.label())
        if request.destination in self.flaky:
            self.flaky[request.destination] += 1
            if self.flaky[request.destination] == 1:
                raise ProviderError("transient")
        try:
            base = self.prices[request.destination]
        except KeyError:
            raise NoFlightsFound("nothing flies there") from None
        price = base if request.return_date == date(2027, 1, 4) else base * 0.8
        return [
            Itinerary(
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=price,
                currency="AUD",
                airlines=["QF"],
                legs=[Leg(request.origin, request.destination, None, None, 400)],
                source=self.name,
            )
        ]


def spec(destinations, windows=WINDOWS):
    return ScanSpec(origin="BNE", destinations=destinations, windows=windows)


def test_scan_prices_every_destination_and_window():
    provider = FakeProvider({"SIN": 1200, "NRT": 1500, "AKL": 600})
    result = scan(spec(["SIN", "NRT", "AKL"]), provider, workers=2, limiter=NO_WAIT)
    assert len(provider.calls) == 6
    assert result.quoted == 6
    assert not result.failures
    for window in WINDOWS:
        assert len(result.deals_by_window[window.label]) == 3


def test_unservable_routes_land_in_failures():
    provider = FakeProvider({"SIN": 1200})
    result = scan(spec(["SIN", "ZZZ"], windows=WINDOWS[:1]), provider, limiter=NO_WAIT)
    assert result.quoted == 1
    assert [f.request.destination for f in result.failures] == ["ZZZ"]
    assert result.failures[0].no_flights is True


def test_transient_errors_are_retried():
    provider = FakeProvider({"SIN": 1200}, flaky={"SIN"})
    result = scan(spec(["SIN"], windows=WINDOWS[:1]), provider, attempts=3, limiter=NO_WAIT)
    assert result.quoted == 1
    assert len(provider.calls) == 2


def test_retries_give_up_and_report():
    provider = FakeProvider({"SIN": 1200}, flaky={"SIN"})
    result = scan(spec(["SIN"], windows=WINDOWS[:1]), provider, attempts=1, limiter=NO_WAIT)
    assert result.quoted == 0
    assert result.failures[0].no_flights is False


def test_best_per_destination_picks_the_cheaper_window():
    provider = FakeProvider({"SIN": 1000})
    result = scan(spec(["SIN"]), provider, limiter=NO_WAIT)
    (best,) = result.best_per_destination()
    assert best.itinerary.price == 800  # the short window, priced at 0.8x


def test_scoring_uses_real_distances():
    provider = FakeProvider({"AKL": 900, "LHR": 2000})
    result = scan(spec(["AKL", "LHR"], windows=WINDOWS[:1]), provider, limiter=NO_WAIT)
    deals = {d.itinerary.destination: d for d in result.deals_by_window[WINDOWS[0].label]}
    assert deals["LHR"].cents_per_km < deals["AKL"].cents_per_km
    assert deals["LHR"].deal_score > deals["AKL"].deal_score


def test_history_is_written_and_then_used_as_a_baseline(tmp_path):
    db = tmp_path / "h.db"
    label = WINDOWS[0].label
    with PriceHistory(db) as hist:
        for _ in range(3):
            scan(spec(["SIN"], windows=WINDOWS[:1]), FakeProvider({"SIN": 2000}),
                 history=hist, limiter=NO_WAIT)
        median, n = hist.baseline("BNE", "SIN", window_label=label)
        assert (median, n) == (2000.0, 3)

        result = scan(spec(["SIN"], windows=WINDOWS[:1]), FakeProvider({"SIN": 1000}),
                      history=hist, limiter=NO_WAIT)
        (deal,) = result.deals_by_window[label]
        assert deal.observations == 3
        assert deal.discount_pct == 50.0


def test_rate_limiter_spaces_requests():
    import time

    limiter = RateLimiter(min_interval=0.05, jitter=0)
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 0.1
