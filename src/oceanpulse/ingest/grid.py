"""The sparse global sampling grid, and the land/sea mask behind it.

**Why a mask at all.** Open-Meteo answers HTTP 200 with all-null values for a
land coordinate. Without a mask roughly a third of every polling cycle is
spent asking about Kansas. The mask is derived from a single global OISST
slice - masked cells in that product *are* land - which costs one request and
adds no dependency, no shapefile and no geometry library.

**Why an equal-area grid.** A naive 2x2 degree lat/lon grid puts as many
points in the 2 degrees around the pole as around the equator, where the
cells are sixty times wider. Longitude spacing is therefore scaled by
1/cos(latitude), which distributes points roughly evenly over the surface.

The mask is stored as a packed bit array: 180 x 360 cells is 64,800 bits,
about 8 KB.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Iterable, Sequence

from ..fileops import atomic_replace
from ..logging_setup import get_logger

log = get_logger(__name__)

MASK_LAT_CELLS = 180
MASK_LON_CELLS = 360
MASK_BYTES = (MASK_LAT_CELLS * MASK_LON_CELLS + 7) // 8
MASK_MAGIC = b"OPM1"


class OceanMask:
    """A 1-degree global land/sea mask backed by a packed bit array."""

    def __init__(self, bits: bytearray | None = None) -> None:
        self.bits = bits if bits is not None else bytearray(MASK_BYTES)

    # -- indexing ---------------------------------------------------------

    @staticmethod
    def _cell(latitude: float, longitude: float) -> int:
        lat_index = int(math.floor(latitude + 90.0))
        lon_index = int(math.floor(((longitude + 180.0) % 360.0)))
        lat_index = max(0, min(MASK_LAT_CELLS - 1, lat_index))
        lon_index = max(0, min(MASK_LON_CELLS - 1, lon_index))
        return lat_index * MASK_LON_CELLS + lon_index

    def set_ocean(self, latitude: float, longitude: float) -> None:
        index = self._cell(latitude, longitude)
        self.bits[index >> 3] |= 1 << (index & 7)

    def is_ocean(self, latitude: float, longitude: float) -> bool:
        index = self._cell(latitude, longitude)
        return bool(self.bits[index >> 3] & (1 << (index & 7)))

    def ocean_cell_count(self) -> int:
        return sum(bin(byte).count("1") for byte in self.bits)

    @property
    def is_empty(self) -> bool:
        return self.ocean_cell_count() == 0

    def is_coastal(self, latitude: float, longitude: float, max_km: float = 60.0) -> bool:
        """True when open water lies within `max_km`.

        Resolution-limited: the mask is 1 degree, so this really answers "is
        there ocean in a nearby cell". Generous rather than strict, which is
        the right way round - a coastal city wrongly excluded is invisible to
        search, while one wrongly included merely sits low in the ranking.
        """
        if self.is_ocean(latitude, longitude):
            return True
        rings = max(1, int(math.ceil(max_km / 111.0)))
        cos_lat = max(0.15, math.cos(math.radians(latitude)))
        lon_rings = max(1, int(math.ceil(max_km / (111.0 * cos_lat))))
        for dlat in range(-rings, rings + 1):
            lat = latitude + dlat
            if not -90.0 <= lat <= 90.0:
                continue
            for dlon in range(-lon_rings, lon_rings + 1):
                if self.is_ocean(lat, longitude + dlon):
                    return True
        return False

    def nearest_ocean_cell(
        self, latitude: float, longitude: float, max_rings: int = 4
    ) -> tuple[float, float] | None:
        """Centre of the closest ocean cell, searching outwards."""
        if self.is_ocean(latitude, longitude):
            return (latitude, longitude)
        for ring in range(1, max_rings + 1):
            best: tuple[float, tuple[float, float]] | None = None
            for dlat in range(-ring, ring + 1):
                for dlon in range(-ring, ring + 1):
                    if max(abs(dlat), abs(dlon)) != ring:
                        continue
                    lat = latitude + dlat
                    lon = ((longitude + dlon + 180.0) % 360.0) - 180.0
                    if not -90.0 <= lat <= 90.0 or not self.is_ocean(lat, lon):
                        continue
                    distance = math.hypot(dlat, dlon * math.cos(math.radians(latitude)))
                    if best is None or distance < best[0]:
                        best = (distance, (lat, lon))
            if best is not None:
                return best[1]
        return None

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(MASK_MAGIC + bytes(self.bits))
        # Swap into place so an interrupted write never leaves a half mask.
        # Windows refuses to replace a file another process holds open, so this
        # goes through the helper rather than Path.replace directly.
        atomic_replace(temporary, path)
        log.info(
            "Ocean mask saved to %s (%d ocean cells of %d)",
            path,
            self.ocean_cell_count(),
            MASK_LAT_CELLS * MASK_LON_CELLS,
        )

    @classmethod
    def load(cls, path: Path) -> "OceanMask | None":
        if not path.is_file():
            return None
        raw = path.read_bytes()
        if len(raw) != len(MASK_MAGIC) + MASK_BYTES or not raw.startswith(MASK_MAGIC):
            log.warning("Ocean mask at %s is malformed; ignoring it", path)
            return None
        return cls(bytearray(raw[len(MASK_MAGIC) :]))

    @classmethod
    def from_points(cls, points: Iterable[tuple[float, float, float]]) -> "OceanMask":
        mask = cls()
        for latitude, longitude, _ in points:
            mask.set_ocean(latitude, longitude)
        return mask


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------


def equal_area_points(spacing_deg: float, max_latitude: float = 80.0) -> list[tuple[float, float]]:
    """Roughly equal-area sample points at the given latitude spacing.

    Latitudes above `max_latitude` are excluded: the wave model has little to
    say about permanently ice-covered water, and those cells would consume
    request budget to return nulls.
    """
    points: list[tuple[float, float]] = []
    latitude = -max_latitude + spacing_deg / 2.0
    while latitude <= max_latitude:
        cos_lat = math.cos(math.radians(latitude))
        if cos_lat <= 1e-6:
            latitude += spacing_deg
            continue
        lon_step = spacing_deg / cos_lat
        count = max(1, int(round(360.0 / lon_step)))
        actual_step = 360.0 / count
        for index in range(count):
            longitude = -180.0 + actual_step * (index + 0.5)
            points.append((round(latitude, 4), round(longitude, 4)))
        latitude += spacing_deg
    return points


def build_macro_grid(
    mask: OceanMask | None, target_points: int = 250, max_latitude: float = 80.0
) -> list[tuple[float, float, bool]]:
    """Choose a spacing that yields about `target_points` ocean cells.

    Binary search rather than arithmetic, because the ocean fraction varies
    with spacing in a way that has no closed form.
    """
    low, high = 1.0, 40.0
    best: list[tuple[float, float]] = []

    for _ in range(24):
        spacing = (low + high) / 2.0
        candidates = equal_area_points(spacing, max_latitude)
        if mask is not None and not mask.is_empty:
            ocean = [p for p in candidates if mask.is_ocean(*p)]
        else:
            ocean = candidates
        if not ocean:
            high = spacing
            continue
        best = ocean
        if len(ocean) > target_points:
            low = spacing  # too many points -> widen the spacing
        else:
            high = spacing
        if abs(len(ocean) - target_points) <= max(3, target_points // 40):
            break

    if mask is None or mask.is_empty:
        # No mask yet: hand back candidates unflagged and let the probe sort
        # land from sea. Costs a couple of extra requests, once.
        return [(lat, lon, True) for lat, lon in best]
    return [(lat, lon, True) for lat, lon in best]


def summarise_grid(points: Sequence[tuple[float, float, bool]]) -> str:
    if not points:
        return "no grid points"
    lats = [p[0] for p in points]
    return (
        f"{len(points)} points, latitude {min(lats):.1f} to {max(lats):.1f}"
    )


def default_mask_date() -> dt.date:
    """A date the OISST final product is certain to cover.

    The final analysis lags real time by roughly two weeks, so asking for
    yesterday returns an error rather than data.
    """
    return dt.date.today() - dt.timedelta(days=45)
