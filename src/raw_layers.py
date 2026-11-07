"""Where each layer's raw input lives under data/raw/: one table that ingest
reads its inputs through and check-data reports on, so the two can't disagree
about which files exist or which formats count."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawSource:
    """One place a layer's raw files can be: each of ``patterns`` globbed in
    each of ``subdirs`` (recursively when ``recursive``)."""

    subdirs: tuple[str, ...]
    patterns: tuple[str, ...]
    recursive: bool = False


@dataclass(frozen=True)
class RawLayer:
    """A raw input layer. ``sources`` are in order of preference: a layer is
    read from the first source that has any files (e.g. HRDEM over CDEM, the
    NSHN geodatabase over a shapefile). ``gdb_layer`` names the layer to read
    inside a File Geodatabase, where there is one."""

    sources: tuple[RawSource, ...]
    type: str  # "raster" or "vector"
    required: bool
    description: str
    gdb_layer: str | None = None


RAW_LAYERS: dict[str, RawLayer] = {
    "dem": RawLayer(
        sources=(RawSource(("hrdem",), ("*.tif",)),
                 RawSource(("dem",), ("cdem_*.tif", "*.tif"))),
        type="raster", required=True,
        description="DEM raster: HRDEM (2-5m) tiles in data/raw/hrdem/ preferred, "
                    "else CDEM (~18m) in data/raw/dem/",
    ),
    "streams": RawLayer(
        sources=(RawSource(("hydro",), ("*.gdb",)), RawSource(("hydro",), ("*.shp",))),
        type="vector", required=True,
        description="NSHN stream network (File Geodatabase, or a shapefile)",
        gdb_layer="nshn_v2_wa_line",
    ),
    "land_cover": RawLayer(
        sources=(RawSource(("land-cover",), ("*POLY*.shp",)),
                 RawSource(("land-cover",), ("*.shp",))),
        type="vector", required=True,
        description="NSTDB land cover (polygon shapefile)",
    ),
    "roads": RawLayer(
        sources=(RawSource(("roads",), ("*.osm.pbf",)), RawSource(("roads",), ("*.gpkg",)),
                 RawSource(("roads",), ("*.shp",))),
        type="vector", required=True,
        description="Road network (OSM .osm.pbf, or a GPKG/shapefile)",
    ),
    "crown_land": RawLayer(
        sources=(RawSource(("crown-land",), ("*.shp",)),),
        type="vector", required=False,
        description="Crown land parcels",
    ),
    "protected_areas": RawLayer(
        sources=(RawSource(("exclusions",), ("*.shp",), recursive=True),),
        type="vector", required=True,
        description="Protected areas (shapefile under data/raw/exclusions/)",
    ),
    "buildings": RawLayer(
        sources=(RawSource(("buildings",), ("*.gpkg",), recursive=True),
                 RawSource(("buildings",), ("*.shp",), recursive=True)),
        type="vector", required=False,
        description="Building footprints",
    ),
    "parcels": RawLayer(
        sources=(RawSource(("parcels",), ("*.gpkg", "*.shp", "*.geojson", "*.gdb"), recursive=True),),
        type="vector", required=False,
        description="Property parcels (Stage B) — or fetch with `ingest-parcels --from-rest`",
    ),
    "civic": RawLayer(
        sources=(RawSource(("civic",), ("*.gpkg", "*.shp", "*.geojson"), recursive=True),),
        type="vector", required=False,
        description="Civic address points",
    ),
    "waterbodies": RawLayer(
        sources=(RawSource(("water", "waterbodies"), ("*.shp", "*.gpkg"), recursive=True),),
        type="vector", required=False,
        description="Waterbody polygons (lakes/ocean) for the buildability mask",
    ),
}


def source_files(raw_dir: Path, source: RawSource) -> list[Path]:
    """The files ``source`` matches under ``raw_dir``: subdir by subdir and
    pattern by pattern, each pattern's matches sorted, each file once."""
    found: list[Path] = []
    for subdir in source.subdirs:
        directory = raw_dir / subdir
        if not directory.is_dir():
            continue
        for pattern in source.patterns:
            matches = directory.rglob(pattern) if source.recursive else directory.glob(pattern)
            found.extend(sorted(matches))
    return list(dict.fromkeys(found))


def files_by_source(raw_dir: Path, name: str) -> list[list[Path]]:
    """The files of each of layer ``name``'s sources, in preference order."""
    return [source_files(raw_dir, s) for s in RAW_LAYERS[name].sources]


def find_raw_files(raw_dir: Path, name: str) -> list[Path]:
    """The files layer ``name`` is read from: those of its first source that
    has any (empty when none does)."""
    for files in files_by_source(raw_dir, name):
        if files:
            return files
    return []


def search_locations(raw_dir: Path, name: str) -> str:
    """Where layer ``name`` is looked for, for messages."""
    parts = []
    for s in RAW_LAYERS[name].sources:
        for subdir in s.subdirs:
            where = f"{raw_dir / subdir}/{'**/' if s.recursive else ''}"
            parts.append(where + "{" + ",".join(s.patterns) + "}")
    return "; ".join(dict.fromkeys(parts))
