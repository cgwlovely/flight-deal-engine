"""--one-way has to reach every way of choosing windows, not only presets."""

from datetime import date

import pytest

from flightdeals.cli import build_parser, windows_from_args


def parse(*argv):
    return build_parser().parse_args(["scan", *argv])


def test_one_way_applies_to_a_monthly_sweep():
    """This silently returned round-trip prices before: the monthly branch ignored it."""
    windows = windows_from_args(parse("--window", "monthly", "--months", "3", "--one-way"))
    assert len(windows) == 3
    assert all(w.ret is None for w in windows)
    assert all(w.nights is None for w in windows)


def test_monthly_without_the_flag_is_still_a_round_trip():
    windows = windows_from_args(parse("--window", "monthly", "--months", "3", "--nights", "9"))
    assert all(w.ret is not None for w in windows)
    assert {w.nights for w in windows} == {9}


def test_one_way_applies_to_a_flex_sweep():
    windows = windows_from_args(
        parse("--depart", "2026-12-17", "--return", "2026-12-27",
              "--flex", "6", "--flex-step", "3", "--one-way")
    )
    assert len(windows) == 5
    assert all(w.ret is None for w in windows)


def test_one_way_applies_to_a_single_custom_window():
    (window,) = windows_from_args(
        parse("--depart", "2027-02-15", "--return", "2027-02-22", "--one-way")
    )
    assert window.depart == date(2027, 2, 15) and window.ret is None


def test_one_way_applies_to_a_season_preset():
    windows = windows_from_args(parse("--window", "christmas", "--year", "2026", "--one-way"))
    assert len(windows) == 3
    assert all(w.ret is None for w in windows)


def test_custom_without_dates_is_rejected():
    with pytest.raises(SystemExit):
        windows_from_args(parse("--window", "custom"))
