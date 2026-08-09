-- OceanPulse primary database schema.
--
-- Timestamps are epoch milliseconds UTC in INTEGER columns throughout.
-- Integer comparison is index-friendly and sidesteps every string-collation
-- and timezone-parsing bug that storing ISO text invites.

CREATE TABLE IF NOT EXISTS marine_observations (
    obs_id                TEXT PRIMARY KEY,   -- sha1(lat|lon|timestamp)
    latitude              REAL NOT NULL,
    longitude             REAL NOT NULL,
    timestamp             INTEGER NOT NULL,   -- epoch milliseconds UTC
    port_id               TEXT,               -- NULL for macro-grid points

    wave_height_m         REAL,
    wave_period_s         REAL,
    wave_direction_deg    REAL,
    current_velocity_kmh  REAL,
    current_direction_deg REAL,
    sst_celsius           REAL,
    sea_level_anomaly_m   REAL,
    geostrophic_u_ms      REAL,
    geostrophic_v_ms      REAL,

    -- Open-Meteo returns future hours alongside past ones. A forecast is not
    -- an observation and analytics must be able to exclude it.
    is_forecast           INTEGER NOT NULL DEFAULT 0,
    is_historical_cache   INTEGER NOT NULL DEFAULT 0,
    sources               TEXT NOT NULL DEFAULT '',

    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_time_location
    ON marine_observations(timestamp, latitude, longitude);

CREATE INDEX IF NOT EXISTS idx_obs_port_time
    ON marine_observations(port_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_obs_time
    ON marine_observations(timestamp);

CREATE INDEX IF NOT EXISTS idx_obs_forecast_time
    ON marine_observations(is_forecast, timestamp);


-- Records completed external fetches so the same range is never requested
-- twice. `dataset` is essential: SST coverage over a box says nothing about
-- sea-level coverage over the same box.
CREATE TABLE IF NOT EXISTS cache_ledger (
    ledger_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset         TEXT NOT NULL,
    min_lat         REAL NOT NULL,
    max_lat         REAL NOT NULL,
    min_lon         REAL NOT NULL,
    max_lon         REAL NOT NULL,
    start_timestamp INTEGER NOT NULL,
    end_timestamp   INTEGER NOT NULL,
    row_count       INTEGER NOT NULL DEFAULT 0,
    fetched_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_dataset
    ON cache_ledger(dataset, start_timestamp, end_timestamp);


-- The sparse global grid. `is_ocean` comes from the bundled mask; `is_valid`
-- records whether the marine model actually returned data when probed. They
-- differ: a cell can be sea by the mask yet sit outside the wave model's
-- domain (enclosed seas, ice-covered water).
CREATE TABLE IF NOT EXISTS grid_points (
    grid_id       TEXT PRIMARY KEY,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    is_ocean      INTEGER NOT NULL DEFAULT 1,
    is_valid      INTEGER,             -- NULL = never probed
    fail_count    INTEGER NOT NULL DEFAULT 0,
    last_checked  INTEGER,
    last_success  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_grid_valid ON grid_points(is_valid);


-- Ports the user has chosen to track. High-resolution time series are only
-- polled for these, never for the whole gazetteer.
CREATE TABLE IF NOT EXISTS tracked_ports (
    port_id          TEXT PRIMARY KEY,
    port_name        TEXT NOT NULL,
    country_code     TEXT NOT NULL DEFAULT '',
    country_name     TEXT NOT NULL DEFAULT '',
    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,
    -- Nearest cell where the marine model has data. Harbour coordinates very
    -- often fall in a land cell and return nothing at all.
    marine_latitude  REAL,
    marine_longitude REAL,
    elevation_m      REAL,
    added_at         INTEGER NOT NULL,
    last_polled_at   INTEGER,
    backfilled_to    INTEGER
);


-- Saved dataset definitions: a named, re-runnable export specification.
CREATE TABLE IF NOT EXISTS saved_datasets (
    dataset_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    spec_json    TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   INTEGER NOT NULL,
    last_run_at  INTEGER,
    last_rows    INTEGER
);


-- UI settings and the daemon heartbeat, so both survive a restart.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
