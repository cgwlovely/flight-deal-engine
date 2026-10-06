"""Turn prices into a ranking.

A bare price list answers "what is cheapest", which is almost always the nearest
airport. A deal engine should answer "where does my money go furthest", so each
quote is scored on three things:

* **price**   -- the absolute fare, ranked against the rest of the scan.
* **cents/km** -- fare divided by round-trip great-circle distance, which is what
  separates "cheap because it is close" from "cheap for how far it is".
* **history** -- discount against the median previously recorded for the same
  route and travel window. Only available once the route has been scanned before.

Each component becomes a 0-100 score, then they are blended using whichever are
available (weights renormalise, so a first-ever scan still ranks sensibly).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Deal, Quote

WEIGHTS = {"cpk": 0.45, "price": 0.35, "history": 0.20}

#: A route needs at least this many prior observations before its own median is
#: treated as a baseline rather than noise.
MIN_OBSERVATIONS = 3


@dataclass(frozen=True)
class Context:
    """Everything the scorer needs that is not on the quote itself."""

    distance_km: float | None = None
    median_price: float | None = None
    observations: int = 0


def _percentile_rank(value: float, population: list[float]) -> float:
    """Fraction of the population at or below `value`, in [0, 1]."""
    if not population:
        return 0.5
    below = sum(1 for p in population if p < value)
    equal = sum(1 for p in population if p == value)
    return (below + 0.5 * equal) / len(population)


def score_all(pairs: list[tuple[Quote, Context]]) -> list[Deal]:
    """Score quotes against each other and return them ranked best-first."""
    prices = [q.cheapest.price for q, _ in pairs]
    cpks: list[float] = []
    for quote, ctx in pairs:
        cpk = _cents_per_km(quote, ctx)
        if cpk is not None:
            cpks.append(cpk)

    deals: list[Deal] = []
    for quote, ctx in pairs:
        it = quote.cheapest
        cpk = _cents_per_km(quote, ctx)

        price_score = 100.0 * (1.0 - _percentile_rank(it.price, prices))
        cpk_score = None if cpk is None else 100.0 * (1.0 - _percentile_rank(cpk, cpks))

        history_score = discount = None
        if ctx.median_price and ctx.observations >= MIN_OBSERVATIONS:
            discount = 100.0 * (ctx.median_price - it.price) / ctx.median_price
            # -25% dearer -> 0, at median -> 50, -25% cheaper -> 100.
            history_score = max(0.0, min(100.0, 50.0 + 2.0 * discount))

        components = {"price": price_score, "cpk": cpk_score, "history": history_score}
        usable = {k: v for k, v in components.items() if v is not None}
        total_weight = sum(WEIGHTS[k] for k in usable) or 1.0
        deal_score = sum(WEIGHTS[k] * v for k, v in usable.items()) / total_weight

        deals.append(
            Deal(
                quote=quote,
                distance_km=ctx.distance_km,
                cents_per_km=cpk,
                price_score=price_score,
                cpk_score=cpk_score,
                history_score=history_score,
                median_price=ctx.median_price,
                discount_pct=discount,
                observations=ctx.observations,
                deal_score=deal_score,
                notes=_notes(quote, cpk, discount),
            )
        )

    return sorted(deals, key=lambda d: d.deal_score, reverse=True)


def _cents_per_km(quote: Quote, ctx: Context) -> float | None:
    if not ctx.distance_km:
        return None
    legs = 2 if quote.cheapest.return_date else 1
    return quote.cheapest.price * 100.0 / (ctx.distance_km * legs)


def _notes(quote: Quote, cpk: float | None, discount: float | None) -> list[str]:
    notes: list[str] = []
    it = quote.cheapest
    if it.is_nonstop:
        notes.append("nonstop")
    else:
        nonstop = quote.cheapest_nonstop
        if nonstop is not None:
            extra = nonstop.price - it.price
            notes.append(f"nonstop available +{extra:,.0f}")
    if cpk is not None and cpk < 5:
        notes.append("very low cents/km")
    if discount is not None:
        notes.append(f"{discount:+.0f}% vs own median")
    return notes
