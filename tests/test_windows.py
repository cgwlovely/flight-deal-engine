from datetime import date

from flightdeals import windows


def test_christmas_rolls_over_after_boxing_day():
    assert windows.christmas(today=date(2026, 10, 6))[0].depart.year == 2026
    assert windows.christmas(today=date(2026, 12, 27))[0].depart.year == 2027


def test_presets_keep_trip_lengths_and_labels():
    peak, short, shoulder = windows.christmas(2026)
    assert (peak.nights, short.nights, shoulder.nights) == (15, 7, 16)
    assert peak.label == "2026-12-20/2027-01-04"


def test_around_keeps_trip_length_fixed():
    flex = windows.around(date(2026, 12, 20), date(2027, 1, 4), flex_days=2)
    assert len(flex) == 5
    assert {w.nights for w in flex} == {15}
