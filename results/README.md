# Ranked candidate snapshot — Lunenburg / South Shore

Committed snapshot of the off-grid suitability ranking, so there's a permanent,
lookup-ready record. (The live pipeline writes to `output/`, which is gitignored
and regenerates on each `score` run — this folder is the saved copy.)

**Generated:** 2026-11-07 · **Study area:** Lunenburg / South Shore NS bbox
(EPSG:2961 `[360000, 4880000, 410000, 4930000]`) · **Parcels scored:** 7,679.

*(Updated from the 2026-11-06 snapshot, commit `aca41c8`, after a bug hunt.
Each fix is its own commit with its before/after numbers:*

*1. **Access** is measured from the edge of a cell, not its centre, so a
cell with a road along one side counts as next to the road (top 50 keeps 46).
The **south-facing bonus** counts only the open ground that faces south, and
a cell with no open ground gets none. **Elevation** reads only a shore cell's
land, not the sea beside it.*

*2. **NSPRD road rights-of-way, water lots and placeholder PIDs** are never
ranked (8,085 → 7,671). A highway strip had ranked 6th, and three more road
strips were in the top 50.*

*3. **Hydro** follows SPEC §3.1:*
*- head comes from intake/outfall pairs 50–500 m apart down the stream;*
*- drainage is read along the stream's own line;*
*- flow is routed on land only, with the sea as the outlet;*
*- coastline and tidal lines no longer count as streams.*

*This is the biggest change (top 50 keeps 35, rank correlation 0.93). The old
rank 1 is now 553: its only cell reaches a flat stretch of the Petite
Rivière with 1.6 m of fall over 500 m, under the 3 m minimum. The old code
had pieced 3 m of head together from points along the river in any order.*

*4. **Parcels re-downloaded** from NSPRD. A parcel crossing the study-area
edge now counts its whole area, not just the part inside (e.g. 60314424:
23 → 75 acres). NSPRD itself changed since July: 87 new PIDs, 26 gone and 510
reshaped. 7,671 → 7,679 ranked; the top 50 keeps 49.*

*Against 2026-11-06, 29 of the old top 50 remain in the top 50, rank
correlation 0.92.)*

*(The 2026-11-06 snapshot had been updated from the 2026-07-02 one in four steps.*

*1. Wind is now scored as the estimated mean speed at a 30 m small-turbine
hub height, extrapolated down from the Global Wind Atlas 50 m and 100 m
layers, instead of GWA's 100 m layer, with small-turbine bands (≥6.0 m/s =
100 … <4.0 = 15). At 100 m nearly every parcel cleared 5.5 m/s, so wind barely
discriminated. At 30 m, inland forested parcels mostly fall below 4 m/s and
coastal parcels stand out: half of parcels within 1 km of the sea reach wind
≥65, against 8% at 1–3 km and 17% further inland (mostly the higher interior
and ridges). Most inland parcels lose ~2.4 points; 869 parcels gain, 70% of
them within 1 km of the sea. The same update fixed a ~190 m offset in how the
wind layer was placed (also present in the July snapshot).*

*2. Cells that are mostly lake, river or sea are now excluded instead of being
scored as land, and a shore cell whose centre is in water counts for the
parcel holding most of its land. 407 parcels whose cells were all mostly
water (mostly water lots and shoreline slivers) drop out and 46 come in.*

*3. The size score and the 40-acre severance rule count land acres, not the
whole mapped area, so a water lot or a lakefront lot drawn out into the lake
no longer scores as a big parcel. 299 parcels drop a size band; none of the
34 parcels that are at least half water stays in the top 200.*

*4. Parcels with less than 1 acre of land are left unranked, as the spec
always intended (the setting existed but did nothing). 394 parcels drop out,
none of them in the top 1,000; every other score is unchanged.*

*Against July, 44 of the old top 50 remain in the top 50, rank correlation
0.98. The 2026-07-02 version is in git history at commit `80405ca`. Earlier,
the 2026-07-02 update had dissolved parcels by PID: one row per PID, no
fragments.)*

## Files

| File | What it is |
|------|------------|
| `ranked_pids.csv` | All 7,679 parcels, **ranked best-first**, keyed by **PID** (one row per PID — no fragments). Columns: rank, PID, score, cell_score, n_cells, area_acres, land_acres, water_share, parcel_type, n_buildings, severance_candidate, lat/lon, Maps link. `area_acres` is the whole mapped area; `land_acres` (new) is that less any lake, river or sea in the parcel, and `water_share` (new) is the share that is water. The score and severance rule use `land_acres`. |
| `top_land_only.csv` | Top 50 **vacant** parcels (buy outright). |
| `severance_candidates.csv` | 74 developed/built parcels with ≥40 acres of land and strong scores — owner-outreach (split-off-land) targets. (Was 58 on 2026-11-06: +14 when access was measured from the cell edge, −4 with the south-bonus fix and +6 with the hydro fixes; road and water-lot strips are never candidates. On 2026-07-02 it was 86: 23 sat just above the cell-score ≥50 cut-off and dropped below it when inland cells lost wind points; 4 others, three of them on the coast, rose above it; 6 more dropped out once water cells stopped counting, and 3 once their land, not their whole mapped area, had to reach 40 acres.) |

## How to use

Paste a **PID** straight into [ViewPoint.ca](https://www.viewpoint.ca) (or the
Maps link to see the spot) for owner, assessment, lot details. Score tiers:
**44 parcels ≥80**, **302 ≥70**, **1,119 ≥60**. They were 43 / 273 / 1,078 on
2026-11-06, and 75 / 410 / 1,430 in July with 100 m wind: the scale shifted
down then because wind no longer hands most parcels 40–65 points. The map
colours parcels in these same tiers, and clicking one shows how its score is
made up.

## How it's scored (summary)

Composite cell score (hydro 40 / access 20 / open-ground 15 / wind 15 /
elevation 10), aggregated to each parcel as `0.65 × mean(top-3 cells) + 0.35 ×
acreage`, where acreage is land acres (water in the parcel doesn't count). All inputs are real data: hydro from intake/outfall pairs 50–500 m apart
along each mapped stream, with drainage from a validated flow-accumulation
raster and HYDAT low-flow runoff; open-ground from a forest/water/coast-aware buildability
mask; wind from the Global Wind Atlas, estimated at a 30 m small-turbine hub
height; coastal-flood zones, protected areas and cells that are mostly water
excluded.
See `../docs/seasonal-energy-balance.md` and `../DATA-SOURCES.md` (§3.3 for wind).

## Caveats — read before acting

- **Screening, not vetted.** This narrows 40k grid cells to candidate parcels;
  it is **not** a substitute for ViewPoint + a site visit. Verify head/flow,
  legal access, zoning, and watercourse per PID.
- **Acreage inflates rank.** Big parcels ride high (e.g. a ~2,660-acre lot at
  rank 22); check `n_cells` to see how *much* of a large parcel actually scored
  well vs one good corner.
- **Acreage is land acres.** The score uses `land_acres`, so water in a
  parcel doesn't count. It is only as good as the water layer: where the
  parcel and water layers' shorelines don't quite match, a sliver of water
  can still push a parcel just under a size band (10/20/50/100 acres).
- **Hydro is rated on the summer low**, so winter potential is ~5–8× higher;
  but small streams are flashier than the LaHave gauge used for the regime.
- **Drainage through lakes is the routing's guess.** The stream map stops at
  lakes, so which outlet or inflow carries a big lake's water is left to the
  DEM routing. Where two lines are close, the routing can pick either. About
  400–500 of the 21,030 eligible cells change hydro score between routings
  that differ only in such choices. Rank 9 (60462983) is one: its river
  reads 32 km² of drainage in this run and 0.4 km² in the June raster, which
  gave the 33 km² to a line 480 m away. On
  a map, check that the stream on the parcel really carries the outflow.
- **Hydro ends at the study-area edge.** Rivers that flow in from outside
  the bbox (the LaHave, for one) lose the drainage they gather outside it.
- **Wind is a ~250 m modelled estimate.** GWA's roughness reflects the
  surrounding km of forest, so a cleared ridge can be windier than its cell
  says. Ground elevation alone adds little (inland, 40–70 m ground is
  ~0.3 m/s windier at 30 m than 0–15 m ground). Treat wind as "is this area
  windy," not as a turbine yield.
- **Waterfront parcels can drop out.** A parcel whose land lies entirely in
  250 m cells that are mostly water gets no cell and isn't ranked (e.g. a
  27-acre lot whose two cells are 52% and 71% water). The grid can't see
  slivers.
- **Not yet calibrated** against a known good/bad site — weights are reasoned.
- Regenerate anytime: `python -m src score` → `output/ranked_parcels.csv`, then
  `python -m src snapshot` rebuilds this folder from it.
