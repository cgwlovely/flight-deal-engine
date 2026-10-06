from flightdeals import catalog, profile


def write(tmp_path, text):
    p = tmp_path / "profile.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_profile_is_empty_not_an_error(tmp_path):
    prof = profile.load(tmp_path / "nope.yaml")
    assert prof.is_empty
    assert prof.unvisited([catalog.get("SIN")]) == [catalog.get("SIN")]


def test_visited_regions_countries_and_airports_all_filter(tmp_path):
    path = write(
        tmp_path,
        """
visited:
  regions: [Southeast Asia]
  countries: [New Zealand]
  airports: [HNL]
wishlist: [NAN, APW]
""",
    )
    prof = profile.load(path)
    picked = [catalog.get(c) for c in ["SIN", "DPS", "AKL", "HNL", "NAN", "PPT"]]
    assert [ap.code for ap in prof.unvisited(picked)] == ["NAN", "PPT"]
    assert prof.wishlist == ["NAN", "APW"]


def test_region_and_country_matching_is_case_insensitive(tmp_path):
    path = write(tmp_path, "visited:\n  regions: [southeast ASIA]\n  countries: [new zealand]\n")
    prof = profile.load(path)
    assert prof.has_visited(catalog.get("SIN"))
    assert prof.has_visited(catalog.get("AKL"))
    assert not prof.has_visited(catalog.get("NAN"))


def test_init_writes_an_example_and_never_clobbers(tmp_path):
    path = tmp_path / "profile.yaml"
    assert profile.init(path) == path
    assert "wishlist" in path.read_text()

    path.write_text("wishlist: [NAN]\n", encoding="utf-8")
    profile.init(path)
    assert path.read_text() == "wishlist: [NAN]\n", "an existing profile is left alone"
    profile.init(path, overwrite=True)
    assert "visited" in path.read_text()


def test_empty_sections_are_tolerated(tmp_path):
    path = write(tmp_path, "visited:\nwishlist:\n")
    prof = profile.load(path)
    assert prof.is_empty and prof.wishlist == []
