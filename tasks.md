# Property Finder Tasks

Public-safe source of truth for project work. Keep private locations, credentials, customer details, and unpublished personal context out of this file.

## Worktree Coordination

- Source of truth: this task file on the repo's `main` branch.
- In worktrees, update this file in the worktree you are using; do not edit another checkout's copy.
- Commit task updates with the code/docs change that changes task status.
- Pull or rebase from `origin/main` before long-running work and resolve task-file conflicts explicitly.

## Active

- [ ] Triage the current local/private backlog into public-safe tracked tasks.
- [ ] Review the `wind-hub-30m` before/after numbers (wind at a 30 m small-turbine hub instead of GWA 100 m), then merge or adjust. Branch is unmerged and unpushed.
- [ ] After the Good/Maybe/Bad property labels exist, recheck the wind bands, the 30 m hub default and the `wind_worth_it` cut-offs (wind ≥65, open ground ≤40) against them.
- [ ] Consider per-layer manifest records for the other remote/derived layers (parcels, flood, DEM derivatives). Only `wind.tif` records its source and method so far.
- [ ] Candidate-grid cells over open water (mostly DEM ≤ 0 m; ~16,000 of 40,000) count as "eligible" and get scored. 80 parcels' boundaries take in ~10,500 of them (shoreline and water lots), where they can count among the parcel's top-3 cells (6 of the top 200 parcels) and make up most `wind_worth_it` flags (~10,100 of ~12,500 flagged parcel cells). Exclude cells that are mostly sea, then recheck those parcels.

## Done

- [x] Wind criterion scores estimated mean speed at a configurable small-turbine hub height (default 30 m), extrapolated down from the GWA 50 m + 100 m layers with a canopy guard (the 10 m layer sits inside the forest canopy and is refused), with small-turbine bands; manifest records the wind source and hub height (branch `wind-hub-30m`, 2026-11-06).
- [x] Stress-tested the wind ingest, config and manifest paths; fixed the ~190 m GWA window misalignment (also in the July run), partial-coverage georeferencing, wind-grid alignment for bboxes that aren't a multiple of the cell size, `score` exiting 0 on stale data, `ingest --force` deleting `wind.tif` before the re-fetch, non-atomic manifest writes and malformed-manifest crashes (branch `wind-hub-30m`, 2026-11-06).

- [x] Created public-safe task source of truth.
