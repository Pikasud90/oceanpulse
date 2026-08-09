"""Shared fixtures.

Every test runs against a temporary directory and needs no network. None of
them can touch a real data directory.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from oceanpulse.models import MarineObservation
from oceanpulse.storage.sqlite_backend import SQLiteStorage

BASE_MS = 1_700_000_000_000  # 2023-11-14T22:13:20Z


@pytest.fixture()
def storage(tmp_path: Path) -> SQLiteStorage:
    store = SQLiteStorage(tmp_path / "test.sqlite")
    store.initialise()
    yield store
    store.close()


@pytest.fixture()
def observation() -> MarineObservation:
    return MarineObservation(
        latitude=30.0,
        longitude=-40.0,
        timestamp=BASE_MS,
        wave_height_m=2.0,
        wave_period_s=8.0,
        wave_direction_deg=270.0,
        sst_celsius=21.5,
    )


@pytest.fixture()
def hourly_series() -> list[MarineObservation]:
    """Twelve consecutive hours at one cell."""
    return [
        MarineObservation(
            latitude=30.0,
            longitude=-40.0,
            timestamp=BASE_MS + hour * 3_600_000,
            wave_height_m=1.0 + hour * 0.1,
            wave_period_s=7.0,
            sst_celsius=20.0 + hour * 0.05,
        )
        for hour in range(12)
    ]


# A real Open-Meteo response shape, trimmed to three hours. Note that the
# land location carries a full structure with null values and a 200 status:
# there is nothing in the transport layer that marks it as empty.
OPEN_METEO_BATCH = [
    {
        "latitude": 30.041664,
        "longitude": -40.041656,
        "elevation": 0.0,
        "hourly_units": {"time": "iso8601", "wave_height": "m"},
        "hourly": {
            "time": ["2026-08-09T00:00", "2026-08-09T01:00", "2026-08-09T02:00"],
            "wave_height": [1.18, 1.2, 1.24],
            "wave_period": [8.75, 8.7, 8.65],
            "wave_direction": [310.0, 312.0, 314.0],
            "ocean_current_velocity": [1.0, 0.9, 0.7],
            "ocean_current_direction": [45.0, 47.0, 50.0],
            "sea_surface_temperature": [28.6, 28.6, 28.5],
        },
    },
    {
        "latitude": 28.625,
        "longitude": 77.20836,
        "elevation": 224.0,
        "hourly_units": {"time": "iso8601", "wave_height": "m"},
        "hourly": {
            "time": ["2026-08-09T00:00", "2026-08-09T01:00", "2026-08-09T02:00"],
            "wave_height": [None, None, None],
            "wave_period": [None, None, None],
            "wave_direction": [None, None, None],
            "ocean_current_velocity": [None, None, None],
            "ocean_current_direction": [None, None, None],
            "sea_surface_temperature": [None, None, None],
        },
    },
]

ERDDAP_SST_CSV = """time,zlev,latitude,longitude,sst
UTC,m,degrees_north,degrees_east,degree_C
2023-01-01T12:00:00Z,0.0,30.125,-40.125,22.07
2023-01-02T12:00:00Z,0.0,30.125,-40.125,22.01
2023-01-03T12:00:00Z,0.0,30.125,-40.125,NaN
"""

ERDDAP_SLA_CSV = """time,latitude,longitude,sla
UTC,degrees_north,degrees_east,m
2024-06-01T00:00:00Z,30.125,-40.125,0.0085
2024-06-02T00:00:00Z,30.125,-40.125,0.004
"""

ERDDAP_INFO_CSV = """Row Type,Variable Name,Attribute Name,Data Type,Value
attribute,time,_CoordinateAxisType,String,Time
attribute,time,actual_range,double,"3.681936E8, 1.784808E9"
attribute,time,units,String,seconds since 1970-01-01T00:00:00Z
"""

WPI_CSV = (
    "portNumber,portName,countryCode,countryName,latitude,longitude,regionName,"
    "harborSize,harborType\n"
    '48430,Abadan,IR,Iran,"30°20\'00""N","48°17\'00""E",IRAN,M,RN\n'
    '15390,Sydney,AU,Australia,"33°51\'00""S","151°12\'00""E",AUSTRALIA,L,CN\n'
    '99999,Broken,XX,Nowhere,"not-a-coordinate","also-bad",NOWHERE,S,CN\n'
)

GEONAMES_TSV = "\t".join(
    [
        "1275339", "Mumbai", "Mumbai", "Bombay,Bombaim", "19.07283", "72.88261",
        "P", "PPLA", "IN", "", "16", "", "", "", "12691836", "", "8", "Asia/Kolkata",
        "2024-01-01",
    ]
) + "\n" + "\t".join(
    [
        "1273294", "Delhi", "Delhi", "Dilli", "28.65195", "77.23149",
        "P", "PPLA", "IN", "", "07", "", "", "", "10927986", "", "216", "Asia/Kolkata",
        "2024-01-01",
    ]
) + "\n"
