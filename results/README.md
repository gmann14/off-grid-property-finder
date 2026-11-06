# Ranked candidate snapshot — Lunenburg / South Shore

Committed snapshot of the off-grid suitability ranking, so there's a permanent,
lookup-ready record. (The live pipeline writes to `output/`, which is gitignored
and regenerates on each `score` run — this folder is the saved copy.)

**Generated:** 2026-11-06 · **Study area:** Lunenburg / South Shore NS bbox
(EPSG:2961 `[360000, 4880000, 410000, 4930000]`) · **Parcels scored:** 8,840.

*(Updated from the 2026-07-02 snapshot: wind is now scored as the estimated
mean speed at a 30 m small-turbine hub height (from the Global Wind Atlas 10 m
and 50 m layers) instead of GWA's 100 m layer, with small-turbine bands
(≥6.0 m/s = 100 … <4.0 = 15). At 100 m nearly every parcel cleared 5.5 m/s, so
wind barely discriminated. At 30 m, forested parcels mostly fall below
4 m/s and coastal/lakeshore parcels stand out. Rankings shift only modestly:
48 of the old top 50 remain in the top 50, rank correlation 0.99. Most
forested parcels lose ~2.4 points; 186 parcels whose best cells have
low-roughness (coastal or open-water) exposure gain. The 2026-07-02
version is in git history at commit `80405ca`. Earlier, the 2026-07-02 update
had dissolved parcels by PID: one row per PID, no fragments.)*

## Files

| File | What it is |
|------|------------|
| `ranked_pids.csv` | All 8,840 parcels, **ranked best-first**, keyed by **PID** (one row per PID — no fragments). Columns: rank, PID, score, cell_score, n_cells, area_acres, parcel_type, n_buildings, severance_candidate, lat/lon, Maps link. |
| `top_land_only.csv` | Top 50 **vacant** parcels (buy outright). |
| `severance_candidates.csv` | 57 developed/built parcels ≥40 acres with strong scores — owner-outreach (split-off-land) targets. (Was 86: 29 sat just above the cell-score ≥50 cut-off and dropped below it when forested cells lost wind points.) |

## How to use

Paste a **PID** straight into [ViewPoint.ca](https://www.viewpoint.ca) (or the
Maps link to see the spot) for owner, assessment, lot details. Score tiers:
**42 parcels ≥80**, **276 ≥70**, **1,010 ≥60** (were 75 / 410 / 1,430 with
100 m wind — the whole scale shifted down because wind no longer hands most
parcels 40–65 points).

## How it's scored (summary)

Composite cell score (hydro 40 / access 20 / open-ground 15 / wind 15 /
elevation 10), aggregated to each parcel as `0.65 × mean(top-3 cells) + 0.35 ×
acreage`. All inputs are real data: hydro from a validated flow-accumulation +
HYDAT low-flow runoff; open-ground from a forest/water/coast-aware buildability
mask; wind from the Global Wind Atlas, estimated at a 30 m small-turbine hub
height; coastal-flood zones excluded.
See `../docs/seasonal-energy-balance.md` and `../DATA-SOURCES.md` (§3.3 for wind).

## Caveats — read before acting

- **Screening, not vetted.** This narrows 40k grid cells to candidate parcels;
  it is **not** a substitute for ViewPoint + a site visit. Verify head/flow,
  legal access, zoning, and watercourse per PID.
- **Acreage inflates rank.** Big parcels ride high (e.g. a ~1,500-acre lot near
  rank 12); check `n_cells` to see how *much* of a large parcel actually scored
  well vs one good corner.
- **Hydro is rated on the summer low**, so winter potential is ~5–8× higher;
  but small streams are flashier than the LaHave gauge used for the regime.
- **Wind is a ~250 m modelled estimate.** GWA's roughness reflects the
  surrounding km of forest, so a cleared ridge can be windier than its cell
  says, and water cells inside big parcels (lakes, shore) score high. Treat
  wind as "is this area windy," not as a turbine yield.
- **Not yet calibrated** against a known good/bad site — weights are reasoned.
- Regenerate anytime: `python -m src score` → `output/ranked_parcels.csv`.
