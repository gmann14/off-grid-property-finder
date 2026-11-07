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
# coastal_low_elevation (SPEC §2.1, only without a flood layer): a cell whose
# elevation score is one of the low-coastal bands, i.e. a mean under this many
# metres, or below sea level (score 0). The no-DEM neutral score (50) isn't one.
COASTAL_LOW_ELEVATION_M = 20
COASTAL_LOW_ELEVATION_SCORES = frozenset(
    {s for _lo, hi, s in ELEVATION_THRESHOLDS if hi <= COASTAL_LOW_ELEVATION_M} | {0}
)

# Distance from the cell polygon (not its centre) to the nearest road or civic
# address. Bands are low < d <= high: SPEC says "within" 50/200/500 m.
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

SQ_M_PER_ACRE = 4046.86

# Scored on land acres (the parcel's acreage less any lake, river or sea in it),
# not the deeded area: see src.exclusions.parcel_land_acres.
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
# Estimated mean wind speed (m/s) at a small off-grid turbine's hub height
# (default 30 m — see WIND_HUB_HEIGHT_M). Bands follow small-turbine practice:
# ~4 m/s is the usual floor for a small turbine to be worth installing, ~5 m/s
# is good, 6+ m/s is excellent. (The old bands were for GWA's 100 m layer —
# utility-turbine height — where nearly every parcel cleared 5.5 m/s.)
WIND_SPEED_THRESHOLDS = [
    (6.0, float("inf"), 100),
    (5.0, 6.0, 85),
    (4.5, 5.0, 65),
    (4.0, 4.5, 40),
    (0, 4.0, 15),
]
# Small off-grid turbines (1–10 kW) sit on ~18–37 m towers; in NS forest
# (trees ~20 m) a 30 m hub clears the canopy by the usual ~10 m. Overridable
# via `wind.hub_height_m` in config.yaml.
WIND_HUB_HEIGHT_M = 30.0
# Global Wind Atlas mean wind speed COGs (all of Canada, EPSG:4326, ~0.0025°
# ≈ 200–280 m). Read by windowed /vsicurl/ range requests (no full ~1.3 GB
# download). Free/public. GWA publishes Canada at 10, 50 and 100 m. The hub
# speed is extrapolated per pixel from the 50 m and 100 m layers with a
# power-law shear profile (see ingest_wind). The 10 m layer is NOT used: over
# NS forest it sits inside the canopy (~0.1–0.5 m/s under ~5 m/s at 50 m).
# Overridable via `wind.gwa_layers`.
WIND_GWA_URL_TEMPLATE = "https://gwa.cdn.nazkamapps.com/country_tifs_v4/CAN_wind-speed_{height}m.tif"
WIND_GWA_LAYERS = {h: WIND_GWA_URL_TEMPLATE.format(height=h) for h in (50, 100)}
# Clamp on the per-pixel shear exponent α = ln(v_hi/v_lo) / ln(h_hi/h_lo).
# Open water/grass sits ~0.1, forest ~0.3–0.5 (study area 50→100 m: median 0.42).
WIND_SHEAR_ALPHA_MIN = 0.05
WIND_SHEAR_ALPHA_MAX = 0.6
# Canopy guard: a pixel whose lower layer is below half the upper layer
# (implied α > 1 between 50/100 m, > 0.43 between 10/50 m) is treated as an
# unusable lower layer and written as nodata. If more than
# WIND_MAX_REJECTED_FRACTION of the pixels fail, the layer pair is refused
# outright. On the real study area 0% of 50/100 m pixels fail; 52% of 10/50 m
# pixels do.
WIND_MIN_LAYER_RATIO = 0.5
WIND_MAX_REJECTED_FRACTION = 0.05
# GDAL /vsicurl/ network settings for the GWA reads (seconds / attempts), so a
# stalled CDN fails with a clear error instead of hanging the ingest.
WIND_HTTP_TIMEOUT_S = 60
WIND_HTTP_MAX_RETRY = 2
# Bump when ingest_wind's method changes, so the manifest invalidates any
# wind.tif built by the old code (see src/manifest.py).
# v2: 50/100 m layers, canopy guard, pixel-snapped/clipped source windows.
WIND_INGEST_METHOD = "gwa-powerlaw-nearest-anchor-v2"
WIND_NODATA = -9999.0

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
WIND_WORTH_IT_WIND_MIN = 65       # wind score at/above this (>= 4.5 m/s at hub)
WIND_WORTH_IT_OPEN_GROUND_MAX = 40  # open-ground score at/below this (< 10% open)

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
FLAG_PARCEL_BELOW_MIN_AREA = "parcel_below_min_area"  # under min_parcel_area_acres of land
FLAG_PARCEL_NOT_PROPERTY = "parcel_not_property"  # road right-of-way, water lot or placeholder PID

# NSPRD's ROADWAT field holds an ordinary parcel's own PID, and a code for
# polygons that are a road right-of-way or a water lot. Measured 2026-11-07 on
# the Lunenburg pull: "00000003" polygons (2,067) lie a median 99.7% within
# 15 m of a mapped road and are long strips (median 4πA/P² 0.07 vs 0.62);
# "00000002" polygons (1,310) are a median 99% water. Neither is land anyone can
# buy, so they are never ranked (one highway strip had ranked 6th).
PARCEL_ROADWAT_NOT_PROPERTY = {"00000002": "water lot", "00000003": "road right-of-way"}
# Placeholder PIDs ("00000000", "00000006" in the pull): not single properties,
# and nothing ViewPoint can look up.
PARCEL_PLACEHOLDER_PID_PATTERN = r"0{7}\d"
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

# Drainage area from the flow-accumulation raster is read along the stream's
# own mapped line: the max of the 3x3-pixel reads within this distance either
# side of a point. NSHN lines wander more than one 20 m routing pixel off the
# DEM-routed channel, so a single-point read can miss a 20+ km² brook entirely.
HYDRO_DRAINAGE_WINDOW_M = 60.0
# ...except the last stretch of a line drawn in its flow direction
# (FLOWDIR = 1), where it meets its receiving river or lake and the 3x3 read
# picks up the receiving water's drainage instead of its own.
HYDRO_DRAINAGE_JUNCTION_SKIP_M = 40.0
# Spacing (m) of the samples taken along each stream line.
HYDRO_PROFILE_STEP_M = 5.0
# Head (SPEC §3.1) is read off a channel profile: each sample takes the lowest
# DEM pixel within this many pixels (3x3 at 1), so a line drawn a few metres
# up the bank reads the channel bed, then a running median over ±this many
# metres along the line damps pixel noise. Head is then the smaller of the
# drops on the carved (min over the HYDRO_HEAD_CONDITION_M upstream) and
# filled (max over the HYDRO_HEAD_CONDITION_M downstream) profile, so DEM
# bumps along the channel aren't head. The window is finite so a road
# embankment or DEM rise further down the flow path can't wipe out the head
# of every pair above it.
HYDRO_CHANNEL_SNAP_PX = 1
HYDRO_HEAD_SMOOTH_M = 20.0
HYDRO_HEAD_CONDITION_M = 200.0

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

# Minimum fraction of ingested parcels that must carry a real PID. If the
# source schema drifts (a service renames/moves the PID field) and no column
# in PARCEL_PID_FIELDS matches, every PID comes back blank — this catches that
# case so ingest fails loudly instead of silently shipping an unusable PID list.
MIN_PID_COVERAGE_FRACTION = 0.5

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


# --- Map score tiers ---------------------------------------------------------
# One table for the map's layers, colours, opacity and legend, with edges at the
# tiers the results README reports (>=80 / >=70 / >=60). Colours are ColorBrewer
# RdYlBu (5-class), which stays distinguishable with red-green colour blindness
# (the old red-to-green ramp did not). (floor, label, colour, fill opacity),
# best first; a score belongs to the first tier whose floor it reaches.
SCORE_TIERS = [
    (80, "Excellent", "#2c7bb6", 0.75),
    (70, "Very good", "#abd9e9", 0.60),
    (60, "Good", "#ffffbf", 0.50),
    (40, "Fair", "#fdae61", 0.40),
    (0, "Poor", "#d7191c", 0.30),
]
NO_SCORE_COLOUR = "#808080"
