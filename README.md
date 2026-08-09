# OceanPulse

**Self-hosted marine data ingestion, analysis and export.**

OceanPulse runs entirely on your own machine. A background daemon continuously
samples the global ocean — waves, currents and sea-surface temperature — into a
local time-series database, and a four-tab web interface lets you watch global
sea state live, drill into any port's history using an offline search over
20,000 ports and coastal places, correlate the physics, and export clean
datasets for time-series modelling.

It costs nothing to run, requires no API keys, no accounts and no cloud
services. Every data source it uses is free and public.

**Verified on:** macOS (Python 3.12) from a clean clone through to a populated
database — 24,288 observations from the first ingestion cycle in 3.3 seconds,
and a 20,401-entry offline gazetteer built in about 90 seconds. The Windows and
Linux paths share the same code and launcher logic but have not been run end to
end.

---

## Contents

**Part 1 — Instruction manual**
1. [What you need](#1-what-you-need)
2. [Installation](#2-installation)
3. [What happens on first run](#3-what-happens-on-first-run)
4. [Using the application](#4-using-the-application)
5. [Glossary](#5-glossary)
6. [The settings bar](#6-the-settings-bar)
7. [Running as a background service](#7-running-as-a-background-service)
8. [Configuration reference](#8-configuration-reference)
9. [Troubleshooting](#9-troubleshooting)
10. [Uninstalling](#10-uninstalling)

**Part 2 — Implementation details**
11. [Architecture](#11-architecture)
12. [Data sources and APIs](#12-data-sources-and-apis)
13. [What the data actually is](#13-what-the-data-actually-is)
14. [Polling strategy and rate limits](#14-polling-strategy-and-rate-limits)
15. [The sampling grid and the ocean mask](#15-the-sampling-grid-and-the-ocean-mask)
16. [Historical fetching and the cache ledger](#16-historical-fetching-and-the-cache-ledger)
17. [The offline gazetteer](#17-the-offline-gazetteer)
18. [Database schema](#18-database-schema)
19. [The math engine](#19-the-math-engine)
20. [Aggregation and export semantics](#20-aggregation-and-export-semantics)
21. [Design decisions and trade-offs](#21-design-decisions-and-trade-offs)
22. [Resilience and error handling](#22-resilience-and-error-handling)
23. [Security](#23-security)
24. [Testing](#24-testing)
25. [Project layout](#25-project-layout)
26. [Using this data for research](#26-using-this-data-for-research)
27. [Attribution and licensing](#27-attribution-and-licensing)

---
---

# PART 1 — INSTRUCTION MANUAL

## 1. What you need

| Requirement | Detail |
|---|---|
| **Python** | 3.10 or newer. Nothing else needs installing. |
| **Disk space** | **~500 MB** once running: ~450 MB Python packages in `.venv/`, ~10 MB port database, ~8 KB ocean mask, and roughly 15 MB of observations per week of continuous polling. |
| **Internet** | Needed to collect data. Every query the interface makes runs against local storage. |
| **Operating system** | Windows 10/11, macOS 11+, or any modern Linux |

You do **not** need: a database server, Docker, an API key, an account, or
administrator rights (except optionally, to install the background service).

### Getting Python

- **Windows** — download from [python.org/downloads](https://www.python.org/downloads/).
  **Tick "Add Python to PATH"** on the first screen of the installer. This is
  the single most common cause of setup failure.
- **macOS** — `brew install python@3.12`, or download from python.org.
- **Linux** — `sudo apt install python3 python3-venv` (Debian/Ubuntu) or your
  distribution's equivalent. The `venv` package is separate on Debian-based
  systems and is required.

---

## 2. Installation

Unzip or clone the project anywhere you like. It does not need to live in a
specific location, and nothing is written outside the project folder.

### Windows

Double-click **`run.bat`**, or from a Command Prompt:

```
run.bat
```

### macOS and Linux

```bash
chmod +x run.sh
./run.sh
```

That is the entire installation. The launcher does everything else.

---

## 3. What happens on first run

**The interface opens in under a minute.** Measured on macOS with Python 3.12:

| Step | What it does | Time | Blocks you? |
|---|---|---|---|
| 1 | Creates a virtual environment in `.venv/`. Your system Python is untouched. | seconds | yes |
| 2 | Installs Dash, Plotly, httpx, pydantic, pandas, PyArrow | ~1 min | yes |
| 3 | **Opens the web interface** at <http://localhost:8050> | — | — |
| 4 | Derives a global land/sea mask from one NOAA request — 64,800 cells, stored as an 8 KB bitmask | ~10 s | no |
| 5 | Builds the sparse sampling grid and polls it: **255 candidate points → 24,288 observations** | ~3 s | no |
| 6 | Downloads and builds the offline port database — 2,951 ports and 17,515 coastal places | ~90 s | no |

Only steps 1 and 2 make you wait. Everything after the interface opens happens
on a worker thread, and the tabs fill in as data arrives.

You will know it is ready when you see:

```
  OceanPulse is running at http://localhost:8050
```

**Subsequent launches take a few seconds.** The ocean mask and port database
are built once and reused.

> **If the port database fails to build**, the application still starts. Only
> the place search on the Port Timeline tab is affected; rebuild later with
> `run.bat gazetteer` / `./run.sh gazetteer`.
>
> **If the ocean mask cannot be fetched**, the grid falls back to probing
> Open-Meteo directly. It costs a few extra requests once and reaches the same
> answer.

### Command reference

| Command | Effect |
|---|---|
| `run.bat` / `./run.sh` | Web interface **and** background ingestion (default) |
| `run.bat --no-daemon` | Web interface only — use when a system service is already ingesting |
| `run.bat daemon` | Ingestion only, no web interface |
| `run.bat gazetteer` | Build the port database if missing |
| `run.bat gazetteer --force` | Rebuild the port database from scratch |
| `run.bat --port 9000` | Use a different port |
| `run.bat --no-browser` | Do not open a browser window automatically |
| `run.bat --help` | Show all options |

macOS and Linux use `./run.sh` with identical arguments.

### Stopping it

Press `Ctrl+C` in the terminal. The daemon stops, the write-ahead log is
checkpointed, and the database is released cleanly. `SIGTERM` does the same, so
a service manager can stop it without waiting for a timeout.

---

## 4. Using the application

### Tab 1 — Global Pulse

A live view of the current state of the world ocean, refreshed every 60 seconds
**entirely from local storage**. This tab never contacts an upstream service.

**Four headline figures:**

| Tile | Meaning |
|---|---|
| **Peak wave power** | Highest wave energy flux across all sampled cells, in kW per metre of wave crest |
| **Highest SST anomaly** | Largest standardised sea-temperature anomaly, in standard deviations |
| **Warm-spell cells** | Cells currently above +2σ — a rolling-baseline approximation, not a formal marine heatwave count |
| **Sampled cells** | How many grid points are reporting, and how recently |

**The map.** Switch between a flat world map and a rotatable globe. Points are
coloured by sea temperature, wave power or wave height, and faint blue arrows
show ocean current direction and relative speed. Hover any point for its full
set of values.

The points are a **sparse sample, not a continuous field**. OceanPulse polls a
few hundred cells spread evenly over the ocean; the gaps between them are not
measurements, and the map does not pretend otherwise by interpolating.

**Severe sea state.** The right-hand panel lists every cell reporting a
significant wave height above 5 metres, largest first. In practice this is
almost always the Southern Ocean.

**Wave power distribution.** The histogram at the bottom shows how energy flux
is distributed across the sampled cells. It is strongly right-skewed: most of
the ocean is calm and a small fraction carries most of the power.

### Tab 2 — Port Timeline

Investigate the marine history of a specific place.

**Step 1 — Find your port.** Start typing. Suggestions appear from the second
character, ranked by population and match quality, and resolve in about **2
milliseconds** with no network traffic at all. The search handles accents
(`Malaga` finds Málaga), alternate names (`Bombay` finds Mumbai) and
apostrophes (`St. John's` works).

Ports from the World Port Index are shown with their harbour metadata; coastal
cities from GeoNames are shown with population. Where both describe the same
place, the port entry wins.

**Step 2 — Set your date range**, and choose whether to include the deep
archive and forecast hours.

**Step 3 — Click "Load timeline".** Three things happen:

1. The port is resolved to a cell the marine model actually covers. Harbour
   coordinates frequently sit in a land cell, and the interface tells you when
   it has moved: *"Marine data is read from 51.90, 4.23 — the nearest cell with
   model coverage."*
2. The port is registered as tracked, so the daemon keeps it current from then on.
3. History is fetched from whichever sources can serve your window.

**The status line reports what each source actually did** — fetched, already
held locally, outside coverage, or failed. An empty chart because sea level
data stops in March is a different situation from a failed fetch, and the
interface distinguishes them rather than leaving you to guess.

**The charts:**

- **Sea temperature and sea level anomaly** on a dual axis. Temperature comes
  from an hourly model near-term and NOAA's 45-year satellite record further
  back; sea level comes from satellite altimetry.
- **Swell direction and period**, as a polar scatter coloured by wave height.
  This is where a port's prevailing swell direction becomes obvious.
- **Wave energy flux** over time, in kW/m.

### Tab 3 — Physics & Correlation

**Correlation matrix.** Pearson *r* between sea temperature, sea level anomaly,
wave height, wave period, wave power and current speed.

Everything is reduced to **daily means before correlating**. The sources do not
share a clock — Open-Meteo is hourly, NOAA's temperature product is stamped at
12:00 UTC and the altimetry at 00:00 — so without alignment no row ever carries
both a temperature and a sea level, and the matrix comes back empty. An empty
matrix reads as "these quantities are unrelated" when it actually means "these
were never compared".

**Wave power spectrum.** Histogram and probability density of energy flux, with
median and 99th percentile.

**Coastal exceedance model.** Two sliders — a threshold height and a storm
surge allowance — against a daily maximum still-water proxy.

> **This is illustrative only.** OceanPulse holds **no tide predictions**. The
> proxy stacks sea level anomaly, your surge allowance, and a crude wave-setup
> term equal to a fifth of offshore wave height. Real wave setup depends on
> beach slope, which is not available here. **It is not a flood forecast and
> must not be used for any life-safety decision.**

### Tab 4 — Data Export

Turns the observation log into the uniform matrix that machine-learning models
expect, and writes it out as CSV or Parquet.

**Spatial scope** — global, a tracked port, or a bounding box.
**Then narrow by** date range, and choose the output shape:

| Control | Options | Meaning |
|---|---|---|
| **Output shape** | Aggregated time series / Raw observation log | A regular grid, or one row per observation |
| **Interval** | 1 hour, 6 hours, 1 day, 1 week | Size of each time step |
| **Gaps in intensive columns** | Leave empty / Forward fill / Linear interpolation / Zero fill | How to represent windows with no measurement |
| **Forecast rows** | Include or exclude | Future hours from the wave model |

Press **Load data** for a preview, then **Download CSV** or **Download
Parquet**.

**Saved datasets.** Name a configuration and click *Save definition* to keep
it. A saved dataset stores the **query, not the rows** — re-running it later
picks up everything ingested since, which is what you want from a dataset you
intend to retrain on. A frozen extract would quietly go stale.

**Which format?** Use **Parquet** for Python or PyTorch: it is compressed
(about 40% smaller than the equivalent CSV in practice), typed, and preserves
timezone-aware timestamps so nothing needs re-parsing. Use **CSV** for
spreadsheets.

---

## 5. Glossary

| Term | Meaning |
|---|---|
| **Hm0 / significant wave height** | Roughly the average height of the highest third of waves. Metres. |
| **Tp / peak period** | The wave period carrying the most energy. Seconds. |
| **Wave energy flux** | Power transported per metre of wave crest, kW/m. See [section 19](#19-the-math-engine). |
| **SST** | Sea surface temperature, °C. |
| **SLA** | Sea level anomaly — height of the sea surface relative to its long-term mean, metres. Positive means the sea stands higher than usual. |
| **Geostrophic current** | Current inferred from the slope of the sea surface, m/s. |
| **Z-score / σ** | How far a value sits from its own recent average, in standard deviations. +2σ is unusual; +3σ is rare. |
| **Analysis vs forecast** | An analysis describes a time that has already happened; a forecast is a future hour. OceanPulse stores both and separates them everywhere. |
| **Grid cell** | One sampled point in the sparse global grid. |
| **Masked cell** | A location where a dataset holds no value — usually land, sometimes ice or an enclosed sea. |

---

## 6. The settings bar

Along the top of every tab:

- **Poll interval** — 15, 30, 45 or 60 minutes. The daemon re-reads this at the
  top of every cycle, so a change takes effect on the next tick with no
  restart, and it persists across restarts.
- **Status readout** — observations held, active grid cells, tracked ports,
  database size on disk, and daemon health:
  - 🟢 **ACTIVE** — running normally
  - 🟡 **DEGRADED** — running, but the last cycle hit an error (usually a
    network blip; it will retry). Hover for the detail.
  - 🔴 **STOPPED** — not running, or no heartbeat for five minutes

---

## 7. Running as a background service

By default, ingestion stops when you close the terminal. Installing a service
keeps collection running continuously and starts it at boot or login.

| Platform | Command | Mechanism |
|---|---|---|
| **Windows** | `scripts\setup_service.bat` — **right-click → Run as administrator** | NSSM, downloaded automatically |
| **macOS** | `./scripts/setup_service.sh` | launchd user agent |
| **Linux** | `./scripts/setup_service.sh` | systemd user unit |

Run `run.bat` / `./run.sh` at least once first — the service needs the virtual
environment the launcher creates.

Neither the macOS nor the Linux path requires `sudo`: both install user-level
services. A personal data-collection daemon has no business running as root.

**On Linux**, a user service normally stops at logout. On a headless machine:

```bash
sudo loginctl enable-linger $USER
```

**Once a service is installed**, start the dashboard with `--no-daemon` so you
do not end up with two pollers competing:

```
run.bat --no-daemon
```

| Platform | Check it with |
|---|---|
| Windows | `services.msc`, look for *OceanPulse Ingestion Daemon* |
| macOS | `launchctl list \| grep oceanpulse` |
| Linux | `systemctl --user status oceanpulse` |

---

## 8. Configuration reference

Copy `.env.example` to `.env` and edit. Every setting is optional.

### Ingestion

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_POLL_INTERVAL_MINUTES` | `30` | Must be 15, 30, 45 or 60. Also settable in the UI. |
| `OCEAN_GRID_TARGET_POINTS` | `250` | Approximate number of ocean cells to sample |
| `OCEAN_GRID_PAST_DAYS` | `2` | Days of overlap fetched each poll |
| `OCEAN_GRID_FORECAST_DAYS` | `2` | Forecast days stored for grid cells |
| `OCEAN_PORT_FORECAST_DAYS` | `3` | Forecast days stored for tracked ports |

### HTTP

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_RATE_LIMIT_PER_SECOND` | `2.0` | Sustained request rate |
| `OCEAN_RATE_LIMIT_BURST` | `4` | Instantaneous burst allowance |
| `OCEAN_HTTP_TIMEOUT` | `60` | Seconds before a request is abandoned |
| `OCEAN_BACKOFF_MAX_ATTEMPTS` | `8` | Retries before a request is declared failed |

### Historical archive

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_ERDDAP_ENABLED` | `true` | Set false to run on Open-Meteo alone |
| `OCEAN_ERDDAP_CHUNK_DAYS` | `365` | Days per historical request |

### Gazetteer

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_GEONAMES_DATASET` | `cities15000` | `cities5000` gives better small-town coverage |
| `OCEAN_GAZETTEER_INCLUDE_CITIES` | `true` | False builds a ports-only gazetteer |
| `OCEAN_COASTAL_MAX_KM` | `60` | How far inland a place may sit and still count as coastal |

### Web interface

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_HOST` | `127.0.0.1` | **Read [section 23](#23-security) before changing.** |
| `OCEAN_PORT` | `8050` | Port |
| `OCEAN_DEBUG` | `false` | Dash debug mode |
| `OCEAN_OPEN_BROWSER` | `true` | Open a browser on start |
| `OCEAN_MAX_PLOT_POINTS` | `2500` | Server-side downsampling threshold |
| `OCEAN_MAX_EXPORT_ROWS` | `2000000` | Row cap on a single export |

### Storage

| Variable | Default | Meaning |
|---|---|---|
| `OCEAN_DATA_DIR` | `./data` | Database, mask and gazetteer |
| `OCEAN_LOG_DIR` | `./logs` | Log files |

---

## 9. Troubleshooting

**"Python was not found" (Windows)**
Python is not on your PATH. Reinstall from python.org and tick *Add Python to
PATH*, or use the Microsoft Store version.

**"ensurepip is not available" (Debian/Ubuntu)**
`sudo apt install python3-venv`.

**Port 8050 already in use**
`run.bat --port 9000`.

**Dashboard is empty / daemon shows STOPPED**
Check `logs/errors.log`. The most common cause is no internet at startup; the
daemon retries automatically and the indicator returns to green.

**Place search finds nothing**
The port database was not built. Run `run.bat gazetteer` (or `./run.sh
gazetteer`). The search box says so explicitly when the database is missing.

**A port shows "no marine model coverage within 1°"**
The place is too far inland, or in an enclosed water body the wave model does
not cover. Try a nearby open-coast port.

**Sea temperature or sea level shows "no data at this location"**
The port sits in a cell that NOAA's product masks as land. OceanPulse searches
up to 1.5° outward for a cell with data before reporting this, so if you see
it, there genuinely is none nearby.

**Sea level anomaly stops months before today**
Expected. The altimetry product runs several months in arrears — its coverage
is reported in the status line when you load a timeline.

**Starting over**
Delete the `data/` folder. Everything is re-downloadable. Deleting `.venv/`
forces a clean reinstall.

---

## 10. Uninstalling

1. Remove the background service if you installed one:
   - Windows: `scripts\uninstall_service.bat` as administrator
   - macOS / Linux: `./scripts/uninstall_service.sh`
2. Delete the project folder.

Nothing is written outside it — no registry keys, no system directories, no
files in your home folder other than the service definition removed in step 1.

---
---

# PART 2 — IMPLEMENTATION DETAILS

## 11. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│   OS service (NSSM / launchd / systemd)   ──or──   run.sh / run.bat  │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
                   ┌──────────────▼──────────────┐
                   │   Async ingestion daemon    │
                   │  • shared token bucket      │
                   │  • exponential backoff      │
                   │  • Pydantic validation gate │
                   │  • all-null rejection       │
                   └──────────────┬──────────────┘
          ┌──────────────────────┼──────────────────────┐
          ▼                      ▼                      ▼
 ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ Open-Meteo      │   │  Local database  │   │  NOAA ERDDAP     │
 │ Marine          │──▶│ SQLite (WAL)     │◀──│  OISST + SSH     │
 │ (batched, 100/  │   │                  │   │  (chunked, with  │
 │  request)       │   │                  │   │   failover)      │
 └─────────────────┘   └────────┬─────────┘   └──────────────────┘
                                │
                   ┌────────────▼────────────┐
                   │     Plotly Dash UI      │       ┌─────────────────┐
                   ├─────────────────────────┤       │  ports.sqlite   │
                   │ Tab 1  Global Pulse     │◀──────│  FTS5 gazetteer │
                   │ Tab 2  Port Timeline    │       │   (offline)     │
                   │ Tab 3  Physics          │       └─────────────────┘
                   │ Tab 4  Data Export      │       ┌─────────────────┐
                   └─────────────────────────┘◀──────│ ocean mask 8 KB │
                                                     └─────────────────┘
```

**Language and runtime.** Python 3.10+. Network I/O is `asyncio` + `httpx`; the
storage layer is synchronous and thread-safe, reached from async code through
`asyncio.to_thread`.

**Process model.** By default the Dash server and the ingestion daemon run in
one process. There is exactly **one event loop**, on one background thread,
shared by the daemon and by any UI callback that needs to fetch something. One
loop means one HTTP connection pool and, critically, **one token bucket** — so
several concurrent user actions cannot collectively exceed a rate limit that
each one individually respects.

---

## 12. Data sources and APIs

Every source is free, public, and requires no key or account.

### 12.1 Open-Meteo Marine

| | |
|---|---|
| **Endpoint** | `https://marine-api.open-meteo.com/v1/marine` |
| **Fields** | `wave_height`, `wave_direction`, `wave_period`, `ocean_current_velocity`, `ocean_current_direction`, `sea_surface_temperature` |
| **Authentication** | None |
| **Licence** | CC BY 4.0 |

Three behaviours drive the whole client design, all observed rather than
documented:

**Many coordinates fit in one request.** Comma-separated `latitude` and
`longitude` lists return a JSON *array* of per-location objects. Verified at
**120 coordinates in a single 102 KB response**. This is not an optimisation:
polling 250 points individually every 15 minutes would be 24,000 calls a day,
over the free allowance. Batched at 100 per request, the same coverage costs a
few hundred calls a day.

**A single coordinate returns an object, not an array.** Anything iterating the
response has to normalise first.

**Failure is silent.** Land coordinates and dates before a variable's archive
floor both return **HTTP 200 with every value `null`**. There is no error to
catch. Whether a response contains data is a decision the client has to make
explicitly, and an all-null observation is rejected at the validation boundary.

**Archive floors differ per variable**, and were measured by bisection:

| Variable | Data from |
|---|---|
| Wave height, period, direction | 2021-12 |
| Ocean current velocity, direction | 2022-01 |
| Sea surface temperature | 2022-12 |

Requesting `start_date=1979-01-01` is accepted and returns a wall of nulls with
a 200 status. Without these floors the ingester would report a successful fetch
of nothing, at scale.

### 12.2 NOAA ERDDAP

| | |
|---|---|
| **Hosts** | `coastwatch.pfeg.noaa.gov/erddap`, with `upwell.pfeg.noaa.gov/erddap` as a fallback |
| **Sea temperature** | `ncdcOisst21Agg` — OISST v2.1, daily 0.25°, **1981-09-01 to present** |
| **Sea level** | `nesdisSSH1day` — sea level anomaly and geostrophic currents, daily 0.25°, **2017-02-13 onwards, several months in arrears** |
| **Authentication** | None |
| **Licence** | Public domain (U.S. Government work) |

ERDDAP is the least reliable dependency in the project, and the client is built
around that. Three distinct failure shapes were observed within one hour of
testing:

- HTTP 503 *"There was a (temporary?) problem"* on a request that had just
  succeeded.
- HTTP 404 *"Currently unknown datasetID=…"* for a dataset that worked minutes
  earlier — ERDDAP reloading, not a missing dataset.
- One longitude variant of a dataset offline while its twin served fine.

So every dataset is declared as a **list of equivalent variants** across
longitude conventions (`_LonPM180` and 0–360) and mirror hosts, and the client
walks that list. Two details matter:

**A 404 here is transient.** The usual rule — 4xx is permanent, 5xx is
transient — is exactly wrong for a reloading ERDDAP dataset. The body is
inspected, and `Currently unknown datasetID` is reclassified as retryable.

**Dimension signatures differ.** OISST is indexed
`[time][zlev][latitude][longitude]`; the sea level product is
`[time][latitude][longitude]` with no depth axis. Assuming one shape breaks the
other.

### 12.3 NGA World Port Index

| | |
|---|---|
| **Endpoint** | `https://msi.nga.mil/api/publications/world-port-index?output=csv` |
| **Contents** | 2,951 ports, 111 columns |
| **Licence** | Public domain |

Two traps. The **bare endpoint returns HTTP 400** — the `output` parameter is
mandatory. And **coordinates are DMS strings** (`30°20'00"N`), not decimal
degrees; reading them as floats yields nothing usable and silently piles every
port in a country onto the same point.

### 12.4 GeoNames

`cities15000.zip` and `countryInfo.txt` from
`https://download.geonames.org/export/dump/`, CC BY 4.0. Used for coastal place
names and to give ports a population — see [section 17](#17-the-offline-gazetteer).

---

## 13. What the data actually is

This matters more here than in most projects, and the interface repeats it
where a reader will see it.

**Open-Meteo marine values are numerical wave-model output, not buoy
measurements.** No instrument recorded a 1.18 m wave at 30°N 40°W; a model
computed it. The model is a good one, and it is the only way to get global
coverage for free, but it is not an observation.

**Some of what is stored is a forecast.** Open-Meteo returns future hours
alongside past ones. Those rows are flagged `is_forecast` and excluded from
every analysis by default. Once an hour has passed it is re-fetched as an
analysis and the flag clears — but it can only ever clear, never be set again,
so a value cannot silently revert to being a prediction.

**Sea temperature has two sources with different resolutions.** Near-term
values are hourly from Open-Meteo; historical values are daily from NOAA OISST.
Where both cover the same hour, the hourly value wins and the daily one only
fills gaps. That precedence is explicit in the storage layer, not incidental.

**"Warm-spell cells" is not a marine heatwave count.** The Hobday et al. (2016)
definition requires a fixed 30-year daily climatology, which a database built
over weeks does not have. What is computed is a rolling-baseline z-score
against each cell's own recent history, and it is labelled as an approximation
everywhere it appears.

**The coastal exceedance model contains no tides.** See
[Tab 3](#tab-3--physics--correlation).

---

## 14. Polling strategy and rate limits

The daemon polls on a configurable interval of 15, 30, 45 or 60 minutes,
defaulting to 30. The interval is re-read from storage at the top of every
cycle, so a UI change takes effect on the next tick.

Each cycle:

1. Refresh the sparse global grid — 253 active cells in **three batched
   requests**.
2. Refresh every tracked port, also batched.
3. Write a heartbeat.

A full cycle takes about **3 seconds** and yields around 24,000 observation
rows, because each request returns a *window* of hours rather than an instant.

### Why fetch a window

`past_days=2` and `forecast_days=2` give 96 hourly values per cell per request.
That means a missed cycle — a sleeping laptop, a dropped connection, a restart
— costs nothing: the next poll re-covers the gap. The upsert is idempotent, so
the overlap collapses.

### Rate limiting

All outbound requests pass through a **shared token bucket**: 2 requests per
second sustained, burst of 4. Because the bucket is shared across every
coroutine on the single event loop, parallel work cannot collectively exceed
the limit.

### Backoff, and where it belongs

Failures retry with exponential backoff and **full jitter** — without jitter,
every client that lost the same outage retries in lockstep and recreates it.

Backoff depth is per-call, and callers with a fallback lower it. This was not a
theoretical concern: because a reloading ERDDAP dataset is classified
transient, the first implementation spent minutes backing off against a broken
variant before ever trying the working one. ERDDAP fetches now run a fast pass
of one attempt per variant, and only if *every* variant fails does a patient
pass with full backoff begin. One dead mirror costs seconds; a real outage
still gets patience.

### The heartbeat

Written during the sleep between cycles, not only at the top of each one.
Writing it once per cycle would leave the timestamp stale for 29 of every 30
minutes and the interface would report a perfectly healthy daemon as STOPPED.

---

## 15. The sampling grid and the ocean mask

### Why a mask

Open-Meteo returns all-null for land. Without a mask, roughly a third of every
polling cycle is spent asking about Kansas.

The mask is derived from **one** global OISST slice at 1° resolution: masked
cells in that product are land. That is 64,800 cells, 66.5% of them ocean,
stored as a packed bit array of **8,104 bytes**. It costs one request, once,
and adds no dependency — no shapefile, no geometry library, no
point-in-polygon.

### Why equal-area

A naive 2°×2° lat/lon grid puts as many points in the 2° around the pole as
around the equator, where the cells are sixty times wider. Longitude spacing is
therefore scaled by 1/cos(latitude), and the spacing itself is chosen by
bisection to land near the configured target count. Latitudes beyond ±80° are
excluded: the wave model has little to say about permanently ice-covered water.

### Mask, then probe

The mask says where the sea is. It does not say where the *wave model* has a
domain. Those differ, so every cell is probed once and the result persisted.

In the verified run, 255 mask-approved candidates produced 253 working cells.
The two disabled were **22.1°N 69.7°E** — the Gulf of Kutch, a shallow enclosed
inlet — and **75.9°N 112.5°W** in the Canadian Arctic Archipelago. Both are sea
by the mask and outside the model's useful domain, which is exactly the
distinction the probe exists to catch. They are never requested again.

---

## 16. Historical fetching and the cache ledger

Historical data is fetched on demand when you load a port timeline, chunked
into one-year windows so a single request cannot time out. Windows are
inclusive at both ends and never overlap — a shared boundary would re-fetch a
day at every chunk edge, invisible in the data because the upsert deduplicates
it, and double the request count for long ranges.

### Resolving a cell that actually has data

**A location one product calls water, another calls land.** Open-Meteo runs its
wave model on its own grid and happily resolves a point a few kilometres off
Rotterdam; OISST, on a 0.25° mask, has that same cell masked and returns NaN
for every day of it.

This is not hypothetical — it was the observed behaviour during verification,
and it made sea temperature and sea level come back empty for exactly the
coastal ports the application exists to examine, in a way indistinguishable
from a broken fetch. Each dataset therefore probes a ten-day window outward
from the requested point, nearest candidate first, up to 1.5°, and caches the
result. For Rotterdam it relocates from 51.900, 4.233 to 51.900, 3.983 and the
data appears.

### The cache ledger

Every completed fetch is recorded with its dataset, bounding box and date
range. A later request is served locally only if one earlier fetch **fully
contains** it:

```
cached.min_lat <= requested.min_lat  AND  cached.max_lat >= requested.max_lat
cached.min_lon <= requested.min_lon  AND  cached.max_lon >= requested.max_lon
cached.start   <= requested.start    AND  cached.end     >= requested.end
cached.dataset == requested.dataset
```

**Containment, not overlap.** The intuitive test — "have we fetched something
near here, over roughly this period" — reports a hit when a one-week cached
window sits inside a five-year request, and the caller is handed a fraction of
the data believing it complete. That failure is silent and produces
confidently wrong analytics, so it has dedicated tests.

The `dataset` column is equally load-bearing: sea temperature coverage over a
box says nothing about sea level coverage over the same box.

---

## 17. The offline gazetteer

Search must work with no network, so it runs against a local SQLite database
with an **FTS5** index. The built database holds **20,401 entries**: 2,951
World Port Index ports and 17,515 coastal places, with 16,565 inland places
discarded by the ocean mask.

### The contentless index trap

The FTS5 table is declared `content=''`, which stores the inverted index
without a second copy of the text. That means it **cannot return column
values** — a contentless table hands back rowids and nothing else. Columns come
from a companion base table joined on rowid.

Declaring latitude and longitude inside a contentless FTS5 table, `UNINDEXED`
or not, yields NULL on every read. There is a test asserting real coordinates
come back.

> An FTS5 table is also **never populated automatically**. Declaring one and
> inserting only into the base table produces an index that matches nothing at
> all, raises no error anywhere, and looks exactly like a search box that never
> finds anything. Rows are inserted explicitly, and a test asserts the two
> tables have equal counts.

### Query safety

FTS5 `MATCH` accepts a query *language*, not a literal string. Raw input
reaches it as syntax, so `St. John's`, `a OR b` or a bare `*` would raise
`sqlite3.OperationalError` from inside an autocomplete callback — which fires
on every keystroke. Input is stripped of operator characters, split into
tokens, and each token quoted; only the final token receives a prefix wildcard.

### Ranking

By match tier, then population, then source:

| Tier | Match |
|---|---|
| 0 | Name is exactly what was typed |
| 1 | Name starts with what was typed |
| 2 | A later word in the name starts with it |
| 3 | It appears somewhere in the name |
| 4 | Matched only via alternate names |

**Ports do not automatically outrank cities.** That ordering seems reasonable
for a marine gazetteer, and it is wrong: it makes a small harbour beat a large
city of the same name, so `sydn` returns Sydney, Nova Scotia (population
105,968) instead of Sydney, Australia (5.6 million). Population comes first;
source only breaks ties. Where both sources describe the same place their
populations are identical, so the port still wins and brings its harbour
metadata with it.

For that tiebreak to work, ports need a population — the World Port Index does
not carry one. Each port therefore inherits the population of the largest
settlement within 35 km, matched through a one-degree bucket index. In the
verified build this enriched **2,111 of 2,951 ports**.

Diacritics are folded, so `Malaga` matches `Málaga`. Measured autocomplete
latency: **≈2 ms** over 20,401 entries.

---

## 18. Database schema

`data/oceanpulse.sqlite`, WAL mode, timestamps as epoch milliseconds in INTEGER
columns throughout — integer comparison is index-friendly and sidesteps every
string-collation and timezone-parsing bug that storing ISO text invites.

| Table | Contents |
|---|---|
| `marine_observations` | One row per position and time, keyed on `sha1(lat\|lon\|timestamp)` |
| `cache_ledger` | Completed fetches: dataset, bounding box, date range |
| `grid_points` | The sparse grid, with mask verdict and probe result |
| `tracked_ports` | Ports the daemon keeps current, with their resolved marine cell |
| `saved_datasets` | Named, re-runnable export specifications |
| `app_settings` | UI settings and the daemon heartbeat |

### Why observations are keyed on position and time alone

The key deliberately **excludes the source**, so an Open-Meteo wave reading and
a NOAA sea-level reading for the same cell and hour converge on one row rather
than two half-empty ones. That is what makes the correlation tab possible
without a join.

But a plain `COALESCE(old, new)` merge would be wrong in the other direction:
it would freeze the first forecast value for an hour forever and never accept
the correction. So each source declares which columns it **owns** (and may
overwrite with its own later value) and which it may only **fill** (write when
NULL):

| Source | Owns | Fills only |
|---|---|---|
| `open_meteo` | waves, currents, SST | — |
| `erddap_sla` | sea level, geostrophic components | — |
| `erddap_sst` | — | SST |

Two flags latch: `is_historical_cache` can only be set, never cleared, and
`is_forecast` can only be cleared, never set.

---

## 19. The math engine

### Wave energy flux

$$P = \frac{\rho g^2}{64\pi} H_{m0}^2 T \approx 0.49 \cdot H_{m0}^2 \cdot T \quad [\text{kW/m}]$$

With ρ = 1025 kg/m³ and g = 9.81 m/s², the coefficient works out to 490.6 W per
m²·s. A 2 m sea with an 8 s period carries about 15.7 kW/m.

> **One honest caveat.** The deep-water formula calls for the *energy* period
> Te, and Open-Meteo publishes the *peak* period Tp. For a JONSWAP-like
> spectrum Te ≈ 0.9 Tp, so using Tp overestimates power by roughly 11%. The
> conversion ratio is a named constant in `math_engine.py` rather than buried,
> so the assumption is visible and adjustable.

Missing inputs return `None`, never `0.0` — an unmeasured sea state is not a
calm one.

### Standardised anomalies

Z = (x − μ) / σ against a trailing rolling baseline that **excludes the current
point**, so a value cannot dampen the very statistic used to judge it.
Zero-variance windows yield NaN rather than dividing by zero.

### Bearings

Wave and current directions are averaged on the unit circle. Averaging 350° and
10° arithmetically gives 180° — due south, for two headings that are both
nearly due north.

### Downsampling

Any series over 2,500 points passes through **Largest-Triangle-Three-Buckets**
before reaching Plotly. LTTB preserves the visual shape — peaks and troughs
survive — which naive stride sampling does not. NaN values are dropped first,
since a triangle area involving NaN is NaN and would swallow a whole bucket.
Downsampling selects whole rows, so hover text stays aligned with the points
that survive.

---

## 20. Aggregation and export semantics

Aggregation resamples onto a uniform grid pinned to the **requested** date
range, not to the first and last observation, so quiet periods at the edges are
represented rather than trimmed away.

### Gap filling is per column, deliberately

The obvious design is a single "fill with 0 or leave blank" switch. Applied
uniformly it corrupts the data, because zero means different things in
different columns:

| Column type | Examples | Quiet window | Why |
|---|---|---|---|
| **Extensive** | `observation_count`, `wave_energy_kwh_m` | **Always 0** | Zero is the *true* value. |
| **Intensive** | `sst_celsius`, `wave_height_m`, `sea_level_anomaly_m` | **User's choice** | There is no measurement. Zero is not "no reading" — it asserts the sea was at freezing point. |

So counts and summed energy always fill with zero, and the interface setting
applies only where it is a genuine modelling decision.

### A stable schema

An export that matches no observations emits the **same columns** as one that
matches thousands. Otherwise a pipeline built against a busy period breaks the
first time it hits a quiet one — the hardest kind of failure to reproduce.

### Formats

**Parquet** is written through an explicit timezone-aware `pyarrow` schema, so
timestamps survive as UTC microseconds and integer counts stay integers. In
practice it runs about 40% the size of the equivalent CSV. **CSV** has no type
system, so timestamps are written as explicit ISO-8601 with a `Z` suffix — the
zone survives as text or not at all.

---

## 21. Design decisions and trade-offs

### One event loop, shared

Dash callbacks are synchronous WSGI functions; every network client here is
async. The naive bridge — `asyncio.run(...)` inside each callback — gives each
call its own loop, its own connection pool and its own token bucket, so N
concurrent user actions collectively blow through a rate limit each one
individually respects. There is instead exactly one loop on one thread, and UI
callbacks submit coroutines to it.

### The storage layer is synchronous

An all-async storage layer does not survive contact with WSGI callbacks:
reaching an async pool from one means either spinning an event loop per request
or bouncing through `run_coroutine_threadsafe`, and both are deadlock
factories. Storage is synchronous and thread-safe; the async daemon reaches it
through `asyncio.to_thread`. Network I/O — the part that genuinely benefits
from async — stays fully non-blocking. SQLite runs in WAL mode so readers and
the writer do not block each other, and WAL is set on **every** connection, not
just the one that created the file.

### A custom autocomplete rather than a Dropdown

Dash's `Dropdown` re-filters server-returned options on the client by substring
against the label. Gazetteer labels carry diacritics, so typing `Malaga`
retrieves Málaga successfully and the browser then discards it — the user sees
"no results" for a place that was found correctly.

The custom component also **does not write the selected label back into the
input**. Doing so changes the input value, which re-triggers the suggestion
callback, which reopens the list the click just closed. The obvious guard —
compare the text against the selected record — depends on Dash having
propagated the store before the input-change callback reads it, which it does
not reliably do. Leaving the typed text alone removes the race instead of
racing it; a confirmation line below the box shows what is selected.

### Shutdown is explicit

Cleaning up is not the same as stopping. The web server blocks the main thread
inside `app.run()`, so a signal handler that stops the daemon and closes the
database leaves the process still serving. `Ctrl+C` happens to work because
Werkzeug installs its own SIGINT handler; **SIGTERM** — what a service manager
and `kill` actually send — would hang until the stop timeout expired and the
process was killed outright. The handler therefore raises `SystemExit` to
unwind the blocking call. Measured: clean exit in about one second.

---

## 22. Resilience and error handling

| Failure mode | Response |
|---|---|
| **Network outage** | Exponential backoff with full jitter, capped at 15 minutes |
| **HTTP 429** | Transient; `Retry-After` honoured when supplied |
| **HTTP 5xx** | Transient; retried |
| **HTTP 4xx** | Permanent — *except* an ERDDAP 404 whose body says `Currently unknown datasetID`, which is a reloading dataset and is retried |
| **HTTP 200 with an error document** | ERDDAP returns `Error {…}` bodies with a 200 status; these are parsed and classified, not trusted |
| **Malformed JSON** | Transient. A truncated response or an HTML error page reaching the JSON parser is an upstream condition |
| **All-null response** | Not an error and not data. Rejected at the validation boundary; for a grid cell it disables the cell permanently |
| **Invalid records** | Dropped at the Pydantic boundary with a warning to `logs/errors.log`. One bad hour never blocks a batch |
| **Duplicate observations** | Idempotent, source-scoped upsert |
| **A dead ERDDAP mirror** | Fast failover to the next variant; patient retry only when all have failed |
| **Missing gazetteer** | Search degrades to a clear message; everything else is unaffected |
| **Cycle failure** | Logged, status set to DEGRADED, loop continues. A daemon that exits on a transient failure is worse than one that reports it and retries |

**Logging.** `logs/daemon.log` holds everything at INFO and above;
`logs/errors.log` holds WARNING and above only, so dropped records and upstream
failures are trivially greppable. Both rotate.

---

## 23. Security

**There is no authentication on the web interface.** It binds to `127.0.0.1` by
default, so only your own machine can reach it.

If you set `OCEAN_HOST=0.0.0.0`, **anyone on your network can read and query
your database**. The application prints a warning when started that way. If you
need remote access, put it behind a reverse proxy that handles authentication.

Other properties worth noting:

- The gazetteer is opened **read-only** (`file:…?mode=ro`), so the interface
  cannot modify it.
- All SQL uses parameter binding; no user input is ever concatenated into a
  query. FTS5 input is tokenised and quoted, as described in
  [section 17](#17-the-offline-gazetteer).
- Export filenames are sanitised, so a port identifier cannot produce a path.
- No telemetry, no analytics, and no outbound connections other than the four
  documented sources.

---

## 24. Testing

**115 tests, all offline.** They need no network and never touch your real data
directory — every test runs against a temporary database.

```bash
.venv/bin/python -m pytest          # macOS / Linux
.venv\Scripts\python -m pytest      # Windows
```

| File | Tests | Focus |
|---|---|---|
| `test_gazetteer.py` | 33 | DMS parsing, coastal filtering, FTS index population, query escaping against hostile input, ranking, population enrichment |
| `test_ingest.py` | 26 | Token bucket, batch parsing, all-null land detection, archive-floor clamping, griddap CSV shape, masked-cell resolution, chunk boundaries, containment |
| `test_storage.py` | 18 | Source-scoped merging, SST precedence, flag latching, true-distance radius, antimeridian wrap, ledger containment |
| `test_export.py` | 15 | Gap-fill semantics, schema stability, Parquet round-trip, CSV timezone text |
| `test_math_engine.py` | 14 | Wave power, circular means, LTTB shape preservation, rolling z-scores, resampling |
| `test_models.py` | 9 | All-null rejection, longitude wrapping, NaN handling, identity |

The tests encode the traps, not just the happy paths: that a land response is
HTTP 200 with nulls, that a 404 can be transient, that a contentless FTS5 table
returns no columns, that averaging bearings needs the unit circle, and that a
cache hit requires containment rather than proximity.

---

## 25. Project layout

```
run.py                        entry point: web, daemon, or gazetteer
run.sh / run.bat              bootstrap: venv, deps, start
requirements.txt              core dependencies
.env.example                  annotated configuration template

src/oceanpulse/
  config.py                   settings, endpoints, measured archive floors
  logging_setup.py            dual rotating logs
  models.py                   Pydantic validation boundary
  math_engine.py              wave power, anomalies, LTTB, resampling
  storage/
    base.py                   interface and filter types
    sqlite_backend.py         WAL, thread-safe, source-scoped upsert
    schema_sqlite.sql         schema
  gazetteer/
    build.py                  WPI DMS parsing, coastal filtering, FTS5 builder
    store.py                  search, ranking, query escaping
  ingest/
    http.py                   token bucket, backoff, error classification
    open_meteo.py             batched marine fetches, archive floors
    noaa_erddap.py            variant failover, masked-cell resolution
    grid.py                   ocean mask, equal-area grid
    cache_ledger.py           containment and chunking arithmetic
    historical.py             on-demand port backfill
    runner.py                 the single shared event loop
    daemon.py                 the polling loop
  exporting/
    aggregate.py              resampling, gap filling, CSV/Parquet
  ui/
    app.py, theme.py, services.py, place_search.py
    tab_pulse.py, tab_timeline.py, tab_analytics.py, tab_export.py

scripts/                      gazetteer builder, service install/uninstall
tests/                        115 offline tests
```

About 8,800 lines of Python.

---

## 26. Using this data for research

### Where the data lives

| File | What it is | Typical size |
|---|---|---|
| `data/oceanpulse.sqlite` | Every observation, ledger entry and setting | ~15 MB per week of polling |
| `data/oceanpulse.sqlite-wal` / `-shm` | Write-ahead log and shared-memory index | up to ~10 MB |
| `data/ports.sqlite` | Offline port and coastal-place search index | ~10 MB |
| `data/ocean_mask_1deg.bin` | Global land/sea bitmask | 8 KB |

The `-wal` and `-shm` files are **part of the database**. If you copy it, copy
all three, or close the app first. Copying only the `.sqlite` gives a stale
snapshot.

### Four caveats to carry into any analysis

**1. Most of this is model output, not measurement.** See
[section 13](#13-what-the-data-actually-is). Wave and current values come from
a numerical model. Sea temperature from NOAA OISST is a satellite analysis; sea
level is satellite altimetry. None of it is a buoy.

**2. Coverage starts at different dates for different variables.** Waves from
2021-12, currents from 2022-01, model SST from 2022-12, NOAA SST from 1981-09,
sea level from 2017-02. A model trained across a boundary will see a regime
change that is an artefact of data availability, not of the ocean.

**3. Sea level lags.** The altimetry product runs several months behind real
time. Any "current" analysis involving sea level is really an analysis of
several months ago.

**4. The grid is a sparse sample.** A few hundred points do not resolve
mesoscale structure. Eddies, fronts and coastal processes fall entirely between
the samples. For anything spatial, use tracked ports rather than the grid.

### Republication

| Component | Licence | Republication |
|---|---|---|
| NOAA OISST, sea level, World Port Index | Public domain (U.S. Government) | Permitted, no restriction |
| Open-Meteo marine | CC BY 4.0 | Attribution required |
| GeoNames place names | CC BY 4.0 | Attribution required |

An exported observation dataset contains no GeoNames data — only coordinates,
timestamps and measurements — so it carries no attribution obligation from that
source. Credit Open-Meteo and NOAA anyway; it costs nothing and it is what
makes a dataset trustworthy to a reader.

---

## 27. Attribution and licensing

**Wave, current and near-term sea-surface temperature data** —
[Open-Meteo](https://open-meteo.com/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This project is not
affiliated with or endorsed by Open-Meteo.

**Historical sea surface temperature** — NOAA Optimum Interpolation SST v2.1
(Huang et al.), served via
[NOAA CoastWatch ERDDAP](https://coastwatch.pfeg.noaa.gov/erddap/). Public
domain.

**Sea level anomaly and geostrophic currents** — NOAA CoastWatch altimetry,
served via ERDDAP. Public domain.

**Port locations** —
[NGA World Port Index](https://msi.nga.mil/Publications/WPI), Publication 150.
Public domain. This project is not affiliated with, endorsed by, or certified
by the National Geospatial-Intelligence Agency.

**Coastal place names** — [GeoNames](https://www.geonames.org/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Attribution is
displayed in the application footer.

**OceanPulse itself** — MIT licence, see [LICENSE](LICENSE).

### A note on accuracy

Values presented here are model output and satellite analyses, labelled as such
throughout the interface. Derived quantities — wave power, anomaly z-scores,
warm-spell counts and the coastal exceedance proxy — are computed by this
software from those inputs and inherit their uncertainty.

**This software is for research and educational use. It is not a navigational
aid and not an emergency warning system, and must not be relied upon for
life-safety decisions.** For operational marine forecasts and warnings, consult
your national hydrographic or meteorological service.
