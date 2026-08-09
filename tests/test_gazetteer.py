"""Gazetteer: DMS parsing, FTS5 safety, ranking, index population."""

from __future__ import annotations

import sqlite3

import pytest

from oceanpulse.gazetteer.build import (
    attach_city_population,
    fold,
    parse_dms,
    parse_geonames,
    parse_wpi,
    write_gazetteer,
)
from oceanpulse.gazetteer.store import GazetteerStore, build_match_query
from oceanpulse.ingest.grid import OceanMask
from oceanpulse.models import PortRecord
from tests.conftest import GEONAMES_TSV, WPI_CSV


# -- DMS parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30°20'00\"N", 30.333333),
        ("48°17'00\"E", 48.283333),
        ("33°51'00\"S", -33.85),
        ("118°25'00\"W", -118.416667),
        ("00°00'00\"N", 0.0),
        ("30.5N", 30.5),
    ],
)
def test_parse_dms(text, expected):
    """World Port Index coordinates are DMS strings, not decimals.

    Reading them with float() yields None or a wrong value for every row, and
    piles every port in a country onto one point.
    """
    assert parse_dms(text) == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("text", ["", "   ", "not-a-coordinate", None])
def test_parse_dms_rejects_junk(text):
    assert parse_dms(text) is None


def test_parse_wpi_skips_unparseable_rows():
    records = parse_wpi(WPI_CSV)
    assert len(records) == 2
    by_name = {r.port_name: r for r in records}
    assert by_name["Abadan"].latitude == pytest.approx(30.3333, abs=1e-3)
    assert by_name["Sydney"].latitude == pytest.approx(-33.85, abs=1e-3)
    assert by_name["Sydney"].longitude == pytest.approx(151.2, abs=1e-3)


# -- coastal filtering -----------------------------------------------------


def test_geonames_filtered_against_ocean_mask():
    """An ocean application should not offer landlocked cities."""
    mask = OceanMask()
    # Mark the Arabian Sea near Mumbai as ocean; leave inland India dry.
    for lat in range(15, 22):
        for lon in range(68, 73):
            mask.set_ocean(lat + 0.5, lon + 0.5)

    kept = parse_geonames(GEONAMES_TSV, mask, coastal_max_km=60.0)
    names = {r.port_name for r in kept}
    assert "Mumbai" in names
    assert "Delhi" not in names


def test_no_mask_keeps_everything():
    kept = parse_geonames(GEONAMES_TSV, OceanMask(), coastal_max_km=60.0)
    assert len(kept) == 2


# -- FTS5 query safety -----------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "St. John's",
        "a OR b",
        "*",
        '"',
        "NEAR(a b)",
        "x\" OR \"1\"=\"1",
        "port^2",
        "-exclude",
        "(unbalanced",
    ],
)
def test_match_query_never_produces_fts_syntax(hostile):
    """FTS5 MATCH takes a query language, so raw input is executable syntax.

    An unescaped apostrophe raises OperationalError from inside a keystroke
    callback, which is about the worst place for an exception.
    """
    query = build_match_query(hostile)
    if query is None:
        return
    assert "OR" not in query.replace('"or"', "")
    # Every wildcard must be attached to a quoted token.
    assert query.count("*") <= 1
    assert not query.startswith("*")


def test_match_query_prefixes_only_the_last_token():
    assert build_match_query("rio de jan") == '"rio" "de" "jan"*'
    assert build_match_query("singapore") == '"singapore"*'


def test_match_query_empty_input():
    assert build_match_query("") is None
    assert build_match_query("   ") is None
    assert build_match_query("***") is None


def test_fold_strips_diacritics():
    assert fold("Nawāda") == "nawada"
    assert fold("Málaga") == "malaga"
    assert fold("SAINT-MALO") == "saint-malo"


# -- end-to-end index ------------------------------------------------------


@pytest.fixture()
def built_gazetteer(tmp_path):
    records = parse_wpi(WPI_CSV) + parse_geonames(GEONAMES_TSV, None)
    path = tmp_path / "ports.sqlite"
    write_gazetteer(path, records, {"geonames:1275339": "bombay bombaim"})
    return path


def test_fts_index_is_actually_populated(built_gazetteer):
    """An FTS5 table is never filled automatically.

    Declaring one and inserting only into the base table produces an index
    that matches nothing at all, raises no error anywhere, and looks exactly
    like a search box that never finds anything.
    """
    connection = sqlite3.connect(f"file:{built_gazetteer}?mode=ro", uri=True)
    try:
        base = connection.execute("SELECT COUNT(*) FROM ports").fetchone()[0]
        indexed = connection.execute("SELECT COUNT(*) FROM ports_fts").fetchone()[0]
    finally:
        connection.close()
    assert base == indexed > 0


def test_contentless_index_still_returns_coordinates(built_gazetteer):
    """The reason for the base table: a contentless FTS5 row has no columns."""
    store = GazetteerStore(built_gazetteer)
    results = store.search("Sydney")
    assert results
    assert results[0]["latitude"] == pytest.approx(-33.85, abs=1e-3)
    assert results[0]["longitude"] == pytest.approx(151.2, abs=1e-3)


def test_search_survives_hostile_input(built_gazetteer):
    store = GazetteerStore(built_gazetteer)
    for hostile in ["St. John's", "a OR b", "*", '"', "NEAR(x)", "'; DROP TABLE ports;--"]:
        assert isinstance(store.search(hostile), list)
    # The table is still there.
    assert store.entry_count() > 0


def test_ports_win_ties_against_identical_cities(tmp_path):
    """Same place from both sources: the port entry wins and keeps metadata."""
    port = PortRecord(
        port_id="wpi:1", port_name="Rotterdam", country_code="NL",
        latitude=51.9, longitude=4.5, water_body="Netherlands",
        harbor_size="L", source="wpi", population=868_135,
    )
    city = PortRecord(
        port_id="geonames:1", port_name="Rotterdam", country_code="NL",
        latitude=51.92, longitude=4.48, source="geonames", population=868_135,
    )
    path = tmp_path / "tie.sqlite"
    write_gazetteer(path, [port, city])
    results = GazetteerStore(path).search("rotter")
    assert results[0]["source"] == "wpi"
    assert results[0]["harbor_size"] == "L"


def test_population_outranks_source(tmp_path):
    """A small harbour must not outrank a much larger city of the same name.

    Ordering by source first is what makes `sydn` return Sydney, Nova Scotia
    instead of Sydney, Australia.
    """
    small_port = PortRecord(
        port_id="wpi:1", port_name="Sydney", country_code="CA",
        latitude=46.1, longitude=-60.2, source="wpi", population=105_968,
    )
    big_city = PortRecord(
        port_id="geonames:1", port_name="Sydney", country_code="AU",
        latitude=-33.87, longitude=151.2, source="geonames", population=5_638_830,
    )
    path = tmp_path / "sydney.sqlite"
    write_gazetteer(path, [small_port, big_city])
    results = GazetteerStore(path).search("sydn")
    assert results[0]["country_code"] == "AU"


def test_attach_city_population_uses_the_largest_nearby_settlement():
    ports = [
        PortRecord(port_id="wpi:1", port_name="Port of Sydney", latitude=-33.85,
                   longitude=151.2, source="wpi"),
        PortRecord(port_id="wpi:2", port_name="Remote Anchorage", latitude=0.0,
                   longitude=0.0, source="wpi"),
    ]
    cities = [
        PortRecord(port_id="geonames:1", port_name="Sydney", latitude=-33.87,
                   longitude=151.21, source="geonames", population=5_638_830),
        PortRecord(port_id="geonames:2", port_name="Manly", latitude=-33.80,
                   longitude=151.28, source="geonames", population=15_000),
    ]
    enriched = attach_city_population(ports, cities)
    assert enriched == 1
    assert ports[0].population == 5_638_830
    # Nothing within range, so it stays honest about knowing nothing.
    assert ports[1].population == 0


def test_missing_gazetteer_degrades_quietly(tmp_path):
    store = GazetteerStore(tmp_path / "nonexistent.sqlite")
    assert not store.available
    assert store.search("anything") == []
    assert store.entry_count() == 0
    assert store.get("wpi:1") is None


def test_nearest_returns_sorted_by_distance(built_gazetteer):
    store = GazetteerStore(built_gazetteer)
    results = store.nearest(-33.9, 151.2, limit=2)
    assert results
    assert results[0]["port_name"] == "Sydney"
    assert results[0]["distance_km"] < 20
