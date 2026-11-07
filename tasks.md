# Property Finder Tasks

Public-safe source of truth for project work. Keep private locations, credentials, customer details, and unpublished personal context out of this file.

## Worktree Coordination

- Source of truth: this task file on the repo's `main` branch.
- In worktrees, update this file in the worktree you are using; do not edit another checkout's copy.
- Commit task updates with the code/docs change that changes task status.
- Pull or rebase from `origin/main` before long-running work and resolve task-file conflicts explicitly.

## Active

- [ ] Triage the current local/private backlog into public-safe tracked tasks.
- [ ] Review the `wind-hub-30m` before/after numbers (wind at a 30 m small-turbine hub instead of GWA 100 m, plus the separate water-cell and land-acres commits on top), then merge or adjust. Branch is unmerged and unpushed.
- [ ] After the Good/Maybe/Bad property labels exist, recheck the wind bands, the 30 m hub default and the `wind_worth_it` cut-offs (wind ≥65, open ground ≤40) against them.
- [ ] Consider per-layer manifest records for the other remote/derived layers (parcels, flood, DEM derivatives). Only `wind.tif` records its source and method so far.

## Done

- [x] Wind criterion scores estimated mean speed at a configurable small-turbine hub height (default 30 m), extrapolated down from the GWA 50 m + 100 m layers with a canopy guard (the 10 m layer sits inside the forest canopy and is refused), with small-turbine bands; manifest records the wind source and hub height (branch `wind-hub-30m`, 2026-11-06).
- [x] Stress-tested the wind ingest, config and manifest paths; fixed the ~190 m GWA window misalignment (also in the July run), partial-coverage georeferencing, wind-grid alignment for bboxes that aren't a multiple of the cell size, `score` exiting 0 on stale data, `ingest --force` deleting `wind.tif` before the re-fetch, non-atomic manifest writes and malformed-manifest crashes (branch `wind-hub-30m`, 2026-11-06).
- [x] Cells that are mostly water (lakes, rivers, the sea; `waterbodies.gpkg`) are excluded from scoring instead of ranking as land: water counts by ≥50% overlap only, never by centroid, and area under two exclusion layers counts once. Shore cells whose centre is in water are assigned to the parcel holding their land, not to a water lot (branch `wind-hub-30m`, 2026-11-06).
- [x] The parcel size score and the 40-acre severance rule use land acres (`land_acres`: the parcel's acreage less any lake, river or sea in it) instead of the whole mapped area, so water lots and lakefront lots no longer rank as big parcels; `land_acres` is exported next to `area_acres` (branch `wind-hub-30m`, 2026-11-06).

- [x] Created public-safe task source of truth.
