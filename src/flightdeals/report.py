"""Reporters: terminal table, Markdown, CSV, JSON and a standalone HTML page."""

from __future__ import annotations

import csv
import html
import json
import statistics
from datetime import datetime
from pathlib import Path

from . import catalog
from .models import Deal
from .scanner import ScanResult


def _fmt_money(value: float, currency: str) -> str:
    symbol = {"AUD": "A$", "USD": "US$", "EUR": "EUR ", "GBP": "GBP "}.get(currency, currency + " ")
    return f"{symbol}{value:,.0f}"


def _duration(minutes: int | None) -> str:
    if not minutes:
        return "-"
    return f"{minutes // 60}h{minutes % 60:02d}"


def _stops(deal: Deal) -> str:
    it = deal.itinerary
    out = "direct" if it.stops_out == 0 else f"{it.stops_out} stop"
    if it.stops_back is not None and it.stops_back != it.stops_out:
        out += f" / {it.stops_back or 'direct'}"
    return out


def _rows(deals: list[Deal]) -> list[dict]:
    rows = []
    for rank, deal in enumerate(deals, start=1):
        it = deal.itinerary
        ap = catalog.get(it.destination)
        rows.append(
            {
                "rank": rank,
                "code": it.destination,
                "city": ap.name,
                "country": ap.country,
                "region": ap.region,
                "price": round(it.price, 2),
                "currency": it.currency,
                "depart": it.depart_date.isoformat(),
                "return": it.return_date.isoformat() if it.return_date else "",
                "nights": it.nights or "",
                "airlines": ", ".join(it.airlines),
                "stops": _stops(deal),
                "flight_time": _duration(it.duration_out_min),
                "distance_km": round(deal.distance_km) if deal.distance_km else "",
                "cents_per_km": round(deal.cents_per_km, 2) if deal.cents_per_km else "",
                "deal_score": round(deal.deal_score, 1),
                "median_price": round(deal.median_price, 2) if deal.median_price else "",
                "discount_pct": (
                    round(deal.discount_pct, 1) if deal.discount_pct is not None else ""
                ),
                "observations": deal.observations,
                "notes": "; ".join(deal.notes),
            }
        )
    return rows


# -- window x destination matrix -------------------------------------------
def matrix_rows(result: ScanResult) -> tuple[list[str], list[dict]]:
    """Pivot a scan into one row per window and one column per destination.

    When a scan holds many windows and few destinations the interesting question
    is "when", not "where", and a table per window buries it. This is the wide,
    rectangular form of the same data: one price per cell, ready to pivot or chart.
    """
    prices: dict[str, dict[str, float]] = {}
    for window in result.spec.windows:
        for deal in result.deals_by_window.get(window.label, []):
            prices.setdefault(window.label, {})[deal.itinerary.destination] = (
                deal.itinerary.price
            )

    # Columns ordered by each destination's median across the scan: cheapest first.
    seen: dict[str, list[float]] = {}
    for row in prices.values():
        for code, price in row.items():
            seen.setdefault(code, []).append(price)
    codes = sorted(seen, key=lambda c: statistics.median(seen[c]))

    rows: list[dict] = []
    for window in result.spec.windows:
        cells = prices.get(window.label, {})
        row: dict = {
            "window": window.name or window.label,
            "depart": window.depart.isoformat(),
            "return": window.ret.isoformat() if window.ret else "",
            "nights": window.nights or "",
        }
        for code in codes:
            row[code] = round(cells[code]) if code in cells else ""
        if cells:
            best = min(cells, key=lambda c: cells[c])
            row["cheapest"] = best
            row["cheapest_price"] = round(cells[best])
        else:
            row["cheapest"] = row["cheapest_price"] = ""
        rows.append(row)
    return codes, rows


def write_matrix_csv(result: ScanResult, path: Path) -> Path:
    _, rows = matrix_rows(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["window"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def print_matrix(result: ScanResult, *, max_cols: int = 14) -> None:
    from rich.console import Console
    from rich.table import Table

    codes, rows = matrix_rows(result)
    shown = codes[:max_cols]
    cur = result.spec.currency
    nights = {w.nights for w in result.spec.windows}
    length = f"{nights.pop()}n" if len(nights) == 1 else "mixed length"

    table = Table(
        title=f"{result.spec.origin} -> {len(codes)} destination(s), {length}, {cur}",
        header_style="bold",
    )
    table.add_column("Window", no_wrap=True)
    for code in shown:
        table.add_column(code, justify="right", no_wrap=True)
    table.add_column("Cheapest", no_wrap=True)

    # Highlight each destination's own best month rather than the row minimum,
    # which would only ever mark the nearest airport.
    best_per_code = {
        code: min((r[code] for r in rows if r.get(code) != ""), default=None) for code in shown
    }
    for row in rows:
        cells = []
        for code in shown:
            value = row.get(code, "")
            if value == "":
                cells.append("[dim]-[/dim]")
            elif value == best_per_code[code]:
                cells.append(f"[bold green]{value:,}[/bold green]")
            else:
                cells.append(f"{value:,}")
        best = f"{row['cheapest']} {row['cheapest_price']:,}" if row["cheapest"] else "-"
        table.add_row(row["window"], *cells, best)
    Console().print(table)
    if len(codes) > max_cols:
        print(f"({len(codes) - max_cols} more destination(s) in the CSV)")


# -- cheapest window x destination combinations -----------------------------
def best_combinations(result: ScanResult, *, limit: int | None = None) -> list[dict]:
    """Flatten a multi-window scan into (window, destination) pairs, cheapest first.

    For a traveller who is flexible on dates, neither "which city" nor "which
    month" is the question on its own -- the answer is a *pair*, and a cheap city
    in its dear month loses to a dear city in its cheap one. The matrix shows the
    whole grid; this ranks the individual cells.
    """
    rows: list[dict] = []
    for window in result.spec.windows:
        for deal in result.deals_by_window.get(window.label, []):
            it = deal.itinerary
            ap = catalog.get(it.destination)
            rows.append(
                {
                    "window": window.name or window.label,
                    "depart": it.depart_date.isoformat(),
                    "return": it.return_date.isoformat() if it.return_date else "",
                    "nights": it.nights or "",
                    "code": it.destination,
                    "city": ap.name,
                    "country": ap.country,
                    "price": round(it.price),
                    "currency": it.currency,
                    "stops": _stops(deal),
                    "flight_time": _duration(it.duration_out_min),
                    "cents_per_km": round(deal.cents_per_km, 2) if deal.cents_per_km else "",
                    "airlines": ", ".join(it.airlines),
                }
            )
    rows.sort(key=lambda r: r["price"])
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows[:limit] if limit else rows


def write_combos_csv(result: ScanResult, path: Path) -> Path:
    rows = best_combinations(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["rank"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def print_combos(result: ScanResult, *, limit: int = 20) -> None:
    from rich.console import Console
    from rich.table import Table

    rows = best_combinations(result, limit=limit)
    if not rows:
        return
    cur = result.spec.currency
    table = Table(
        title=f"Cheapest date x destination combinations from {result.spec.origin}",
        header_style="bold",
    )
    for name, justify in [
        ("#", "right"),
        ("Depart", "left"),
        ("Destination", "left"),
        ("Price", "right"),
        ("Stops", "left"),
        ("Flight", "right"),
        ("c/km", "right"),
        ("Airline", "left"),
    ]:
        table.add_column(name, justify=justify, no_wrap=(name != "Airline"))
    for r in rows:
        table.add_row(
            str(r["rank"]),
            r["depart"],
            f"{r['city']} ({r['code']})",
            _fmt_money(r["price"], cur),
            r["stops"],
            r["flight_time"],
            str(r["cents_per_km"]),
            (r["airlines"] or "-")[:26],
        )
    Console().print(table)


# -- carrier attribution ----------------------------------------------------
def carrier_summary(result: ScanResult) -> list[dict]:
    """Which carriers actually hold the cheapest fares, counted over the scan.

    This is the sound way to ask "is carrier X cheap on this market". The obvious
    alternative -- rerun the scan with `--airlines X` and compare -- does not work:
    Google returns a *dearer* fare for the identical carrier and routing once the
    filter is applied (observed A$1,390 unfiltered vs A$1,767 filtered on the same
    BNE-CAN-IST itinerary, same minute), because the filter also narrows which fare
    and ticketing combinations are considered. So attribute the open-market winners
    instead of re-pricing a restricted market.

    Grouped by primary marketing carrier, which needs no taxonomy and so cannot
    quietly encode an opinion about which airlines belong together.
    """
    cells = best_combinations(result)
    groups: dict[str, list[dict]] = {}
    for cell in cells:
        primary = (cell["airlines"].split(",")[0] or "unknown").strip()
        groups.setdefault(primary, []).append(cell)

    rows = []
    for carrier, won in groups.items():
        prices = [c["price"] for c in won]
        best = min(won, key=lambda c: c["price"])
        rows.append(
            {
                "carrier": carrier,
                "cells_won": len(won),
                "share_pct": round(100.0 * len(won) / len(cells), 1) if cells else 0,
                "median_price": round(statistics.median(prices)),
                "cheapest": min(prices),
                "best_city": best["city"],
                "best_depart": best["depart"],
                "currency": result.spec.currency,
            }
        )
    rows.sort(key=lambda r: (-r["cells_won"], r["median_price"]))
    return rows


def write_carriers_csv(result: ScanResult, path: Path) -> Path:
    rows = carrier_summary(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["carrier"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def print_carriers(result: ScanResult, *, limit: int = 12) -> None:
    from rich.console import Console
    from rich.table import Table

    rows = carrier_summary(result)
    if not rows:
        return
    cur = result.spec.currency
    table = Table(title="Who holds the cheapest fares", header_style="bold")
    table.add_column("Carrier")
    table.add_column("Cells won", justify="right")
    table.add_column("Share", justify="right")
    table.add_column("Median", justify="right")
    table.add_column("Cheapest", justify="right")
    table.add_column("Best cell")
    for r in rows[:limit]:
        table.add_row(
            r["carrier"][:24],
            str(r["cells_won"]),
            f"{r['share_pct']}%",
            _fmt_money(r["median_price"], cur),
            _fmt_money(r["cheapest"], cur),
            f"{r['best_city']} {r['best_depart'][:7]}",
        )
    Console().print(table)


# -- terminal ---------------------------------------------------------------
def print_table(deals: list[Deal], *, title: str, limit: int = 25) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title=title, header_style="bold")
    for name, justify in [
        ("#", "right"),
        ("Destination", "left"),
        ("Price", "right"),
        ("Stops", "left"),
        ("Flight", "right"),
        ("c/km", "right"),
        ("Score", "right"),
        ("Airline", "left"),
    ]:
        table.add_column(name, justify=justify, no_wrap=(name != "Airline"))

    for row in _rows(deals)[:limit]:
        table.add_row(
            str(row["rank"]),
            f"{row['city']} ({row['code']})",
            _fmt_money(row["price"], row["currency"]),
            row["stops"],
            row["flight_time"],
            str(row["cents_per_km"]),
            str(row["deal_score"]),
            (row["airlines"] or "-")[:28],
        )
    Console().print(table)


# -- files ------------------------------------------------------------------
def write_csv(deals: list[Deal], path: Path) -> Path:
    rows = _rows(deals)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["rank"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_json(result: ScanResult, path: Path) -> Path:
    payload = {
        "origin": result.spec.origin,
        "currency": result.spec.currency,
        "scanned_at": result.started_at.isoformat(timespec="seconds"),
        "duration_s": round((result.finished_at - result.started_at).total_seconds(), 1),
        "destinations_requested": len(result.spec.destinations),
        "quotes": result.quoted,
        "windows": [
            {
                "label": w.label,
                "name": w.name,
                "depart": w.depart.isoformat(),
                "return": w.ret.isoformat() if w.ret else None,
                "nights": w.nights,
                "deals": _rows(result.deals_by_window.get(w.label, [])),
            }
            for w in result.spec.windows
        ],
        "failures": [
            {"route": f.request.label(), "reason": f.reason, "no_flights": f.no_flights}
            for f in result.failures
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_markdown(result: ScanResult, path: Path, *, limit: int = 30) -> Path:
    cur = result.spec.currency
    lines = [
        f"# Fares from {result.spec.origin} — scanned {result.started_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"{result.quoted} priced routes across {len(result.spec.windows)} travel window(s). "
        f"All prices are return, {result.spec.adults} adult, {result.spec.seat}, in {cur}.",
        "",
    ]
    for window in result.spec.windows:
        deals = result.deals_by_window.get(window.label, [])
        if not deals:
            continue
        lines += [
            f"## {window.describe()}",
            "",
            "| # | Destination | Price | Stops | Flight | c/km | Score | Airline |",
            "|--:|---|--:|---|--:|--:|--:|---|",
        ]
        for row in _rows(deals)[:limit]:
            lines.append(
                f"| {row['rank']} | {row['city']} ({row['code']}) | "
                f"{_fmt_money(row['price'], cur)} | {row['stops']} | {row['flight_time']} | "
                f"{row['cents_per_km']} | {row['deal_score']} | {row['airlines'] or '-'} |"
            )
        lines.append("")

    blank = result.blank_windows
    if blank:
        lines += [
            "## Windows with no prices at all",
            "",
            "Nothing priced in these windows for any destination, which usually means "
            "the airlines have not loaded schedules that far out rather than that "
            "nothing flies:",
            "",
        ]
        lines += [f"- {w.describe()}" for w in blank]
        lines.append("")

    blank_dates = {w.depart for w in blank}
    other = [f for f in result.failures if f.request.depart_date not in blank_dates]
    if other:
        lines += ["## Not priced", ""]
        for f in other:
            why = "no flights offered" if f.no_flights else f.reason
            lines.append(f"- `{f.request.label()}` — {why}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


HTML_TEMPLATE = """<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #fbfaf8; --panel: #ffffff; --ink: #1b1a18; --muted: #6c6762;
    --line: #e4e0da; --accent: #1f6f5c; --accent-soft: #e7f1ee; --warn: #9a5b10;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #16171a; --panel: #1e2024; --ink: #ecebe8; --muted: #a2a09b;
      --line: #32353b; --accent: #6fd3b4; --accent-soft: #1f3b34; --warn: #e0a95a;
    }
  }
  :root[data-theme="dark"] {
    --bg: #16171a; --panel: #1e2024; --ink: #ecebe8; --muted: #a2a09b;
    --line: #32353b; --accent: #6fd3b4; --accent-soft: #1f3b34; --warn: #e0a95a;
  }
  body { background: var(--bg); color: var(--ink); margin: 0;
         font: 15px/1.5 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif; }
  main { max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); margin: 0 0 28px; }
  h2 { font-size: 18px; margin: 36px 0 4px; }
  .note { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
  .wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px;
          background: var(--panel); }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line);
           white-space: nowrap; }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em;
       color: var(--muted); font-weight: 600; background: var(--bg); }
  tr:last-child td { border-bottom: 0; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .price { font-weight: 650; }
  .bar { position: relative; }
  .bar span { position: absolute; inset: 0 auto 0 0; background: var(--accent-soft);
              border-radius: 3px; z-index: 0; }
  .bar b { position: relative; z-index: 1; font-weight: 600; }
  .pill { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 12px;
          background: var(--accent-soft); color: var(--accent); }
  .muted { color: var(--muted); }
  footer { margin-top: 40px; color: var(--muted); font-size: 13px; }
</style>
<main>
<h1>__TITLE__</h1>
<p class="sub">__SUB__</p>
__SECTIONS__
<footer>__FOOTER__</footer>
</main>
"""


def write_html(
    result: ScanResult, path: Path, *, limit: int = 40, title: str | None = None
) -> Path:
    cur = result.spec.currency
    title = title or f"Christmas fares from {result.spec.origin}"
    sections: list[str] = []

    for window in result.spec.windows:
        deals = result.deals_by_window.get(window.label, [])
        if not deals:
            continue
        rows = _rows(deals)[:limit]
        max_score = max((r["deal_score"] for r in rows), default=100) or 100
        body = []
        for r in rows:
            width = 100.0 * r["deal_score"] / max_score
            body.append(
                "<tr>"
                f'<td class="num muted">{r["rank"]}</td>'
                f'<td><b>{html.escape(str(r["city"]))}</b> '
                f'<span class="muted">{r["code"]}</span></td>'
                f'<td class="muted">{html.escape(str(r["country"]))}</td>'
                f'<td class="num price">{_fmt_money(r["price"], cur)}</td>'
                f'<td>{html.escape(r["stops"])}</td>'
                f'<td class="num">{r["flight_time"]}</td>'
                f'<td class="num">{r["cents_per_km"]}</td>'
                f'<td class="num bar"><span style="width:{width:.0f}%"></span>'
                f'<b>{r["deal_score"]}</b></td>'
                f'<td class="muted">{html.escape((r["airlines"] or "-")[:34])}</td>'
                "</tr>"
            )
        sections.append(
            f"<h2>{html.escape(window.describe())}</h2>"
            f'<p class="note">{len(deals)} destinations priced. '
            "Score blends absolute price, price per kilometre, and any recorded history.</p>"
            '<div class="wrap"><table><thead><tr>'
            '<th class="num">#</th><th>Destination</th><th>Country</th>'
            '<th class="num">Return fare</th><th>Stops</th><th class="num">Flight</th>'
            '<th class="num">c/km</th><th class="num">Score</th><th>Airline</th>'
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        )

    sub = (
        f"{result.quoted} priced routes · {result.spec.adults} adult · "
        f"{result.spec.seat} · return fares in {cur} · "
        f"scanned {result.started_at:%d %b %Y %H:%M} UTC"
    )
    footer = (
        f"Source: Google Flights, live at scan time. Generated by flight-deal-engine. "
        f"{len(result.failures)} route(s) returned no price."
    )
    if result.blank_windows:
        footer += (
            f" {len(result.blank_windows)} window(s) priced nothing at all — most likely "
            "beyond the airlines' booking horizon rather than unflown."
        )
    page = (
        HTML_TEMPLATE.replace("__TITLE__", html.escape(title))
        .replace("__SUB__", html.escape(sub))
        .replace("__SECTIONS__", "\n".join(sections))
        .replace("__FOOTER__", html.escape(footer))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def write_all(result: ScanResult, out_dir: Path, *, stamp: str | None = None) -> dict[str, Path]:
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M")
    base = out_dir / f"{result.spec.origin.lower()}-{stamp}"
    written = {
        "json": write_json(result, base.with_suffix(".json")),
        "markdown": write_markdown(result, base.with_suffix(".md")),
        "html": write_html(result, base.with_suffix(".html")),
    }
    best = result.best_per_destination()
    if best:
        written["csv"] = write_csv(best, base.with_suffix(".csv"))
    if len(result.spec.windows) > 1:
        written["matrix"] = write_matrix_csv(result, base.with_name(base.name + "-matrix.csv"))
        written["combos"] = write_combos_csv(result, base.with_name(base.name + "-combos.csv"))
        written["carriers"] = write_carriers_csv(
            result, base.with_name(base.name + "-carriers.csv")
        )
    return written
