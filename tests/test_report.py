import csv
import json
from datetime import date

from flightdeals import report
from flightdeals.models import Itinerary, Leg
from flightdeals.providers.base import NoFlightsFound
from flightdeals.scanner import RateLimiter, ScanSpec, Window, scan

WINDOW = Window(date(2026, 12, 20), date(2027, 1, 4), name="peak")


class Provider:
    name = "fake"

    def search(self, request):
        prices = {"SIN": 1200, "NRT": 1500}
        if request.destination not in prices:
            raise NoFlightsFound("nothing flies there")
        return [
            Itinerary(
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=prices[request.destination],
                currency="AUD",
                airlines=["Singapore Airlines"],
                legs=[Leg(request.origin, request.destination, None, None, 470)],
                source="fake",
            )
        ]


def make_result():
    return scan(
        ScanSpec(origin="BNE", destinations=["SIN", "NRT", "ZZZ"], windows=[WINDOW]),
        Provider(),
        limiter=RateLimiter(min_interval=0, jitter=0),
    )


def test_write_all_produces_every_format(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    assert set(written) == {"json", "markdown", "html", "csv"}
    assert all(p.exists() and p.stat().st_size > 0 for p in written.values())


def test_json_carries_prices_and_failures(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    payload = json.loads(written["json"].read_text())
    assert payload["origin"] == "BNE"
    assert payload["quotes"] == 2
    (window,) = payload["windows"]
    assert {d["code"] for d in window["deals"]} == {"SIN", "NRT"}
    assert payload["failures"][0]["no_flights"] is True


def test_csv_has_one_row_per_destination(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    rows = list(csv.DictReader(written["csv"].open()))
    # SIN is both cheaper and lower cents/km than NRT, so it ranks first.
    assert [r["code"] for r in rows] == ["SIN", "NRT"]
    assert rows[0]["city"] == "Singapore"


def test_html_is_self_contained_and_theme_aware(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    page = written["html"].read_text()
    assert "<title>" in page and "prefers-color-scheme" in page
    assert "http://" not in page and "src=" not in page, "no external assets"
    assert "A$1,200" in page


def test_markdown_lists_unpriced_routes(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    md = written["markdown"].read_text()
    assert "## Not priced" in md
    assert "BNE-ZZZ" in md


class SeasonalProvider:
    """Cheap in April, dear in December, and not flying at all in February."""

    name = "fake"

    def search(self, request):
        month = request.depart_date.month
        if month == 2:
            raise NoFlightsFound("seasonal route, not operating")
        price = {4: 600}.get(month, 1800 if month == 12 else 1000)
        return [
            Itinerary(
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=price if request.destination == "LDH" else price * 2,
                currency="AUD",
                airlines=["QantasLink"],
                legs=[Leg(request.origin, request.destination, None, None, 120)],
                source="fake",
            )
        ]


def seasonal_result():
    from flightdeals import windows

    return scan(
        ScanSpec(
            origin="BNE",
            destinations=["LDH", "NAN"],
            windows=windows.monthly(months=12, nights=7, today=date(2026, 10, 6)),
        ),
        SeasonalProvider(),
        workers=4,
        limiter=RateLimiter(min_interval=0, jitter=0),
    )


def test_matrix_is_rectangular_with_a_row_per_window():
    codes, rows = report.matrix_rows(seasonal_result())
    assert codes == ["LDH", "NAN"]  # ordered by median price, cheapest first
    assert len(rows) == 12
    assert len({tuple(r.keys()) for r in rows}) == 1, "every row has the same columns"


def test_matrix_marks_the_cheapest_month_and_blanks_unflown_ones():
    _, rows = report.matrix_rows(seasonal_result())
    by_month = {r["window"]: r for r in rows}
    assert by_month["2027-04"]["LDH"] == 600
    assert by_month["2026-12"]["LDH"] == 1800
    assert by_month["2027-02"]["LDH"] == "", "no flights leaves the cell empty, not zero"
    assert by_month["2027-04"]["cheapest"] == "LDH"


def test_matrix_csv_is_written_for_multi_window_scans(tmp_path):
    written = report.write_all(seasonal_result(), tmp_path, stamp="fixed")
    assert "matrix" in written
    rows = list(csv.DictReader(written["matrix"].open()))
    assert rows[0]["window"] == "2026-11"
    assert "LDH" in rows[0] and "NAN" in rows[0]


class HorizonProvider:
    """Prices nothing past a cutoff, the way an unloaded schedule behaves."""

    name = "fake"

    def __init__(self, cutoff):
        self.cutoff = cutoff

    def search(self, request):
        if request.depart_date >= self.cutoff:
            raise NoFlightsFound("no flights")
        return [
            Itinerary(
                origin=request.origin,
                destination=request.destination,
                depart_date=request.depart_date,
                return_date=request.return_date,
                price=1000,
                currency="AUD",
                legs=[Leg(request.origin, request.destination, None, None, 120)],
                source="fake",
            )
        ]


def horizon_result():
    from flightdeals import windows

    return scan(
        ScanSpec(
            origin="BNE",
            destinations=["NAN", "VLI", "APW"],
            windows=windows.monthly(months=12, nights=7, today=date(2026, 10, 6)),
        ),
        HorizonProvider(cutoff=date(2027, 6, 1)),
        limiter=RateLimiter(min_interval=0, jitter=0),
    )


def test_wholly_blank_windows_are_separated_from_unflown_routes():
    result = horizon_result()
    blank = [w.name for w in result.blank_windows]
    assert blank == ["2027-06", "2027-07", "2027-08", "2027-09", "2027-10"]


def test_markdown_explains_blank_windows_rather_than_calling_them_unflown(tmp_path):
    written = report.write_all(horizon_result(), tmp_path, stamp="fixed")
    md = written["markdown"].read_text()
    assert "Windows with no prices at all" in md
    assert "not loaded schedules that far out" in md
    # Those routes must not also be listed as individually unflown.
    assert "## Not priced" not in md


def test_a_single_unflown_route_is_still_reported_normally(tmp_path):
    written = report.write_all(make_result(), tmp_path, stamp="fixed")
    md = written["markdown"].read_text()
    assert "## Not priced" in md and "BNE-ZZZ" in md
    assert "Windows with no prices at all" not in md


def test_single_destination_scan_claims_no_horizon():
    """One blank route is "it doesn't fly", not "schedules aren't loaded".

    The inference needs at least three destinations agreeing before it is worth
    making, so a one-route sweep never triggers it.
    """
    from flightdeals import windows

    result = scan(
        ScanSpec(
            origin="BNE",
            destinations=["LDH"],
            windows=windows.monthly(months=12, nights=7, today=date(2026, 10, 6)),
        ),
        HorizonProvider(cutoff=date(2027, 6, 1)),
        limiter=RateLimiter(min_interval=0, jitter=0),
    )
    assert result.blank_windows == []
    assert len(result.failures) == 5


def test_combinations_rank_cells_not_cities_or_months():
    """A dear city in its cheap month can beat a cheap city in its dear one."""
    rows = report.best_combinations(seasonal_result())
    assert len(rows) == 2 * 11, "two destinations x eleven flown months"
    assert rows[0]["price"] == 600 and rows[0]["code"] == "LDH"
    assert rows[0]["window"] == "2027-04"
    assert [r["rank"] for r in rows[:3]] == [1, 2, 3]
    assert rows == sorted(rows, key=lambda r: r["price"])
    # NAN in April (1200) must outrank LDH in December (1800).
    order = [(r["code"], r["window"]) for r in rows]
    assert order.index(("NAN", "2027-04")) < order.index(("LDH", "2026-12"))


def test_combos_csv_is_written_and_capped_by_limit(tmp_path):
    result = seasonal_result()
    written = report.write_all(result, tmp_path, stamp="fixed")
    rows = list(csv.DictReader(written["combos"].open()))
    assert len(rows) == 22
    assert rows[0]["code"] == "LDH" and rows[0]["depart"] == "2027-04-15"
    assert len(report.best_combinations(result, limit=5)) == 5
