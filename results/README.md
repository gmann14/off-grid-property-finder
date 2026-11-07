# Ranked candidate snapshot — Lunenburg / South Shore

Committed snapshot of the off-grid suitability ranking, so there's a permanent,
lookup-ready record. (The live pipeline writes to `output/`, which is gitignored
and regenerates on each `score` run — this folder is the saved copy.)

**Generated:** 2026-11-06 · **Study area:** Lunenburg / South Shore NS bbox
(EPSG:2961 `[360000, 4880000, 410000, 4930000]`) · **Parcels scored:** 8,479.

*(Updated from the 2026-07-02 snapshot in three steps.*

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

*Against July, 44 of the old top 50 remain in the top 50, rank correlation
0.98. The 2026-07-02 version is in git history at commit `80405ca`. Earlier,
the 2026-07-02 update had dissolved parcels by PID: one row per PID, no
fragments.)*

## Files

| File | What it is |
|------|------------|
| `ranked_pids.csv` | All 8,479 parcels, **ranked best-first**, keyed by **PID** (one row per PID — no fragments). Columns: rank, PID, score, cell_score, n_cells, area_acres, land_acres, water_share, parcel_type, n_buildings, severance_candidate, lat/lon, Maps link. `area_acres` is the whole mapped area; `land_acres` (new) is that less any lake, river or sea in the parcel, and `water_share` (new) is the share that is water. The score and severance rule use `land_acres`. |
| `top_land_only.csv` | Top 50 **vacant** parcels (buy outright). |
| `severance_candidates.csv` | 58 developed/built parcels with ≥40 acres of land and strong scores — owner-outreach (split-off-land) targets. (Was 86: 23 sat just above the cell-score ≥50 cut-off and dropped below it when inland cells lost wind points; 4 others, three of them on the coast, rose above it; 6 more dropped out once water cells stopped counting, and 3 once their land, not their whole mapped area, had to reach 40 acres.) |

## How to use

Paste a **PID** straight into [ViewPoint.ca](https://www.viewpoint.ca) (or the
Maps link to see the spot) for owner, assessment, lot details. Score tiers:
**43 parcels ≥80**, **273 ≥70**, **1,078 ≥60** (were 75 / 410 / 1,430 with
100 m wind — the whole scale shifted down because wind no longer hands most
parcels 40–65 points).

## How it's scored (summary)

Composite cell score (hydro 40 / access 20 / open-ground 15 / wind 15 /
elevation 10), aggregated to each parcel as `0.65 × mean(top-3 cells) + 0.35 ×
acreage`, where acreage is land acres (water in the parcel doesn't count). All inputs are real data: hydro from a validated flow-accumulation +
HYDAT low-flow runoff; open-ground from a forest/water/coast-aware buildability
mask; wind from the Global Wind Atlas, estimated at a 30 m small-turbine hub
height; coastal-flood zones, protected areas and cells that are mostly water
excluded.
See `../docs/seasonal-energy-balance.md` and `../DATA-SOURCES.md` (§3.3 for wind).

## Caveats — read before acting

- **Screening, not vetted.** This narrows 40k grid cells to candidate parcels;
  it is **not** a substitute for ViewPoint + a site visit. Verify head/flow,
  legal access, zoning, and watercourse per PID.
- **Acreage inflates rank.** Big parcels ride high (e.g. a ~1,500-acre lot at
  rank 14); check `n_cells` to see how *much* of a large parcel actually scored
  well vs one good corner.
- **Acreage is land acres.** The score uses `land_acres`, so water in a
  parcel doesn't count. It is only as good as the water layer: where the
  parcel and water layers' shorelines don't quite match, a sliver of water
  can still push a parcel just under a size band (10/20/50/100 acres).
- **Hydro is rated on the summer low**, so winter potential is ~5–8× higher;
  but small streams are flashier than the LaHave gauge used for the regime.
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
- Regenerate anytime: `python -m src score` → `output/ranked_parcels.csv`.
