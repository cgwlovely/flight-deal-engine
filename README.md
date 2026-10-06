# flight-deal-engine

Price a hundred-odd destinations from one airport in a single command, then rank
them by how good a deal they actually are — not just by which is cheapest.

Cheapest is a boring question: the answer is always the nearest airport. The
interesting question is *where does my money go furthest*, so every fare is also
scored against the distance it buys and against the price history of that same
route and travel window.

Prices come from Google Flights' public search page, so there is **no API key and
no signup**.

```
$ flightdeals scan --origin BNE --window christmas --region "Southeast Asia,Pacific"

                     peak (2026-12-20 -> 2027-01-04, 15n)
  #  Destination            Price  Stops    Flight   c/km  Score  Airline
  1  Nadi (NAN)            A$853   direct     3h50   15.1   88.2  Fiji Airways
  2  Bali (DPS)          A$1,021   direct     6h10    9.8   85.6  Jetstar
  3  Singapore (SIN)      A$1,190   direct     7h50    9.9   81.3  Scoot
  …
```

## Install

Needs Python 3.11+.

```bash
git clone https://github.com/cgwlovely/flight-deal-engine.git
cd flight-deal-engine
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

Or without cloning: `uv tool install git+https://github.com/cgwlovely/flight-deal-engine`.

## Use

```bash
# Christmas from Brisbane, every tier-1 destination, three date shapes
flightdeals scan --origin BNE --window christmas

# One region, wider catalogue, nonstop only
flightdeals scan --origin SYD --region "East Asia" --all --max-stops 0

# Your own dates, plus/minus three days at the same trip length
flightdeals scan --depart 2027-04-02 --return 2027-04-14 --flex 3

# What's in the catalogue, and how far away is it
flightdeals destinations --origin BNE --region Europe

# Price history for one route, once you've scanned it more than once
flightdeals history --dest NRT --window 2026-12-20/2027-01-04

# Just hand me the Google Flights link
flightdeals url BNE SIN 2026-12-20 2027-01-04
```

Every scan writes `out/<origin>-<timestamp>.{json,md,html,csv}`. The HTML file is
a standalone report with no external assets; the CSV is one wide row per
destination, ready to pivot.

### Useful flags

| Flag | Why |
|---|---|
| `--window christmas` \| `new-year` | Seasonal presets; `--year` to pick the year |
| `--depart` / `--return` / `--flex N` | Custom dates, optionally swept ±N days |
| `--region`, `--dest`, `--exclude`, `--all` | Scope the destination list |
| `--seat`, `--adults`, `--children`, `--currency` | Search parameters |
| `--max-stops 0` | Nonstop only |
| `--workers`, `--min-interval`, `--attempts` | Throughput vs. politeness |
| `--no-history`, `--db PATH` | Where (or whether) to record prices |

## How the ranking works

Each quote gets three 0–100 component scores, blended 45 / 35 / 20:

- **cents per km** — fare ÷ round-trip great-circle distance, ranked against the
  rest of the scan. This is what separates "cheap because it's close" from "cheap
  for how far it is".
- **price** — the absolute fare, ranked against the rest of the scan. Distance is
  not free, so raw affordability still counts.
- **history** — discount against the median previously recorded for the same route
  *and the same travel window*. Needs at least three prior observations; until
  then the weights renormalise over the other two, so a first-ever scan still
  ranks sensibly.

Windows are scored independently — a 7-night trip is never ranked against a
15-night one.

## Architecture

```
src/flightdeals/
  providers/        search(SearchRequest) -> list[Itinerary]; add a source here
    google_flights.py   builds the ?tfs protobuf query, parses the page payload
  catalog.py        airport catalogue + great-circle distances (data/airports.yaml)
  windows.py        named travel windows (christmas, new-year, ±N-day sweeps)
  scanner.py        thread pool + rate limiter + retries, one Quote per route
  scoring.py        the three components above -> a ranked list of Deals
  history.py        SQLite price log; the baseline for the history component
  report.py         terminal table, Markdown, JSON, CSV, standalone HTML
```

To add a fare source, implement `FareProvider.search` and register it — nothing
downstream knows where prices came from.

## Recording history

The history component only earns its weight if you scan the same window
repeatedly. A daily cron is enough:

```bash
0 9 * * * cd ~/flight-deal-engine && .venv/bin/flightdeals scan \
  --origin BNE --window christmas --quiet --out out/daily
```

All observations land in `~/.flight-deal-engine/history.db`.

## Caveats, honestly

- **No API, no contract.** This reads a public web page. Google can change the
  payload shape — `PAYLOAD_SECTIONS` and `_itinerary()` in
  `providers/google_flights.py` are the two places to fix, and
  `tests/test_google_flights.py` pins the indices so breakage is loud.
- **Be gentle.** The scanner rate-limits itself (≈0.8 s between requests, 4
  workers) on purpose. Turning that off on a 120-destination scan will get your
  IP throttled part-way through, and the partial scan is worse than a slow one.
- **Quoted ≠ bookable.** Prices are indicative, exclude bags unless the fare
  includes them, and move. Always open the itinerary before believing it.
- **Only the shortlist.** The page returns Google's "best" list plus the first
  few "other" options — typically 5–15 itineraries, not the whole market.
- **Distances are approximate.** Airport coordinates in `data/airports.yaml` are
  city/airport level, fine to ~1 km, which is far below the noise in cents/km.

## Licence

MIT
