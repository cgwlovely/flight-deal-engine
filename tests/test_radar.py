from datetime import UTC, date, datetime, timedelta

from flightdeals import radar
from flightdeals.history import PriceHistory
from flightdeals.models import Itinerary, Leg
from flightdeals.scanner import RateLimiter, ScanSpec, Window, scan

WINDOW = Window(date(2027, 3, 15), date(2027, 4, 5), name="custom")
NO_WAIT = RateLimiter(min_interval=0, jitter=0)


class FixedProvider:
    name = "fake"

    def __init__(self, price):
        self.price = price

    def search(self, request):
        return [
            Itinerary(
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=self.price,
                currency="AUD",
                airlines=["QR"],
                legs=[Leg(request.origin, request.destination, None, None, 1300)],
                source="fake",
            )
        ]


def run(price, history, dest="FCO"):
    return scan(
        ScanSpec(origin="BNE", destinations=[dest], windows=[WINDOW]),
        FixedProvider(price),
        history=history,
        limiter=NO_WAIT,
    )


def seed(history, prices, dest="FCO"):
    """Record prior observations stamped in the past so a cutoff can exclude today."""
    for i, price in enumerate(prices):
        history.record(
            [
                Itinerary(
                    origin="BNE",
                    destination=dest,
                    depart_date=WINDOW.depart,
                    return_date=WINDOW.ret,
                    price=price,
                    currency="AUD",
                    fetched_at=datetime.now(UTC) - timedelta(days=10 - i),
                )
            ],
            window_label=WINDOW.label,
        )


def test_thin_history_is_watching_not_an_alert(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2100])
        findings = radar.detect(run(900, h), h)
        assert [f.kind for f in findings] == ["watching"]
        assert radar.alerts(findings) == []
        assert "2/3 observations" in findings[0].message()


def test_price_below_median_raises_a_below_baseline_alert(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        # A past low of 1500 keeps 1600 from counting as a record, so the median
        # rule is what has to fire.
        seed(h, [2400, 2000, 2000, 1500])
        (finding,) = radar.detect(run(1600, h), h)
        assert finding.kind == "below-baseline"
        assert finding.pct_below_median == 20.0
        assert finding.observations == 4
        assert "20% below its median" in finding.message()


def test_a_new_low_is_reported_as_a_record_even_below_threshold(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 1900, 1850])
        (finding,) = radar.detect(run(1700, h), h)
        assert finding.kind == "record-low"
        assert finding.minimum == 1850
        assert "lowest of 4 observations" in finding.message()


def test_a_trivial_new_low_is_not_an_alert(tmp_path):
    """One dollar under a flat history is not news, and must not page anyone."""
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000])
        # One scan, judged twice: a second run() would record 1999 into history and
        # become its own baseline.
        result = run(1999, h)
        assert radar.detect(result, h) == []
        assert radar.detect(result, h, record_margin_pct=0) != []


def test_an_ordinary_price_produces_no_finding_at_all(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000])
        assert radar.detect(run(1950, h), h) == []


def test_a_dearer_price_is_never_an_alert(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000])
        assert radar.detect(run(3000, h), h) == []


def test_threshold_is_configurable(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000])
        assert radar.detect(run(1800, h), h, threshold_pct=15) != []
        assert radar.detect(run(1800, h), h, threshold_pct=25) == []


def test_the_scan_under_test_is_excluded_from_its_own_baseline(tmp_path):
    """Without the cutoff, today's price joins the median and nothing looks odd."""
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000])
        result = run(1000, h)  # this scan records 1000 before detect() runs
        (finding,) = radar.detect(result, h)
        assert finding.median == 2000, "the fresh 1000 must not drag the median down"
        assert finding.observations == 3


def test_record_lows_sort_above_shallower_dips(tmp_path):
    with PriceHistory(tmp_path / "h.db") as h:
        seed(h, [2000, 2000, 2000], dest="FCO")
        seed(h, [1000, 1000, 1000], dest="BCN")
        result = scan(
            ScanSpec(origin="BNE", destinations=["FCO", "BCN"], windows=[WINDOW]),
            FixedProvider(800),
            history=h,
            limiter=NO_WAIT,
        )
        findings = radar.detect(result, h)
        # Both are record lows, so the deeper discount leads: FCO is 60% under its
        # median against BCN's 20%.
        assert [f.deal.itinerary.destination for f in findings] == ["FCO", "BCN"]
        assert findings[0].kind == "record-low"


def test_notify_is_a_no_op_for_an_empty_alert_list():
    assert radar.notify_macos([]) is False
