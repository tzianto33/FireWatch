"""Terrain around an ignition point: elevation, slope and water.

Elevation comes from AWS Terrain Tiles (Terrarium encoding: SRTM, GMTED and
ETOPO1 bathymetry, public on the AWS Open Data registry, no key or quota).
A handful of 256 px PNG tiles covers a simulation; they are cached on disk
and resampled onto a square grid centred near the ignition. Coordinates
inside the model are metres east (x) and north (y) of the ignition.
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

import httpx
from PIL import Image

from firewatch.weather import CACHE_DIR

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_DIR = CACHE_DIR.parent / "tiles"
M_PER_DEG_LAT = 111_320.0
TARGET_STEP_M = 500   # grid resolution aimed for, whatever the extent
MAX_GRID_N = 121
EARTH_M_PER_PX_Z0 = 156_543.03  # metres per pixel at zoom 0 on the equator


@lru_cache(maxsize=64)
def _tile(z: int, x: int, y: int) -> list[list[float]]:
    """Elevation (m) for one tile, as rows of 256 values."""
    path = TILE_DIR / f"{z}_{x}_{y}.png"
    if path.exists():
        data = path.read_bytes()
    else:
        resp = httpx.get(TILE_URL.format(z=z, x=x, y=y), timeout=30)
        resp.raise_for_status()
        data = resp.content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    px = Image.open(io.BytesIO(data)).convert("RGB").load()
    return [[px[i, j][0] * 256 + px[i, j][1] + px[i, j][2] / 256 - 32768 for i in range(256)]
            for j in range(256)]


def elevation_at(lat: float, lon: float, z: int) -> float:
    """Nearest-pixel elevation from the Web Mercator tile pyramid."""
    n = 2 ** z
    gx = (lon + 180) / 360 * n * 256
    gy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n * 256
    tx, ty = int(gx // 256) % n, min(max(int(gy // 256), 0), n - 1)
    return _tile(z, tx, ty)[min(int(gy % 256), 255)][min(int(gx % 256), 255)]


class FlatTerrain:
    """Level land everywhere (for tests and as a fallback)."""

    source = "flat terrain assumed"

    def gradient(self, x: float, y: float) -> tuple[float, float]:
        return 0.0, 0.0

    def is_water(self, x: float, y: float) -> bool:
        return False


SNAP_DEG = 0.05                          # grid centres snap to this, so nearby ignitions share a download
HALF_SIZES = (6_000, 12_000, 20_000, 34_000)  # metres; fixed sizes so the cache is reused


class GridTerrain:
    def __init__(self, lat0: float, lon0: float, half_size_m: float, n: int, elev: list[list[float]]):
        self.lat0, self.lon0, self.half, self.n = lat0, lon0, half_size_m, n
        self.ox = self.oy = 0.0  # ignition position relative to the grid centre, metres
        self.step = 2 * half_size_m / (n - 1)
        self.elev = elev  # elev[j][i]: row j northwards, column i eastwards
        self.source = f"terrain from AWS Terrain Tiles, {self.step:.0f} m grid"
        # Central-difference gradients on the grid (one-sided at the edges).
        self.dzdx = [[0.0] * n for _ in range(n)]
        self.dzdy = [[0.0] * n for _ in range(n)]
        for j in range(n):
            for i in range(n):
                i0, i1 = max(i - 1, 0), min(i + 1, n - 1)
                j0, j1 = max(j - 1, 0), min(j + 1, n - 1)
                self.dzdx[j][i] = (elev[j][i1] - elev[j][i0]) / ((i1 - i0) * self.step)
                self.dzdy[j][i] = (elev[j1][i] - elev[j0][i]) / ((j1 - j0) * self.step)

    @classmethod
    def fetch(cls, lat0: float, lon0: float, half_size_m: float, n: int | None = None) -> "GridTerrain":
        n = n or max(41, min(MAX_GRID_N, int(2 * half_size_m / TARGET_STEP_M) + 1))
        m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
        step = 2 * half_size_m / (n - 1)
        # Zoom whose pixels are about half the grid step: detailed, but only a few tiles.
        px_m = EARTH_M_PER_PX_Z0 * math.cos(math.radians(lat0))
        z = max(8, min(13, round(math.log2(px_m / (step / 2)))))
        elev = [[elevation_at(lat0 + (-half_size_m + j * step) / M_PER_DEG_LAT,
                              lon0 + (-half_size_m + i * step) / m_per_deg_lon, z)
                 for i in range(n)] for j in range(n)]
        return cls(lat0, lon0, half_size_m, n, elev)

    @classmethod
    def around(cls, lat: float, lon: float, reach_m: float) -> "GridTerrain":
        """Terrain covering `reach_m` around an ignition, from a shared, snapped grid."""
        clat = round(round(lat / SNAP_DEG) * SNAP_DEG, 4)
        clon = round(round(lon / SNAP_DEG) * SNAP_DEG, 4)
        slack = SNAP_DEG / 2 * M_PER_DEG_LAT * math.sqrt(2)  # worst-case offset of the ignition from the centre
        half = next((h for h in HALF_SIZES if h >= reach_m + slack), HALF_SIZES[-1])
        grid = cls.fetch(clat, clon, half)
        grid.ox = (lon - clon) * M_PER_DEG_LAT * math.cos(math.radians(clat))
        grid.oy = (lat - clat) * M_PER_DEG_LAT
        return grid

    def _cell(self, x: float, y: float) -> tuple[int, int, float, float] | None:
        x, y = x + self.ox, y + self.oy
        fx = (x + self.half) / self.step
        fy = (y + self.half) / self.step
        if not (0 <= fx <= self.n - 1 and 0 <= fy <= self.n - 1):
            return None
        i, j = min(int(fx), self.n - 2), min(int(fy), self.n - 2)
        return i, j, fx - i, fy - j

    def _bilinear(self, grid: list[list[float]], x: float, y: float) -> float:
        cell = self._cell(x, y)
        if cell is None:
            return 0.0
        i, j, u, v = cell
        return ((1 - u) * (1 - v) * grid[j][i] + u * (1 - v) * grid[j][i + 1]
                + (1 - u) * v * grid[j + 1][i] + u * v * grid[j + 1][i + 1])

    def gradient(self, x: float, y: float) -> tuple[float, float]:
        """(dz/dx, dz/dy): rise per metre eastwards and northwards."""
        return self._bilinear(self.dzdx, x, y), self._bilinear(self.dzdy, x, y)

    def is_water(self, x: float, y: float) -> bool:
        """Sea (elevation 0) at the nearest grid node. Outside the grid counts as land."""
        fx = round((x + self.ox + self.half) / self.step)
        fy = round((y + self.oy + self.half) / self.step)
        if not (0 <= fx < self.n and 0 <= fy < self.n):
            return False
        return self.elev[fy][fx] <= 0.0
