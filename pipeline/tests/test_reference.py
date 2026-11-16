"""Tests for the tile grid, river buffers, district matching and the dry-run loader."""

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from galamsey import grid as gridmod
from galamsey.geo import UTM30N, area_ha, buffer_metres
from galamsey.ingest_reference import __main__ as cli
from galamsey.ingest_reference.layers import osm_rivers_from_overpass
from galamsey.ingest_reference.priority import match_districts, normalise_name
from galamsey.ingest_reference.sources import LayerData, Source, SourceUnavailable, first_available

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def ghana():
    return gpd.read_file(FIX / "ghana_outline_naturalearth.geojson")


@pytest.fixture(scope="module")
def ghana_grid(ghana):
    return gridmod.build_grid(ghana)


# --- grid -------------------------------------------------------------------

def test_grid_covers_ghana_with_expected_tile_count(ghana, ghana_grid):
    # Ghana is ~238,500 km² → ~2,385 full tiles, plus partial edge tiles.
    assert 2_400 < len(ghana_grid) < 2_900
    covered = ghana_grid.to_crs(UTM30N).union_all()
    country = ghana.to_crs(UTM30N).union_all()
    assert country.difference(covered).area / country.area < 1e-6   # nothing left uncovered


def test_tiles_are_10km_squares_with_unique_stable_ids(ghana, ghana_grid):
    areas = ghana_grid.to_crs(UTM30N).area
    assert areas.round().eq(100_000_000).all()
    assert ghana_grid["id"].is_unique
    rebuilt = gridmod.build_grid(ghana)
    assert list(rebuilt["id"]) == list(ghana_grid["id"])               # re-runs give identical ids
    assert ghana_grid["id"].str.fullmatch(r"E\d{4}_N\d{4}").all()


def test_tile_id_encodes_south_west_corner_km():
    assert gridmod.tile_id(610_000, 700_000) == "E0610_N0700"
    assert gridmod.tile_id(615_432, 709_999) == "E0615_N0709"


def test_priority_flag_marks_tiles_touching_zones(ghana_grid):
    atewa = gpd.GeoDataFrame({"name": ["Atewa test zone"]}, geometry=[box(-0.65, 6.15, -0.50, 6.30)], crs="EPSG:4326")
    flagged = gridmod.flag_priority(ghana_grid, atewa)
    high = flagged[flagged["priority"] == "high"]
    assert 2 <= len(high) <= 9                                          # a ~16x16 km box touches a few tiles
    assert set(flagged["priority"]) == {"high", "normal"}
    assert (gridmod.flag_priority(ghana_grid, None)["priority"] == "normal").all()


# --- buffers ----------------------------------------------------------------

def test_river_buffers_are_dissolved_per_river_and_sized_in_metres():
    rivers = osm_rivers_from_overpass(json.loads((FIX / "overpass_rivers.json").read_text()))
    major = cli.select_major_rivers(rivers, ["Birim", "Offin", "Pra"])
    assert sorted(major["river"].unique()) == ["Birim", "Offin"]       # "Birim River" and "Birim" both match
    buf = buffer_metres(major[["river", "geometry"]], 100, dissolve_by="river")
    assert len(buf) == 2 and buf.crs.to_string() == "EPSG:4326"
    offin = buf[buf["river"] == "Offin"].geometry.iloc[0]
    length_m = major[major["river"] == "Offin"].to_crs(UTM30N).length.sum()
    expected_ha = (length_m * 200 + 3.14159 * 100**2) / 10_000          # a 200 m wide ribbon with round ends
    assert area_ha(offin) == pytest.approx(expected_ha, rel=0.02)


def test_overpass_parser_skips_nodes_and_degenerate_ways():
    rivers = osm_rivers_from_overpass(json.loads((FIX / "overpass_rivers.json").read_text()))
    assert sorted(rivers["osm_id"]) == [1, 2, 3, 4]


# --- district matching ------------------------------------------------------

def test_normalise_name_ignores_case_punctuation_and_admin_words():
    assert normalise_name("Prestea Huni-Valley Municipal") == normalise_name("prestea-huni valley")
    assert normalise_name("Obuasi Municipal") == "obuasi"


def test_match_districts_reports_missing_names():
    districts = gpd.GeoDataFrame(
        {"name": ["Amansie West", "Atiwa West District", "Accra Metropolitan"]},
        geometry=[box(0, 0, 1, 1), box(1, 1, 2, 2), box(2, 2, 3, 3)], crs="EPSG:4326")
    zones, missing = match_districts(districts, {"Ashanti": ["Amansie West"], "Eastern": ["Atiwa West", "Nowhere"]})
    assert sorted(zones["name"]) == ["Amansie West", "Atiwa West District"]
    assert missing == ["Nowhere (Eastern)"]


# --- source chain -----------------------------------------------------------

def test_source_chain_falls_back_and_records_why():
    def down():
        raise SourceUnavailable("not configured")
    good = gpd.GeoDataFrame({"name": ["x"]}, geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")
    data, skipped = first_available([
        Source("agency:official", "terms", True, down),
        Source("open:fallback", "CC BY", False, lambda: good),
    ])
    assert data.source == "open:fallback" and data.verified is False and data.licence == "CC BY"
    assert skipped == ["agency:official: not configured"]


def test_source_chain_raises_when_nothing_works():
    def down():
        raise SourceUnavailable("offline")
    with pytest.raises(SourceUnavailable):
        first_available([Source("a", "", False, down)])


# --- end-to-end dry run (no database, no network) ----------------------------

def test_dry_run_writes_grid_and_priority_layers(tmp_path, ghana, monkeypatch):
    store = cli.FileStore(tmp_path)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    store.write("admin0", LayerData(ghana, "test:naturalearth", "Public domain", False, now))
    districts = gpd.GeoDataFrame({"name": ["Atiwa West", "Tamale Metropolitan"]},
                                 geometry=[box(-0.75, 6.1, -0.5, 6.35), box(-0.9, 9.3, -0.7, 9.5)], crs="EPSG:4326")
    store.write("admin2", LayerData(districts, "test", "", False, now))
    monkeypatch.setattr(cli, "load_config", lambda: {"priority_districts": {"Eastern": ["Atiwa West"]}})

    cli.step_priority_zones(store)
    cli.step_grid(store)

    tiles = gpd.read_file(tmp_path / "tiles.geojson")
    zones = gpd.read_file(tmp_path / "priority_zone.geojson")
    assert list(zones["name"]) == ["Atiwa West"] and zones["source"].iloc[0] == "galamsey_watch:priority_districts"
    assert (tiles["priority"] == "high").sum() >= 4
