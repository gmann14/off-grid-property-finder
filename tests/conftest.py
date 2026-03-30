"""Shared synthetic fixtures for property-finder tests."""

import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import LineString, Point, Polygon, box

CRS = "EPSG:2961"
BBOX = (380000.0, 4900000.0, 381000.0, 4901000.0)  # 1km x 1km study area
CELL_SIZE = 250


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temporary directory for test outputs."""
    return tmp_path


@pytest.fixture
def study_bbox():
    return BBOX


@pytest.fixture
def small_grid():
    """4x4 grid of 250m cells covering the 1km bbox."""
    from src.grid import generate_candidate_grid
    return generate_candidate_grid(BBOX, cell_size=CELL_SIZE, crs=CRS)


@pytest.fixture
def sample_dem(tmp_path):
    """Write a synthetic 100x100 DEM raster (10m resolution) over BBOX."""
    dem_path = tmp_path / "dem.tif"
    xmin, ymin, xmax, ymax = BBOX
    width, height = 100, 100
    transform = from_bounds(xmin, ymin, xmax, ymax, width, height)

    # Create a simple tilted plane: elevation increases from south to north
    row_vals = np.linspace(50, 200, height)
    dem = np.tile(row_vals[:, np.newaxis], (1, width)).astype(np.float32)
    # Flip because raster row 0 is top (north)
    dem = dem[::-1]

    with rasterio.open(
        dem_path, "w", driver="GTiff",
        height=height, width=width, count=1, dtype="float32",
        crs=CRS, transform=transform, nodata=-9999,
    ) as dst:
        dst.write(dem, 1)

    return dem_path


@pytest.fixture
def sample_slope(tmp_path, sample_dem):
    """Generate slope raster from sample DEM."""
    from src.dem import generate_slope
    slope_path = tmp_path / "slope.tif"
    return generate_slope(sample_dem, slope_path)


@pytest.fixture
def sample_aspect(tmp_path, sample_dem):
    """Generate aspect raster from sample DEM."""
    from src.dem import generate_aspect
    aspect_path = tmp_path / "aspect.tif"
    return generate_aspect(sample_dem, aspect_path)


@pytest.fixture
def sample_roads(tmp_path):
    """Create a sample roads GeoDataFrame and save to GPKG."""
    xmin, ymin, xmax, ymax = BBOX
    roads = gpd.GeoDataFrame(
        {"road_id": [1, 2]},
        geometry=[
            LineString([(xmin, ymin + 500), (xmax, ymin + 500)]),  # horizontal road
            LineString([(xmin + 500, ymin), (xmin + 500, ymax)]),  # vertical road
        ],
        crs=CRS,
    )
    path = tmp_path / "roads.gpkg"
    roads.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def sample_streams(tmp_path):
    """Create sample stream network."""
    xmin, ymin, xmax, ymax = BBOX
    streams = gpd.GeoDataFrame(
        {
            "stream_id": [1],
            "drainage_area_km2": [2.5],
        },
        geometry=[
            LineString([
                (xmin + 100, ymax - 100),
                (xmin + 200, ymax - 300),
                (xmin + 300, ymax - 600),
                (xmin + 400, ymax - 900),
            ]),
        ],
        crs=CRS,
    )
    path = tmp_path / "streams.gpkg"
    streams.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def sample_exclusion_zones(tmp_path):
    """Protected area polygon covering the NE quadrant."""
    xmin, ymin, xmax, ymax = BBOX
    gdf = gpd.GeoDataFrame(
        {"exclusion_reason": ["protected_area"]},
        geometry=[box(xmin + 500, ymin + 500, xmax, ymax)],
        crs=CRS,
    )
    path = tmp_path / "protected_areas.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def sample_parcels(tmp_path):
    """Create 4 large parcels covering the study area."""
    xmin, ymin, xmax, ymax = BBOX
    mid_x = (xmin + xmax) / 2
    mid_y = (ymin + ymax) / 2
    parcels = gpd.GeoDataFrame(
        {"parcel_id": [1, 2, 3, 4]},
        geometry=[
            box(xmin, ymin, mid_x, mid_y),
            box(mid_x, ymin, xmax, mid_y),
            box(xmin, mid_y, mid_x, ymax),
            box(mid_x, mid_y, xmax, ymax),
        ],
        crs=CRS,
    )
    path = tmp_path / "parcels.gpkg"
    parcels.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def sample_civic(tmp_path):
    """Create sample civic address points."""
    xmin, ymin, xmax, ymax = BBOX
    civic = gpd.GeoDataFrame(
        {"civic_id": [1, 2]},
        geometry=[
            Point(xmin + 125, ymin + 125),  # inside first cell
            Point(xmin + 750, ymin + 750),  # elsewhere
        ],
        crs=CRS,
    )
    path = tmp_path / "civic.gpkg"
    civic.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def config_with_paths(tmp_path, sample_dem, sample_slope, sample_aspect,
                      sample_roads, sample_streams, sample_civic):
    """Create a Config object pointing to test fixtures."""
    from src.config import Config, Paths, StudyArea

    processed = tmp_path
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)

    # Copy/link fixtures into expected paths
    import shutil
    for src_name, dst_name in [
        (sample_dem, "dem.tif"),
        (sample_slope, "slope.tif"),
        (sample_aspect, "aspect.tif"),
        (sample_roads, "roads.gpkg"),
        (sample_streams, "streams.gpkg"),
        (sample_civic, "civic.gpkg"),
    ]:
        dst = processed / dst_name
        if not dst.exists() and src_name.exists():
            shutil.copy2(src_name, dst)

    return Config(
        study_area=StudyArea(bbox=BBOX, name="test"),
        cell_size_m=CELL_SIZE,
        paths=Paths(
            raw=tmp_path / "raw",
            processed=processed,
            output=output,
        ),
    )
