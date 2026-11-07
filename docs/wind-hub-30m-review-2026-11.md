# Wind at a 30 m small-turbine hub — before/after (2026-11-06)

Branch `wind-hub-30m`, unmerged and unpushed.

- **Before** = the committed July run: GWA 100 m layer, utility-turbine bands
  (8.5/7.5/6.5/5.5 m/s).
- **After** = GWA mean speed estimated at a 30 m hub, extrapolated down from
  the 50 m and 100 m layers, with small-turbine bands: ≥6.0 → 100,
  5.0–6.0 → 85, 4.5–5.0 → 65, 4.0–4.5 → 40, <4.0 → 15.

Hydro, access, open ground, elevation, weights and parcel aggregation are
unchanged. Both runs: 40,000 cells, 1,079 excluded, 8,840 ranked PIDs.

- **+ Water excluded** = "after" plus a separate fix in its own commit: cells
  that are mostly lake, river or sea no longer count as land, and shore cells
  are assigned to the parcel holding their land. 18,970 cells excluded, 8,479
  ranked PIDs. See "Water cells excluded". Where a table has this column,
  the prose above it describes the July → "after" step.

A first pass earlier the same day used the 10 m + 50 m layers. It is replaced
(see "Why not the 10 m layer"); its numbers are kept below for reference only.

"Parcel level" means the max over a parcel's eligible cells, assigned by
centroid containment, as the scorer does it (with the water fix, a shore cell
whose centroid is in water goes to the parcel holding most of its land). This
reproduces the July baseline exactly (241 / 122).
"Distance to sea" uses connected areas of DEM ≤ 0 m of at least 2 km².

## Method

Per 250 m cell:

- α = ln(v100 / v50) / ln 2, clamped to 0.05–0.6.
- v30 = v50 × (30/50)^α.

The anchor is whichever layer is nearer the hub in log-height, so 50 m for a
30 m hub. Any hub height works; at 50 m or 100 m it returns that layer
exactly.

On the study area α runs 0.12–0.52 (median 0.32): ~0.13 over open water and
0.4–0.5 over forest. The clamp binds on 12 of 40,000 pixels, so v30 is almost
everywhere a straight fit through two real GWA layers.

**Canopy guard.** A pixel where the lower layer is below half the upper one
gets no value. If more than 5% of pixels fail, the layer pair is refused and
nothing is written. 50 m + 100 m fails on 0 pixels.

## Does wind now separate coast and ridges from inland?

**Coast from inland: yes, clearly. Ridges from valleys: only modestly, about
one band. Wind ≥65 is now mostly a coastal signal.**

**Parcel-level wind score by distance to sea** (a parcel's nearest cell;
water excluded)

| Distance | Parcels | 15 | 40 | 65 | 85 | 100 | ≥65 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0–1 km | 2,062 | 551 | 499 | 377 | 400 | 235 | **49%** |
| 1–3 km | 1,741 | 1,065 | 541 | 96 | 39 | 0 | **8%** |
| ≥3 km | 4,676 | 2,474 | 1,420 | 564 | 212 | 6 | **17%** |

With sea cells still scored, the 0–1 km row was 51% ≥65 (2,213 parcels);
the other rows barely move.

Land cells across the whole study area:

- **v30 ≥ 4.5 m/s** (4,481 cells): 47% are within 1 km of the sea and 52%
  within 3 km. Only 12% are inland ridges or upper slopes (TPI ≥ 7 m, ≥3 km
  from the sea). Much of the rest is the higher interior: ground above 70 m,
  more than 10 km from the sea, averages 4.2 m/s.
- **v30 ≥ 5.0 m/s** (2,158 cells): 68% are within 1 km of the sea.
- **Median v30:** 4.6 m/s within 1 km of the sea, 5.0 within 500 m, and 3.9
  more than 3 km inland (5th–95th percentile 3.3–4.9).

Inland ridges register but mostly stay below 4.5 m/s. Inland cells with
TPI ≥ 15 m average 4.4 m/s against 3.8 in inland valleys, and 37% of them
reach 4.5 m/s.

## Graham's question: 0 m ground + 30 m, or 50 m ground + 30 m?

> "the distinction we'd want to make, presumably, would be if something was
> 0m + 30m, or already 50m + 30m and the resultant wind differences (if there
> are any, or if they can be distinguished)"

GWA's layers are heights above ground, so both cases are "30 m above ground."
The only difference the model sees between them is the terrain effect it
resolves at ~250 m.

**Mean v30 in m/s by ground elevation and distance to sea** (land cells; cell
count in brackets)

| Distance to sea | 0–15 m ground | 15–40 m | 40–70 m ground | >70 m | 40–70 minus 0–15 | 0–15 m ground at 80 m above ground |
|---|---:|---:|---:|---:|---:|---:|
| 0–1 km | 4.97 (2,213) | 4.56 (1,380) | 4.58 (218) | 4.75 (42) | **−0.39** | 6.57 |
| 1–3 km | 3.81 (335) | 3.70 (1,646) | 3.89 (1,312) | 4.17 (506) | **+0.08** | 5.78 |
| 3–10 km | 3.56 (223) | 3.45 (1,588) | 3.86 (4,354) | 4.03 (3,205) | **+0.30** | 5.46 |
| >10 km | (4 cells) | 3.36 (209) | 3.72 (1,044) | 4.19 (5,447) | — | — |

Within-group standard deviations are 0.25–0.45 m/s inland (0.68 for the
335 low cells at 1–3 km) and 0.4–1.0 within 1 km of the sea.

- **Near the sea, low ground is windier.** Within 1 km, 0–15 m ground beats
  40–70 m ground by 0.4 m/s. Shoreline exposure to sea fetch matters more
  than height.
- **Inland, higher ground is a little windier.** At 3–10 km, 40–70 m ground
  is +0.3 m/s (~8%) over 0–15 m ground; above 70 m it is +0.5. At 1–3 km
  there is essentially no difference. Over inland cells, v30 correlates 0.46
  with ground elevation and 0.36 with TPI.
- **A 30 m tower on 50 m ground is not an 80 m tower at sea level.** Low
  ground at 80 m above ground averages 5.5–6.6 m/s, 1.6–2.0 m/s more than
  40–70 m ground at 30 m. Wind follows height above the local surface and
  canopy, not height above the sea.
- **Can the difference be seen? On average, yes; site by site, barely.** The
  inland +0.3 m/s is about one band step, e.g. 3.9 → 4.2 m/s moves a cell
  from 15 to 40. But it is no larger than the spread within each group. GWA
  also doesn't resolve a knoll or clearing smaller than its ~250 m pixel, so
  a cleared 50 m hilltop can be windier than its cell says.

There is no double counting with the elevation criterion. That criterion is
only a coastal-flood penalty below 20 m and doesn't reward height.

**Mean v30 in m/s by TPI band** (exposure.tif, the scorer's proxy bands; land
cells)

| TPI (m) | All land | Coast (<3 km) | Inland (≥3 km) | Inland share ≥4.5 m/s |
|---|---:|---:|---:|---:|
| ≥15 ridge / hilltop | 4.62 (1,300) | 4.86 (565) | 4.43 (735) | 37% |
| 7–15 | 4.32 (2,890) | 4.59 (1,158) | 4.14 (1,732) | 15% |
| 2–7 | 4.18 (4,236) | 4.54 (1,477) | 3.99 (2,759) | 10% |
| −2–2 flat / mid-slope | 4.11 (5,722) | 4.41 (1,701) | 3.98 (4,021) | 16% |
| <−2 valley | 3.84 (9,578) | 3.93 (2,751) | 3.80 (6,827) | 10% |

The ordering is monotonic: inland ridges average 0.6 m/s above inland valleys.

## Headline numbers

**Parcel-level wind score distribution** (8,840 parcels; 8,479 with water
excluded)

| Wind score | Before (100 m) | After (30 m, from 50/100 m) | + Water excluded | First pass (from 10/50 m, superseded) |
|---:|---:|---:|---:|---:|
| 15 | 3 | 4,221 | 4,090 | 6,769 |
| 40 | 4,794 | 2,535 | 2,460 | 1,214 |
| 65 | 3,621 | 1,085 | 1,037 | 439 |
| 85 | 396 | 705 | 651 | 336 |
| 100 | 26 | 294 | 241 | 82 |

The bands are as given in the brief, not retuned. With them, 48% of parcels
score 15 (under 4 m/s at 30 m) and 24% score ≥65 (23% with water excluded).

**Hydro ≥60 + wind ≥65 + open_ground ≥65 (parcel level)**

| | Before | After | + Water excluded | First pass |
|---|---:|---:|---:|---:|
| Each criterion's max cleared independently | 241 (122 vacant) | 108 (47 vacant) | **89 (36 vacant)** | 29 (14 vacant) |
| One cell clears all three | 115 (52 vacant) | 45 (18 vacant) | 43 (17 vacant) | 6 (2 vacant) |

**Ranking stability**

- **Top 50: 47 of 50 stay.**
  - Out: `60695830` (39 → 66). Also `70101548` and `60303575`, which went
    from tied 48th to tied 50th and now sort just outside the first 50 rows.
  - In: `60314408` (55 → 40); `60573417` (57 → 46, a 17,306-acre "developed"
    holding); `60732054` (77 → 47).
- Top 50 vacant (`top_land_only.csv`): 47 of 50 stay. Top 200: 189 of 200 stay.
- Spearman rank correlation over all 8,840 parcels: 0.991.
- Scores shift down: median parcel −2.44, i.e. an inland top cell losing 25
  wind points × 0.15 weight × 0.65 cell weight.
  - 869 parcels rose (70% of them within 1 km of the sea), 6,523 fell and
    1,448 were unchanged.
  - Tiers: ≥80 went 75 → 46, ≥70 410 → 292, ≥60 1,430 → 1,149.
- Severance candidates 86 → 67.
  - Out: 23. All had a cell score of 50.1–55.0 and slipped under the absolute
    `SEVERANCE_MIN_CELL_SCORE = 50` (to 46.4–49.9).
  - In: 4. Three are on the coast; one is on windy high ground 12 km inland.
- **July → with water excluded** (both changes together): top 50 keeps 46 of
  50, top 50 vacant 45, top 200 181. Spearman 0.988 over the 8,433 parcels
  ranked in both; median parcel −2.44. Tiers 75 / 410 / 1,430 → 45 / 282 /
  1,103; severance 86 → 61.

**Biggest moves among parcels in the old or new top 200** (July → "after")

| PID | Rank before → after | Type | Where (lat, lon) | Wind on its top-3 cells, before → after |
|---|---|---|---|---|
| 60541646 | 311 → 179 | land_only, 997 ac | 44.322, −64.544 | 65,65,65 → 85,85,85 |
| 60272028 | 254 → 158 | developed, 139 ac | 44.472, −64.639 | 40,65,40 → 40,65,65 |
| 60543055 | 138 → 60 | lightly_built, 666 ac | 44.259, −64.369 | 65,65,65 → 85,65,85 (shoreline cells*) |
| 60321460 | 162 → 246 | land_only, 24 ac | 44.422, −64.735 | 65 → 15 (1 cell) |
| 60484094 | 191 → 268 | land_only, 121 ac | 44.302, −64.650 | 65,65,40 → 15,15,15 |
| 60529575 | 134 → 193 | land_only, 68 ac | 44.325, −64.620 | 65,65,65 → 15,15,15 |
| 60349677 | 127 → 184 | developed, 201 ac | 44.290, −64.546 | 65,65,65 → 15,15,15 |

\* Its 42 cells are all mostly sea; with water excluded it is unscored. See
"Water cells excluded" below.

Two parcels rose 69–80 places with no score change at all
(`60664828`, `60671989`): wind there was already 40 and the parcels around
them fell. Largest moves overall: `60501418` 6,522 → 5,149 (65 → 100) and
`70094834` 4,211 → 5,142 (65,65 → 15,15).

## Sanity check on real pixels

These are 250 m cells. Within each class, the samples are the cells at the
20th and 80th percentile of v30.

| Cell (lat, lon) | Class | Ground (m) | To sea (km) | TPI | v10 | v50 | v100 | raw α | **v30** | Score |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 44.3661, −64.4950 | coastal (<500 m) | 44 | 0.25 | 4.9 | 1.48 | 5.03 | 6.44 | 0.36 | **4.19** | 40 |
| 44.1294, −64.5234 | coastal (<500 m) | 1 | 0.21 | 0.7 | 2.74 | 6.61 | 7.49 | 0.18 | **6.03** | 100 |
| 44.2510, −64.6800 | inland ridge | 93 | 11.8 | 18.9 | 0.47 | 5.01 | 6.59 | 0.39 | **4.10** | 40 |
| 44.3410, −64.5258 | inland ridge | 89 | 3.7 | 15.6 | 1.60 | 5.55 | 6.94 | 0.32 | **4.70** | 65 |
| 44.1567, −64.6648 | inland valley | 10 | 3.2 | −2.9 | 0.41 | 4.28 | 5.85 | 0.45 | **3.39** | 15 |
| 44.4891, −64.7151 | inland valley | 105 | 19.3 | −3.2 | 3.44 | 4.99 | 6.39 | 0.36 | **4.16** | 40 |
| 44.3598, −64.6266 | inland flat ≥90 m | 90 | 8.6 | −0.9 | 1.29 | 4.77 | 6.46 | 0.44 | **3.82** | 15 |
| 44.5092, −64.5679 | inland flat ≥90 m | 92 | 14.5 | 0.8 | 1.61 | 5.20 | 6.52 | 0.33 | **4.41** | 40 |

v10 is shown only to illustrate the canopy problem; it isn't used. Each v30
equals v50 × 0.6^α to the shown precision.

Whole-raster checks:

- `wind.tif` matches an independent recompute exactly (max difference 0).
- All 40,000 pixels are finite and none is nodata.
- v30 lies within the extrapolation bounds [v50 × 0.6^0.6, v50 × 0.6^0.05]
  on 100% of pixels, and below v50 on 100%.
- The scorer's per-cell wind band matches a direct lookup of the pixel under
  each cell centroid on 100% of eligible cells, and each cell maps to its own
  pixel.

## Why not the 10 m layer

Over NS forest, GWA's 10 m layer sits inside the canopy:

- Median v10/v50 over the study area is 0.36, and 0.07 at the 5th percentile.
- 55% of pixels fail the canopy guard.

Any profile through it is either implausible (~0.8 m/s at 30 m) or a function
of the α clamp. The first pass anchored on 50 m to dodge this, but its α was
still clamped on about half the area. That's why it scored 6,769 parcels at
15, against 4,221 now. `ingest_wind` now refuses such a pair outright
(DATA-SOURCES §3.3).

## A pre-existing alignment bug, fixed here

`ingest_wind` read the GWA window with floored pixel offsets but
georeferenced it with the fractional ones. That placed the wind data 0.96 ×
0.41 GWA pixels (~190 m east, ~115 m south) off its true position.

- **Affected:** every `wind.tif` so far, including the July 100 m one used
  as "before" here.
- **Effect of fixing it:** v30 moves by 0.07 m/s on average (95th percentile
  0.22, max 0.82), and the wind band changes on 3,198 of 40,000 cells (8%).
- **Not separated:** the before/after numbers above include both this fix and
  the hub-height change.

## `wind_worth_it` flag

Kept at **wind ≥65 and open_ground ≤40**, which now reads "≥4.5 m/s at the
hub and <10% of the cell open/buildable". 4.5 m/s clears the ~4 m/s
small-turbine floor with margin, and open-ground scoring didn't change.

| Parcel cells flagged | Before | After | + Water excluded |
|---|---|---|---|
| All eligible cells in parcels | 16,052 cells, 2,455 parcels (1,893 vacant) | 12,535 cells, 1,072 parcels (783 vacant) | **1,635 cells, 959 parcels (688 vacant)** |
| Land cells only (DEM > 0) | 5,980 cells, 2,402 parcels (1,859 vacant) | 2,394 cells, 1,021 parcels (750 vacant) | — |

Most "after" flags were on water: 16,388 of the 18,023 flagged cells are now
excluded as mostly water, leaving 1,635. The DEM > 0 row overstates land
because lakes sit above sea level. Sensitivity, all parcel cells "after":
≥4.0 m/s (wind ≥40) gives 2,655 parcels; ≥5.0 m/s (wind ≥85) gives 477.

## Water cells excluded (separate commit)

**Bug.** `apply_exclusions` used only protected areas and flood zones, so
cells over lakes, rivers and the sea were scored as land. The 17,891 cells
this fix excludes averaged wind 92 against 30 on the cells left (overall
score 18). They carried 16,388 of the 18,023 `wind_worth_it` flags, and were
among the top-3 cells of parcels such as `60573417` and `60543055`.
`waterbodies.gpkg`, already used by the buildability mask, covers all three
kinds of water, the sea included: 3,938 features, 1,968 km² of the 2,500 km²
study box. DEM ≤ 0 m adds only ~140 cells beyond it.

**Rule.**

- A cell is excluded when protected, flood and water polygons together cover
  at least 50% of it. Area under more than one layer counts once.
  - Before, the overlap was summed per polygon, so sea that is also a mapped
    flood zone counted twice. On this data that alone pushed 103 cells over
    50%.
  - The change readmits no cell that was excluded before.
- Water counts by overlap only. Protected areas and flood zones still also
  exclude a cell whose centroid falls in them; water doesn't.
- A shore cell whose centroid is in water but which is mostly land goes to
  the parcel holding the largest share of its land.
- The 3 invalid (self-intersecting) water polygons are repaired on load.
- A missing `waterbodies.gpkg` is skipped, like the other layers.

**Why not the centroid rule for water too.** The review asked for the
existing centroid-or-overlap rule.

- That rule also drops 728 cells whose centre is in water but which are under
  half water (median 34%). 605 of them are otherwise eligible.
- 99 of those 605 score hydro ≥ 60: lake outlets and stream mouths, the kind
  of site the hydro criterion is for.
- Dropping them takes those streams away from the land parcels around them:
  8,399 ranked parcels instead of 8,479.

**Why the assignment change.** Keeping those cells exposed a second bug.

- Cells go to parcels by centroid, so a shore cell centred just offshore went
  to whatever parcel holds that water.
- 200 of the 605 went to parcels that are at least half water (water lots),
  38 of them with hydro ≥ 60.
- With the sea cells gone, such a parcel's top-3 mean is set by that one land
  cell, while its acreage still counts the water:
  - `60573417` (17,306 acres, 99.9% water) went from rank 46 to 2;
  - `60560349` (96% water) went from rank 1,272 to 31.
- Placing the point on the cell's land alone isn't enough: it can fall in a
  neighbour holding a minority of that land, which wrongly dropped some
  small parcels. Hence "largest share of its land".

**Effect** ("after" → + water excluded; July → both is under "Ranking
stability")

| | After | + Water excluded |
|---|---:|---:|
| Excluded cells | 1,079 | 18,970 |
| Eligible cells, mean wind score | 38,921, 58.6 | 21,030, 30.0 |
| Ranked PIDs | 8,840 | **8,479** |
| Tiers ≥80 / ≥70 / ≥60 | 46 / 292 / 1,149 | 45 / 282 / 1,103 |
| Severance candidates | 67 | 61 |
| Median parcel score | 44.82 | 45.06 |

- **Ranked list:**
  - 407 parcels drop out because every cell they had is mostly water: 332
    vacant, 52 lightly built, 23 developed. 11 were in the top 200.
  - 46 parcels are newly ranked because a shore cell's land is mostly
    theirs.
- **Top 50: 47 of 50 stay.**
  - Out: `60555083` (10 → 464: 40 cells → 3, 92% water); `70202817` (30 →
    unscored, a river lot, see below); `60573417` (46 → unscored).
  - In: `60453073` (2,098 → 35), which now gets two shore cells (hydro 100,
    open ground 75) whose land is mostly in it; `60329687` (375 → 44);
    `60303575` (tied 50th → 49th).
- **Top 50 vacant:** 47 of 50 stay. **Top 200:** 184 of 200 stay.
- **Scores:** Spearman 0.998 over parcels ranked in both. 252 parcels change
  score: 119 fall, 133 rise.
  - Largest fall: `60567559`, 95 → 3,875. It is 809 acres and 98% water, and
    54 cells drop to 1.
  - Largest rise: `60453073`, +26.5.
- **Severance:** 6 candidates drop out, none come in.

**Hydro: did parcels keep their streams?** Mostly yes.

- 110 newly excluded cells scored hydro ≥ 60. Hydro rates any stream within
  `STREAM_BUFFER_M` of a cell, so for 95 of them a neighbouring eligible
  cell scores ≥ 60 too, and only 1 has no eligible neighbour at all.
- 87 ranked parcels' best hydro drops below 60. 61 lost it to an excluded
  water cell and 26 to the reassignment.
- **Lost to an excluded cell (61):** 50 of these parcels are at least half
  water. 11 are mostly land, and a stream crosses the land of 6 of those:
  - `60296423` (27.5 acres, 22 of them land): now unscored. Both its cells
    are mostly water (52% and 71%), and the 52% one scored hydro 80. This is
    the clearest real loss.
  - `60428505` (9 acres) and `60358926` (4 acres): also now unscored.
  - `60307956`, `60220803`, `60342433` stay ranked, but their best hydro
    drops to 0. Each has 150–215 m of stream on its land; the hydro ≥ 60
    cells along those streams belong to neighbouring parcels.
- **The biggest drop is intended:** `70202817` went from rank 30 to unscored.
  It is a 238-acre river and estuary lot, 90% water, with its land in strips
  along the banks.
  - 6 of its 13 cells are now excluded: mostly water, or 41% water plus 41%
    protected area.
  - The other 7 still count, but for the neighbouring land parcels that hold
    most of their land.
- **Lost to the reassignment (26):** 19 are water lots. The other 7 are small
  mostly-land parcels (7–24 acres) whose shore cell's land lies mostly in a
  neighbour's parcel.

**Not fixed here**

- **Acreage still counts water.** The size score (35% of a parcel's score)
  uses the deeded area, so water lots and lakefront lots that take in lake
  area score as big parcels. 198 ranked parcels are at least 25% water, and
  34 are at least half. In the top 50: `60559630` (#7, 25 acres, 60% water)
  and `60554987` (#45, 85 acres, 85% water), each on one land cell of its
  own. Scoring land acres instead would also change the 40-acre severance
  rule, so it is filed in `tasks.md` for a decision.
- **Grid resolution.** A parcel whose land lies entirely in mostly-water
  cells gets no cell and drops out (`60296423` above).
- **`real-estate-eval`:** its atlas registry imported the 8,840 July scores
  in September. The smaller list only matters if someone reimports: 407 of
  those PIDs would then have no score, and 46 new ones would.

## Stress test

Every fix below has a regression test. The window, coverage and wind-grid
tests were also run against the pre-fix code to confirm they fail there.
The suite is 290 tests, all passing, all network-free.

**What was tested**

1. **Ingest** (`tests/test_ingest_wind.py`, GWA reads mocked with local
   fixtures):
   - NaN, nodata, 0, negative and ±inf in either layer: no value, never
     NaN/inf.
   - v100 < v50: α clamps low.
   - An all-nodata or all-zero layer: no file written.
   - The 50 m and 100 m layers on different grids (extent, size and
     resolution all differ): every cell exact to 0.002 m/s on a gradient
     field.
   - A bbox partly outside coverage: nodata outside, exact inside. Entirely
     outside: clear error, no file.
   - A timeout on the 100 m read after the 50 m read succeeded: previous
     `wind.tif` bytes and manifest unchanged, no partial file. The error names
     the 100 m layer and says the old file is unchanged.
   - A crash at the rename step: old file and record intact, temp removed.
   - A 10 m + 50 m forest pair: refused. A small canopy patch: masked, rest
     written.
   - The HTTP timeout and retry settings are passed to GDAL.
2. **Config:**
   - Hub 50 and 100 reproduce those layers exactly (to 1e-6, even with α
     clamped). Hubs 20, 70, 75 and 120 match the power law; 12, 20.5 and 140
     are accepted.
   - Rejected with a clear message: hub 0, −30, NaN, inf, `"thirty"`, `"30"`,
     `true`, blank and a list; a missing or unknown key (e.g. `hub_height:`);
     `wind: 30`; one or three layers; non-whole, zero or non-numeric heights;
     an empty or null URL.
   - No `wind:` block → the defaults, so older configs behave as before.
3. **Cache/manifest:**
   - A hub, source or method change each invalidate only `wind.tif`; the DEM
     derivatives and parcels are untouched.
   - Old manifests: stale if a `wind.tif` exists, fine otherwise. Malformed
     `layers` records are handled.
   - Re-running is a no-op: manifest bytes and mtime unchanged. `--force`
     with nothing stale deletes nothing.
   - `score` refuses a mismatched or unrecorded `wind.tif` with exit code 1.
4. **Real data:**
   - `score` refused the first-pass `wind.tif` (exit 1).
   - `ingest --force` rebuilt only `wind.tif` in ~1 s; md5s of every other
     processed file were unchanged. A plain `ingest` re-run was a no-op.
   - The smoke run (`--limit 400`) passed, then two full `score` runs (~3 min
     each).
   - Every CSV and GeoJSON that `score` writes is byte-identical between the
     two runs, including `ranked_parcels.csv`. The GeoPackages are content-identical
     (only GPKG write timestamps differ).
5. **Outputs vs July** (what `real-estate-eval` reads):
   - Identical column names, dtypes, GPKG layer names, geometry types, CRS
     and feature counts in `ranked_parcels.csv`, `ranked_eligible.csv`,
     `scored_cells.csv`, `scored_parcels.gpkg`, `scored_cells.gpkg` and both
     GeoJSONs.
   - 8,840 unique PIDs. No inf anywhere.
   - NaN only where July had it too: the 1,079 excluded cells and the 31,357
     unscored parcels.
   - With water excluded: same schema, 8,479 unique PIDs, no inf, NaN only
     on the 18,970 excluded cells and 31,718 unscored parcels. Two more full
     runs were byte-identical (GeoPackages content-identical).
   - Band edges at exactly 4.0/4.5/5.0/6.0 and just below are unit-tested.

**Bugs found and fixed**

1. GWA window misalignment, ~190 m (pre-existing; see above).
2. A window running off the edge of GWA coverage was clipped on read but
   kept the unclipped georeferencing. A window entirely outside crashed with
   a GDAL error. It now clips correctly and fails clearly.
3. The wind grid stretched pixels when the bbox isn't a multiple of 250 m, so
   cells drifted off their wind pixels. Pre-existing; the current bbox
   doesn't trigger it.
4. `score` logged "stale data" but exited 0, so `score && visualize` carried
   on.
5. `ingest --force` deleted `wind.tif` before re-fetching, so a failed fetch
   lost it. Now the rebuild goes to a temp file and is swapped in, and the
   manifest record is written only after success.
6. Recording the wind layer could rewrite the manifest's study-area fields,
   blessing other outputs built for a different config.
7. Manifest writes weren't atomic.
8. A manifest that was valid JSON but not an object crashed.
9. A malformed layer record crashed the staleness check.
10. `/vsicurl/` reads had no HTTP timeout and could hang.
11. The config silently accepted typos, partial `wind:` blocks, bools and
    quoted numbers.

**Not tested**

- **Network failures** were simulated by mocking the open call. Nothing ran
  against a slow or flaky CDN.
- **Mismatched source grids and partial coverage** were tested only on
  fixtures. The real 50 m and 100 m layers share one grid, and the study area
  is fully covered.
- **The guard settings are judgement calls**, not calibrated: the 0.5 ratio
  and the 5% refusal fraction.
- **No ground truth.** GWA's 30 m estimate isn't checked against measured
  winds; no NS mast data was used.
- **`real-estate-eval` wasn't run** (not to be touched). Compatibility was
  checked by schema only.
- **`map.html`** regenerated cleanly but wasn't inspected visually.
- **The alignment fix's effect on rankings** isn't separated from the
  hub-height change.
- **The water layer wasn't checked against imagery.** Tidal flats, salt
  marsh and river polygons are taken as mapped; a cell's water fraction is
  only as good as `waterbodies.gpkg`.

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
