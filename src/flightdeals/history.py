"""SQLite price history.

Every scan appends its quotes here. Repeat scans of the same window are what
turn a raw price into a signal: "$1,190 to Tokyo" means little, "$1,190 against
a 9-observation median of $1,560" means a lot.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Itinerary

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scanned_at    TEXT    NOT NULL,
    origin        TEXT    NOT NULL,
    destination   TEXT    NOT NULL,
    depart_date   TEXT    NOT NULL,
    return_date   TEXT,
    window_label  TEXT    NOT NULL DEFAULT '',
    price         REAL    NOT NULL,
    currency      TEXT    NOT NULL,
    airlines      TEXT    NOT NULL DEFAULT '',
    stops_out     INTEGER,
    duration_min  INTEGER,
    source        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_route_window
    ON observations (origin, destination, window_label);
CREATE INDEX IF NOT EXISTS idx_scanned_at ON observations (scanned_at);
"""


class PriceHistory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PriceHistory:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing ------------------------------------------------------------
    def record(self, itineraries: Iterable[Itinerary], *, window_label: str = "") -> int:
        rows = [
            (
                datetime.now(UTC).isoformat(),
                it.origin,
                it.destination,
                it.depart_date.isoformat(),
                it.return_date.isoformat() if it.return_date else None,
                window_label,
                float(it.price),
                it.currency,
                ",".join(it.airlines),
                it.stops_out,
                it.duration_out_min,
                it.source,
            )
            for it in itineraries
        ]
        if not rows:
            return 0
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                """INSERT INTO observations
                   (scanned_at, origin, destination, depart_date, return_date,
                    window_label, price, currency, airlines, stops_out,
                    duration_min, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        self._conn.commit()
        return len(rows)

    # -- reading ------------------------------------------------------------
    def baseline(
        self,
        origin: str,
        destination: str,
        *,
        window_label: str = "",
        before: datetime | None = None,
    ) -> tuple[float | None, int]:
        """Median price previously seen for this route+window, and the sample size.

        `before` excludes the scan currently in progress so a fresh price is not
        compared against itself. Timestamps are stored at microsecond precision
        precisely so that this cutoff can separate two scans seconds apart.
        """
        sql = (
            "SELECT price FROM observations "
            "WHERE origin=? AND destination=? AND window_label=?"
        )
        params: list = [origin, destination, window_label]
        if before is not None:
            sql += " AND scanned_at < ?"
            params.append(before.isoformat())
        with closing(self._conn.cursor()) as cur:
            prices = [r["price"] for r in cur.execute(sql, params)]
        if not prices:
            return None, 0
        return statistics.median(prices), len(prices)

    def series(
        self, origin: str, destination: str, *, window_label: str = ""
    ) -> list[tuple[str, float]]:
        with closing(self._conn.cursor()) as cur:
            return [
                (r["scanned_at"], r["price"])
                for r in cur.execute(
                    "SELECT scanned_at, price FROM observations "
                    "WHERE origin=? AND destination=? AND window_label=? "
                    "ORDER BY scanned_at",
                    (origin, destination, window_label),
                )
            ]

    def scans(self) -> list[tuple[str, int]]:
        with closing(self._conn.cursor()) as cur:
            return [
                (r["day"], r["n"])
                for r in cur.execute(
                    "SELECT substr(scanned_at,1,10) AS day, COUNT(*) AS n "
                    "FROM observations GROUP BY day ORDER BY day"
                )
            ]


def window_label(depart: date, ret: date | None) -> str:
    """Stable key for a departure/return pair, so history lines up across scans."""
    return f"{depart.isoformat()}/{ret.isoformat() if ret else 'oneway'}"
