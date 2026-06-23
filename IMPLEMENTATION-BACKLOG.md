# Off-Grid Property Finder — Implementation Backlog

> Concrete execution plan for the revised parcel-independent-first MVP.
> Last updated: 2026-03-30

## 1. Delivery Strategy

Build in this order:

1. ~~Data validation and project bootstrap~~ ✅
2. ~~Candidate-cell scoring MVP~~ ✅
3. Parcel join and parcel-aware reporting ← **next**
4. Validation and calibration ← **next**
5. Optional productization work

This backlog assumes:
- Python-first local workflow
- Suitability surface / candidate-cell scoring before parcel integration
- Stage A unit = fixed `250m × 250m` square candidate cell
- No listing scraping in MVP
- Output schema is first-class, not polish: `score`, `status`, `exclusion_reasons`, `confidence_score`, `confidence_band`, `flags`

## 2. Milestones

### M0: De-risk the data stack ✅ COMPLETE

Outcome:
- ✅ Confirmed critical data sources are usable
- ✅ Parcel access decision: **delayed** (NSGI account not yet registered)

Exit criteria:
- ✅ DEM, NSHN, land cover, roads, and at least one exclusion layer load cleanly
- ✅ Vertical datum assumptions are documented
- ⚠️ Parcel access decision is explicit: `delayed` — NSGI registration needed

### M1: Candidate-Cell MVP ✅ COMPLETE

Outcome:
- ✅ Scored study area near Lunenburg using fixed candidate cells (~40,000 cells)
- ✅ Export GeoJSON/CSV with `score`, `status`, `exclusion_reasons`, `confidence_score`, `confidence_band`, and `flags`
- ✅ Interactive Folium map

Exit criteria:
- ✅ End-to-end CLI run works (check-data, ingest, prepare, score, visualize)
- ⚠️ Top-ranked results need calibration validation (too many 100/100 scores)
- ✅ Runtime is acceptable for the target study area
- ✅ 92 tests passing across 15 test files

### M2: Parcel-Aware Pipeline 🟢 IMPLEMENTED (pending real-data run)

Outcome:
- Join candidate scores to authoritative parcels when available ✅
- Add parcel size and parcel-level reporting ✅ (PID-ranked `output/ranked_parcels.csv`)

Exit criteria:
- Parcel-level output exists and preserves underlying score/confidence/flag context ✅
- Parcel aggregation logic is deterministic and documented ✅
- Parcel aggregation uses the mean of the top 3 eligible candidate-cell scores whose centroids fall within the parcel ✅

**Blocker resolved:** the NSGI-account assumption was wrong. NSPRD parcels with PID are public — GeoNOVA download or the `gis7.nsgc.gov.ns.ca/.../ISD_GIS/Property/MapServer` REST service (see DATA-SOURCES §5.1).
**Code status:** `ingest_parcels` (+ NSPRD REST fetch) ingests/normalizes PID/AAN; `aggregate_to_parcels` carries PID through, ranks, and flags; `export_ranked_parcels` emits the PID list; `score` wires it in; the map gains a "Scored Parcels (PID)" layer. Verified end-to-end against the 40k real scored cells with a synthetic parcel grid (real NSPRD download still pending — REST is geofenced from this environment).

### M3: Calibration and Product Hardening 🟢 SCORING REDESIGN DONE

Outcome:
- Improve hydro calibration, exclusions, and scoring differentiation

Exit criteria:
- Calibration notes documented
- Core outputs are stable enough for repeatable use
- Score distribution has meaningful differentiation (not clustered at 100/100)

**Scoring redesign (off-grid energy focus).** Replaced the dead criteria and
realigned weights to the actual goal (hydro-first; wind as conditional bonus):
- `solar` (98% at 100) + `buildable` (literally constant 100) → merged into one
  area-based `open_ground` metric, fed by a buildability mask that now actually
  excludes water + buildings (was slope-only → 99.8% buildable; now ~94%).
- `elevation` demoted from a 25% positive (rewarded mid, penalized high) to a
  10% coastal-flood penalty — high, exposed ground is no longer penalized, so it
  stops fighting the wind goal.
- New `wind` criterion: Global Wind Atlas raster when present, else terrain
  exposure (TPI) proxy. New `score_allrounder` (geometric mean) + `wind_worth_it`.
- Weights: hydro 40 / access 20 / open_ground 15 / wind 15 / elevation 10.

**🔴 Data bug found: `flow_accumulation.tif` is broken (max 47 cells).** D8 routing
dies on the large sea-level flats, so accumulation never propagates — which is why
hydro used a segment-length proxy. The confidence flag keyed off the file's
*existence*, so it wrongly reported high hydro confidence. Fixes: (1) hydro now
validates the raster (`flow_accumulation_valid`) and only uses it when river-scale,
else falls back to the proxy and reports it honestly; (2) `prepare`/`dem.py` now use
depression **breaching** (handles flats) instead of fill — **re-run `prepare` to
regenerate a valid accumulation raster** for real hydro drainage areas.

**Remaining limitation:** `open_ground` water exclusion only carves out watercourse
*lines* (streams.gpkg) + any typed water polygons in land-cover; without a waterbody
/coastline polygon layer it can still credit lakes/ocean flats. Add NSTDB waterbody
polygons to fully fix.

## 3. Recommended Initial Stack ✅ LOCKED

- Language: Python 3.12
- Environment: `venv`
- Vector I/O: GeoPandas + Pyogrio
- Raster I/O: Rasterio
- Terrain/hydrology: WhiteboxTools
- Geometry: Shapely
- Storage: GeoPackage (vectors) + GeoTIFF (rasters) + CSV (tabular)
- Visualization: Folium
- Config: `config.yaml`
- Logging: standard library `logging`
- CLI: Click

## 4. Backlog

Legend:
- Priority: `P0`, `P1`, `P2`
- Status: ✅ done, 🔴 blocked, 🟡 not started

### Epic A: Project Bootstrap ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| A1. Repository skeleton | ✅ | Full src/ tree, data dirs, config, pyproject.toml |
| A2. Dependency lock and CLI entrypoint | ✅ | Click CLI with 5 subcommands |
| A3. Config schema | ✅ | config.yaml with study area, weights, thresholds |

### Epic B: Data Validation and Acquisition ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| B1. Data source smoke-test script | ✅ | `check-data` CLI command |
| B2. Download / ingest workflow | ✅ | `ingest` CLI command, handles GDB/PBF/DEM |
| B3. Parcel availability decision | ⚠️ | Decision: **delayed**. NSGI account not yet registered. |
| B4. Datum and CRS normalization notes | ✅ | EPSG:2961 working CRS, compound CRS handling |

### Epic C: Data Preparation ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| C1. Study-area clipping | ✅ | Integrated into prepare pipeline |
| C2. DEM derivatives | ✅ | slope.tif, aspect.tif, flow_accumulation.tif |
| C3. Open-area / buildability mask | ✅ | Land cover + slope + water + buildings |
| C4. Exclusion layers prep | ✅ | Protected areas + flood zones |
| C5. Rural candidate generation | ✅ | 250m grid, rural mask filtering |

### Epic D: Scoring Engine ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| D1. Base scoring framework | ✅ | Pluggable registry, weight renormalization |
| D2. Elevation / terrain scoring | ✅ | NS-coastal-tuned thresholds |
| D3. Solar scoring | ✅ | Aspect/slope + land-cover mask |
| D4. Access-confidence scoring | ✅ | Road/civic distance + `access_unverified` flag |
| D5. Hydro scoring MVP | ✅ | Stream search, intake/outfall pairs, power calc |
| D6. Buildable/open-area scoring | ✅ | Slope ≤20° percentage |
| D7. Exclusions, flags, and confidence | ✅ | Full implementation with bands |
| D8. Optional preference metrics | 🟡 | Town proximity, water-body, crown-land adjacency not implemented |

### Epic E: Outputs and UX ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| E1. CSV / GeoJSON export | ✅ | CSV, GeoJSON (WGS84), GeoPackage |
| E2. Folium map | ✅ | Color-coded cells, tooltips, layer controls |
| E3. Per-record detail summary | 🟡 | Not implemented (P1) |

### Epic F: Parcel Integration 🟢 IMPLEMENTED

| Task | Status | Notes |
|------|--------|-------|
| F1. Parcel ingest and prep | 🟢 | `ingest_parcels` (local file or `--from-rest`); normalizes PID/AAN, computes area_acres. NSPRD is public — no account. |
| F2. Parcel aggregation logic | 🟢 | `aggregate_to_parcels` carries PID/AAN, adds n_cells/rank/flags; tested + verified against real scored cells |
| F3. Parcel-level flags and reports | 🟢 | `export_ranked_parcels` → `ranked_parcels.csv`; "Scored Parcels (PID)" map layer |
| F4. Real NSPRD run | 🟡 | Download GeoNOVA parcels into `data/raw/parcels/` and re-run `score` (REST geofenced from CI) |

### Epic G: Validation and Calibration 🟡 NOT STARTED

| Task | Status | Notes |
|------|--------|-------|
| G1. Known-site hydro sanity checks | 🟡 | Needs manual site comparison |
| G2. Visual validation sweep | 🟡 | Validation PNGs exist but formal review not done |
| G3. Weight and threshold tuning | 🟡 | Current scores too clustered at 100/100 |
| G4. Performance pass | 🟡 | Runtime seems acceptable but not profiled |

### Epic T: Testing ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| T1. Test harness and scoring unit tests | ✅ | 92 tests, synthetic fixtures, all scorers covered |
| T2. Integration test for end-to-end pipeline | ✅ | Full pipeline with synthetic data |

### Epic H: Deferred / Post-MVP

- `P2` HYDAT-based low-flow regression model
- `P2` Better coastal/flood coverage by region
- `P2` LiDAR-based canopy and shading refinement
- `P2` Listing integration via compliant feed or manual workflow
- `P2` Web dashboard
- `P2` Multi-province support

## 5. Suggested Sprint Order

### Sprint 1 ✅ COMPLETE
- A1, A2, A3
- B1, B2, B3, B4
- C1, C2

### Sprint 2 ✅ COMPLETE
- C3, C4, C5
- D1, D2, D3, D4
- T1

### Sprint 3 ✅ COMPLETE
- D5, D6, D7
- E1, E2
- T2
- G1, G2 (partial — validation PNGs generated but formal review pending)

### Sprint 4 ← CURRENT
- F1, F2, F3 if parcel data becomes available
- Otherwise D8, E3, G3, G4

## 6. Definition of Done for MVP ✅ MET

The MVP is done when:

- ✅ A documented setup works from a clean repo
- ✅ Required datasets for the study area can be prepared without manual debugging
- ✅ The scorer runs end-to-end for the study area
- ✅ Output includes `score`, `status`, `exclusion_reasons`, `confidence_score`, `confidence_band`, and `flags`
- ✅ A map and tabular export are produced
- ✅ T1 unit tests pass on a fresh checkout
- ⚠️ Top-ranked candidates survive manual sanity-check review — **needs formal validation**

## 7. What's Next

Priority order for continued development:

1. **Scoring calibration** (G3) — Fix the too-many-100s problem. Investigate score distributions, tune thresholds.
2. **Visual validation** (G2) — Formal review of top/bottom/middle cells against satellite imagery.
3. **NSGI account** (B3/F1) — Register for DataLocator, attempt parcel download to unblock Stage B.
4. **Hydro sanity checks** (G1) — Compare DEM-derived head estimates to known stream sites.
5. **Optional preferences** (D8) — Town proximity, water-body amenity, crown-land adjacency.
6. **Per-record summaries** (E3) — Human-readable explanations for top-N candidates.
