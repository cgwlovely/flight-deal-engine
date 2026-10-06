"""Airport catalogue loading and lookup."""

from __future__ import annotations

import functools
from importlib import resources
from pathlib import Path

import yaml

from .models import Airport

_BUNDLED = "airports.yaml"


def _candidate_paths() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parent / "data" / _BUNDLED,          # installed as package data
        here.parent.parent.parent / "data" / _BUNDLED,  # running from a checkout
    ]


@functools.lru_cache(maxsize=4)
def load_catalog(path: str | Path | None = None) -> dict[str, Airport]:
    """Load the airport catalogue, keyed by uppercase IATA code."""
    raw: str | None = None
    if path is not None:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        for candidate in _candidate_paths():
            if candidate.is_file():
                raw = candidate.read_text(encoding="utf-8")
                break
        if raw is None:  # pragma: no cover - only when packaged oddly
            raw = resources.files("flightdeals").joinpath("data/" + _BUNDLED).read_text()

    payload = yaml.safe_load(raw) or {}
    catalog: dict[str, Airport] = {}
    for entry in payload.get("airports", []):
        code = str(entry["code"]).upper()
        catalog[code] = Airport(
            code=code,
            name=entry.get("name", code),
            city=entry.get("city", entry.get("name", "")),
            country=entry.get("country", ""),
            region=entry.get("region", ""),
            lat=entry.get("lat"),
            lon=entry.get("lon"),
        )
    # Tier is a scan hint rather than a property of the airport, so it lives in a
    # sidecar map instead of on the frozen dataclass.
    _TIERS.clear()
    _TIERS.update(
        {str(e["code"]).upper(): int(e.get("tier", 1)) for e in payload.get("airports", [])}
    )
    return catalog


_TIERS: dict[str, int] = {}


def tier_of(code: str) -> int:
    if not _TIERS:
        load_catalog()
    return _TIERS.get(code.upper(), 1)


def get(code: str, path: str | Path | None = None) -> Airport:
    """Look up an airport, falling back to a bare code with no coordinates."""
    catalog = load_catalog(path)
    code = code.upper()
    return catalog.get(code, Airport(code=code, name=code))


def select(
    *,
    origin: str,
    regions: list[str] | None = None,
    include_tier2: bool = False,
    explicit: list[str] | None = None,
    exclude: list[str] | None = None,
    path: str | Path | None = None,
) -> list[Airport]:
    """Pick the destinations for a scan.

    `explicit` short-circuits everything else; otherwise filter the catalogue by
    region and tier, always dropping the origin itself.
    """
    catalog = load_catalog(path)
    skip = {origin.upper(), *(c.upper() for c in (exclude or []))}

    if explicit:
        return [get(c, path) for c in explicit if c.upper() not in skip]

    wanted_regions = {r.lower() for r in regions} if regions else None
    picked = [
        ap
        for code, ap in catalog.items()
        if code not in skip
        and (include_tier2 or tier_of(code) == 1)
        and (wanted_regions is None or ap.region.lower() in wanted_regions)
    ]
    return sorted(picked, key=lambda ap: (ap.region, ap.code))


def regions(path: str | Path | None = None) -> list[str]:
    seen = {ap.region for ap in load_catalog(path).values() if ap.region}
    return sorted(seen)
