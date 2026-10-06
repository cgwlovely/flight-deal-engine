from datetime import UTC, date, datetime, timedelta

from flightdeals.history import PriceHistory, window_label
from flightdeals.models import Itinerary


def itin(price, dest="SIN"):
    return Itinerary(
        origin="BNE",
        destination=dest,
        depart_date=date(2026, 12, 20),
        return_date=date(2027, 1, 4),
        price=price,
        currency="AUD",
        source="test",
    )


def test_baseline_is_median_of_recorded_prices(tmp_path):
    label = window_label(date(2026, 12, 20), date(2027, 1, 4))
    with PriceHistory(tmp_path / "h.db") as hist:
        assert hist.record([itin(1000), itin(1400), itin(1200)], window_label=label) == 3
        median, n = hist.baseline("BNE", "SIN", window_label=label)
        assert (median, n) == (1200.0, 3)


def test_baseline_ignores_other_routes_and_windows(tmp_path):
    label = window_label(date(2026, 12, 20), date(2027, 1, 4))
    with PriceHistory(tmp_path / "h.db") as hist:
        hist.record([itin(1000)], window_label=label)
        hist.record([itin(9000, dest="LHR")], window_label=label)
        hist.record([itin(50)], window_label="other-window")
        assert hist.baseline("BNE", "SIN", window_label=label) == (1000.0, 1)
        assert hist.baseline("BNE", "NRT", window_label=label) == (None, 0)


def test_before_cutoff_excludes_the_current_scan(tmp_path):
    """A fresh price must not be allowed to move its own baseline."""
    label = window_label(date(2026, 12, 20), date(2027, 1, 4))
    with PriceHistory(tmp_path / "h.db") as hist:
        hist.record([itin(1000)], window_label=label)
        future = datetime.now(UTC) - timedelta(hours=1)
        assert hist.baseline("BNE", "SIN", window_label=label, before=future) == (None, 0)


def test_reopening_the_database_keeps_observations(tmp_path):
    db = tmp_path / "h.db"
    with PriceHistory(db) as hist:
        hist.record([itin(1000)], window_label="w")
    with PriceHistory(db) as hist:
        assert len(hist.series("BNE", "SIN", window_label="w")) == 1
        assert hist.scans()[0][1] == 1
