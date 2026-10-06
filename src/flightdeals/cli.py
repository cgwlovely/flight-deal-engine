"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__, catalog, providers, report, windows
from .history import PriceHistory
from .providers.base import SearchRequest
from .scanner import RateLimiter, ScanSpec, Window, scan

DEFAULT_DB = Path.home() / ".flight-deal-engine" / "history.db"


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _csv_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flightdeals",
        description="Scan live airfares across many destinations and rank the deals.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="price every destination over one or more windows")
    scan_p.add_argument("--origin", default="BNE", help="origin IATA code (default: BNE)")
    scan_p.add_argument(
        "--window",
        default="christmas",
        help="preset name (christmas, new-year) or 'custom' with --depart/--return",
    )
    scan_p.add_argument("--year", type=int, help="year for a seasonal preset")
    scan_p.add_argument("--depart", type=_parse_date, help="custom departure date")
    scan_p.add_argument("--return", dest="ret", type=_parse_date, help="custom return date")
    scan_p.add_argument(
        "--flex", type=int, default=0, help="with --depart, also try +/- N days (fixed length)"
    )
    scan_p.add_argument("--dest", type=_csv_list, help="explicit destinations, comma separated")
    scan_p.add_argument("--region", type=_csv_list, help="limit to these catalogue regions")
    scan_p.add_argument("--exclude", type=_csv_list, help="skip these destinations")
    scan_p.add_argument("--all", action="store_true", help="include tier-2 destinations too")
    scan_p.add_argument("--adults", type=int, default=1)
    scan_p.add_argument("--children", type=int, default=0)
    scan_p.add_argument(
        "--seat",
        default="economy",
        choices=["economy", "premium-economy", "business", "first"],
    )
    scan_p.add_argument("--currency", default="AUD")
    scan_p.add_argument("--max-stops", type=int, default=None)
    scan_p.add_argument("--one-way", action="store_true", help="price one-way instead of return")
    scan_p.add_argument("--workers", type=int, default=4, help="concurrent requests (default 4)")
    scan_p.add_argument(
        "--min-interval",
        type=float,
        default=0.8,
        help="seconds between requests, jittered (default 0.8)",
    )
    scan_p.add_argument("--attempts", type=int, default=3, help="retries per route")
    scan_p.add_argument("--provider", default="google-flights", choices=providers.available())
    scan_p.add_argument("--out", type=Path, default=Path("out"), help="report directory")
    scan_p.add_argument("--db", type=Path, default=DEFAULT_DB, help="price history database")
    scan_p.add_argument("--no-history", action="store_true", help="do not read or write history")
    scan_p.add_argument("--limit", type=int, default=30, help="rows per table in reports")
    scan_p.add_argument("--quiet", action="store_true", help="no per-route progress output")

    dest_p = sub.add_parser("destinations", help="show the destination catalogue")
    dest_p.add_argument("--origin", default="BNE")
    dest_p.add_argument("--region", type=_csv_list)
    dest_p.add_argument("--all", action="store_true")

    hist_p = sub.add_parser("history", help="inspect recorded prices")
    hist_p.add_argument("--db", type=Path, default=DEFAULT_DB)
    hist_p.add_argument("--origin", default="BNE")
    hist_p.add_argument("--dest", help="show the price series for one destination")
    hist_p.add_argument("--window", default="", help="window label, e.g. 2026-12-20/2027-01-04")

    url_p = sub.add_parser("url", help="print the Google Flights URL for one search")
    url_p.add_argument("origin")
    url_p.add_argument("destination")
    url_p.add_argument("depart", type=_parse_date)
    url_p.add_argument("ret", type=_parse_date, nargs="?")
    url_p.add_argument("--currency", default="AUD")

    return parser


def _windows_from_args(args) -> list[Window]:
    if args.depart:
        ret = None if args.one_way else args.ret
        if args.flex:
            return windows.around(args.depart, ret, flex_days=args.flex)
        return [Window(args.depart, ret, name="custom")]
    if args.window == "custom":
        raise SystemExit("--window custom needs --depart (and usually --return)")
    picked = windows.preset(args.window, args.year)
    if args.one_way:
        picked = [Window(w.depart, None, name=w.name) for w in picked]
    return picked


def cmd_scan(args) -> int:
    destinations = catalog.select(
        origin=args.origin,
        regions=args.region,
        include_tier2=args.all,
        explicit=args.dest,
        exclude=args.exclude,
    )
    if not destinations:
        print("no destinations selected", file=sys.stderr)
        return 1

    window_list = _windows_from_args(args)
    spec = ScanSpec(
        origin=args.origin.upper(),
        destinations=[ap.code for ap in destinations],
        windows=window_list,
        adults=args.adults,
        children=args.children,
        seat=args.seat,
        currency=args.currency,
        max_stops=args.max_stops,
    )

    total = len(destinations) * len(window_list)
    print(
        f"Scanning {len(destinations)} destinations from {spec.origin} "
        f"over {len(window_list)} window(s) = {total} searches",
        file=sys.stderr,
    )
    for window in window_list:
        print(f"  · {window.describe()}", file=sys.stderr)

    done = {"n": 0}

    def progress(request: SearchRequest, quote, error):
        done["n"] += 1
        if args.quiet:
            return
        if quote is not None:
            price = f"{quote.cheapest.price:,.0f} {spec.currency}"
            tail = "direct" if quote.cheapest.is_nonstop else f"{quote.cheapest.stops_out} stop"
            print(f"[{done['n']}/{total}] {request.label()}  {price}  {tail}", file=sys.stderr)
        else:
            print(f"[{done['n']}/{total}] {request.label()}  — {error}", file=sys.stderr)

    provider = providers.build(args.provider)
    history = None if args.no_history else PriceHistory(args.db)
    try:
        result = scan(
            spec,
            provider,
            history=history,
            workers=args.workers,
            attempts=args.attempts,
            limiter=RateLimiter(min_interval=args.min_interval),
            on_result=progress,
        )
    finally:
        if history is not None:
            history.close()

    for window in window_list:
        deals = result.deals_by_window.get(window.label, [])
        if deals:
            report.print_table(deals, title=window.describe(), limit=args.limit)

    written = report.write_all(result, args.out)
    print("\nWrote:", file=sys.stderr)
    for kind, path in written.items():
        print(f"  {kind:9} {path}", file=sys.stderr)
    if result.failures:
        print(f"{len(result.failures)} route(s) returned no price.", file=sys.stderr)
    return 0


def cmd_destinations(args) -> int:
    picked = catalog.select(origin=args.origin, regions=args.region, include_tier2=args.all)
    current = None
    for ap in picked:
        if ap.region != current:
            current = ap.region
            print(f"\n{current}")
        origin = catalog.get(args.origin)
        dist = origin.distance_km(ap)
        print(f"  {ap.code}  {ap.name:<22} {ap.country:<18} {dist:>7,.0f} km" if dist
              else f"  {ap.code}  {ap.name:<22} {ap.country}")
    print(f"\n{len(picked)} destinations", file=sys.stderr)
    return 0


def cmd_history(args) -> int:
    with PriceHistory(args.db) as hist:
        if args.dest:
            series = hist.series(args.origin.upper(), args.dest.upper(), window_label=args.window)
            if not series:
                print("no observations for that route/window", file=sys.stderr)
                return 1
            for when, price in series:
                print(f"{when}  {price:>10,.0f}")
            median, n = hist.baseline(
                args.origin.upper(), args.dest.upper(), window_label=args.window
            )
            print(f"\nmedian {median:,.0f} over {n} observation(s)")
        else:
            for day, count in hist.scans():
                print(f"{day}  {count:>6} observations")
    return 0


def cmd_url(args) -> int:
    provider = providers.build("google-flights")
    print(
        provider.search_url(
            SearchRequest(
                origin=args.origin.upper(),
                destination=args.destination.upper(),
                depart_date=args.depart,
                return_date=args.ret,
                currency=args.currency,
            )
        )
    )
    return 0


COMMANDS = {
    "scan": cmd_scan,
    "destinations": cmd_destinations,
    "history": cmd_history,
    "url": cmd_url,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
