# Off-Grid Property Finder — Tasks

> Source of truth for project status. Updated after every work session.
> Last updated: 2026-03-30

## Project State
- **Stage:** M1 MVP COMPLETE + confidence fix + flow accumulation fix + analyze command
- **GitHub:** https://github.com/gmann14/off-grid-property-finder
- **Stack:** Python 3.12, GDAL/Fiona/Rasterio/GeoPandas/WhiteboxTools, Click CLI
- **Tests:** 92 passing across 15 test files
- **Output:** ~40,000 scored cells, ~39,924 eligible, interactive Folium map generated
- **Modules:** 28 source files in src/ + scoring/ subpackage (8 files)

## Completed Milestones

### M0: De-risk Data Stack ✅
- [x] SPEC.md written and updated to match implementation
- [x] DATA-SOURCES.md written (all NS geospatial sources documented)
- [x] IMPLEMENTATION-BACKLOG.md with milestone-based delivery, updated with completion status
- [x] Pipeline scaffolding (full src/ tree with modular scorers)

### M1: Candidate-Cell MVP ✅
- [x] Project skeleton + CLI entrypoint (Click: check-data, ingest, prepare, score, visualize)
- [x] Config schema (config.yaml: study area, weights, thresholds, enabled criteria)
- [x] Data ingest pipeline (OSM PBF → roads, NSHN GDB → streams, DEM reprojection)
- [x] DEM derivatives (slope.tif, aspect.tif, flow_accumulation.tif via WhiteboxTools)
- [x] Buildability mask from land cover + slope
- [x] Rural-eligibility mask + 250m candidate grid generation
- [x] 5-criterion scoring engine (Hydro 45%, Elevation 25%, Access 20%, Solar 5%, Buildable 5%)
- [x] Exclusions (protected areas, flood zones) → score=null
- [x] Confidence scoring + banding (high/medium/low)
- [x] CSV + GeoJSON + GeoPackage export
- [x] Interactive Folium map (output/map.html)
- [x] 67 unit + integration tests passing
- [x] Documentation synced (SPEC.md, README.md, IMPLEMENTATION-BACKLOG.md, tasks.md)

## Known Issues

### Scoring Calibration ⚠️ (PARTIALLY ADDRESSED)
- Too many cells scoring 100/100 on buildable (100%) and solar (98%) — thresholds too generous
- ~~All confidence bands show "medium" (60.0)~~ → FIXED: per-cell deductions now applied
- ~~Flow accumulation not generating (WhiteboxTools silent failure)~~ → FIXED: absolute path bug in dem.py
- Confidence now: 8.7% high (80), 91.3% medium (55-70), 0% low — meaningful differentiation
- Hydro is inverted: 80% of cells score 0 (most have no stream) — expected but means hydro only differentiates the ~20% with streams
- `python -m src analyze` now available for distribution diagnostics
- Flood data deferred — no flood.gpkg yet (contributes -20 global confidence deduction)
- Composite score spread is healthy: P10=12.5, P50=32.5, P90=71.0

## Upcoming Milestones

### M2: Parcel-Aware Pipeline 🔴 BLOCKED on NSGI data
- [ ] Register NSGI DataLocator account and test parcel data access
- [ ] Download parcels for Lunenburg study area
- [ ] Test parcel aggregation with real data (code exists in scoring/preferences.py)
- [ ] Parcel size scoring (10% weight when enabled)
- [ ] Parcel-level flags and reports

### M3: Calibration 🟡 NOT STARTED
- [ ] Analyze score distributions — where does clustering happen?
- [ ] Tune thresholds (hydro, elevation, buildable especially)
- [ ] Validate hydro scoring against known stream sites
- [ ] Flow accumulation validation (WhiteboxTools output vs NSHN network)
- [ ] Visual validation sweep of top/bottom/middle cells
- [ ] Weight sensitivity analysis

---

## Prioritized Task List

### Things Claude Can Do (automated)

| Priority | Task | Effort | Description |
|----------|------|--------|-------------|
| P0 | ~~Sync all documentation~~ | 1h | ✅ DONE — SPEC.md, README.md, BACKLOG.md, tasks.md updated |
| P0 | ~~Commit M1 work~~ | 30m | ✅ DONE — systematic commits to git |
| P0 | ~~Create GitHub repo + push~~ | 10m | ✅ DONE — https://github.com/gmann14/off-grid-property-finder |
| P1 | ~~Score distribution analysis~~ | 1-2h | ✅ DONE — `python -m src analyze` command added |
| P1 | ~~Confidence scoring fix~~ | 1h | ✅ DONE — per-cell deductions now applied (access: -15, hydro: -10) |
| P1 | Optional preference scorers | 2-3h | Implement D8: town proximity, water-body amenity, crown-land adjacency |
| P1 | Per-record detail summary | 1-2h | E3: human-readable explanations for top-N candidates |
| P2 | Performance profiling | 1-2h | Profile the pipeline, identify bottlenecks |
| P2 | Additional test coverage | 2-3h | Edge cases, mask generation, ingest error paths |

### Things Graham Needs to Do (manual)

| Priority | Task | Effort | Description |
|----------|------|--------|-------------|
| **P0** | **Register NSGI DataLocator account** | 30m | https://nsgi.novascotia.ca/gdd/ — required to unblock M2 parcel pipeline |
| **P0** | **Visual validation of top cells** | 1-2h | Open map.html + satellite imagery, check if top-10 cells make sense |
| P1 | Download parcel data | 30m | Once NSGI account is active, download Lunenburg-area parcels |
| P1 | Hydro ground-truth | 2-4h | Pick 2-3 known stream sites, compare DEM-derived head to reality |
| P1 | Check known off-grid properties | 1h | Find 2-3 known off-grid homesteads, verify they score well |
| P2 | Review scoring weights | 30m | Do the 45/25/20/5/5 weights match your intuition after seeing results? |
| P2 | Test on a different study area | 1h | Try a bbox outside Lunenburg to check generalization |

## Blockers
1. **Property parcels** — data/raw/parcels/ is EMPTY. NSGI DataLocator account needed for M2.
2. **Scoring calibration** — too many 100/100 cells, poor differentiation. Fixable by Claude.
3. **No formal validation** — top cells haven't been checked against satellite imagery by a human.
