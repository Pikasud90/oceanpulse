"""Configuration loading for OceanPulse.

Every setting has a default that works with no configuration at all. A `.env`
file in the project root overrides the defaults; real environment variables
override the `.env` file.

The `.env` parser is hand-rolled rather than pulled from `python-dotenv`
because it is twenty lines and removes a dependency from a project whose
whole premise is "unzip and run".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Endpoints. These are verified-working values; see README section 11.
# --------------------------------------------------------------------------

OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# ERDDAP mirrors, tried in order. A dataset can 404 with "Currently unknown
# datasetID" while it reloads, so having a second host matters.
ERDDAP_HOSTS = (
    "https://coastwatch.pfeg.noaa.gov/erddap",
    "https://upwell.pfeg.noaa.gov/erddap",
)

WPI_URL = "https://msi.nga.mil/api/publications/world-port-index?output=csv"
GEONAMES_BASE_URL = "https://download.geonames.org/export/dump"

# --------------------------------------------------------------------------
# Open-Meteo marine archive floors.
#
# These are MEASURED, not documented. The API happily accepts start_date=1979
# and returns HTTP 200 with every value null, so without these floors the
# ingester would write millions of empty rows and report success. Each
# variable has its own floor.
# --------------------------------------------------------------------------

MARINE_ARCHIVE_FLOORS = {
    "wave_height": "2021-12-01",
    "wave_period": "2021-12-01",
    "wave_direction": "2021-12-01",
    "ocean_current_velocity": "2022-01-01",
    "ocean_current_direction": "2022-01-01",
    "sea_surface_temperature": "2022-12-01",
}

# The earliest date any marine variable is available.
MARINE_ARCHIVE_FLOOR = min(MARINE_ARCHIVE_FLOORS.values())

MARINE_HOURLY_VARIABLES = tuple(MARINE_ARCHIVE_FLOORS.keys())

ALLOWED_POLL_INTERVALS = (15, 30, 45, 60)

# Open-Meteo accepts many coordinates in one request and returns a JSON array.
# 120 was verified working at ~102 KB. Batching is not an optimisation here:
# one-request-per-point would exceed the free daily call allowance.
MAX_COORDS_PER_REQUEST = 100


def _project_root() -> Path:
    # src/oceanpulse/config.py -> src/oceanpulse -> src -> project root
    return Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal `.env` file. Missing file is not an error."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


class _Env:
    """Environment lookup: real env wins over `.env`, which wins over default."""

    def __init__(self, dotenv: dict[str, str]) -> None:
        self._dotenv = dotenv

    def raw(self, key: str) -> str | None:
        if key in os.environ:
            return os.environ[key]
        return self._dotenv.get(key)

    def str(self, key: str, default: str) -> str:
        value = self.raw(key)
        return default if value is None or value == "" else value

    def int(self, key: str, default: int) -> int:
        value = self.raw(key)
        if value is None or value == "":
            return default
        try:
            return int(float(value))
        except ValueError:
            return default

    def float(self, key: str, default: float) -> float:
        value = self.raw(key)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def bool(self, key: str, default: bool) -> bool:
        value = self.raw(key)
        if value is None or value == "":
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Resolved application configuration."""

    project_root: Path
    data_dir: Path
    log_dir: Path

    # Web
    host: str = "127.0.0.1"
    port: int = 8050
    debug: bool = False
    open_browser: bool = True
    run_daemon_in_ui: bool = True

    # Ingestion
    poll_interval_minutes: int = 30
    grid_target_points: int = 250
    grid_past_days: int = 2
    grid_forecast_days: int = 2
    port_forecast_days: int = 3

    # HTTP
    rate_limit_per_second: float = 2.0
    rate_limit_burst: int = 4
    http_timeout: float = 60.0
    backoff_max_attempts: int = 8
    backoff_cap_seconds: float = 900.0
    user_agent: str = "OceanPulse/1.0 (self-hosted; +https://github.com/topics/oceanography)"

    # ERDDAP
    erddap_chunk_days: int = 365
    erddap_enabled: bool = True

    # Gazetteer
    geonames_dataset: str = "cities15000"
    gazetteer_include_cities: bool = True
    coastal_max_km: float = 60.0

    # UI limits
    max_plot_points: int = 2500
    max_export_rows: int = 2_000_000

    extras: dict[str, Any] = field(default_factory=dict)

    # -- derived paths ----------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.data_dir / "oceanpulse.sqlite"

    @property
    def ports_db_path(self) -> Path:
        return self.data_dir / "ports.sqlite"

    @property
    def ocean_mask_path(self) -> Path:
        return self.data_dir / "ocean_mask_1deg.bin"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def load_config(overrides: dict[str, Any] | None = None) -> Config:
    """Build a `Config` from `.env`, real environment, and explicit overrides."""
    root = _project_root()
    env = _Env(_load_dotenv(root / ".env"))

    data_dir = Path(env.str("OCEAN_DATA_DIR", str(root / "data"))).expanduser()
    log_dir = Path(env.str("OCEAN_LOG_DIR", str(root / "logs"))).expanduser()
    if not data_dir.is_absolute():
        data_dir = (root / data_dir).resolve()
    if not log_dir.is_absolute():
        log_dir = (root / log_dir).resolve()

    poll = env.int("OCEAN_POLL_INTERVAL_MINUTES", 30)
    if poll not in ALLOWED_POLL_INTERVALS:
        poll = 30

    cfg = Config(
        project_root=root,
        data_dir=data_dir,
        log_dir=log_dir,
        host=env.str("OCEAN_HOST", "127.0.0.1"),
        port=env.int("OCEAN_PORT", 8050),
        debug=env.bool("OCEAN_DEBUG", False),
        open_browser=env.bool("OCEAN_OPEN_BROWSER", True),
        run_daemon_in_ui=env.bool("OCEAN_RUN_DAEMON_IN_UI", True),
        poll_interval_minutes=poll,
        grid_target_points=max(24, env.int("OCEAN_GRID_TARGET_POINTS", 250)),
        grid_past_days=max(0, min(92, env.int("OCEAN_GRID_PAST_DAYS", 2))),
        grid_forecast_days=max(1, min(7, env.int("OCEAN_GRID_FORECAST_DAYS", 2))),
        port_forecast_days=max(1, min(7, env.int("OCEAN_PORT_FORECAST_DAYS", 3))),
        rate_limit_per_second=env.float("OCEAN_RATE_LIMIT_PER_SECOND", 2.0),
        rate_limit_burst=env.int("OCEAN_RATE_LIMIT_BURST", 4),
        http_timeout=env.float("OCEAN_HTTP_TIMEOUT", 60.0),
        backoff_max_attempts=env.int("OCEAN_BACKOFF_MAX_ATTEMPTS", 8),
        backoff_cap_seconds=env.float("OCEAN_BACKOFF_CAP_SECONDS", 900.0),
        erddap_chunk_days=env.int("OCEAN_ERDDAP_CHUNK_DAYS", 365),
        erddap_enabled=env.bool("OCEAN_ERDDAP_ENABLED", True),
        geonames_dataset=env.str("OCEAN_GEONAMES_DATASET", "cities15000"),
        gazetteer_include_cities=env.bool("OCEAN_GAZETTEER_INCLUDE_CITIES", True),
        coastal_max_km=env.float("OCEAN_COASTAL_MAX_KM", 60.0),
        max_plot_points=env.int("OCEAN_MAX_PLOT_POINTS", 2500),
        max_export_rows=env.int("OCEAN_MAX_EXPORT_ROWS", 2_000_000),
    )

    for key, value in (overrides or {}).items():
        if value is not None and hasattr(cfg, key):
            setattr(cfg, key, value)

    return cfg
