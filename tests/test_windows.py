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


def test_monthly_starts_next_month_and_holds_length():
    sweep = windows.monthly(months=12, nights=7, today=date(2026, 10, 6))
    assert len(sweep) == 12
    assert sweep[0].depart == date(2026, 11, 15)
    assert sweep[-1].depart == date(2027, 10, 15)
    assert {w.nights for w in sweep} == {7}
    assert [w.name for w in sweep][:2] == ["2026-11", "2026-12"]


def test_monthly_rolls_the_year_over_from_december():
    sweep = windows.monthly(months=3, today=date(2026, 12, 20))
    assert [w.depart for w in sweep] == [
        date(2027, 1, 15), date(2027, 2, 15), date(2027, 3, 15)
    ]


def test_monthly_clamps_day_to_short_months():
    sweep = windows.monthly(months=4, day=31, today=date(2026, 12, 31))
    assert [w.depart.day for w in sweep] == [31, 28, 31, 30]  # Jan, Feb, Mar, Apr 2027
