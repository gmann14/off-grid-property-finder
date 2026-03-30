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

# Elevation thresholds — tuned for coastal NS off-grid resilience.
# Moderate inland elevation is ideal (flood/storm/sea-level-rise buffer).
# Very low coastal land is risky; very high is remote and exposed.
ELEVATION_THRESHOLDS = [
    (30, 100, 100),              # Sweet spot: well above sea level, not too remote
    (100, 200, 90),              # Higher inland — solid
    (20, 30, 70),                # Above immediate flood risk
    (200, 300, 60),              # Getting remote/exposed
    (10, 20, 40),                # Low coastal — some flood risk
    (300, float("inf"), 30),     # Very high — exposed, remote
    (0, 10, 10),                 # Coastal floodplain — significant risk
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
    (50, float("inf"), 100),
    (20, 50, 90),
    (10, 20, 70),
    (5, 10, 40),
    (2, 5, 20),
    (1, 2, 5),
]

# Default scoring weights (Stage A only, sum to 100)
# Hydro is the primary differentiator for off-grid NS — solar works almost
# everywhere but winter needs a secondary power source.  Elevation reflects
# coastal-flood / sea-level-rise resilience.  Solar and buildable are near-
# uniform across the study area so carry minimal weight.
DEFAULT_WEIGHTS = {
    "hydro": 45,
    "elevation": 25,
    "access": 20,
    "solar": 5,
    "buildable": 5,
}

# Confidence deductions
CONFIDENCE_DEDUCTIONS = {
    "no_flood_data": 20,
    "hydro_drainage_proxy_only": 20,
    "hydro_20m_dem": 15,
    "incomplete_land_cover_mask": 10,
    "no_road_evidence_200m": 15,
}

# Confidence bands
CONFIDENCE_BANDS = [
    (80, 100, "high"),
    (55, 79, "medium"),
    (0, 54, "low"),
]

# Flag names
FLAG_ACCESS_UNVERIFIED = "access_unverified"
FLAG_COASTAL_LOW_ELEVATION = "coastal_low_elevation"
FLAG_HYDRO_LOW_CONFIDENCE = "hydro_low_confidence"
FLAG_SOLAR_LOW_CONFIDENCE = "solar_low_confidence"
FLAG_PARCEL_NO_CANDIDATES = "parcel_no_assigned_candidates"

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
