# Off-Grid Property Finder — Specification

> Score Nova Scotia properties for off-grid capability and self-sufficiency potential.
> "WaveScout but for land."

## 1. Project Overview

A geospatial analysis tool that evaluates Nova Scotia properties on their suitability for off-grid living, focusing on micro-hydro electricity potential, solar exposure, elevation, water access, and proximity to key locations. The tool ingests open GIS data, overlays it with property parcels, and produces a scored ranking of properties.

## 2. Scoring Methodology

### 2.1 Primary Criteria (70% total weight)

#### Micro-Hydro Potential (30%)

**What we're measuring:** The likelihood that a property can generate meaningful micro-hydro electricity.

**Core formula:**
```
Power (W) = Q (L/s) × H (m) × g (9.81) × η (0.5–0.7)
```
Where Q = flow rate, H = head (elevation drop), η = system efficiency.

**Scoring approach:**
1. Identify stream segments within/adjacent to each parcel using NSHN hydro network
2. Extract elevation values along each stream segment from DEM data
3. Calculate maximum available head over reasonable penstock lengths (50–500m along stream)
4. Estimate flow using drainage area regression (see §3.3)
5. Compute theoretical power output

| Power Estimate | Score |
|---|---|
| ≥2 kW continuous | 100 |
| 1–2 kW | 80 |
| 500W–1 kW | 60 |
| 200–500W | 40 |
| 50–200W | 20 |
| No stream / <50W | 0 |

**Minimum viable thresholds:** 3m head + 10 L/s ≈ 200W continuous. This powers lights, fridge, and basic electronics.

#### Solar Exposure (25%)

**What we're measuring:** South-facing slope area suitable for solar panels.

**Scoring approach:**
1. Derive aspect and slope rasters from DEM
2. Calculate the percentage of each parcel with "good solar" characteristics:
   - Aspect: 135°–225° (SE to SW facing) = optimal; 90°–270° = acceptable
   - Slope: 5°–35° preferred (matches panel tilt for ~44°N latitude)
   - Flat land (slope <5°) is also good (panels can be tilted manually)
3. Exclude heavily forested areas if land cover data available
4. Cross-reference with NRCan solar insolation data for regional baseline

| % of parcel with good solar | Score |
|---|---|
| ≥40% south-facing suitable | 100 |
| 25–40% | 80 |
| 15–25% | 60 |
| 5–15% | 40 |
| <5% | 20 |

**Bonus:** Multiply by regional solar insolation factor (NS average: ~3.5 kWh/m²/day annual mean).

#### Elevation (15%)

**What we're measuring:** Property elevation for flood protection, views, and general site quality.

| Elevation (m ASL) | Score |
|---|---|
| 100–300m | 100 |
| 50–100m | 80 |
| 300–500m | 60 |
| 20–50m | 40 |
| <20m | 20 |

**Note:** NS doesn't have extreme elevations (max ~535m at Cape Breton Highlands). The sweet spot is 50–200m — high enough for flood protection and views, low enough for accessibility.

### 2.2 Secondary Criteria (30% total weight)

#### Proximity to Lunenburg (8%)

**Reference point:** Lunenburg, NS (44.3764°N, 64.3103°W)

| Drive time | Score |
|---|---|
| <30 min | 100 |
| 30–60 min | 80 |
| 1–2 hours | 50 |
| 2–3 hours | 25 |
| >3 hours | 10 |

**Implementation:** Euclidean distance as proxy initially (15km ≈ 15 min rural driving). Upgrade to OSRM routing for v2.

#### Lake/Water Body Access (6%)

| Distance to nearest lake/river | Score |
|---|---|
| Bordering (0m) | 100 |
| <200m | 80 |
| 200m–1km | 50 |
| 1–3km | 25 |
| >3km | 0 |

#### Crown Land Adjacency (6%)

| Crown land relationship | Score |
|---|---|
| Shares boundary | 100 |
| <500m | 60 |
| 500m–2km | 30 |
| >2km | 0 |

#### Road Access (5%)

**Binary gate + bonus:**
- No road within 500m → score = 0, AND flag as "potentially landlocked" (disqualify)
- Road touches parcel → 100
- Road within 100m → 80
- Road within 500m → 50

#### Parcel Size (5%)

| Size (acres) | Score |
|---|---|
| ≥50 acres | 100 |
| 20–50 | 90 |
| 10–20 | 70 |
| 5–10 | 40 |
| 2–5 | 20 |
| <2 | 5 |

### 2.3 Composite Score

```
Score = (hydro × 0.30) + (solar × 0.25) + (elevation × 0.15)
      + (proximity × 0.08) + (water_access × 0.06)
      + (crown_land × 0.06) + (road × 0.05) + (parcel_size × 0.05)
```

Weights are configurable. User can adjust via CLI flags or config file.

**Disqualifiers (score → 0 regardless):**
- No road access within 500m
- Parcel size < 1 acre
- In a flood zone / below 5m elevation near coast

## 3. Micro-Hydro Assessment Methodology

### 3.1 Head Estimation from DEM

1. Buffer each parcel boundary by 100m to catch adjacent streams
2. Clip stream network (NSHN) to buffered parcel
3. For each stream segment within/near parcel:
   - Sample DEM elevation at 10m intervals along the stream
   - Calculate maximum elevation difference over distances of 50m, 100m, 200m, 500m
   - This gives "available head" for various penstock lengths
4. Best head value per parcel = max across all nearby stream segments

### 3.2 Flow Estimation

**Challenge:** Direct flow measurements exist only at WSC gauging stations (~80 active in NS). Most small streams are ungauged.

**Approach: Regional regression model** (following Cyr et al., 2011 — NB methodology):
1. Download HYDAT database (all WSC station records for NS)
2. For each gauged station, extract: drainage area, mean annual flow, low-flow statistics
3. Build regression: Q = f(drainage_area, precipitation, slope)
4. For ungauged streams: delineate drainage area upstream of each point using DEM flow accumulation
5. Apply regression to estimate Q at any point

**Simplified MVP approach:**
- Use flow accumulation raster (derived from DEM) as a proxy for relative stream size
- Assume specific runoff of ~15–20 L/s/km² for NS (typical Maritime climate value)
- Q_estimate = drainage_area_km² × specific_runoff

### 3.3 Reference Values for NS

- Mean annual precipitation: ~1,400mm
- Typical specific discharge: 15–25 L/s/km² (varies by watershed)
- Minimum useful micro-hydro: 3m head × 10 L/s = ~150W (with 50% efficiency)
- Good micro-hydro: 10m head × 30 L/s = ~1.5kW
- Excellent: 20m+ head × 50+ L/s = ~5kW+

## 4. Technical Architecture

### 4.1 Processing Stack

```
┌─────────────────────────────────────────┐
│              User Interface              │
│  (CLI + Web Map Visualization)           │
├─────────────────────────────────────────┤
│           Scoring Engine                 │
│  Python: GeoPandas + Rasterio + SciPy   │
├─────────────────────────────────────────┤
│         Geospatial Processing            │
│  DEM Analysis (slope, aspect, flow accum)│
│  Vector Overlays (parcels × features)    │
│  Distance Calculations                   │
├─────────────────────────────────────────┤
│            Data Layer                    │
│  GeoPackage / PostGIS                    │
│  Raster: GeoTIFF (DEM, aspect, slope)   │
│  Vector: Parcels, Streams, Crown Land    │
└─────────────────────────────────────────┘
```

### 4.2 Core Libraries

| Library | Purpose |
|---|---|
| **GeoPandas** | Vector data manipulation (parcels, streams, crown land) |
| **Rasterio** | DEM/raster reading and processing |
| **RichDEM** or **WhiteboxTools** | Flow accumulation, watershed delineation |
| **Shapely** | Geometry operations (buffers, intersections, distances) |
| **GDAL/OGR** | Format conversion, reprojection |
| **Folium** or **Leaflet** | Web map visualization |
| **DuckDB** or **SQLite+SpatiaLite** | Tabular storage and queries |

### 4.3 Google Earth Engine Option

CDEM is already available in GEE (`NRCan/CDEM`). This enables:
- Server-side DEM processing (slope, aspect, flow accumulation)
- No need to download/store large rasters locally
- Easy visualization

**BUT:** Property parcels, NSHN, Crown Land are NOT in GEE. Would need to upload or do a hybrid approach:
- GEE for raster analysis → export results as GeoTIFF
- Local Python for vector overlay scoring

**Recommendation:** Start local (Python + Rasterio). Consider GEE for v2 if performance is an issue.

### 4.4 Pipeline Steps

```
1. DATA PREP (one-time)
   ├── Download all datasets (see DATA-SOURCES.md)
   ├── Reproject everything to EPSG:2961 (NAD83 UTM Zone 20N)
   ├── Clip to study area (e.g., 100km radius from Lunenburg)
   ├── Generate derived rasters:
   │   ├── Slope raster from DEM
   │   ├── Aspect raster from DEM
   │   ├── Flow accumulation raster from DEM
   │   └── Flow direction raster from DEM
   └── Build spatial index on all vector layers

2. PARCEL SCORING (per-parcel)
   For each property parcel:
   ├── Extract zonal stats from DEM (min, max, mean elevation)
   ├── Extract zonal stats from aspect raster (% south-facing)
   ├── Extract zonal stats from slope raster
   ├── Find streams within/near parcel (spatial join)
   ├── Calculate hydro head from stream elevations
   ├── Estimate flow from drainage area
   ├── Calculate distance to nearest road
   ├── Calculate distance to nearest water body
   ├── Calculate distance to nearest Crown land
   ├── Calculate distance to Lunenburg
   ├── Get parcel area
   └── Compute composite score

3. OUTPUT
   ├── Ranked CSV/GeoJSON of all parcels with scores
   ├── Interactive web map (Folium/Leaflet)
   └── Per-parcel detail report
```

### 4.5 Performance Considerations

- NS has ~400,000 property parcels. Processing all would take hours.
- **Strategy:** Pre-filter to rural/resource parcels only (exclude urban, commercial, <1 acre)
- DEM zonal statistics can be vectorized with `rasterstats` library
- Target: process study area in <30 minutes on a MacBook Air

## 5. MVP Approach

### Phase 1: Proof of Concept (1–2 weekends)

**Goal:** Score 100 known rural properties near Lunenburg and validate the scoring makes intuitive sense.

**Scope:**
- Download CDEM (20m resolution) for Lunenburg County
- Download NSHN stream network
- Download Crown Land polygons
- Use NS road network from OpenStreetMap
- Manually select ~100 large rural parcels (or use OSM land parcels as proxy if official data unavailable)
- Compute all 8 scores
- Output: ranked list + basic Folium map

**Skip for MVP:**
- Property listing integration (MLS/Realtor.ca)
- Actual flow regression model (use drainage area proxy)
- Web UI beyond basic map
- Automated parcel data pipeline

**Tech:** Single Python script, ~500 lines. Input: downloaded GIS files. Output: scored GeoJSON + HTML map.

### Phase 2: Full Pipeline (2–4 weeks)

- Integrate NS parcel data (requires NSGI account or Regrid data)
- Build proper flow regression from HYDAT data
- Add realtor.ca scraping for active listings
- Interactive web dashboard
- Configurable weights
- Export to Google Sheets

### Phase 3: Productization (future)

- Web app with search/filter
- Alert system for new listings matching criteria
- Extend to other provinces
- Drone/satellite imagery analysis for tree cover

## 6. Data Source Inventory

See [DATA-SOURCES.md](./DATA-SOURCES.md) for the complete inventory with URLs, formats, and access methods.

**Summary of key sources:**

| Dataset | Source | Format | Cost | Availability |
|---|---|---|---|---|
| DEM (20m) | NS Dept. Natural Resources | GeoTIFF | Free | ✅ Direct download |
| DEM (CDEM ~23m) | NRCan via GEE | GeoTIFF/EE | Free | ✅ GEE or FTP |
| HRDEM (1-2m LiDAR) | NRCan | GeoTIFF | Free | ✅ Partial NS coverage |
| Stream network (NSHN) | NS Open Data | Shapefile | Free | ✅ Direct download |
| Crown Land | NS Open Data | Shapefile | Free | ✅ Direct download |
| Solar insolation | NRCan | GDB/CSV | Free | ✅ FTP download |
| Property parcels | NSGI/GeoNova | Varies | Licensed¹ | ⚠️ Requires account |
| Road network | OpenStreetMap | PBF/SHP | Free | ✅ Download |
| Water body polygons | NSHN/NHN | Shapefile | Free | ✅ Direct download |
| Stream gauge data | WSC/HYDAT | SQLite | Free | ✅ Download |
| Active listings | Realtor.ca | Scrape/API | Varies² | ⚠️ Complex |

¹ NS property parcel data is the most constrained data source. NSGI requires a free account for basic data, but detailed parcel boundaries may be licensed.
² CREA DDF API requires being a licensed REALTOR or approved third-party. Scraping Realtor.ca is technically possible but against TOS.

## 7. Legal & Regulatory Considerations

### 7.1 Micro-Hydro Permits in Nova Scotia

**Water withdrawal approval required.** Under the NS Environment Act and Water Resources Protection Act:
- Any withdrawal, storage, or diversion of water from a watercourse requires approval from Nova Scotia Environment and Climate Change (NSECC)
- Threshold: withdrawals >23,000 L/day from surface water
- Micro-hydro that returns water downstream (run-of-river) may have lighter requirements
- Contact NSECC for pre-consultation: https://novascotia.ca/nse/water/withdrawalApproval.asp

**Key regulations:**
- Activities Designation Regulations (watercourse alteration)
- Environment Act, Part V (water resources)
- Water Resources Protection Act

**Practical note:** Many small run-of-river micro-hydro installations (<5kW) operate in a regulatory grey area in NS. The key principle is that water must be returned to the watercourse (no consumptive use). A formal permit application involves a hydrological assessment.

### 7.2 Solar

- No specific provincial permits for residential solar in NS
- Municipal building permits typically required for roof-mounted
- Ground-mounted on own property: generally permitted in rural areas
- Net metering available through NS Power (up to 100kW for residential)

### 7.3 Crown Land Adjacency

- Adjacent Crown land cannot be annexed or exclusively used
- Hunting, fishing, hiking, and wood gathering for personal use are generally permitted on Crown land
- No structures, land clearing, or development on Crown land without a license of occupation
- Crown Lands Act governs all use

## 8. Limitations & Data Gaps

### 8.1 Known Gaps

1. **Property parcel boundaries** — The biggest gap. NS parcel data through NSGI requires registration and may be restricted for bulk download. Workaround: Regrid.com has NS coverage (paid API) or use Property Online's web interface for individual lookups.

2. **Stream flow data** — Direct measurements exist for only ~80 gauged watersheds in NS. Our flow estimates for small ungauged streams are modeled approximations, not measured values. Error margin: ±50% or more.

3. **Tree canopy / forest cover** — Solar scoring assumes cleared land. In reality, most NS rural land is forested. We'd need land cover classification (available in NSTDB) to discount heavily forested parcels. Added in Phase 2.

4. **Seasonal variation** — Stream flow varies dramatically (spring freshet vs. August low flow). Micro-hydro scoring should ideally use low-flow estimates (Q95 or 7Q10), not mean annual flow. HYDAT provides monthly stats for gauged stations.

5. **Soil/geology** — Not scored, but affects building foundations, well drilling, and septic. Could add from NS geological mapping data (free from Dept. Natural Resources).

6. **Internet/cell coverage** — Critical for off-grid livability but not included. Could add from CRTC coverage maps.

### 8.2 Accuracy Disclaimers

- DEM-derived hydro potential is a screening tool, NOT a site assessment
- Any serious micro-hydro installation requires on-site flow measurement over multiple seasons
- Solar aspect analysis doesn't account for tree shading
- Parcel boundaries from GIS data may not match legal survey boundaries
- Scores are relative rankings, not absolute suitability ratings

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

**The unique value:** Combining micro-hydro potential (which nobody does at scale) with solar, elevation, and property data into a single scored output. The Cyr et al. (2011) methodology for NB small hydro assessment is the closest academic work, but it's not a user-facing tool and doesn't cover property matching.

**Market opportunity:** Off-grid/homesteading is a growing movement. A tool that answers "which properties in Nova Scotia have the best micro-hydro + solar potential?" would be genuinely novel and useful.

## 10. Estimated Development Time

| Phase | Effort | Calendar Time |
|---|---|---|
| MVP (proof of concept) | 15–20 hours | 1–2 weekends |
| Data acquisition + prep | 5–10 hours | 1 week (download time) |
| Full scoring pipeline | 30–40 hours | 2–3 weeks |
| Web visualization | 15–20 hours | 1 week |
| Listing integration | 10–15 hours | 1 week |
| **Total to usable tool** | **75–105 hours** | **4–6 weeks** |

MVP can deliver a ranked, scored list of properties with an interactive map within a single weekend sprint.

## 11. Implementation Plan & Risk Management

### 11.1 Implementation Phases (Detailed)

**Phase 0: Data Validation Sprint (Day 1, ~4 hours)**
*Goal: Confirm all critical data sources actually work before writing any scoring code.*

| Task | Time | De-risks |
|---|---|---|
| Download NS Enhanced DEM (20m) from novascotia.ca | 30 min | Confirm it's still available, not paywalled |
| Download NSHN stream network shapefile | 15 min | Confirm format, schema, completeness |
| Download Crown Land polygons from NS Open Data | 15 min | Confirm coverage, currency |
| Register NSGI DataLocator account, attempt parcel download | 1 hour | **Critical gate** — if parcels unavailable, need Regrid fallback |
| Load all datasets in QGIS, visually verify alignment | 30 min | Catch projection mismatches early |
| Test: extract elevation profile along one known stream | 30 min | Prove the DEM→hydro head pipeline works |
| Test: compute aspect/slope for one known property | 30 min | Prove solar scoring pipeline works |

**Go/no-go decision after Phase 0.** If parcel data is blocked, either:
- A) Use Regrid API (paid, ~$0.01/parcel) → budget ~$4,000 for all NS
- B) Use OSM building/land polygons as proxy (free but incomplete)
- C) Pivot to "score any point on map" instead of "score parcels"

**Phase 1: MVP Scoring (Weekend 1, ~15 hours)**
1. Build data prep script — download, reproject, clip to study area
2. Build hydro module — stream extraction, head calculation, flow proxy
3. Build solar module — aspect/slope raster → % south-facing per parcel
4. Build scoring engine — all 8 criteria, configurable weights
5. Test on 100 known rural parcels near Lunenburg
6. Basic Folium map output

**Phase 1.5: Validation (Week 2, ~5 hours)**
1. Graham drives past top-5 scored properties — do they look right?
2. Check bottom-5 — are they correctly scored low?
3. Spot-check 10 random mid-range properties
4. Adjust weights/thresholds based on ground truth
5. Cross-reference with realtor.ca listings — are any top-scored properties for sale?

**Phase 2: Full Pipeline (Weeks 3-4, ~30 hours)**
1. Scale to all NS rural parcels (~100K+ after filtering)
2. Build proper HYDAT flow regression model
3. Add realtor.ca listing integration (scrape or manual)
4. Interactive web dashboard with search/filter
5. Property detail pages with per-criterion breakdown

**Phase 3: Productization (Month 2+)**
1. Listing alerts (new properties matching criteria)
2. Expand to New Brunswick, PEI
3. Tree canopy / forest cover overlay
4. Soil/well/septic feasibility layer
5. Consider SaaS model

### 11.2 Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **NS parcel data access blocked** — NSGI restricts bulk download | Medium | CRITICAL | Phase 0 de-risk. Fallback: Regrid API ($), OSM proxy, or point-based scoring |
| R2 | **Flow estimates wildly inaccurate** — ±50% error on ungauged streams | High | Medium | Use conservative estimates, flag as "screening only", validate at gauged sites |
| R3 | **DEM resolution insufficient** — 20m misses small streams/features | Medium | Medium | HRDEM LiDAR (1-2m) available for southern NS. Use for drill-down on top candidates |
| R4 | **Forest cover masks solar** — most rural NS is heavily forested | High | Medium | Add NSTDB land cover layer in Phase 2. For MVP, note limitation clearly |
| R5 | **Seasonal flow variation ignored** — mean annual flow ≠ winter flow | High | Low-Med | Use Q95 or 7Q10 low-flow stats from HYDAT where available. Document assumption. |
| R6 | **Legal complexity of micro-hydro** — permits may be onerous | Medium | Low | Informational only — tool is for screening, not permitting. Add disclaimer. |
| R7 | **Performance at scale** — 400K parcels × raster extraction = slow | Medium | Medium | Pre-filter to rural >1 acre. Vectorized raster stats. Target <30 min on MacBook. |
| R8 | **Realtor.ca blocks scraping** — TOS violation, IP ban | High | Low | Not critical for MVP. Manual cross-reference. Consider Regrid or ViewPoint for listings. |
| R9 | **Data goes stale** — NS Open Data URLs change or datasets updated | Low | Low | Pin download dates, version data files, periodic re-download script |

### 11.3 Early De-risking Priority

**Do these FIRST before any code beyond Phase 0:**

1. **🔴 Parcel data access** (R1) — Register NSGI account, attempt bulk parcel download. This is the single biggest unknown. If blocked, the entire project architecture changes.
2. **🟡 DEM→hydro head proof** — Take one known micro-hydro site in NS, compute head from DEM, compare to reality. If the 20m DEM can't resolve the stream valley, we need HRDEM.
3. **🟡 Flow accumulation validation** — Run WhiteboxTools flow accumulation on a small DEM tile. Compare stream network output to NSHN. If they diverge significantly, the drainage area estimates will be unreliable.
4. **🟢 Solar aspect sanity check** — Pick a known south-facing hillside, compute aspect from DEM, confirm it reads 160-200°.

### 11.4 Key Assumptions

1. Run-of-river micro-hydro (no dam/reservoir) is the target use case
2. Properties are for residential off-grid living, not commercial power generation
3. Southern Nova Scotia (within ~150km of Lunenburg) is the primary study area
4. Users will verify top candidates with site visits before purchasing
5. Tool is a screening/ranking system, not a definitive site assessment
6. All scores are relative (property A vs property B), not absolute guarantees

### 11.5 Success Criteria

**MVP is successful if:**
- [ ] Top-10 scored properties include at least 3 that Graham would personally consider buying
- [ ] No obviously unsuitable properties in the top-20 (e.g., downtown Halifax, tiny lots)
- [ ] Hydro scores correlate with known micro-hydro sites (if any can be identified)
- [ ] Solar scores correlate with south-facing terrain visible on Google Maps
- [ ] Total processing time <30 minutes for study area

## 12. File Structure

```
~/Coding/property-finder/
├── SPEC.md                 # This file
├── DATA-SOURCES.md         # Detailed data source reference
├── data/
│   ├── raw/                # Downloaded GIS data
│   │   ├── dem/
│   │   ├── hydro/
│   │   ├── crown-land/
│   │   ├── parcels/
│   │   └── roads/
│   └── processed/          # Derived rasters and clipped vectors
├── src/
│   ├── download.py         # Data acquisition scripts
│   ├── prepare.py          # Data preprocessing + derived rasters
│   ├── score.py            # Main scoring engine
│   ├── hydro.py            # Micro-hydro analysis module
│   ├── solar.py            # Solar exposure analysis
│   └── visualize.py        # Map generation
├── output/
│   ├── scores.geojson      # Scored parcels
│   ├── scores.csv          # Tabular output
│   └── map.html            # Interactive web map
└── config.yaml             # Weights, study area, thresholds
```
