"""Shared constants and threshold tables from the specification."""

WORKING_CRS = "EPSG:2961"  # NAD83(CSRS) UTM Zone 20N
DEFAULT_CELL_SIZE_M = 250
DEFAULT_EFFICIENCY = 0.5
GRAVITY = 9.81

# Scoring threshold tables — each maps a value range to a score.
# Ranges are checked in order; first match wins.

HYDRO_POWER_THRESHOLDS = [
    (2000, float("inf"), 100),   # >=2 kW
    (1000, 2000, 80),            # 1-2 kW
    (500, 1000, 60),             # 500W-1kW
    (200, 500, 40),              # 200-500W
    (50, 200, 20),               # 50-200W
    (0, 50, 0),                  # <50W or no stream
]

SOLAR_PERCENT_THRESHOLDS = [
    (40, float("inf"), 100),
    (25, 40, 80),
    (15, 25, 60),
    (5, 15, 40),
    (0, 5, 20),
]

# Elevation thresholds — a COASTAL-FLOOD PENALTY, not a positive criterion.
# Redesign note: the old table rewarded mid elevation (30-100m) and penalized
# high ground (300m+). That fought the wind goal (turbines want exposed high
# ground). Now elevation only penalizes genuinely low/at-risk coastal land;
# everything safely above storm-surge scores full marks (high ground is fine).
ELEVATION_THRESHOLDS = [
    (20, float("inf"), 100),     # Safe from coastal flooding (incl. high, exposed ground)
    (10, 20, 70),                # Marginal — low coastal
    (5, 10, 40),                 # Low coastal — real flood/surge risk
    (0, 5, 10),                  # Tidal / storm-surge zone
    # below 0 (at/below sea level) falls through to default score 0
]

ACCESS_DISTANCE_THRESHOLDS = [
    (0, 0, 100),     # Road intersects cell or civic address in cell
    (0, 50, 80),     # Within 50m
    (50, 200, 50),   # Within 200m
    (200, 500, 20),  # Within 500m
    (500, float("inf"), 0),
]

BUILDABLE_PERCENT_THRESHOLDS = [
    (30, float("inf"), 100),
    (20, 30, 80),
    (10, 20, 60),
    (5, 10, 30),
    (0, 5, 0),
]

PARCEL_SIZE_THRESHOLDS = [
    (100, float("inf"), 100),    # 100+ acres — room for everything + severance
    (50, 100, 92),
    (20, 50, 80),
    (10, 20, 60),
    (5, 10, 40),
    (2, 5, 20),
    (1, 2, 5),
]

# --- Open-ground capacity (merged solar + buildable) -----------------------
# For NS, PV yield is near-uniform (~1090 kWh/kWp), so usable solar potential is
# overwhelmingly about how much flat, open, unbuilt, non-water land a site has —
# not aspect. This replaces the old (saturated, aspect-based) solar criterion
# and the (constant-100) buildable criterion with one area-based metric.
OPEN_GROUND_PERCENT_THRESHOLDS = [
    (30, float("inf"), 100),     # >=30% of cell open & buildable: ample ground-mount + building room
    (20, 30, 85),
    (10, 20, 65),
    (5, 10, 40),
    (2, 5, 20),
    (0, 2, 0),
]
# Small bonus (added, capped at 100) when a good share of the open ground also
# faces south — nice-to-have for ground-mount, not decisive.
OPEN_GROUND_SOUTH_BONUS = 10
OPEN_GROUND_SOUTH_FRACTION = 0.25  # >=25% south-facing to earn the bonus

# --- Wind + terrain exposure -----------------------------------------------
# Wind is a BONUS/winter-redundancy play, not a core driver (solar covers most
# needs in NS). Scored from a Global Wind Atlas raster when present, else from
# terrain exposure (TPI) as a lower-confidence proxy.
# Mean wind speed (m/s) at ~100m hub height (Global Wind Atlas):
WIND_SPEED_THRESHOLDS = [
    (8.5, float("inf"), 100),
    (7.5, 8.5, 85),
    (6.5, 7.5, 65),
    (5.5, 6.5, 40),
    (0, 5.5, 15),
]
# Global Wind Atlas mean wind speed at 100m hub height — a Cloud-Optimized
# GeoTIFF (all of Canada, EPSG:4326, ~250m). Read by windowed /vsicurl/ range
# requests (no full 1.3GB download) into processed/wind.tif. Free/public.
WIND_GWA_URL = "https://gwa.cdn.nazkamapps.com/country_tifs_v4/CAN_wind-speed_100m.tif"

# Topographic Position Index (m): cell mean elevation minus surrounding-area
# mean. Positive = ridge/hilltop (exposed, windy); negative = valley (sheltered).
EXPOSURE_TPI_THRESHOLDS = [
    (15, float("inf"), 100),
    (7, 15, 80),
    (2, 7, 60),
    (-2, 2, 40),
    (float("-inf"), -2, 15),
]
EXPOSURE_RADIUS_M = 1000  # neighbourhood radius for TPI

# "Wind worth it here" flag: meaningful wind resource AND limited room to just
# add more solar instead (your own logic — wind only pays off when solar can't).
WIND_WORTH_IT_WIND_MIN = 65       # wind score at/above this
WIND_WORTH_IT_OPEN_GROUND_MAX = 40  # open-ground score at/below this

# --- Hydro flow-accumulation validity gate ---------------------------------
# A D8 flow-accumulation raster over flat coastal terrain can silently fail
# (flow never propagates). Only trust it when its max upstream-cell count is
# plausibly river-scale; otherwise fall back to the segment-length proxy and
# report low hydro confidence honestly.
FLOW_ACCUM_MIN_VALID_CELLS = 10000
# Resolution (m) to route flow at; coarser than fine DEMs so D8 propagates
# through coastal flats. None = route at native DEM resolution.
FLOW_ACCUM_TARGET_RES_M = 20.0

# Default scoring weights (sum to 100 over enabled criteria).
# Redesign rationale: hydro is the scarce, hard-to-replicate differentiator and
# best winter generator, so it leads. open_ground (solar room + buildability)
# and access are practicality gates. wind is a conditional winter-redundancy
# bonus. elevation is now only a low-coastal flood penalty (see thresholds).
# The old solar (5) + buildable (5) criteria were near-constant in NS and are
# replaced by open_ground; they remain registered but are off by default.
DEFAULT_WEIGHTS = {
    "hydro": 40,
    "access": 20,
    "open_ground": 15,
    "wind": 15,
    "elevation": 10,
}

# Confidence deductions
CONFIDENCE_DEDUCTIONS = {
    "no_flood_data": 20,
    "hydro_drainage_proxy_only": 20,
    "hydro_20m_dem": 15,
    "incomplete_land_cover_mask": 10,
    "no_road_evidence_200m": 15,
    "wind_proxy_only": 10,  # wind from terrain exposure, not a wind-resource raster
}

# Confidence bands — contiguous half-open ranges [low, high) so fractional
# confidence values can't fall into a gap (top band is inclusive of 100).
CONFIDENCE_BANDS = [
    (80, 100.001, "high"),
    (55, 80, "medium"),
    (0, 55, "low"),
]

# Flag names
FLAG_ACCESS_UNVERIFIED = "access_unverified"
FLAG_COASTAL_LOW_ELEVATION = "coastal_low_elevation"
FLAG_HYDRO_LOW_CONFIDENCE = "hydro_low_confidence"
FLAG_SOLAR_LOW_CONFIDENCE = "solar_low_confidence"
FLAG_PARCEL_NO_CANDIDATES = "parcel_no_assigned_candidates"
FLAG_WIND_PROXY_ONLY = "wind_exposure_proxy_only"  # no wind raster; TPI used
FLAG_WIND_WORTH_IT = "wind_worth_it"  # good wind + limited solar room

# Access flag threshold — scores below this trigger the flag
ACCESS_FLAG_THRESHOLD = 50

# Hydro feasibility minimums
MIN_HEAD_M = 3
MIN_DRAINAGE_AREA_KM2 = 0.5

# Intake/outfall pair separations (meters along stream)
HYDRO_PAIR_SEPARATIONS = [50, 100, 200, 500]

# Low-flow specific runoff for southern NS (L/s/km²)
# Calibrated from HYDAT station 01EF001 (LaHave River, 1250 km²):
# Summer (Jul-Sep) average = 8.1 L/s/km², annual average = 28.8 L/s/km²
SPECIFIC_RUNOFF_LOW = 8.0

# Stream search buffer (meters beyond candidate cell)
STREAM_BUFFER_M = 100

# Solar aspect classification
SOLAR_OPTIMAL_ASPECT = (135, 225)
SOLAR_ACCEPTABLE_ASPECT = (90, 270)
SOLAR_OPTIMAL_SLOPE = (5, 35)
SOLAR_ACCEPTABLE_SLOPE = (5, 45)
SOLAR_FLAT_SLOPE = 5  # Below this is considered flat

# Exclusion defaults
DEFAULT_EXCLUSION_OVERLAP_THRESHOLD = 0.5  # 50% overlap triggers exclusion

# --- Parcel / PID integration (Stage B) ------------------------------------
# NS Property Records Database (NSPRD) parcels — queryable polygons WITH a PID
# field, served by the same public ArcGIS service the Provincial Landscape
# Viewer (nsgi.novascotia.ca/plv) uses. Host `nsgiwa2` is reachable from the
# general internet (unlike the internal `gis7.nsgc.gov.ns.ca/ISD_GIS` host,
# which times out off the NS-gov network). Layer 0 = NSPRD.Property; SR 3857.
NSPRD_PARCEL_SERVICE = (
    "https://nsgiwa2.novascotia.ca/arcgis/rest/services/PLAN/PLAN_NSPRD_WM84/MapServer"
)
NSPRD_PARCEL_LAYER_ID = 0
NSPRD_PAGE_SIZE = 1000  # records per REST query page

# Source field names that should normalize to canonical PID / AAN columns.
# NSPRD exposes "PID"; variants cover other NSGI/PVSC extracts and aggregators.
PARCEL_PID_FIELDS = ("PID", "PID_NUMBER", "PIDNUMBER", "PID_NUM", "PROPERTY_ID")
PARCEL_AAN_FIELDS = (
    "AAN", "ASSESS_NO", "ASSESS_ACCT", "ACCT_NUM",
    "ASSESSMENT_ACCOUNT_NUMBER", "TARGET_AAN",
)

# Parcel aggregation defaults (Stage B)
# cell_weight lowered from 0.8 -> 0.65 so acreage (a stated top priority, and
# the basis for severance/privacy/solar-room) pulls harder on parcel ranking.
PARCEL_TOP_N_CELLS = 3      # parcel score = mean of top-N eligible cell scores
PARCEL_CELL_WEIGHT = 0.65   # weight on cell score vs. parcel-size score

# Parcel typing (Stage B) — land-only vs developed, and severance candidates.
PARCEL_TYPE_LAND_ONLY = "land_only"        # no buildings
PARCEL_TYPE_LIGHTLY_BUILT = "lightly_built"  # 1-2 buildings
PARCEL_TYPE_DEVELOPED = "developed"        # 3+ buildings
PARCEL_LIGHT_MAX_BUILDINGS = 2             # <= this (and >0) = lightly built
# A severance candidate: a developed/lightly-built parcel big enough that the
# resource-bearing land could plausibly be split off and sold/approached.
SEVERANCE_MIN_ACRES = 40.0
SEVERANCE_MAX_BUILDINGS = 3
SEVERANCE_MIN_CELL_SCORE = 50.0
FLAG_SEVERANCE_CANDIDATE = "severance_candidate"

# Sea-level exclusion for the buildability mask: DEM pixels at/below this
# elevation (m) are treated as water/ocean and not buildable. A cheap coastline
# proxy when no waterbody polygon layer is available.
SEA_LEVEL_M = 0.0

# Coastal flooding (NS Coastal Program) — a raster MapServer, exported and
# polygonized into a flood-exclusion layer (processed/flood.gpkg). Worst-case
# 2100 is the conservative default for a multi-decade off-grid build.
FLOOD_SERVICE = (
    "https://nsgiwa.novascotia.ca/arcgis/rest/services/"
    "OCN/OCN_Projected_Worst_Case_Flooding_2100_UT83/MapServer"
)
FLOOD_EXPORT_SIZE = 2048  # px per side over the study bbox (~24m at 50km)
