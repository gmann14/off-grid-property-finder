# Ranked candidate snapshot — Lunenburg / South Shore

Committed snapshot of the off-grid suitability ranking, so there's a permanent,
lookup-ready record. (The live pipeline writes to `output/`, which is gitignored
and regenerates on each `score` run — this folder is the saved copy.)

**Generated:** 2026-07-02 · **Study area:** Lunenburg / South Shore NS bbox
(EPSG:2961 `[360000, 4880000, 410000, 4930000]`) · **Parcels scored:** 8,840.

*(Updated from the original 2026-06-24 snapshot: parcels are now dissolved by
PID before scoring — NSPRD serves multiple polygon records for some PIDs, and
clipping to the study bbox can split one across the boundary. The original
snapshot had 8,980 rows for 8,840 unique PIDs, i.e. ~140 properties appeared
as more than one ranked row with fragment-level acreage. That's fixed now:
every row below is a distinct PID with its full combined geometry/acreage.)*

## Files

| File | What it is |
|------|------------|
| `ranked_pids.csv` | All 8,840 parcels, **ranked best-first**, keyed by **PID** (one row per PID — no fragments). Columns: rank, PID, score, cell_score, n_cells, area_acres, parcel_type, n_buildings, severance_candidate, lat/lon, Maps link. |
| `top_land_only.csv` | Top 50 **vacant** parcels (buy outright). |
| `severance_candidates.csv` | 86 developed/built parcels ≥40 acres with strong scores — owner-outreach (split-off-land) targets. |

## How to use

Paste a **PID** straight into [ViewPoint.ca](https://www.viewpoint.ca) (or the
Maps link to see the spot) for owner, assessment, lot details. Score tiers:
**75 parcels ≥80**, **410 ≥70**, **1,430 ≥60**.

## How it's scored (summary)

Composite cell score (hydro 40 / access 20 / open-ground 15 / wind 15 /
elevation 10), aggregated to each parcel as `0.65 × mean(top-3 cells) + 0.35 ×
acreage`. All inputs are real data: hydro from a validated flow-accumulation +
HYDAT low-flow runoff; open-ground from a forest/water/coast-aware buildability
mask; wind from the Global Wind Atlas (100 m); coastal-flood zones excluded.
See `../docs/seasonal-energy-balance.md` and `../DATA-SOURCES.md`.

## Caveats — read before acting

- **Screening, not vetted.** This narrows 40k grid cells to candidate parcels;
  it is **not** a substitute for ViewPoint + a site visit. Verify head/flow,
  legal access, zoning, and watercourse per PID.
- **Acreage inflates rank.** Big parcels ride high (e.g. a ~1,500-acre lot near
  rank 9); check `n_cells` to see how *much* of a large parcel actually scored
  well vs one good corner.
- **Hydro is rated on the summer low**, so winter potential is ~5–8× higher;
  but small streams are flashier than the LaHave gauge used for the regime.
- **Not yet calibrated** against a known good/bad site — weights are reasoned.
- Regenerate anytime: `python -m src score` → `output/ranked_parcels.csv`.
