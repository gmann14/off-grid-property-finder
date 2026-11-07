"""Where each layer's raw input lives under data/raw/: one table that ingest
reads its inputs through and check-data reports on, so the two can't disagree
about which files exist or which formats count."""

from dataclasses import dataclass
from pathlib import Path


class AmbiguousRawDataError(RuntimeError):
    """Several raw files were found where only one can be the right input."""


@dataclass(frozen=True)
class RawSource:
    """One place a layer's raw files can be: each of ``patterns`` globbed in
    each of ``subdirs`` (recursively when ``recursive``).

    ``combine``: every matching file is read and the results concatenated
    (tiles, per-county downloads, several water layers). Otherwise exactly one
    file is expected, and several is an error (`AmbiguousRawDataError`)
    rather than an arbitrary pick; ``hint`` says how to resolve it."""

    subdirs: tuple[str, ...]
    patterns: tuple[str, ...]
    recursive: bool = False
    combine: bool = False
    hint: str = ""


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
        # Tiles are mosaicked (ingest_dem); CDEM also fills HRDEM's gaps.
        sources=(RawSource(("hrdem",), ("*.tif",), combine=True),
                 RawSource(("dem",), ("cdem_*.tif", "*.tif"), combine=True)),
        type="raster", required=True,
        description="DEM raster: HRDEM (2-5m) tiles in data/raw/hrdem/ preferred, "
                    "else CDEM (~18m) in data/raw/dem/",
    ),
    "streams": RawLayer(
        # NSHN is one provincial dataset: two geodatabases (or shapefiles) are
        # two versions, or lines next to polygons, and only one is right.
        sources=(RawSource(("hydro",), ("*.gdb",), hint="Keep one NSHN geodatabase in data/raw/hydro/."),
                 RawSource(("hydro",), ("*.shp",),
                           hint="Keep only the stream-line shapefile in data/raw/hydro/.")),
        type="vector", required=True,
        description="NSHN stream network (File Geodatabase, or a shapefile)",
        gdb_layer="nshn_v2_wa_line",
    ),
    "land_cover": RawLayer(
        # NSTDB polygon files (*POLY*) can come one per map sheet: read them
        # all. A folder of other shapefiles mixes lines/points: one expected.
        sources=(RawSource(("land-cover",), ("*POLY*.shp",), combine=True),
                 RawSource(("land-cover",), ("*.shp",),
                           hint="Name the land-cover polygon shapefile(s) *POLY*.shp, or keep only one.")),
        type="vector", required=True,
        description="NSTDB land cover (polygon shapefile)",
    ),
    "roads": RawLayer(
        # One road network: two OSM extracts overlap (and a country extract
        # dwarfs a province one), so several files is ambiguous.
        sources=(RawSource(("roads",), ("*.osm.pbf",), hint="Keep one OSM extract in data/raw/roads/."),
                 RawSource(("roads",), ("*.gpkg",), hint="Keep one road file in data/raw/roads/."),
                 RawSource(("roads",), ("*.shp",), hint="Keep one road file in data/raw/roads/.")),
        type="vector", required=True,
        description="Road network (OSM .osm.pbf, or a GPKG/shapefile)",
    ),
    "crown_land": RawLayer(
        # One provincial Crown land layer.
        sources=(RawSource(("crown-land",), ("*.shp",),
                           hint="Keep one Crown land shapefile in data/raw/crown-land/."),),
        type="vector", required=False,
        description="Crown land parcels",
    ),
    "protected_areas": RawLayer(
        # exclusions/ is a general folder (a flood layer can land there too),
        # so every shapefile is read only from exclusions/protected_areas/;
        # elsewhere under exclusions/ a single shapefile is taken as protected
        # areas and several are refused.
        sources=(RawSource(("exclusions/protected_areas",), ("*.shp",), recursive=True, combine=True),
                 RawSource(("exclusions",), ("*.shp",), recursive=True,
                           hint="Put the protected-area shapefile(s) in data/raw/exclusions/protected_areas/ "
                                "(every shapefile there is read) and other layers elsewhere.")),
        type="vector", required=True,
        description="Protected areas (shapefiles in data/raw/exclusions/protected_areas/, "
                    "or a single shapefile under data/raw/exclusions/)",
    ),
    "buildings": RawLayer(
        # Footprints come in tiles: read them all (exact duplicates across
        # files are dropped by ingest_buildings).
        sources=(RawSource(("buildings",), ("*.gpkg",), recursive=True, combine=True),
                 RawSource(("buildings",), ("*.shp",), recursive=True, combine=True)),
        type="vector", required=False,
        description="Building footprints",
    ),
    "parcels": RawLayer(
        # Per-county downloads (the study area spans two counties): read them
        # all; a PID in two files is merged by the PID dissolve.
        sources=(RawSource(("parcels",), ("*.gpkg", "*.shp", "*.geojson", "*.gdb"), recursive=True,
                           combine=True),),
        type="vector", required=False,
        description="Property parcels (Stage B) — or fetch with `ingest-parcels --from-rest`",
    ),
    "civic": RawLayer(
        sources=(RawSource(("civic",), ("*.gpkg", "*.shp", "*.geojson"), recursive=True, combine=True),),
        type="vector", required=False,
        description="Civic address points",
    ),
    "waterbodies": RawLayer(
        # Lakes, rivers and sea may be separate files; all of them are water
        # (overlaps count once downstream).
        sources=(RawSource(("water", "waterbodies"), ("*.shp", "*.gpkg"), recursive=True, combine=True),),
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


def raw_files_to_read(raw_dir: Path, name: str) -> list[Path]:
    """The files ingest reads for layer ``name``: those of its first source
    that has any. Raises `AmbiguousRawDataError` when that source expects one
    file and several match."""
    for source, files in zip(RAW_LAYERS[name].sources, files_by_source(raw_dir, name)):
        if not files:
            continue
        if len(files) > 1 and not source.combine:
            raise AmbiguousRawDataError(
                f"{name}: {len(files)} files found where one is expected, and which one is "
                f"right can't be told: {', '.join(str(f) for f in files)}. "
                + (source.hint or "Keep only the file to use.")
            )
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
