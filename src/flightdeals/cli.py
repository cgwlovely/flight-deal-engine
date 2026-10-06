"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__, catalog, profile, providers, radar, report, windows
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


# -- argument groups shared by `scan` and `radar` ----------------------------
def _add_selection_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--origin", default="BNE", help="origin IATA code (default: BNE)")
    p.add_argument("--dest", type=_csv_list, help="explicit destinations, comma separated")
    p.add_argument("--region", type=_csv_list, help="limit to these catalogue regions")
    p.add_argument("--exclude", type=_csv_list, help="skip these destinations")
    p.add_argument("--all", action="store_true", help="include tier-2 destinations too")
    p.add_argument(
        "--wishlist", action="store_true", help="use the wishlist from your profile"
    )
    p.add_argument(
        "--unvisited",
        action="store_true",
        help="drop anywhere your profile marks as already visited",
    )
    p.add_argument("--profile", type=Path, help=f"profile file (default: {profile.DEFAULT_PATH})")


def _add_window_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--window",
        default="christmas",
        help="preset name (christmas, new-year), 'monthly' for a seasonality "
        "sweep, or 'custom' with --depart/--return",
    )
    p.add_argument("--year", type=int, help="year for a seasonal preset")
    p.add_argument("--depart", type=_parse_date, help="custom departure date")
    p.add_argument("--return", dest="ret", type=_parse_date, help="custom return date")
    p.add_argument(
        "--flex", type=int, default=0, help="with --depart, also try +/- N days (fixed length)"
    )
    p.add_argument("--months", type=int, default=12, help="with --window monthly: how many months")
    p.add_argument("--nights", type=int, default=7, help="with --window monthly: trip length")
    p.add_argument("--day", type=int, default=15, help="with --window monthly: day of month")
    p.add_argument("--one-way", action="store_true", help="price one-way instead of return")


def _add_search_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--children", type=int, default=0)
    p.add_argument(
        "--seat", default="economy", choices=["economy", "premium-economy", "business", "first"]
    )
    p.add_argument("--currency", default="AUD")
    p.add_argument("--max-stops", type=int, default=None)
    p.add_argument("--max-hours", type=float, default=None, help="cap total travel time")
    p.add_argument(
        "--no-self-transfer",
        action="store_true",
        help="drop separate-ticket and self-transfer itineraries",
    )
    p.add_argument("--bags", type=int, default=0, help="carry-on bags to price in")
    p.add_argument("--checked-bags", type=int, default=0, help="checked bags to price in")
    p.add_argument(
        "--airlines",
        type=_csv_list,
        help="only these marketing carriers, e.g. CZ,MU,CA. Use when you want to "
        "fly them — NOT to compare their prices: the filter also narrows which "
        "fares are offered and returns a dearer price for the same flights. To ask "
        "whether a carrier is cheap, scan unfiltered and read the carrier table.",
    )
    p.add_argument("--provider", default="google-flights", choices=providers.available())
    p.add_argument("--workers", type=int, default=4, help="concurrent requests (default 4)")
    p.add_argument(
        "--min-interval", type=float, default=0.8, help="seconds between requests, jittered"
    )
    p.add_argument("--attempts", type=int, default=3, help="retries per route")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="price history database")
    p.add_argument("--quiet", action="store_true", help="no per-route progress output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flightdeals",
        description="Scan live airfares across many destinations and rank the deals.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="price every destination over one or more windows")
    _add_selection_args(scan_p)
    _add_window_args(scan_p)
    _add_search_args(scan_p)
    scan_p.add_argument("--out", type=Path, default=Path("out"), help="report directory")
    scan_p.add_argument("--no-history", action="store_true", help="do not read or write history")
    scan_p.add_argument("--limit", type=int, default=30, help="rows per table in reports")

    radar_p = sub.add_parser(
        "radar",
        help="scan watched routes and report only fares that are unusual for themselves",
    )
    _add_selection_args(radar_p)
    _add_window_args(radar_p)
    _add_search_args(radar_p)
    radar_p.add_argument("--out", type=Path, default=None, help="also write full reports here")
    radar_p.add_argument(
        "--threshold",
        type=float,
        default=radar.DEFAULT_THRESHOLD_PCT,
        help="percent below a route's own median to count as an anomaly",
    )
    radar_p.add_argument(
        "--record-margin",
        type=float,
        default=radar.DEFAULT_RECORD_MARGIN_PCT,
        help="percent a new low must beat the old one by before it counts",
    )
    radar_p.add_argument(
        "--min-observations",
        type=int,
        default=radar.MIN_OBSERVATIONS,
        help="prior observations required before an anomaly is claimed",
    )
    radar_p.add_argument("--notify", action="store_true", help="post a macOS notification")
    radar_p.add_argument(
        "--show-watching", action="store_true", help="also list routes still building a baseline"
    )

    dest_p = sub.add_parser("destinations", help="show the destination catalogue")
    _add_selection_args(dest_p)

    prof_p = sub.add_parser("profile", help="show or create your traveller profile")
    prof_p.add_argument("--profile", type=Path, help="profile file")
    prof_p.add_argument("--init", action="store_true", help="write an example profile")

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


# -- shared plumbing --------------------------------------------------------
def select_destinations(args) -> list:
    """Resolve the destination list from flags plus the traveller profile."""
    prof = profile.load(getattr(args, "profile", None))
    explicit = args.dest
    if getattr(args, "wishlist", False):
        if not prof.wishlist:
            raise SystemExit(
                f"--wishlist needs a wishlist in {prof.path}; run `flightdeals profile --init`"
            )
        explicit = (explicit or []) + prof.wishlist

    picked = catalog.select(
        origin=args.origin,
        regions=args.region,
        include_tier2=args.all or bool(explicit),
        explicit=explicit,
        exclude=args.exclude,
    )
    if getattr(args, "unvisited", False):
        before = len(picked)
        picked = prof.unvisited(picked)
        if before and not picked:
            raise SystemExit("--unvisited removed every destination; check your profile")
    return picked


def windows_from_args(args) -> list[Window]:
    if args.depart:
        ret = None if args.one_way else args.ret
        if args.flex:
            return windows.around(args.depart, ret, flex_days=args.flex)
        return [Window(args.depart, ret, name="custom")]
    if args.window == "monthly":
        return windows.monthly(months=args.months, nights=args.nights, day=args.day)
    if args.window == "custom":
        raise SystemExit("--window custom needs --depart (and usually --return)")
    picked = windows.preset(args.window, args.year)
    if args.one_way:
        picked = [Window(w.depart, None, name=w.name) for w in picked]
    return picked


def build_spec(args, destinations: list, window_list: list[Window]) -> ScanSpec:
    return ScanSpec(
        origin=args.origin.upper(),
        destinations=[ap.code for ap in destinations],
        windows=window_list,
        adults=args.adults,
        children=args.children,
        seat=args.seat,
        currency=args.currency,
        max_stops=args.max_stops,
        max_duration_minutes=int(args.max_hours * 60) if args.max_hours else None,
        airlines=tuple(c.upper() for c in (args.airlines or ())),
        hide_self_transfer=args.no_self_transfer,
        carry_on_bags=args.bags,
        checked_bags=args.checked_bags,
    )


def run_scan(args, spec: ScanSpec, *, history: PriceHistory | None):
    total = len(spec.destinations) * len(spec.windows)
    print(
        f"Scanning {len(spec.destinations)} destinations from {spec.origin} "
        f"over {len(spec.windows)} window(s) = {total} searches",
        file=sys.stderr,
    )
    for window in spec.windows:
        print(f"  · {window.describe()}", file=sys.stderr)

    done = {"n": 0}

    def progress(request: SearchRequest, quote, error):
        done["n"] += 1
        if args.quiet:
            return
        if quote is not None:
            tail = "direct" if quote.cheapest.is_nonstop else f"{quote.cheapest.stops_out} stop"
            price = f"{quote.cheapest.price:,.0f} {spec.currency}"
            print(f"[{done['n']}/{total}] {request.label()}  {price}  {tail}", file=sys.stderr)
        else:
            print(f"[{done['n']}/{total}] {request.label()}  — {error}", file=sys.stderr)

    return scan(
        spec,
        providers.build(args.provider),
        history=history,
        workers=args.workers,
        attempts=args.attempts,
        limiter=RateLimiter(min_interval=args.min_interval),
        on_result=progress,
    )


def report_shortfalls(result) -> None:
    if result.failures:
        print(f"{len(result.failures)} route(s) returned no price.", file=sys.stderr)
    blank = result.blank_windows
    if blank:
        print(
            f"{len(blank)} window(s) priced nothing for any destination "
            f"({blank[0].depart} onwards) — most likely beyond the airlines' "
            "booking horizon, not unflown.",
            file=sys.stderr,
        )


# -- commands ---------------------------------------------------------------
def cmd_scan(args) -> int:
    destinations = select_destinations(args)
    if not destinations:
        print("no destinations selected", file=sys.stderr)
        return 1

    window_list = windows_from_args(args)
    spec = build_spec(args, destinations, window_list)
    history = None if args.no_history else PriceHistory(args.db)
    try:
        result = run_scan(args, spec, history=history)
    finally:
        if history is not None:
            history.close()

    if len(window_list) > 1 and len(destinations) <= 12:
        # Many windows, few destinations: the question is "when", so pivot to a
        # window-by-destination matrix instead of printing a table per window.
        report.print_matrix(result)
        report.print_combos(result, limit=args.limit)
        report.print_carriers(result)
    else:
        for window in window_list:
            deals = result.deals_by_window.get(window.label, [])
            if deals:
                report.print_table(deals, title=window.describe(), limit=args.limit)

    written = report.write_all(result, args.out)
    print("\nWrote:", file=sys.stderr)
    for kind, path in written.items():
        print(f"  {kind:9} {path}", file=sys.stderr)
    report_shortfalls(result)
    return 0


def cmd_radar(args) -> int:
    destinations = select_destinations(args)
    if not destinations:
        print("no destinations selected", file=sys.stderr)
        return 1

    spec = build_spec(args, destinations, windows_from_args(args))
    with PriceHistory(args.db) as history:
        result = run_scan(args, spec, history=history)
        findings = radar.detect(
            result,
            history,
            threshold_pct=args.threshold,
            record_margin_pct=args.record_margin,
            min_observations=args.min_observations,
        )

    alerts = radar.alerts(findings)
    print()
    if alerts:
        print(f"{len(alerts)} anomaly/anomalies:")
        for finding in alerts:
            print(f"  [{finding.kind}] {finding.message(spec.currency)}")
    else:
        print("No anomalies: every priced route is normal for itself.")

    watching = [f for f in findings if not f.is_alert]
    if watching:
        if args.show_watching:
            print(f"\nStill building a baseline ({len(watching)}):")
            for finding in watching:
                print(f"  {finding.message(spec.currency)}")
        else:
            print(
                f"({len(watching)} route(s) still building a baseline — "
                f"need {args.min_observations} runs before anomalies can be claimed; "
                "pass --show-watching to list them)"
            )

    if args.notify and alerts:
        delivered = radar.notify_macos([f.message(spec.currency) for f in alerts[:4]])
        if not delivered:
            print("(could not post a desktop notification)", file=sys.stderr)

    if args.out:
        written = report.write_all(result, args.out)
        print("\nWrote:", file=sys.stderr)
        for kind, path in written.items():
            print(f"  {kind:9} {path}", file=sys.stderr)

    report_shortfalls(result)
    return 0


def cmd_destinations(args) -> int:
    picked = select_destinations(args)
    origin = catalog.get(args.origin)
    current = None
    for ap in picked:
        if ap.region != current:
            current = ap.region
            print(f"\n{current}")
        dist = origin.distance_km(ap)
        print(
            f"  {ap.code}  {ap.name:<22} {ap.country:<18} {dist:>7,.0f} km"
            if dist
            else f"  {ap.code}  {ap.name:<22} {ap.country}"
        )
    print(f"\n{len(picked)} destinations", file=sys.stderr)
    return 0


def cmd_profile(args) -> int:
    if args.init:
        path = profile.init(args.profile)
        print(f"profile at {path}")
    prof = profile.load(args.profile)
    print(prof.describe())
    if prof.is_empty:
        print("\n(empty — edit the file, or run `flightdeals profile --init` to create it)")
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
    "radar": cmd_radar,
    "destinations": cmd_destinations,
    "profile": cmd_profile,
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
