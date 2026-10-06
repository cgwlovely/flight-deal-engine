from datetime import date

from flightdeals.models import Itinerary, Leg, Quote
from flightdeals.scoring import Context, score_all


def itin(dest, price, *, legs=1, ret=True):
    return Itinerary(
        origin="BNE",
        destination=dest,
        depart_date=date(2026, 12, 20),
        return_date=date(2027, 1, 4) if ret else None,
        price=price,
        currency="AUD",
        airlines=["QF"],
        legs=[Leg("BNE", dest, None, None, 500)] * legs,
    )


def test_distance_normalisation_beats_raw_price():
    """A far destination at a low cents/km should outrank a near, cheap one."""
    near = Quote(cheapest=itin("SYD", 400))
    far = Quote(cheapest=itin("NRT", 900))
    deals = score_all(
        [(near, Context(distance_km=750)), (far, Context(distance_km=7000))]
    )
    assert deals[0].itinerary.destination == "NRT"
    assert deals[0].cents_per_km < deals[1].cents_per_km


def test_history_component_needs_enough_observations():
    quote = Quote(cheapest=itin("SIN", 1000))
    (thin,) = score_all([(quote, Context(distance_km=6000, median_price=2000, observations=1))])
    assert thin.history_score is None

    (rich,) = score_all([(quote, Context(distance_km=6000, median_price=2000, observations=5))])
    assert rich.discount_pct == 50.0
    assert rich.history_score == 100.0


def test_one_way_uses_single_distance():
    quote = Quote(cheapest=itin("SIN", 600, ret=False))
    (deal,) = score_all([(quote, Context(distance_km=6000))])
    assert deal.cents_per_km == 10.0


def test_missing_distance_still_scores():
    quote = Quote(cheapest=itin("XXX", 500))
    (deal,) = score_all([(quote, Context())])
    assert deal.cents_per_km is None
    assert deal.deal_score > 0


def test_nonstop_alternative_is_noted():
    cheap = itin("DPS", 800)
    cheap.legs = [Leg("BNE", "DRW", None, None, 230), Leg("DRW", "DPS", None, None, 170)]
    direct = itin("DPS", 950)
    quote = Quote(cheapest=cheap, alternatives=[direct])
    (deal,) = score_all([(quote, Context(distance_km=5000))])
    assert any("nonstop available" in n for n in deal.notes)
