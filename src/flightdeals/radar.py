"""Anomaly detection over recorded fare history.

The distinction this module exists to respect: an airline *sale* and a cheap
*fare* are different events. A sale is a marketing calendar; an anomaly is this
route's own price falling out of its own distribution. Only the second is worth
waking someone up for, so nothing here knows or cares what a sale is.

The signal is deliberately plain -- how far below its own history a fare has
fallen -- because with a handful of observations per route anything cleverer is
fitting noise. Build the history first.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

from .history import PriceHistory
from .models import Deal
from .scanner import ScanResult, Window

#: A route needs this many prior observations before an anomaly claim is credible.
MIN_OBSERVATIONS = 3

#: How far below the median counts as an anomaly worth reporting.
DEFAULT_THRESHOLD_PCT = 15.0

#: How far a new low must beat the old one before it is news. Without a margin a
#: flat history turns every one-dollar dip into a "record low", which is how a
#: radar teaches its owner to ignore it.
DEFAULT_RECORD_MARGIN_PCT = 3.0


@dataclass
class Finding:
    deal: Deal
    window: Window
    kind: str
    """One of: record-low, below-baseline, watching (not yet enough history)."""
    median: float | None
    minimum: float | None
    observations: int
    pct_below_median: float | None

    @property
    def is_alert(self) -> bool:
        return self.kind in {"record-low", "below-baseline"}

    def message(self, currency: str = "AUD") -> str:
        it = self.deal.itinerary
        where = f"{it.origin}-{it.destination}"
        dates = f"{it.depart_date}" + (f"/{it.return_date}" if it.return_date else "")
        price = f"{currency} {it.price:,.0f}"
        if self.kind == "record-low":
            return (
                f"{where} {dates}: {price} — lowest of {self.observations + 1} "
                f"observations (previous best {self.minimum:,.0f})"
            )
        if self.kind == "below-baseline":
            return (
                f"{where} {dates}: {price} — {self.pct_below_median:.0f}% below its "
                f"median of {self.median:,.0f} over {self.observations} observations"
            )
        return (
            f"{where} {dates}: {price} — baseline building "
            f"({self.observations}/{MIN_OBSERVATIONS} observations)"
        )


def detect(
    result: ScanResult,
    history: PriceHistory,
    *,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    record_margin_pct: float = DEFAULT_RECORD_MARGIN_PCT,
    min_observations: int = MIN_OBSERVATIONS,
    before: datetime | None = None,
) -> list[Finding]:
    """Compare a fresh scan against what this database already knows.

    `before` must exclude the scan being judged, or a fare ends up compared against
    itself and nothing ever looks unusual.
    """
    cutoff = before or result.started_at
    findings: list[Finding] = []

    for window in result.spec.windows:
        for deal in result.deals_by_window.get(window.label, []):
            it = deal.itinerary
            prior = history.prices(
                it.origin, it.destination, window_label=window.label, before=cutoff
            )
            if len(prior) < min_observations:
                findings.append(
                    Finding(
                        deal=deal,
                        window=window,
                        kind="watching",
                        median=statistics.median(prior) if prior else None,
                        minimum=min(prior) if prior else None,
                        observations=len(prior),
                        pct_below_median=None,
                    )
                )
                continue

            median = statistics.median(prior)
            minimum = min(prior)
            pct_below = 100.0 * (median - it.price) / median

            if it.price < minimum * (1.0 - record_margin_pct / 100.0):
                kind = "record-low"
            elif pct_below >= threshold_pct:
                kind = "below-baseline"
            else:
                continue

            findings.append(
                Finding(
                    deal=deal,
                    window=window,
                    kind=kind,
                    median=median,
                    minimum=minimum,
                    observations=len(prior),
                    pct_below_median=pct_below,
                )
            )

    # Deepest discount first; record lows outrank ordinary dips at equal depth.
    findings.sort(
        key=lambda f: (
            not f.is_alert,
            f.kind != "record-low",
            -(f.pct_below_median or 0.0),
        )
    )
    return findings


def alerts(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.is_alert]


def notify_macos(lines: list[str], *, title: str = "Flight deal radar") -> bool:
    """Post a macOS notification. Returns False if it could not be delivered.

    Best-effort by design: a radar that crashes because the desktop is locked or
    the platform is not macOS is worse than one that stays quiet.
    """
    if not lines:
        return False
    import shutil
    import subprocess

    osascript = shutil.which("osascript")
    if osascript is None:
        return False

    body = "; ".join(lines)[:1800].replace('"', "'")
    safe_title = title.replace('"', "'")
    try:
        subprocess.run(
            [osascript, "-e", f'display notification "{body}" with title "{safe_title}"'],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return True
