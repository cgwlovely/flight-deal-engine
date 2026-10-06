# Contributing

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
pytest -q && ruff check .
```

The test suite never touches the network. The Google Flights parser is exercised
against synthetic copies of the page payload in `tests/test_google_flights.py`,
and the scanner against a `FakeProvider`. Keep it that way — a suite that needs
Google to be up is a suite nobody runs.

## Adding a fare source

1. Implement `search(SearchRequest) -> list[Itinerary]` in
   `src/flightdeals/providers/yours.py`.
2. Register it in `providers/__init__.py` (or use the `@register` decorator from
   out of tree).
3. Raise `NoFlightsFound` when the source answered "nothing flies there" and
   `ProviderError` for anything retryable — the scanner retries the second and
   not the first.

Nothing downstream of the provider knows where a price came from, so that is the
whole integration.

## Adding destinations

Append to `data/airports.yaml`. `tier: 1` means "include in a default scan";
use `tier: 2` for places that only make sense when asked for explicitly.
Coordinates only feed great-circle distance, so airport-or-city level is plenty.
