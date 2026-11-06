# Wind at a 30 m small-turbine hub — before/after (2026-11-06)

Branch `wind-hub-30m`, unmerged and unpushed. **Before** = the committed July
run (GWA 100 m layer, utility-turbine bands 8.5/7.5/6.5/5.5 m/s). **After** =
GWA speed estimated at a 30 m hub from the 10 m + 50 m layers, with small-turbine
bands (≥6.0 → 100, 5.0–6.0 → 85, 4.5–5.0 → 65, 4.0–4.5 → 40, <4.0 → 15). Hydro,
access, open ground, elevation, weights and parcel aggregation are unchanged.
Both runs: 40,000 cells, 1,079 excluded, 8,840 ranked PIDs.

"Parcel level" below means the max over a parcel's eligible cells, assigned by
centroid containment. This definition reproduces the July baseline exactly
(241 / 122).

## Headline numbers

**Parcel-level wind score distribution** (8,840 parcels)

| Wind score | Before (100 m) | After (30 m hub) | After, 10 m-anchored variant* |
|---:|---:|---:|---:|
| 15 | 3 | 6,769 | 7,797 |
| 40 | 4,794 | 1,214 | 480 |
| 65 | 3,621 | 439 | 238 |
| 85 | 396 | 336 | 243 |
| 100 | 26 | 82 | 82 |

\* The brief's literal formula, v_hub = v10 × (hub/10)^α, scored with the same
bands; see "Method decision" below.

**Hydro ≥60 + wind ≥65 + open_ground ≥65 (parcel level)**

| | Before | After | 10 m-anchored variant |
|---|---:|---:|---:|
| Each criterion's max cleared independently | 241 (122 vacant) | **29 (14 vacant)** | 27 (14 vacant) |
| One cell clears all three | 115 (52 vacant) | 6 (2 vacant) | 6 (2 vacant) |

**Ranking stability**

- Top 50: **48 of 50 stay.** Out: `60695830` (39 → 60), `60290434` (45 → 63).
  In: `60573417` (57 → 42, a 17,306-acre "developed" holding), `60591377` (52 → 50).
- Top 50 vacant (`top_land_only.csv`): 47 of 50 stay.
- Spearman rank correlation, all 8,840 parcels: 0.993.
- Scores shift down: median parcel −2.44 (= forested top cells losing 25 wind
  points × 0.15 weight × 0.65 cell weight). 186 parcels rose, 8,187 fell, 467
  unchanged. Tiers: ≥80 75 → 42, ≥70 410 → 276, ≥60 1,430 → 1,010.
- Severance candidates 86 → 57. All 29 drop-outs had a cell score just above
  the absolute `SEVERANCE_MIN_CELL_SCORE = 50` cut-off (61.6–70.8 parcel score
  before) and slipped under it. None were added.

**Biggest moves among parcels in the old or new top 200**

| PID | Rank before → after | Type | Where (lat, lon) | Wind on its top-3 cells, before → after |
|---|---|---|---|---|
| 60053154 | 411 → 188 | developed, 106 ac | 44.368, −64.313 (Lunenburg) | 65,65,65 → 85,85,85 |
| 60544459 | 302 → 157 | land_only, 621 ac | 44.348, −64.334 | 40,40,65 → 65,40,85 |
| 60544590 | 209 → 111 | land_only, 400 ac | 44.317, −64.402 | 40,65,40 → 40,85,65 |
| 60543055 | 138 → 53 | lightly_built, 666 ac | 44.259, −64.370 | 65,65,65 → 85,85,85 |
| 60363017 | 189 → 289 | land_only, 75 ac | 44.194, −64.462 | 65,65,65 → 15,15,15 |
| 60728284 | 180 → 270 | land_only, 13 ac | 44.311, −64.705 | 65 → 15 (1 cell) |
| 60290228 | 176 → 260 | lightly_built, 6 ac | 44.353, −64.542 | 65 → 15 (1 cell) |

The risers are all around Lunenburg (within ~13 km of the town), where low
surface roughness keeps 30 m speeds at 5–6 m/s (wind 85). The fallers' top
cells are inland, where the 100 m layer's 6.5–7.5 m/s (wind 65) drops below
4 m/s (wind 15) at 30 m.

Largest moves overall: `60458221` 5,162 → 3,696; `60472891` 3,371 → 4,084.

## Sanity check on real pixels

250 m working-grid cells, with GWA values resampled bilinearly. "Coastal" means
a parcel cell within 500 m of open water; "interior" means at least 5 km from
it. The samples are the 10th/50th/90th-percentile cells of each class.

| Cell (lat, lon) | Class | v10 | v50 | v100 | raw α | **v30** | v30 if 10 m-anchored | Wind score before → after |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 44.2870, −64.3458 | coastal | 4.85 | 6.16 | 7.26 | 0.15 | **5.71** | 5.71 | 65 → 85 |
| 44.2192, −64.3661 | coastal | 5.87 | 6.99 | 7.91 | 0.11 | **6.61** | 6.61 | 85 → 100 |
| 44.1996, −64.6566 | forest interior | 0.42 | 4.41 | 5.90 | 1.45 | **3.25** | 0.82 | 40 → 15 |
| 44.3533, −64.6139 | forest interior | 0.91 | 5.08 | 6.70 | 1.07 | **3.74** | 1.76 | 65 → 15 |
| 44.4172, −64.3676 | open field interior | 1.78 | 4.91 | 6.36 | 0.63 | **3.62** | 3.43 | 40 → 15 |
| 44.3719, −64.5674 | open field interior | 1.32 | 5.69 | 6.74 | 0.91 | **4.19** | 2.56 | 65 → 40 |

Class medians of v30: coastal 6.6 m/s, forest interior 3.7, open-field interior
3.6, all parcel land 3.8 (v50 land median 5.1, v100 6.6).

Whole-raster checks:

- v30 lies between v10 and v50 on 100% of pixels and below v100 on 100%.
- `wind.tif` matches an independent recompute.
- The scorer's per-cell band matches a direct pixel lookup on every
  non-excluded cell.
- α is clamped high on 51% of all pixels (59% of parcel land) and never
  clamped low.
- Coastal α of 0.10–0.15 is the textbook open-water/coastal shear (~0.1).
- Over forest, v30/v50 = 0.6^0.6 ≈ 0.74. A displacement-height log law for a
  20 m canopy (d ≈ 14 m, z0 ≈ 1.5 m) gives ~0.70–0.75.

"Open field" cells still read as forest. GWA's roughness reflects the
surrounding few km, not a 250 m clearing, so a cleared hilltop may be windier
than its cell says.

## Method decision (deviation from the brief)

The brief's formula anchors the power law on v10: v_hub = v10 × (hub/10)^α,
with α clamped to 0.05–0.6. Over NS forest, GWA's 10 m layer is inside the
canopy, ~0.2–0.5 m/s against ~4–5 m/s at 50 m (raw α 1–2). With the clamp
binding, the 10 m anchor puts a 30 m hub at a median ~0.8 m/s on clamped
pixels. That's physically implausible, and it makes the result a function of
the clamp value.

**Chosen:** keep the same α and clamp, but anchor on the layer nearer the hub in
log-height (50 m for a 30 m hub). Wherever α isn't clamped, both anchors give
the identical answer, because the profile passes through both points. Where α
is clamped, the 50 m anchor stays near real data: ~3.5 m/s, consistent with the
log-law check above.

Practical effect: the 29 vs 27 all-rounder parcels barely differ. What changes
is whether forested parcels score 40 or 15 (1,214 vs 480 parcels at 40). To use
the literal formula instead, swap the anchor rule in
`src/ingest.py:wind_speed_at_hub` and bump `WIND_INGEST_METHOD`, so the manifest
regenerates `wind.tif`.

## `wind_worth_it` flag

Kept **wind ≥65 and open_ground ≤40**. The flag now reads "≥4.5 m/s at the hub
and <10% of the cell open/buildable":

- 4.5 m/s clears the ~4 m/s small-turbine floor with margin.
- Open-ground scoring didn't change, so its cut-off still means the same thing.

On parcel land, the 100 m bands flagged half of all land cells (16,052 of
33,433; 2,455 parcels, 1,893 vacant), which is noise. The flag now covers
11,345 cells in **404 parcels (312 vacant)**, mostly coastal or lakeshore.

Sensitivity: ≥4.0 m/s (wind ≥40) gives 1,016 parcels; ≥5.0 m/s (wind ≥85)
gives 196.

Two caveats:

- Many flagged cells are water inside big parcels (lakes, shoreline).
- The candidate grid's ocean cells are "eligible" and get flagged in the
  cell-level CSVs, before and after. That's pre-existing and unchanged here.

## Cache handling

`data/processed/.manifest.json` now has a `layers.wind` record (GWA URLs, hub
height, `WIND_INGEST_METHOD`). Changing any of these, or a `wind.tif` with no
record (as the July file had), makes `score` refuse to run. `ingest --force`
then deletes and re-fetches only `wind.tif`; the DEM derivatives and parcels
stay.

On the real data: `score --limit 400` refused the July `wind.tif`.
`ingest --force` removed exactly one file and rebuilt it in ~1 s. The mtimes of
the other processed files were unchanged.

## Restoring the July state

The backup is at `~/Coding/.backups/property-finder-wind100m-2026-11-06/`. It
holds `output/`, `results/`, `wind.tif` and `.manifest.json` as of the July run.

**If you check out `main` before merging, restore the July `wind.tif` first:**

```bash
cp ~/Coding/.backups/property-finder-wind100m-2026-11-06/wind.tif data/processed/wind.tif
```

Otherwise `main`'s code, which doesn't track wind inputs, will score the 30 m
speeds against the 100 m bands. The new manifest is harmless on `main`, which
ignores the `layers` key.
