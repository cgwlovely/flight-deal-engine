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

# When is this route cheap? One fixed-length trip per month for a year
flightdeals scan --dest LDH --window monthly --months 12 --nights 7

# Is a carrier cheap on this market? Scan unfiltered and read -carriers.csv
flightdeals scan --dest FCO,BCN,AMS,IST --window monthly --months 6

# Plan around where you have not been, and watch what you want
flightdeals profile --init                 # then edit ~/.flight-deal-engine/profile.yaml
flightdeals destinations --unvisited       # catalogue minus anywhere you have been
flightdeals scan --wishlist --window monthly --months 6 --nights 10

# Radar: scan the wishlist and report only fares that are unusual for themselves
flightdeals radar --wishlist --window monthly --months 3 --notify

# What's in the catalogue, and how far away is it
flightdeals destinations --origin BNE --region Europe

# Price history for one route, once you've scanned it more than once
flightdeals history --dest NRT --window 2026-12-20/2027-01-04

# Published airline sale posts for your origin (announcements, not measurements)
flightdeals sales --origin BNE --priced-only

# Just hand me the Google Flights link
flightdeals url BNE SIN 2026-12-20 2027-01-04
```

Every scan writes `out/<origin>-<timestamp>.{json,md,html,csv}`. The HTML file is
a standalone report with no external assets; the CSV is one wide row per
destination, ready to pivot.

A scan with more than one window also writes `-matrix.csv`: one row per window,
one column per destination, one price per cell. That is the right shape for
"when is this cheap" — with `--window monthly` and a handful of destinations the
terminal switches to the same pivoted view automatically.

### Useful flags

| Flag | Why |
|---|---|
| `--window christmas` \| `new-year` | Seasonal presets; `--year` to pick the year |
| `--window monthly` | Seasonality sweep: `--months`, `--nights`, `--day` |
| `--depart` / `--return` / `--flex N` | Custom dates, optionally swept ±N days |
| `--region`, `--dest`, `--exclude`, `--all` | Scope the destination list |
| `--seat`, `--adults`, `--children`, `--currency` | Search parameters |
| `--max-stops 0` | Nonstop only |
| `--airlines CZ,MU,CA` | Only these marketing carriers (see the warning below) |
| `--workers`, `--min-interval`, `--attempts` | Throughput vs. politeness |
| `--no-history`, `--db PATH` | Where (or whether) to record prices |
| `--wishlist`, `--unvisited` | Scope by your profile instead of by region |
| `--no-self-transfer`, `--max-hours`, `--bags` | Quality filters before comparing |

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

For a seasonality sweep the score is beside the point: comparing one destination
against itself across twelve months is a question about the price column, which
is why `--window monthly` pivots to the matrix instead.

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

## The radar

`flightdeals radar` scans, records, and then reports only what is unusual:

- **record-low** — cheaper than anything previously seen for this route and window,
  by a margin (`--record-margin`, default 3%). Without a margin a flat history turns
  every one-dollar dip into an alert, which is how a radar teaches you to ignore it.
- **below-baseline** — at least `--threshold` percent (default 15) under this route's
  own median.
- **watching** — fewer than `--min-observations` (default 3) prior observations, so no
  claim is made. A brand-new watchlist is entirely in this state; it needs three runs
  before it can say anything.

An airline sale is not an input. A sale is a marketing calendar; an anomaly is this
route's own price leaving its own distribution, and only the second is worth an alert.

`--notify` posts a macOS desktop notification, best-effort: a radar that crashes
because the platform is not macOS would be worse than one that stays quiet.
`contrib/com.flightdeals.radar.plist` is a launchd agent that runs it daily — edit the
two absolute paths, then `launchctl load` it.

Keep the daily volume modest. A wishlist of 15 destinations over 3 monthly windows is
45 searches a run, which is fine once a day and not fine once an hour.

## Recording history

The history component only earns its weight if you scan the same window
repeatedly. A daily cron is enough:

```bash
0 9 * * * cd ~/flight-deal-engine && .venv/bin/flightdeals scan \
  --origin BNE --window christmas --quiet --out out/daily
```

All observations land in `~/.flight-deal-engine/history.db`.

## Sales are not fares

`flightdeals sales` reads the airfare posts from OzBargain's published travel feed —
one request per run, from the RSS the site publishes for syndication, not scraped
pages. It is deliberately a separate command from `scan`, because a sale and a cheap
fare are different objects:

- A **scan** prices a route on a date and can say whether that fare is unusual.
- A **sale** is an announcement — date-restricted, capacity-limited, often expired by
  the time it is read. It explains why a fare moved; it does not establish that one
  is cheap now.

They are worth having side by side because neither sees the other. Measured
2026-10-06: a published China Southern sale had Brisbane–London at A$1,320 while the
deepest fare this engine found by scanning was A$1,683 — scanning the 15th of each
month cannot see a fare that only exists on particular dates. The same comparison ran
the other way for Tokyo, where scanning found A$881 against the sale's A$907.

A filtered scan is never recorded in the price history, for the reason below.

## Do not use `--airlines` to compare prices

`--airlines` narrows more than the carrier: Google returns a **dearer** fare for the
identical airline and routing once the filter is on. Measured on BNE-IST, same dates,
same minute: A$1,390 unfiltered versus A$1,767 filtered, both of them China Southern
BNE-CAN-IST. Filtering apparently also restricts which fare and ticketing
combinations are offered, so a filtered scan systematically overstates what a
carrier charges, and "rerun it filtered and subtract" gives a confidently wrong
answer.

Use `--airlines` when you have decided to fly someone. To find out whether a
carrier is cheap, scan unfiltered and read the carrier table (`-carriers.csv`,
also printed for multi-window scans): it counts the window x destination cells
whose cheapest fare each carrier appears on.

Every carrier on a fare is credited, not just the first. The first listed is
often a short domestic feeder — "Virgin Australia, Qatar Airways" for a
BNE-MEL-DOH-FCO fare — and crediting only the primary would file a 22-hour trip
under the airline that flew two hours of it. Which carrier "really" operated a
trip would need per-leg airlines and this source does not publish them, so credit
is shared rather than guessed, and shares sum to more than 100%.

## Planning around where you have not been

Which destinations are worth scanning is a fact about you, not about airports, so it
lives in `~/.flight-deal-engine/profile.yaml` rather than in the shared catalogue:

```yaml
visited:
  regions: [Southeast Asia, New Zealand]
  countries: [Japan]
  airports: [SIN]
wishlist: [NAN, BOB, APW, HNL]
```

`--unvisited` drops anything matched; `--wishlist` scans the list. Marking a region
visited quietly removes twenty destinations from every future scan without editing a
file that everyone shares.

A note on Antarctica: there are no scheduled commercial flights, so nothing can be
priced to it. The catalogue carries the expedition ports instead — Ushuaia (USH) and
Punta Arenas (PUQ) — which is what you actually buy a ticket to.

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
