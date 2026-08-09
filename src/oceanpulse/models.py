"""The validation boundary.

Nothing reaches the database without passing through here. Upstream payloads
are treated as hostile: a single malformed record is dropped with a warning
rather than being allowed to abort a batch or poison a table.

The rules encoded below come from observed behaviour of the real endpoints,
not from their documentation:

* Open-Meteo returns HTTP 200 with *every value null* for land coordinates and
  for dates before a variable's archive floor. An all-null observation is not
  data and must never be stored.
* Longitudes outside +/-180 are wrapped rather than dropped, because a 0-360
  ERDDAP dataset legitimately produces them.
* Every marine field is nullable in practice. A NOT NULL constraint on, say,
  wave height would stop ingestion the first time a grid cell reported only
  sea-surface temperature.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Columns that carry an actual measurement. Used to decide whether a record is
# empty, and to drive the column-scoped upsert in the storage layer.
MEASUREMENT_FIELDS = (
    "wave_height_m",
    "wave_period_s",
    "wave_direction_deg",
    "current_velocity_kmh",
    "current_direction_deg",
    "sst_celsius",
    "sea_level_anomaly_m",
    "geostrophic_u_ms",
    "geostrophic_v_ms",
)

# Which source owns which columns. Open-Meteo and ERDDAP both supply SST, so
# precedence has to be explicit rather than last-write-wins.
SOURCE_FIELDS = {
    "open_meteo": (
        "wave_height_m",
        "wave_period_s",
        "wave_direction_deg",
        "current_velocity_kmh",
        "current_direction_deg",
        "sst_celsius",
    ),
    "erddap_sla": (
        "sea_level_anomaly_m",
        "geostrophic_u_ms",
        "geostrophic_v_ms",
    ),
    "erddap_sst": ("sst_celsius",),
}

# Fields a source may only fill when the existing value is NULL, never
# overwrite. ERDDAP OISST is a daily 0.25-degree product; Open-Meteo marine is
# hourly. Where both cover the same hour we keep the hourly value.
FILL_ONLY_FIELDS = {
    "erddap_sst": ("sst_celsius",),
}


def wrap_longitude(lon: float) -> float:
    """Normalise any longitude onto [-180, 180)."""
    return ((float(lon) + 180.0) % 360.0) - 180.0


def observation_id(latitude: float, longitude: float, timestamp_ms: int) -> str:
    """Stable identity for a point in space and time.

    Deliberately *excludes* the source, so that an ERDDAP sea-level reading and
    an Open-Meteo wave reading for the same cell and hour converge on one row
    rather than two half-empty ones. That is what makes the correlation tab
    possible without a join.
    """
    key = f"{round(float(latitude), 4):.4f}|{round(float(longitude), 4):.4f}|{int(timestamp_ms)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class MarineObservation(BaseModel):
    """One validated measurement at one place and time."""

    model_config = ConfigDict(extra="ignore")

    latitude: float
    longitude: float
    timestamp: int = Field(description="Epoch milliseconds, UTC")
    port_id: str | None = None
    source: str = "open_meteo"
    is_forecast: bool = False
    is_historical_cache: bool = False

    wave_height_m: float | None = None
    wave_period_s: float | None = None
    wave_direction_deg: float | None = None
    current_velocity_kmh: float | None = None
    current_direction_deg: float | None = None
    sst_celsius: float | None = None
    sea_level_anomaly_m: float | None = None
    geostrophic_u_ms: float | None = None
    geostrophic_v_ms: float | None = None

    @field_validator("latitude")
    @classmethod
    def _check_latitude(cls, value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or not -90.0 <= value <= 90.0:
            raise ValueError(f"latitude out of range: {value}")
        return value

    @field_validator("longitude")
    @classmethod
    def _wrap_longitude(cls, value: float) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"longitude not finite: {value}")
        return wrap_longitude(value)

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: int) -> int:
        value = int(value)
        # 1970-01-01 .. 2100-01-01 in milliseconds. Catches a seconds-vs-
        # milliseconds mix-up, which would otherwise land silently in 1970.
        if not 0 <= value <= 4_102_444_800_000:
            raise ValueError(f"timestamp outside plausible range: {value}")
        return value

    @field_validator(
        "wave_height_m",
        "wave_period_s",
        "current_velocity_kmh",
        "sst_celsius",
        "sea_level_anomaly_m",
        "geostrophic_u_ms",
        "geostrophic_v_ms",
        mode="before",
    )
    @classmethod
    def _reject_non_finite(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        # ERDDAP writes NaN for masked (land) cells in CSV output.
        return None if not math.isfinite(number) else number

    @field_validator("wave_direction_deg", "current_direction_deg", mode="before")
    @classmethod
    def _normalise_bearing(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number % 360.0

    @model_validator(mode="after")
    def _reject_empty(self) -> "MarineObservation":
        """An observation with no measurements is not an observation.

        This is the single most important rule in the file. Open-Meteo answers
        HTTP 200 for Delhi and for the year 1979 alike; only the all-null
        payload distinguishes them from real data.
        """
        if all(getattr(self, name) is None for name in MEASUREMENT_FIELDS):
            raise ValueError("observation carries no measurements")
        return self

    @property
    def obs_id(self) -> str:
        return observation_id(self.latitude, self.longitude, self.timestamp)

    def measured_fields(self) -> dict[str, float]:
        """Non-null measurements only."""
        return {
            name: getattr(self, name)
            for name in MEASUREMENT_FIELDS
            if getattr(self, name) is not None
        }


class PortRecord(BaseModel):
    """A gazetteer entry: a port from the World Port Index or a coastal city."""

    model_config = ConfigDict(extra="ignore")

    port_id: str
    port_name: str
    country_code: str = ""
    country_name: str = ""
    latitude: float
    longitude: float
    water_body: str = ""
    harbor_size: str = ""
    harbor_type: str = ""
    source: str = "wpi"
    population: int = 0
    # Nearest cell for which the marine model actually returns data. A harbour
    # coordinate frequently sits inside a land cell.
    marine_latitude: float | None = None
    marine_longitude: float | None = None

    @field_validator("latitude")
    @classmethod
    def _check_latitude(cls, value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or not -90.0 <= value <= 90.0:
            raise ValueError(f"latitude out of range: {value}")
        return value

    @field_validator("longitude")
    @classmethod
    def _wrap_longitude(cls, value: float) -> float:
        return wrap_longitude(value)
