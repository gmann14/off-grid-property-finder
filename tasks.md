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
- [ ] Now that `wind-hub-30m` is merged, decide whether to reimport the scores into the real-estate-eval atlas. It holds the 8,840 July scores; a reimport would leave 407 of those PIDs unscored and add 46. `area_acres` is unchanged, and `land_acres` / `water_share` are new columns only.
- [ ] `min_parcel_area_acres` in `config.yaml` is parsed but used nowhere. Remove it, or make parcel ingest drop parcels under it; the latter would remove the ~390 ranked parcels under 1 acre, a ranking change to review first.

## Done

- [x] Wind criterion scores estimated mean speed at a configurable small-turbine hub height (default 30 m), extrapolated down from the GWA 50 m + 100 m layers with a canopy guard (the 10 m layer sits inside the forest canopy and is refused), with small-turbine bands; manifest records the wind source and hub height (branch `wind-hub-30m`, 2026-11-06).
- [x] Stress-tested the wind ingest, config and manifest paths; fixed the ~190 m GWA window misalignment (also in the July run), partial-coverage georeferencing, wind-grid alignment for bboxes that aren't a multiple of the cell size, `score` exiting 0 on stale data, `ingest --force` deleting `wind.tif` before the re-fetch, non-atomic manifest writes and malformed-manifest crashes (branch `wind-hub-30m`, 2026-11-06).
- [x] Cells that are mostly water (lakes, rivers, the sea; `waterbodies.gpkg`) are excluded from scoring instead of ranking as land: water counts by ≥50% overlap only, never by centroid, and area under two exclusion layers counts once. Shore cells whose centre is in water are assigned to the parcel holding their land, not to a water lot (branch `wind-hub-30m`, 2026-11-06).
- [x] The parcel size score and the 40-acre severance rule use land acres (`land_acres`: the parcel's acreage less any lake, river or sea in it) instead of the whole mapped area, so water lots and lakefront lots no longer rank as big parcels; `land_acres` is exported next to `area_acres` (branch `wind-hub-30m`, 2026-11-06).
- [x] Reviewed the `wind-hub-30m` before/after numbers (30 m hub wind, water cells excluded, land acres) and merged the branch into `main` (fast-forward, 2026-11-07; not pushed).

- [x] Created public-safe task source of truth.
