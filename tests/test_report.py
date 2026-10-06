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
