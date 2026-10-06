"""Traveller profile: where you have already been, and where you want to go.

Which destinations are worth scanning is a fact about the traveller, not about
airports, so it lives in the user's own file rather than in the shared catalogue.
Marking Southeast Asia visited should quietly drop twenty destinations from every
future scan without editing the catalogue that everyone shares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Airport

DEFAULT_PATH = Path.home() / ".flight-deal-engine" / "profile.yaml"

EXAMPLE = """\
# Where you have already been. Anything matched here is dropped by --unvisited.
visited:
  regions: []        # e.g. [Southeast Asia, New Zealand]
  countries: []      # e.g. [Australia, Japan]
  airports: []       # e.g. [SIN, DPS]

# Places you actively want priced. Used by --wishlist and by `flightdeals radar`.
wishlist: []         # e.g. [NAN, APW, PPT, HNL]
"""


@dataclass
class Profile:
    visited_regions: set[str] = field(default_factory=set)
    visited_countries: set[str] = field(default_factory=set)
    visited_airports: set[str] = field(default_factory=set)
    wishlist: list[str] = field(default_factory=list)
    path: Path | None = None

    def has_visited(self, airport: Airport) -> bool:
        return (
            airport.code.upper() in self.visited_airports
            or airport.country.casefold() in self.visited_countries
            or airport.region.casefold() in self.visited_regions
        )

    def unvisited(self, airports: list[Airport]) -> list[Airport]:
        return [ap for ap in airports if not self.has_visited(ap)]

    @property
    def is_empty(self) -> bool:
        return not (
            self.visited_regions
            or self.visited_countries
            or self.visited_airports
            or self.wishlist
        )

    def describe(self) -> str:
        lines = [f"profile: {self.path or '(none)'}"]
        lines.append(f"  visited regions:   {', '.join(sorted(self.visited_regions)) or '-'}")
        lines.append(f"  visited countries: {', '.join(sorted(self.visited_countries)) or '-'}")
        lines.append(f"  visited airports:  {', '.join(sorted(self.visited_airports)) or '-'}")
        lines.append(f"  wishlist ({len(self.wishlist)}): {', '.join(self.wishlist) or '-'}")
        return "\n".join(lines)


def load(path: str | Path | None = None) -> Profile:
    """Load a profile. A missing file is not an error -- it is an empty profile."""
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return Profile(path=target)

    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    visited = payload.get("visited") or {}
    return Profile(
        # Case-folded on the way in so that "southeast asia" matches the catalogue.
        visited_regions={str(r).casefold() for r in (visited.get("regions") or [])},
        visited_countries={str(c).casefold() for c in (visited.get("countries") or [])},
        visited_airports={str(a).upper() for a in (visited.get("airports") or [])},
        wishlist=[str(c).upper() for c in (payload.get("wishlist") or [])],
        path=target,
    )


def init(path: str | Path | None = None, *, overwrite: bool = False) -> Path:
    """Write a commented example profile, leaving an existing one alone."""
    target = Path(path) if path else DEFAULT_PATH
    if target.exists() and not overwrite:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXAMPLE, encoding="utf-8")
    return target
