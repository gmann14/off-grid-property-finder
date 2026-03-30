# Off-Grid Property Finder — Specification

> Rank Nova Scotia land for off-grid capability and self-sufficiency potential.
> "WaveScout but for land."

## 1. Project Overview

A geospatial analysis tool that evaluates Nova Scotia land on its suitability for off-grid living, focusing on micro-hydro electricity potential, solar exposure, terrain safety, access confidence, and developability. The tool ingests open GIS data, produces a scored suitability surface or candidate-cell ranking, and optionally joins those results to property parcels when authoritative parcel data is available.

## 2. Scoring Methodology

The scoring model is split into three layers:

1. **Hard exclusions** — only applied when supported by authoritative data
2. **Base suitability score** — default off-grid potential score
3. **Optional preference layers** — user-specific lifestyle preferences such as distance to a town

The working unit is fixed by stage:
- **Stage A (MVP):** a fixed `250m × 250m` square candidate cell evaluated against a coarse rural-eligibility mask
- **Stage B:** an authoritative parcel that aggregates underlying candidate-cell results

**Important Stage A rule:** candidate cells stay fixed squares. The rural-eligibility mask determines which cells are included in analysis, but it does **not** clip cell geometry. Buildability/open-area constraints are scored later; they are not used to decide whether a cell exists.

Every result should include:
- `score` — the normalized score over enabled criteria
- `confidence_score` — numeric confidence from `0-100`
- `confidence_band` — `high`, `medium`, or `low`
- `status` — `eligible` or `excluded`
- `exclusion_reasons` — empty for eligible records
- `flags` — non-fatal warnings such as `access_unverified` or `coastal_low_elevation`

### 2.1 Hard Exclusions and Flags

**Hard exclusions** should be conservative and only use authoritative layers:
- **Stage A candidate cell** is excluded only when its centroid falls inside a mapped protected area or other no-development land designation, or when excluded-area overlap exceeds a configurable threshold (default: `>=50%`)
- **Stage A candidate cell** is excluded only when its centroid falls inside a mapped high-risk flood polygon where authoritative flood mapping exists, or when excluded-area overlap exceeds the same configurable threshold
- **Stage B parcel** is below configurable minimum parcel area (default: 1 acre)

**Flags** should not automatically zero out a score:
- No mapped road or civic-address evidence nearby → `access_unverified`
- Candidate cell is low elevation near the coast but no authoritative flood layer is available → `coastal_low_elevation`
- Hydro estimate is based on low-resolution DEM or ungauged watershed proxy only → `hydro_low_confidence`
- Solar estimate lacks land-cover/building mask → `solar_low_confidence`

Excluded records should still be emitted in output with `status = excluded` and explicit `exclusion_reasons`.

**Stage B flags** (only when parcel data is available):
- No eligible candidate-cell centroids fall within the parcel → `parcel_no_assigned_candidates`

**Output semantics:**
- Eligible records keep a numeric `score`
- Excluded records set `score = null`
- Ranked views should sort and display eligible records by default, with excluded records available in the same export or a clearly labeled filtered view

### 2.2 Stage A Candidate Suitability Criteria (100% of default MVP score)

> **Implementation note:** The weights below reflect the implemented defaults in `config.yaml` and `src/constants.py`. These were tuned for Nova Scotia off-grid screening: hydro is the primary differentiator because solar works almost everywhere in NS, while micro-hydro potential varies dramatically by location. Solar and buildable carry minimal weight because they are near-uniform across the rural study area. Weights are configurable and auto-renormalize when criteria are disabled.

#### Micro-Hydro Potential (45%)

**What we're measuring:** The likelihood that a candidate cell has a nearby feasible micro-hydro opportunity.

**Core formula:**
```
Power (W) = Q (L/s) × H (m) × g (9.81) × η (0.5–0.7)
```
Where Q = flow rate, H = head (elevation drop), η = system efficiency.

**Scoring approach:**
1. Identify stream reaches within or adjacent to each candidate cell using NSHN (100m buffer)
2. Generate candidate intake/outfall pairs along the same connected flow path over 50–500m separations
3. Extract DEM elevations for each candidate pair after normalizing vertical datum
4. Estimate **design low-flow** discharge using drainage area and conservative runoff assumptions (see §3.2)
5. Compute theoretical continuous power for each feasible candidate
6. Use the **best feasible low-flow site** reachable from the candidate cell buffer, not simply the maximum head anywhere nearby

| Estimated Continuous Low-Flow Power | Score |
|---|---|
| ≥2 kW continuous | 100 |
| 1–2 kW | 80 |
| 500W–1 kW | 60 |
| 200–500W | 40 |
| 50–200W | 20 |
| No stream / <50W | 0 |

**Minimum viable threshold:** 3m head + 10 L/s ≈ 150W continuous at 50% efficiency. This is a weak but still meaningful micro-hydro site.

**Implementation constants:** `MIN_HEAD_M = 3`, `MIN_DRAINAGE_AREA_KM2 = 0.5`, `SPECIFIC_RUNOFF_LOW = 8.0 L/s/km²` (calibrated from HYDAT station 01EF001, LaHave River summer low-flow average).

#### Solar Exposure (5%)

**What we're measuring:** The amount of candidate-cell area that is realistically suitable for ground-mounted or roof-independent solar.

**Why low weight:** Solar aspect/slope suitability is near-uniform across rural southern NS. Almost every cell has some acceptable solar exposure, so this criterion provides little differentiation. It still contributes to the composite score to reward cells with particularly good south-facing terrain.

**Scoring approach:**
1. Derive aspect and slope rasters from DEM
2. Score each cell for solar suitability:
   - Aspect 135°–225° and slope 5°–35° = optimal
   - Aspect 90°–270° and slope 5°–45° = acceptable
   - Flat land (slope <5°) = good if not otherwise constrained
   - North-facing steep slopes = poor
3. Mask out obvious constraints where data exists: water/wetland polygons, dense forest cover, existing buildings
4. Aggregate to candidate cell as `% solar-suitable open area`
5. Store NRCan insolation as metadata for reporting, but do **not** multiply the score by it in the MVP because regional variation within the study area is small

| % of candidate cell that is solar-suitable open area | Score |
|---|---|
| ≥40% solar-suitable (any qualifying aspect) | 100 |
| 25–40% | 80 |
| 15–25% | 60 |
| 5–15% | 40 |
| <5% | 20 |

#### Elevation and Terrain Safety (25%)

**What we're measuring:** Terrain that is high enough to avoid obvious low-lying risk while still being practical to access and build on.

> **Implementation note:** Thresholds below are tuned for coastal Nova Scotia, where most land is under 200m ASL. The sweet spot is 30–100m — well above sea level and storm surge, but not remote highland. These differ from generic ranges used in early spec drafts.

| Elevation (m ASL) | Score |
|---|---|
| 30–100m | 100 |
| 100–200m | 90 |
| 20–30m | 70 |
| 200–300m | 60 |
| 10–20m | 40 |
| >300m | 30 |
| <10m | 10 |

**Note:** This is a coarse terrain preference, not a substitute for flood mapping. Flood and coastal constraints should be handled separately via exclusions/flags.

#### Access Confidence (20%)

**What we're measuring:** How likely it is that a candidate cell has practical access based on mapped roads and civic addressing.

**Important:** This is **not** a legal-access test. Road proximity cannot prove deeded frontage, easements, or title access.

| Evidence of access | Score |
|---|---|
| Public road intersects candidate cell or civic address exists in cell | 100 |
| Public road within 50m | 80 |
| Public road within 200m | 50 |
| Public road within 500m | 20 |
| >500m / no road evidence | 0 |

Any candidate cell scoring below 50 here should receive `access_unverified`.

#### Buildable / Open Area (5%)

**What we're measuring:** How much of the candidate-cell area is plausibly usable for siting structures, solar, gardens, or other off-grid infrastructure.

**Why low weight:** Like solar, buildable area is relatively uniform across rural NS (most cells have adequate buildable percentage). The criterion still contributes to reward notably open or constrained cells.

Mask inputs:
- Land cover / canopy
- Water polygons and mapped swamp/wet hydro classes used as conservative masking inputs
- Building footprints
- Slope threshold (≤20° preferred)

| % of candidate cell that is buildable or already open | Score |
|---|---|
| ≥30% | 100 |
| 20–30% | 80 |
| 10–20% | 60 |
| 5–10% | 30 |
| <5% | 0 |

**Note on forest overlap:** Both Solar Exposure (5%) and Buildable/Open Area (5%) penalize dense forest cover. This is intentional — forested land genuinely requires clearing cost for both solar and building. With the current weights, only 10% of the total score is sensitive to canopy, which avoids over-penalizing the heavily forested rural NS landscape.

### 2.3 Stage B Parcel-Aware Criteria (disabled in candidate-only MVP)

Stage B is only used after authoritative parcel data is available. Parcel scoring starts from the aggregated Stage A candidate scores within a parcel, then applies any enabled parcel-aware criteria.

#### Parcel Size (10% when enabled)

| Size (acres) | Score |
|---|---|
| ≥50 acres | 100 |
| 20–50 | 90 |
| 10–20 | 70 |
| 5–10 | 40 |
| 2–5 | 20 |
| 1–2 | 5 |

### 2.4 Optional Preference Criteria (disabled by default)

These are valid filters for a specific buyer, but they should not be baked into the default off-grid suitability score.

#### Reference Town Proximity (0% by default)

Example reference point: Lunenburg, NS (44.3764°N, 64.3103°W)

| Drive time | Score |
|---|---|
| <30 min | 100 |
| 30–60 min | 80 |
| 1–2 hours | 50 |
| 2–3 hours | 25 |
| >3 hours | 10 |

Use routing in later versions. Euclidean distance is acceptable only as a rough MVP proxy.

#### Water-Body Amenity (0% by default)

| Distance to nearest lake/river | Score |
|---|---|
| Bordering (0m) | 100 |
| <200m | 80 |
| 200m–1km | 50 |
| 1–3km | 25 |
| >3km | 0 |

Treat this as an amenity preference, not a pure utility signal. It can correlate with setbacks, wetlands, or flood exposure.

#### Crown Land Adjacency (0% by default)

| Crown land relationship | Score |
|---|---|
| Shares boundary | 100 |
| <500m | 60 |
| 500m–2km | 30 |
| >2km | 0 |

Treat this as an amenity/preference score only. It does not imply any right to build, harvest, or secure access over Crown land.

### 2.5 Composite Score

```
Candidate Score = Σ(stage_a_metric_i × enabled_weight_i) / Σ(enabled_stage_a_weight_i)

Parcel Score = Σ(all_enabled_metric_j × weight_j) / Σ(all_enabled_weight_j)
  where metrics include:
    - aggregate_candidate_score (treated as a single metric with its configured weight)
    - enabled parcel-aware metrics (e.g. parcel size)
    - enabled preference metrics (e.g. town proximity)
```

Weights are configurable. Disabled criteria are omitted and the remaining weights are renormalized automatically. When Stage B metrics are enabled, the aggregate candidate score is assigned a single weight (default: 80%) and competes with parcel-aware and preference weights in the same normalization pool.

**Parcel aggregation rule:** assign candidate cells to parcels using a single geometry rule: cell centroid within parcel. Parcel score = mean of the top 3 assigned eligible candidate-cell scores. If fewer than 3 assigned cells exist, use all assigned eligible cells. If no eligible candidate-cell centroids fall within a parcel, do not compute a parcel score and emit a parcel-level flag such as `parcel_no_assigned_candidates`.

**Confidence score v1:** start at `100` and subtract:
- `20` if flood/coastal exclusion data is unavailable for the study area
- `20` if hydro uses drainage proxy only
- `15` if hydro relies on 20m DEM where higher-resolution terrain is unavailable
- `10` if land-cover/building masks are incomplete
- `15` if there is no road or civic-address evidence within 200m

Clamp the result to `max(0, score)`.

Band the result as:
- `high` = `80-100`
- `medium` = `55-79`
- `low` = `0-54`

Do not zero out good candidates just because access or flood information is incomplete. Exclude only on explicit authoritative rules; otherwise emit an eligible record with lower confidence and clear flags.

## 3. Micro-Hydro Assessment Methodology

### 3.1 Head Estimation from DEM

1. Buffer each candidate cell by 100m to identify nearby stream reaches
2. Clip stream network (NSHN) to the buffered candidate cell to find candidate streams
3. Normalize elevation sources to a common horizontal and vertical datum before sampling
4. For each connected reach that intersects the buffer:
   - Trace the reach along the flow network (which may extend beyond the buffer) and generate candidate intake/outfall pairs 50m, 100m, 200m, and 500m apart along the same downstream path
   - At least one endpoint of each pair must fall within the buffered candidate cell
   - Sample DEM elevation at endpoints and intermediate vertices
   - Compute gross head = upstream elevation - downstream elevation
5. Reject infeasible candidates:
   - Gross head < 3m
   - Drainage area below minimum threshold
6. Best candidate-cell hydro score = the candidate with the highest **conservative low-flow power**, not the candidate with the highest raw head alone

### 3.2 Flow Estimation

**Challenge:** Direct flow measurements exist only at WSC gauging stations (~80 active in NS). Most small streams are ungauged.

**Long-term approach: regional low-flow regression model** (following Cyr et al., 2011 — NB methodology):
1. Download HYDAT database (all WSC station records for NS)
2. For each gauged station, extract: drainage area, mean annual flow, and low-flow statistics such as Q95 / 7Q10
3. Build regression: Q = f(drainage_area, precipitation, slope)
4. For ungauged streams: delineate drainage area upstream of each point using DEM flow accumulation
5. Apply regression to estimate Q at any point

**Simplified MVP approach:**
- Use flow accumulation raster (derived from DEM) as a proxy for relative stream size
- Use a **conservative low-flow proxy**, not mean annual runoff
- Start with specific runoff of ~8–12 L/s/km² for low-flow screening in southern NS, then calibrate against HYDAT by watershed
- `Q_design = drainage_area_km² × specific_runoff_low`

### 3.3 Reference Values for NS

- Mean annual precipitation: ~1,400mm
- Typical mean annual specific discharge: 15–25 L/s/km² (varies by watershed)
- Conservative low-flow screening proxy for MVP: 8–12 L/s/km²
- Minimum useful micro-hydro: 3m head × 10 L/s = ~150W (with 50% efficiency)
- Good micro-hydro: 10m head × 30 L/s = ~1.5kW
- Excellent: 20m+ head × 50+ L/s = ~5kW

## 4. Technical Architecture

### 4.1 Processing Stack

```
┌─────────────────────────────────────────┐
│              User Interface             │
│       CLI + static web map output       │
├─────────────────────────────────────────┤
│             Scoring Engine              │
│       Python modules + config file      │
├─────────────────────────────────────────┤
│         Geospatial Processing           │
│  DEM derivatives, masks, overlays, QA   │
├─────────────────────────────────────────┤
│              Local Storage              │
│  GeoTIFF rasters + GeoPackage vectors   │
│  Optional DuckDB/Parquet for analytics  │
└─────────────────────────────────────────┘
```

### 4.2 Core Libraries

| Library | Purpose |
|---|---|
| **GeoPandas + Pyogrio** | Vector data manipulation and fast I/O |
| **Rasterio** | DEM/raster reading and processing |
| **WhiteboxTools** | Flow accumulation, watershed delineation, terrain analysis |
| **Shapely** | Geometry operations (buffers, intersections, distances) |
| **GDAL/OGR** | Format conversion, reprojection, datum handling |
| **rasterstats** | Zonal statistics for parcels and candidate cells |
| **Folium** or **Leaflet** | Web map visualization |
| **DuckDB** | Optional analytical queries and export workflows |

### 4.3 Google Earth Engine Note

`NRCan/CDEM` exists in GEE, but GEE is out of scope for MVP. Use it later only if local raster processing becomes the clear bottleneck.

### 4.4 Pipeline Steps

```
1. DATA PREP (one-time)
   ├── Download all datasets (see DATA-SOURCES.md)
   ├── Normalize horizontal / vertical datums
   ├── Reproject working layers to EPSG:2961 (NAD83(CSRS) UTM Zone 20N)
   ├── Clip to study area
   ├── Generate derived rasters:
   │   ├── Slope raster from DEM
   │   ├── Aspect raster from DEM
   │   ├── Flow accumulation raster from DEM
   │   └── Flow direction raster from DEM
   ├── Build coarse rural-eligibility mask
   ├── Build fixed 250m square candidate grid
   ├── Filter candidate cells by the rural-eligibility mask
   ├── Build land-cover / open-area / buildability mask
   └── Build spatial index on all vector layers

2. STAGE A: SITE / CELL SCORING
   For each candidate cell:
   ├── Extract terrain stats from DEM
   ├── Score solar-suitable open area
   ├── Find nearby stream reaches
   ├── Calculate conservative hydro potential
   ├── Calculate access confidence from roads / civic data
   ├── Apply exclusions and attach flags
   ├── Compute confidence score + band
   └── Compute normalized base score

3. STAGE B: PARCEL JOIN (when parcels are available)
   For each parcel:
   ├── Aggregate assigned candidate-cell scores within parcel
   ├── Add parcel area metric
   ├── Add optional preference metrics
   ├── Recompute normalized parcel score
   └── Preserve flags + confidence_score/confidence_band from underlying candidates

4. OUTPUT
   ├── Ranked CSV/GeoJSON of eligible candidate cells or parcels
   ├── Excluded-record export or clearly labeled excluded view
   ├── status + exclusion_reasons for every record
   ├── confidence_score + confidence_band + flags for every record
   ├── Interactive web map (Folium/Leaflet)
   └── Per-parcel detail report
```

### 4.5 Performance Considerations

- NS has ~400,000 property parcels. Processing all would take hours.
- **Strategy:** compute a candidate-cell suitability surface first, then join/aggregate to parcels only where needed
- Pre-filter to rural/resource areas using a coarse eligibility mask only. Do not pre-filter by buildability score.
- DEM zonal statistics can be vectorized with `rasterstats`
- Default candidate resolution: `250m` square cells. Only reduce cell size after measuring runtime and output quality.
- Target: process a 100km-radius study area in <30 minutes on a laptop; full-province processing should be treated as a batch job

## 5. MVP Approach

### Phase 1: Proof of Concept (1–2 weekends)

**Goal:** Produce a ranked candidate-cell suitability surface near Lunenburg and validate that the top results make intuitive sense before parcel integration.

**Scope:**
- Download NS Enhanced DEM (20m) for the study area
- Download NSHN stream network
- Download land-cover/open-area data
- Download road network and, if available, civic-address data
- Download protected-area and flood/coastal-risk data if accessible for the study area
- Generate a fixed `250m × 250m` candidate grid over the study area, then keep cells passing the coarse rural-eligibility mask
- Compute the **base suitability score**, `status`, `exclusion_reasons`, `confidence_score`, `confidence_band`, and `flags`
- Output: ranked candidate cells + basic Folium map

**Skip for MVP:**
- Property listing integration (MLS/Realtor.ca)
- Formal HYDAT-based regression model (use conservative drainage proxy)
- Web UI beyond basic map
- Legal-access determination from title/frontage documents
- Province-wide parcel pipeline

**Tech:** Small Python package or script collection. Input: downloaded GIS files. Output: scored GeoJSON/CSV + HTML map.

### Phase 2: Full Pipeline (2–4 weeks)

- Integrate NS parcel data (requires NSGI account or Regrid data)
- Add authoritative flood/protected/civic-address layers where available
- Build proper HYDAT low-flow regression from station data
- Add parcel join and parcel-level reporting
- Add manual or licensed listing cross-reference workflow
- Interactive web dashboard
- Configurable weights
- Export to Google Sheets

### Phase 3: Productization (future)

- Web app with search/filter
- Alert system for new listings matching criteria
- Extend to other provinces
- Better canopy/shading analysis from LiDAR or satellite data

## 6. Data Source Inventory

See [DATA-SOURCES.md](./DATA-SOURCES.md) for the complete inventory with URLs, formats, and access methods.

**Summary of key sources:**

| Dataset | Source | Format | Cost | Availability |
|---|---|---|---|---|
| DEM (20m) | NS Dept. Natural Resources | GeoTIFF | Free | ✅ Direct download |
| DEM (CDEM ~23m) | NRCan FTP / Open Data | GeoTIFF | Free | ✅ Direct download |
| HRDEM (1-2m LiDAR) | NRCan | GeoTIFF | Free | ✅ Partial NS coverage |
| Stream network (NSHN) | NS Open Data | Shapefile | Free | ✅ Direct download |
| Land cover | NSTDB / NSGI | Shapefile | Free | ✅ Direct download |
| Solar insolation | NRCan | GDB/CSV | Free | ✅ FTP download |
| Property parcels | NSGI/GeoNova | Varies | Licensed¹ | ⚠️ Requires account |
| Road network | OSM or NS road network | PBF/SHP | Free | ✅ Download |
| Civic addressing | GeoNova | Varies | Free/account mix | ⚠️ Useful for access confidence |
| Water / wet polygons | NSHN / NHN | Shapefile | Free | ✅ Direct download |
| Building footprints | Open Government Canada / Microsoft | GeoJSON/SHP/GPKG | Free | ✅ Direct download |
| Protected areas | GeoNova / NS Open Data | Varies | Free | ✅ Available |
| Flood/coastal risk | GeoNova / other NS sources | Varies | Mixed | ⚠️ Coverage varies |
| Stream gauge data | WSC/HYDAT | SQLite | Free | ✅ Download |
| Active listings | Licensed feeds / manual review | Varies | Varies² | ⚠️ Complex |

¹ NS property parcel data is the most constrained data source. NSGI requires a free account for basic data, but detailed parcel boundaries may be licensed.
² CREA DDF API requires being a licensed REALTOR or approved third-party. Scraping Realtor.ca is technically possible but against TOS.

## 7. Legal & Regulatory Considerations

### 7.1 Micro-Hydro Permits in Nova Scotia

**Water withdrawal and watercourse alteration require case-by-case review.** Under Nova Scotia's surface-water and environmental approval framework:
- Surface-water withdrawals above 23,000 L/day trigger formal approval thresholds
- Any diversion, storage, intake, penstock, or in-stream works associated with micro-hydro may also trigger review even if consumptive use is low
- Run-of-river design reduces impact, but it should not be treated as an exemption
- Contact NSECC for current guidance and pre-consultation: https://novascotia.ca/nse/water/withdrawalApproval.asp

**Key regulations:**
- Activities Designation Regulations (watercourse alteration)
- Environment Act, Part V (water resources)
- Water Resources Protection Act

**Practical note:** This tool is for screening only. Any serious micro-hydro candidate needs project-specific regulatory review and likely a hydrological assessment.

### 7.2 Solar

- No specific provincial permits for residential solar in NS
- Municipal and utility requirements still matter
- Building permits and electrical/interconnection requirements vary by municipality and installation type
- Ground-mounted systems may still trigger local setback or permitting rules
- Net-metering and self-generation program details can change over time, so treat them as operational guidance rather than scoring inputs

### 7.3 Crown Land Adjacency

- Adjacent Crown land cannot be annexed or exclusively used
- Adjacency does not create access rights, development rights, or harvesting rights
- No structures, land clearing, road building, or exclusive use on Crown land without the appropriate licence or authorization
- Do not assume firewood cutting or timber use is allowed without specific provincial permission
- Crown Lands Act governs all use

## 8. Limitations & Data Gaps

### 8.1 Known Gaps

1. **Property parcel boundaries** — Still the biggest delivery risk. NS parcel data through NSGI requires registration and may be restricted for bulk download. If bulk parcel access is blocked, the fallback is a parcel-independent suitability surface or candidate-cell workflow, not OSM pseudo-parcels.

2. **Legal access / frontage** — Road proximity is only a heuristic. GIS road layers do not prove deeded frontage, easements, or legal right-of-way. High-scoring parcels may still fail title review.

3. **Stream flow data** — Direct measurements exist for only a limited number of gauged watersheds in NS. Flow estimates for small ungauged streams are modeled approximations, not measured values. Error margin may easily exceed ±50%.

4. **Tree canopy / forest cover** — Land cover helps, but it still does not fully capture shading, mature canopy density, or clearing cost. LiDAR and imagery would improve this materially.

5. **Flood/coastal-risk coverage** — Flood mapping is not equally available or equally current across all of Nova Scotia. Some areas will need a lower-confidence coastal-risk flag instead of an authoritative exclusion.

6. **Seasonal variation** — Stream flow varies dramatically. Micro-hydro scoring should use conservative low-flow estimates wherever possible, not mean annual flow.

7. **Soil/geology / well / septic** — Not scored in the MVP, but they materially affect build cost and livability.

8. **Internet/cell coverage** — Critical for off-grid livability but not included. Could add from CRTC coverage maps.

9. **Wetland mapping specificity** — NSHN/NHN hydro polygons are useful conservative masks, but they are not a substitute for a dedicated province-wide authoritative wetland-development constraint layer.

### 8.2 Accuracy Disclaimers

- DEM-derived hydro potential is a screening tool, NOT a site assessment
- Any serious micro-hydro installation requires on-site flow measurement over multiple seasons
- Solar scoring is still a terrain-and-cover proxy; it does not fully account for local shading
- Road proximity does not establish legal access
- Parcel boundaries from GIS data may not match legal survey boundaries
- Scores are relative rankings, not absolute suitability ratings
- Confidence matters: a lower-scoring high-confidence parcel may be a better real-world lead than a higher-scoring low-confidence parcel

## 9. Competitive Landscape

**Nothing exactly like this exists.** The closest tools are:

| Tool | What it does | Gap |
|---|---|---|
| **PVCase Prospect** | Solar/wind site selection for utility-scale | Commercial, not residential off-grid |
| **Transect** | Renewable energy siting + environmental risk | US-focused, enterprise pricing |
| **LandWatch.com** | Filters land listings by features | No geospatial scoring, no hydro analysis |
| **Realtor.ca** | Property search with basic filters | No off-grid scoring, no geospatial analysis |
| **Regrid** | Parcel data + overlays | Raw data, no off-grid scoring |
| **Google Project Sunroof** | Rooftop solar potential | Rooftops only, US-focused |
| **RETScreen** (NRCan) | Renewable energy project analysis | Single-site tool, not bulk screening |

**The unique value:** Combining micro-hydro potential with solar, terrain, and access screening into a single spatial ranking. The MVP does this at candidate-cell level first, then joins the results back to properties when parcel data exists. The Cyr et al. (2011) methodology for NB small hydro assessment is the closest academic work, but it's not a user-facing screening tool and doesn't cover parcel matching.

**Market opportunity:** Off-grid/homesteading is a growing movement. A tool that answers "which areas or properties in Nova Scotia have the best micro-hydro + solar potential?" would be genuinely novel and useful.

## 10. Estimated Development Time

| Phase | Effort | Calendar Time |
|---|---|---|
| MVP (proof of concept, best case) | 30–45 hours | 2–3 weekends |
| Data acquisition + prep | 8–15 hours | 1 week |
| Full parcel-integrated pipeline | 40–60 hours | 3–5 weeks |
| Web visualization | 15–20 hours | 1 week |
| Listing integration | 10–15 hours | 1 week |
| **Total to usable parcel-aware tool** | **100–155 hours** | **5–8 weeks** |

MVP can deliver a ranked candidate-cell suitability map in a short sprint, but the schedule above is the more realistic planning baseline. Parcel-aware output still depends on parcel-data access.

## 11. Implementation Plan & Risk Management

### 11.1 Implementation Phases (Detailed)

**Phase 0: Data Validation Sprint (Day 1–2)**
*Goal: Confirm all critical data sources actually work before writing any scoring code. Manual validation tasks below take ~4-5 hours; the full Epic B scope (including scripting and automation in IMPLEMENTATION-BACKLOG.md) is ~7-14 hours.*

| Task | Time | De-risks |
|---|---|---|
| Download NS Enhanced DEM (20m) from novascotia.ca | 30 min | Confirm it's still available, not paywalled |
| Download NSHN stream network shapefile | 15 min | Confirm format, schema, completeness |
| Download land-cover, building-footprint, and protected-area layers | 30 min | Confirm developability filters are available |
| Check flood/coastal-risk data availability for study area | 30 min | Confirm whether flood exclusion can be authoritative or only flagged |
| Register NSGI DataLocator account, attempt parcel download | 1 hour | **Critical gate** — if parcels unavailable, Stage B is delayed |
| Load all datasets in QGIS, visually verify alignment | 30 min | Catch projection mismatches early |
| Check vertical datum metadata for DEM / LiDAR layers | 15 min | Avoid bogus head calculations from datum mismatch |
| Test: extract elevation profile along one known stream | 30 min | Prove the DEM→hydro head pipeline works |
| Test: compute aspect/slope/open-area score for one known hillside | 30 min | Prove solar scoring pipeline works |

**Go/no-go decision after Phase 0.** If parcel data is blocked, either:
- A) Continue with parcel-independent candidate-cell scoring as MVP
- B) Use Regrid API later for parcel integration
- C) Limit parcel validation to manual lookups in Property Online / PVSC / ViewPoint for top-ranked results

**Phase 1: MVP Scoring (Sprint 1, ~20-25 hours)**
1. Build data prep script — download, reproject, clip to study area
2. Build coarse rural-eligibility mask and fixed 250m candidate grid
3. Build hydro module — stream extraction, candidate head calculation, conservative flow proxy
4. Build solar/open-area module — aspect/slope raster + land-cover mask
5. Build scoring engine — base criteria + status + exclusion reasons + confidence score/band + flags
6. Basic Folium map and CSV/GeoJSON output
7. Test on candidate cells near Lunenburg

**Phase 1.5: Validation (Week 2, ~5 hours)**
1. Graham visits or reviews top-5 scored candidate areas — do they look right?
2. Check bottom-5 — are they correctly scored low?
3. Spot-check 10 random mid-range candidate cells
4. Adjust weights/thresholds based on ground truth
5. If parcel data is available, cross-reference top areas to actual parcels and active listings manually

**Phase 2: Full Pipeline (Weeks 3-4, ~30 hours)**
1. Integrate authoritative parcel data and aggregate candidate-cell scores to parcels
2. Build proper HYDAT low-flow regression model
3. Add flood/protected/civic-address constraints where available
4. Add manual or licensed listing cross-reference
5. Interactive web dashboard with search/filter
6. Property detail pages with per-criterion breakdown and confidence score/band

**Phase 3: Productization (Month 2+)**
1. Listing alerts (new properties matching criteria)
2. Expand to New Brunswick, PEI
3. Tree canopy / forest cover overlay
4. Soil/well/septic feasibility layer
5. Consider SaaS model

### 11.2 Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **NS parcel data access blocked** — NSGI restricts bulk download | Medium | CRITICAL | Phase 0 de-risk. Fallback: parcel-independent scoring now, Regrid/manual parcel lookup later |
| R2 | **Flow estimates wildly inaccurate** — ±50% error on ungauged streams | High | Medium | Use conservative estimates, flag as "screening only", validate at gauged sites |
| R3 | **DEM resolution insufficient** — 20m misses small streams/features | Medium | Medium | HRDEM LiDAR (1-2m) available for southern NS. Use for drill-down on top candidates |
| R4 | **Forest cover masks solar** — most rural NS is heavily forested | High | Medium | NSTDB land cover is included in MVP masking. Limitation: canopy density and shading detail still require LiDAR (Phase 2+) |
| R5 | **Seasonal flow variation ignored** — mean annual flow ≠ winter flow | High | Low-Med | Use Q95 or 7Q10 low-flow stats from HYDAT where available. Document assumption. |
| R6 | **Legal complexity of micro-hydro** — permits may be onerous | Medium | Low | Informational only — tool is for screening, not permitting. Add disclaimer. |
| R7 | **Performance at scale** — 400K parcels × raster extraction = slow | Medium | Medium | Score surface first, then aggregate to parcels. Pre-filter rural areas. |
| R8 | **Road proximity misread as legal access** | High | High | Keep as confidence/flag only. Manual title/frontage review for finalists. |
| R9 | **Data goes stale** — NS Open Data URLs change or datasets updated | Low | Low | Pin download dates, version data files, periodic re-download script |
| R10 | **Flood mapping incomplete** — exclusions unavailable in some areas | Medium | Medium | Use authoritative layers where available, coastal low-elevation flags elsewhere |

### 11.3 Early De-risking Priority

**Do these FIRST before any code beyond Phase 0:**

1. **🔴 Parcel data access** (R1) — Register NSGI account, attempt bulk parcel download. This is the single biggest unknown. If blocked, the entire project architecture changes.
2. **🟡 DEM→hydro head proof** — Take one known micro-hydro site in NS, compute head from DEM, compare to reality. If the 20m DEM can't resolve the stream valley, we need HRDEM.
3. **🟡 Flow accumulation validation** — Run WhiteboxTools flow accumulation on a small DEM tile. Compare stream network output to NSHN. If they diverge significantly, the drainage area estimates will be unreliable.
4. **🟢 Flood / protected data availability** — Confirm we can apply real exclusions in the study area rather than speculative heuristics.
5. **🟢 Solar aspect sanity check** — Pick a known south-facing hillside, compute aspect from DEM, confirm it reads 160-200°.

### 11.4 Key Assumptions

1. Run-of-river micro-hydro (no dam/reservoir) is the target use case
2. Properties are for residential off-grid living, not commercial power generation
3. Southern Nova Scotia (within ~150km of Lunenburg) is the primary study area
4. Users will verify top candidates with site visits before purchasing
5. Tool is a screening/ranking system, not a definitive site assessment
6. All scores are relative (property A vs property B), not absolute guarantees
7. Access rights, permitting, and title review happen outside the model

### 11.5 Success Criteria

**MVP is successful if:**
- [ ] Top-10 scored candidate areas include at least 3 that Graham would personally investigate further
- [ ] No obviously unsuitable areas in the top-20 (e.g., urban cores, tiny constrained sites, protected lands)
- [ ] Hydro scores correlate with known steep/stream-rich terrain and, where possible, known hydro sites
- [ ] Solar/open-area scores correlate with south-facing or already-open terrain visible on imagery
- [ ] Results clearly expose `status`, `exclusion_reasons`, `confidence_score`, `confidence_band`, and `flags`
- [ ] Total processing time <30 minutes for study area

## 12. File Structure

See [IMPLEMENTATION-BACKLOG.md](./IMPLEMENTATION-BACKLOG.md) for the execution plan, task breakdown, and milestone order.

```
~/Coding/property-finder/
├── SPEC.md                    # This file
├── DATA-SOURCES.md            # Detailed data source reference
├── IMPLEMENTATION-BACKLOG.md  # Concrete build backlog
├── README.md                  # Quick start and usage guide
├── pyproject.toml             # Python project metadata + dependencies
├── config.yaml                # Weights, study area, thresholds
├── .python-version            # Python 3.12
├── data/
│   ├── raw/                   # Downloaded GIS data (not in git)
│   │   ├── dem/               # CDEM / NS Enhanced DEM rasters
│   │   ├── hrdem/             # High-resolution LiDAR DEMs
│   │   ├── hydro/             # NSHN stream network (File Geodatabase)
│   │   ├── hydat/             # HYDAT station data (SQLite)
│   │   ├── land-cover/        # NSTDB land cover polygons
│   │   ├── buildings/         # NRCan building footprints
│   │   ├── exclusions/        # Protected areas, flood zones
│   │   ├── crown-land/        # Crown land parcels
│   │   ├── parcels/           # Property parcels (NSGI, account required)
│   │   ├── roads/             # OSM road network (PBF)
│   │   └── civic/             # Civic address points (NSGI)
│   └── processed/             # Derived rasters and clipped vectors
│       ├── dem.tif            # Reprojected, clipped DEM
│       ├── slope.tif          # Slope derivative
│       ├── aspect.tif         # Aspect derivative
│       ├── flow_accumulation.tif # Flow accumulation (WhiteboxTools D8)
│       ├── candidate_grid.gpkg   # 250m grid filtered by rural mask
│       ├── streams.gpkg       # Ingested hydro network
│       ├── roads.gpkg         # Ingested road network
│       ├── buildings.gpkg     # Ingested building footprints
│       ├── land_cover.gpkg    # Ingested land cover
│       ├── parcels.gpkg       # Ingested parcels (when available)
│       ├── civic_addresses.gpkg  # Ingested civic addresses
│       ├── protected_areas.gpkg  # Clipped protected areas
│       └── flood.gpkg         # Clipped flood zones
├── src/
│   ├── __init__.py
│   ├── __main__.py            # python -m src entrypoint
│   ├── cli.py                 # Click CLI (check-data, ingest, prepare, score, visualize, analyze)
│   ├── config.py              # YAML config loading and validation
│   ├── constants.py           # Threshold tables, weights, flags
│   ├── logging_config.py      # Logging setup
│   ├── ingest.py              # Raw data ingestion (format conversion)
│   ├── prepare.py             # Data preparation orchestrator
│   ├── score.py               # Scoring pipeline orchestrator
│   ├── export.py              # CSV/GeoJSON export
│   ├── visualize.py           # Folium map generation
│   ├── grid.py                # 250m candidate grid generation
│   ├── dem.py                 # DEM derivatives (slope, aspect, flow accumulation)
│   ├── mask.py                # Rural-eligibility and buildability masks
│   ├── exclusions.py          # Exclusion layer loading and application
│   ├── clip.py                # Raster/vector clipping utilities
│   ├── crs.py                 # CRS utilities
│   ├── check_data.py          # Data validation
│   └── scoring/               # Pluggable scoring module
│       ├── __init__.py
│       ├── registry.py        # Scorer registration + weighted composite
│       ├── hydro.py           # Micro-hydro power estimation
│       ├── solar.py           # Solar suitability scoring
│       ├── elevation.py       # Elevation scoring
│       ├── access.py          # Road/civic address proximity
│       ├── buildable.py       # Buildable area percentage
│       ├── confidence.py      # Confidence scoring and banding
│       └── preferences.py     # Parcel aggregation (Stage B)
├── tests/                     # 92 tests across 15 test files
│   ├── conftest.py            # Synthetic test fixtures
│   ├── test_config.py
│   ├── test_cli.py
│   ├── test_grid.py
│   ├── test_exclusions.py
│   ├── test_export.py
│   ├── test_analyze.py        # Score distribution analysis tests
│   ├── test_scoring_composite.py
│   ├── test_scoring_confidence.py
│   ├── test_scoring_hydro.py
│   ├── test_scoring_elevation.py
│   ├── test_scoring_access.py
│   ├── test_scoring_buildable.py
│   ├── test_scoring_solar.py
│   ├── test_scoring_preferences.py
│   └── test_integration.py    # End-to-end pipeline test
└── output/                    # Final results (not in git)
    ├── scored_cells.gpkg      # Full results with geometry
    ├── scored_cells.csv       # All cells with lat/lon, scores, confidence
    ├── scored_cells.geojson   # GeoJSON in WGS84
    ├── ranked_eligible.csv    # Eligible cells sorted by rank
    ├── scored_parcels.gpkg    # (Stage B) Parcel-level results
    └── map.html               # Interactive Folium map
```

## 13. References

- Cyr, J.-F., Landry, M., & Gagnon, Y. (2011). Methodology for the large-scale assessment of small hydroelectric potential: Application to the Province of New Brunswick (Canada). *Renewable Energy*, 36(11), 2940–2950. https://doi.org/10.1016/j.renene.2011.04.003
