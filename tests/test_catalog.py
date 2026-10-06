from flightdeals import catalog


def test_distance_brisbane_singapore_is_about_6000km():
    bne, sin = catalog.get("BNE"), catalog.get("SIN")
    assert 5900 < bne.distance_km(sin) < 6200


def test_select_drops_origin_and_respects_tier():
    picked = catalog.select(origin="BNE")
    codes = {ap.code for ap in picked}
    assert "BNE" not in codes
    assert "SIN" in codes
    assert "OOL" not in codes  # tier 2
    assert "OOL" in {ap.code for ap in catalog.select(origin="BNE", include_tier2=True)}


def test_select_by_region_and_exclude():
    picked = catalog.select(origin="BNE", regions=["Europe"], exclude=["LHR"])
    codes = {ap.code for ap in picked}
    assert "CDG" in codes and "LHR" not in codes and "SIN" not in codes


def test_unknown_code_degrades_gracefully():
    ap = catalog.get("ZZZ")
    assert ap.code == "ZZZ"
    assert ap.distance_km(catalog.get("BNE")) is None
