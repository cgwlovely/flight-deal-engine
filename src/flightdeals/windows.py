"""Named travel windows.

Peak-season pricing is driven by the exact dates far more than by the destination,
so windows are first-class: a scan prices every destination over the same set of
date pairs, which is the only way the resulting comparison means anything.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .scanner import Window


def _next_christmas(today: date | None = None) -> int:
    today = today or date.today()
    # After Boxing Day, "Christmas" means next year's.
    return today.year if today <= date(today.year, 12, 26) else today.year + 1


def christmas(year: int | None = None, today: date | None = None) -> list[Window]:
    """Three ways to take Christmas off, so the price of flexibility is visible.

    * **peak** -- fly out the weekend before Christmas, home after New Year. The
      most expensive shape and the one most people actually book.
    * **short** -- Christmas week only.
    * **shoulder** -- leave on Boxing Day, home mid-January: the same holiday with
      the two worst-priced days avoided.
    """
    year = year or _next_christmas(today)
    return [
        Window(date(year, 12, 20), date(year + 1, 1, 4), name="peak"),
        Window(date(year, 12, 23), date(year, 12, 30), name="short"),
        Window(date(year, 12, 26), date(year + 1, 1, 11), name="shoulder"),
    ]


def new_year(year: int | None = None, today: date | None = None) -> list[Window]:
    year = year or _next_christmas(today)
    return [Window(date(year, 12, 28), date(year + 1, 1, 6), name="new-year")]

def monthly(
    *,
    start: date | None = None,
    months: int = 12,
    nights: int = 7,
    day: int = 15,
    today: date | None = None,
) -> list[Window]:
    """One fixed-length trip per month, to expose a route's seasonality.

    Answers "when is this cheap" rather than "where is cheap". Trip length is held
    constant and departures are pinned to the same day of each month (the 15th by
    default -- mid-month, so the probe is not sitting inside school holidays) so
    that month-to-month differences are the route's own seasonality and not an
    artefact of the dates drifting.

    Months begin with the next whole month, since a departure a few weeks out is
    priced as last-minute and would not compare fairly against one a year ahead.
    """
    today = today or date.today()
    if start is None:
        year, month = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
        start = date(year, month, 1)

    windows: list[Window] = []
    year, month = start.year, start.month
    for _ in range(months):
        depart = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        windows.append(
            Window(depart, depart + timedelta(days=nights), name=f"{year}-{month:02d}")
        )
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)
    return windows


def around(depart: date, ret: date | None, *, flex_days: int = 0, step: int = 1) -> list[Window]:
    """A window plus its neighbours, keeping the trip length fixed."""
    windows = []
    for offset in range(-flex_days, flex_days + 1, step):
        shift = timedelta(days=offset)
        name = "base" if offset == 0 else f"{offset:+d}d"
        windows.append(
            Window(depart + shift, (ret + shift) if ret else None, name=name)
        )
    return windows


PRESETS = {
    "christmas": christmas,
    "new-year": new_year,
}


def preset(name: str, year: int | None = None) -> list[Window]:
    try:
        builder = PRESETS[name]
    except KeyError:
        raise ValueError(
            f"unknown window preset {name!r}; try: {', '.join(sorted(PRESETS))}"
        ) from None
    return builder(year)
