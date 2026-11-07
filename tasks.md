# Property Finder Tasks

Public-safe source of truth for project work. Keep private locations, credentials, customer details, and unpublished personal context out of this file.

## Worktree Coordination

- Source of truth: this task file on the repo's `main` branch.
- In worktrees, update this file in the worktree you are using; do not edit another checkout's copy.
- Commit task updates with the code/docs change that changes task status.
- Pull or rebase from `origin/main` before long-running work and resolve task-file conflicts explicitly.

## Active

- [ ] Triage the current local/private backlog into public-safe tracked tasks.
- [ ] After the Good/Maybe/Bad property labels exist, recheck the wind bands, the 30 m hub default, the `wind_worth_it` cut-offs (wind ≥65, open ground ≤40) and the severance cell-score cut-off (`SEVERANCE_MIN_CELL_SCORE = 50`) against them. The severance cut-off is absolute and was kept at 50 for now (2026-11-07), although the 30 m bands lowered the whole score scale: 23 July candidates fell from 50–55 to 46–50 and dropped out (severance 86 → 58 on `wind-hub-30m`).
- [ ] Consider per-layer manifest records for the other remote/derived layers (parcels, flood, DEM derivatives). Only `wind.tif` records its source and method so far.
- [ ] Now that `wind-hub-30m` is merged, decide whether to reimport the scores into the real-estate-eval atlas. It holds the 8,840 July scores. Those reimport numbers (407 PIDs left unscored, 46 added) predate the 2026-11-07 fixes, which left 7,679 parcels ranked; recount before deciding. `area_acres` now counts an edge parcel's whole area, and `land_acres`, `water_share`, `confidence` and `top_*` are new columns only.
- [ ] Decide the open scoring design questions in `docs/improvements-spec-2026-11.md` (items 2–5 have prototype numbers):
  - smooth the size bands (log-acres);
  - whether hydro credit needs a stream on the parcel's own land: 25% of hydro-credited parcels have none;
  - area-weighting the cell part, which needs labels and a sliver guard;
  - the severance rule: 21–92 candidates depending on the rule;
  - the south-facing range: 90–270° or 135–225°;
  - confidence deductions: every cell is now "high";
  - whether run-level deductions should apply when their criterion is disabled.
- [ ] Hydro routing through lakes: the stream network stops at lakes, so which mapped outlet or inflow carries a lake's water is the D8 routing's choice. About 400–500 eligible cells change hydro score between equally valid routings. Rank 9 (PID 60462983) is one of them: its line reads 32 km² in the current raster and 0.4 km² in the June one. Check it on a map, and consider burning the NSHN lines into the routing DEM.
- [ ] Hydro drainage from beyond the bbox (bug-hunt H4) is blocked on data. It needs a DEM at 20 m or finer reaching about 30–50 km past the W and N edges (LaHave, Medway and the other watersheds that cross the bbox), plus a routing extent in config. A +10 km² inflow test raised 163 cells, and the top 200 kept 180.
- [ ] Hydro tuning to recheck once labels exist: the 60 m drainage window with its 40 m junction skip, the 200 m head-conditioning window, and the 3 m minimum over at most 500 m. That minimum gives gentle big rivers (e.g. the Petite Rivière at 0.3% grade, 102 km²) no hydro score.
- [ ] Check the flood-export aspect-ratio fix against the live ArcGIS service on the next flood ingest. It has only been tested against a mock; the current square bbox is unaffected.

## Done

- [x] Wind criterion scores estimated mean speed at a configurable small-turbine hub height (default 30 m), extrapolated down from the GWA 50 m + 100 m layers with a canopy guard (the 10 m layer sits inside the forest canopy and is refused), with small-turbine bands; manifest records the wind source and hub height (branch `wind-hub-30m`, 2026-11-06).
- [x] Stress-tested the wind ingest, config and manifest paths; fixed the ~190 m GWA window misalignment (also in the July run), partial-coverage georeferencing, wind-grid alignment for bboxes that aren't a multiple of the cell size, `score` exiting 0 on stale data, `ingest --force` deleting `wind.tif` before the re-fetch, non-atomic manifest writes and malformed-manifest crashes (branch `wind-hub-30m`, 2026-11-06).
- [x] Cells that are mostly water (lakes, rivers, the sea; `waterbodies.gpkg`) are excluded from scoring instead of ranking as land: water counts by ≥50% overlap only, never by centroid, and area under two exclusion layers counts once. Shore cells whose centre is in water are assigned to the parcel holding their land, not to a water lot (branch `wind-hub-30m`, 2026-11-06).
- [x] The parcel size score and the 40-acre severance rule use land acres (`land_acres`: the parcel's acreage less any lake, river or sea in it) instead of the whole mapped area, so water lots and lakefront lots no longer rank as big parcels; `land_acres` is exported next to `area_acres` (branch `wind-hub-30m`, 2026-11-06).
- [x] Reviewed the `wind-hub-30m` before/after numbers (30 m hub wind, water cells excluded, land acres) and merged the branch into `main` (fast-forward) and pushed, 2026-11-07.
- [x] `min_parcel_area_acres` (SPEC §2.1 Stage B hard exclusion) now works: parcels with less land than it (default 1 acre) get no score or rank and are flagged `parcel_below_min_area`. 394 parcels drop out of the ranking, none in the top 1,000 (2026-11-07).
- [x] Exported rankings break score ties by PID, so a tied parcel's place no longer depends on unrelated rows (7,135 of 8,085 ranked parcels share a rank; ranks, scores and every top-N set unchanged, only the order inside ties) (2026-11-07).
- [x] `python -m src snapshot` rebuilds the committed `results/` from `output/ranked_parcels.csv` (it was a scratch script outside the repo); on today's output it reproduces `results/` byte for byte, and it refuses a missing, empty or half-written export (2026-11-07).
- [x] The map's formula panel is built from the config it was made with (weights, enabled criteria, parcel formula, minimum acres); it hardcoded 40/20/15/15/10, so any weight change made the map describe a formula that wasn't used (2026-11-07).
- [x] The map draws excluded cells again: they read back from the GPKG with score NaN, not None, so the "Excluded" layer was always empty (18,970 of 40,000 cells missing while the legend showed them). They are now one grey layer, dissolved by sorted exclusion reason (7 groups, +0.24 MB). `visualize` also checks the processed-data manifest like `score`, and `visualize` and `ingest-parcels` exit 1 when they produce nothing (2026-11-07).
- [x] Renaming the study area no longer marks data/processed/ stale: the name is only a map label, but `--force` then deleted every processed file, REST-fetched parcels included (2026-11-07).
- [x] `config.yaml` is validated at load: range and type checks for the bbox, CRS, cell size, weights, overlap threshold and parcel aggregation; unknown keys and empty blocks are errors; a partial `confidence_deductions` block merges over the defaults instead of replacing them (2026-11-07).
- [x] NSPRD road rights-of-way (`ROADWAT` 00000003, a median 99.7% within 15 m of a road) and water lots (00000002, 99% water) and placeholder PIDs (`00000000`, `00000006`) are never ranked or made severance candidates; they are flagged `parcel_not_property`. 414 parcels leave the ranking (8,085 -> 7,671), four of them in the top 50 (ranks 10, 26, 26, 47 after the scoring fixes; one highway strip had ranked 6th); the order of the rest is unchanged (2026-11-07).
- [x] Scoring fixes from the 2026-11 bug hunt (`docs/improvements-spec-2026-11.md`):
  - access is measured from the cell's edge, with inclusive band edges;
  - the south bonus counts south-facing open ground only;
  - elevation reads only a shore cell's land;
  - SPEC §2.1 cell flags are set from their conditions;
  - parcels carry confidence, flags and `top_<criterion>` means;
  - stale outputs are removed before a run;
  - a failed scorer exits 1 cleanly.

  Baseline to this point: top 50 keeps 45, Spearman 0.990 (2026-11-07).
- [x] Ingest fixes from the bug hunt:
  - an edge parcel's acres are measured before the bbox clip;
  - DEM tiles are mosaicked, with HRDEM gaps filled from CDEM;
  - NSPRD paging no longer loses rows;
  - civic addresses are ingested;
  - writes are atomic;
  - invalid parcel polygons are repaired;
  - the flood export uses the bbox's aspect ratio;
  - the bbox is reprojected from densified edges;
  - multi-file layers are read whole.

  (2026-11-07)
- [x] Hydro fixes from the bug hunt:
  - coastline and tidal lines are dropped;
  - drainage is read along the stream's own line;
  - head comes from SPEC §3.1 intake/outfall pairs 50–500 m down the flow path;
  - flow is routed on land with the sea as nodata, and the raster was regenerated;
  - DEM nodata carries through slope and aspect;
  - fallbacks log their reason.

  Effect: top 50 keeps 35, Spearman 0.933. The old rank 1 had pieced 3 m of head together from points in any order; on its real reach it has 1.6 m over 500 m (2026-11-07).
- [x] Re-fetched NSPRD parcels so edge parcels count their whole area: 87 new PIDs, 26 gone, 510 reshaped since July. Ranked 7,671 → 7,679; top 50 keeps 49 (2026-11-07).
- [x] Regenerated `results/` (7,679 ranked, 74 severance candidates). Against 2026-11-06 the top 50 keeps 29, Spearman 0.92 (2026-11-07).
- [x] One shared band lookup whose open-ended top band takes +inf, instead of seven copies. A cell with no wind-raster value now scores a neutral 40 with a warning, not 15 in silence. Rankings unchanged (2026-11-07).
- [x] CI runs ruff (pyflakes and syntax errors); the 25 unused imports and variables it found are gone (2026-11-07).
- [x] Map: clicking a parcel opens a popup that explains its rank (cell part vs land-acreage part, the top cells' criterion means with their weights, land vs mapped acres, water share, type, severance, confidence, flags, ViewPoint and Maps links), built in the browser so it adds no HTML per parcel. Score layers, colours and legend now come from one tier table aligned to the results README (>=80 / 70-79 / 60-69 / 40-59 / <40) in a colour-blind-safe palette (ColorBrewer RdYlBu) (2026-11-07).

- [x] Created public-safe task source of truth.
