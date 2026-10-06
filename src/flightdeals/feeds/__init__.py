"""Deal feeds: published sale events, as distinct from priced fares.

A sale and a cheap fare are different objects, and the engine keeps them apart on
purpose. A scan prices a route on a date and can say whether that fare is unusual.
A sale is an announcement — date-restricted, capacity-limited, often gone by the
time it is read — and it is evidence about *why* a fare moved, not a measurement
that something is cheap now.

They are worth having side by side because a scan cannot see a sale: probing the
15th of each month misses fares that only exist on particular dates. Measured
2026-10-06, a published China Southern sale showed Brisbane-London at A$1,320
while the deepest fare this engine found by scanning was A$1,683.
"""

from .ozbargain import SaleDeal, fetch_airfare_deals, parse_feed

__all__ = ["SaleDeal", "fetch_airfare_deals", "parse_feed"]
