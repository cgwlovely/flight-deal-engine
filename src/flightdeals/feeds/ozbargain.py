"""Airfare sale posts from OzBargain's published category feed.

Reads the RSS feed the site publishes for syndication rather than scraping pages;
its robots.txt disallows /api/, /search/ and a few others, and permits /cat/.
Keep the request rate to what a feed reader would do -- this is one request per
run, not per route.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

FEED_URL = "https://www.ozbargain.com.au/cat/travel/feed"
USER_AGENT = "flight-deal-engine/0.1 (personal fare tracker)"
NS = {"ozb": "https://www.ozbargain.com.au", "dc": "http://purl.org/dc/elements/1.1/"}

#: Titles that look like a flight deal rather than a hotel or a tour.
AIRFARE = re.compile(
    r"\b(flight|flights|airfare|air fare|return|one[- ]way|o/w|economy|business class|"
    r"qantas|jetstar|virgin|scoot|batik|vietjet|cebu|air ?asia|fiji airways|air ?nz|"
    r"air new zealand|cathay|singapore air|emirates|etihad|qatar|china southern|"
    r"china eastern|air china|hainan|korean air|asiana|ana|jal|thai|malaysia air|"
    r"philippine air|garuda|turkish|united|delta|american|air canada|latam)\b",
    re.I,
)
#: Hotels, cruises, tours and flight-plus-hotel packages share the category and are
#: not fares. "5 Nights at ... with Flights" prices a holiday, not a seat.
NOT_AIRFARE = re.compile(
    r"\b(hotel|resort|cruise|accommodation|car hire|rental car|esim|luggage|insurance|"
    r"tour|package|\d+\s*nights?\b|twin share)\b",
    re.I,
)

#: City codes and names as they appear in these titles: "BNE $1320", "Brisbane $907".
ORIGIN_ALIASES = {
    "BNE": ("BNE", "Brisbane"), "SYD": ("SYD", "Sydney"), "MEL": ("MEL", "Melbourne"),
    "PER": ("PER", "Perth"), "ADL": ("ADL", "Adelaide"), "OOL": ("OOL", "Gold Coast"),
    "CNS": ("CNS", "Cairns"), "DRW": ("DRW", "Darwin"), "HBA": ("HBA", "Hobart"),
    "CBR": ("CBR", "Canberra"), "AKL": ("AKL", "Auckland"),
}


@dataclass
class SaleDeal:
    title: str
    url: str
    posted: datetime | None
    votes: int | None = None
    expiry: str = ""
    origin_price: float | None = None
    """Price quoted for the requested origin, when the title breaks prices out by city."""
    airline: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if not self.expiry:
            return False
        try:
            return parsedate_to_datetime(self.expiry) < datetime.now(UTC)
        except (TypeError, ValueError):
            return False


def _price_for_origin(title: str, origin: str) -> float | None:
    """Pull the price quoted against one city out of a multi-city title."""
    for alias in ORIGIN_ALIASES.get(origin.upper(), (origin.upper(),)):
        # "BNE $1320", "Brisbane from $907", "BNE: $1,320"
        m = re.search(rf"\b{re.escape(alias)}\b[^\d$]{{0,14}}\$\s?([\d,]+)", title, re.I)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _airline(title: str) -> str:
    """The carrier is conventionally the part before the first colon."""
    head = title.split(":", 1)[0] if ":" in title else ""
    if 0 < len(head) <= 48 and AIRFARE.search(head):
        return head.strip()
    found = AIRFARE.search(title)
    return found.group(0) if found else ""


def parse_feed(xml: bytes | str, *, origin: str = "BNE") -> list[SaleDeal]:
    """Turn the feed into airfare deals, dropping hotels, tours and cruises."""
    root = ET.fromstring(xml)
    deals: list[SaleDeal] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title or NOT_AIRFARE.search(title) or not AIRFARE.search(title):
            continue
        meta = item.find("ozb:meta", NS)
        votes = None
        expiry = ""
        if meta is not None:
            raw = meta.get("votes-pos")
            votes = int(raw) if raw and raw.isdigit() else None
            expiry = meta.get("expiry") or ""
        posted = None
        try:
            posted = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            pass
        deals.append(
            SaleDeal(
                title=title,
                url=(item.findtext("link") or "").strip(),
                posted=posted,
                votes=votes,
                expiry=expiry,
                origin_price=_price_for_origin(title, origin),
                airline=_airline(title),
                tags=[c.text for c in item.findall("category") if c.text],
            )
        )
    return deals


def fetch_airfare_deals(
    *, origin: str = "BNE", url: str = FEED_URL, timeout: int = 25
) -> list[SaleDeal]:
    """Fetch and parse the feed. One request per run."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return parse_feed(response.read(), origin=origin)
