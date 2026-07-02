# Project review — June 2026

A full retrospective of the Stage-B / scoring-redesign push (the 11 commits
merged to main on 2026-06-24), combining a session post-mortem (what went
wrong and how it was caught), an independent fresh-eyes code review of main,
open design questions, and a prioritized roadmap.

**State at review time:** 126 tests passing (~3s), full score run ~2 min,
8,980 PID-keyed parcels ranked, all five criteria on real data (no proxies).

## Update (2026-07-02): §3–§8 items addressed

Every "now/next" quick win, plus the CI/lockfile and calibration-adjacent test
gaps, from the roadmap below were implemented and merged (commits `d5aac1b`
through `80405ca`, 184 tests, CI green on GitHub Actions for every commit):

- **§3 reliability risks:** manifest-based stale-cache guard + `--force`
  (`src/manifest.py`); ArcGIS error-JSON detection + PID-coverage assertion in
  the NSPRD fetch; flood empty-vs-failed distinction (cached sentinel vs retry).
- **§4 testing/CI:** GitHub Actions workflow + pinned `requirements-lock.txt`;
  added the top five missing-test categories (NSPRD fetch mocked, zonal-stats
  parity vs rasterstats, `run_visualize` smoke tests, `ingest_wind` mocked,
  CLI `--force`/`--layer`/`--service` behavior) — net +58 tests (126 → 184).
- **§5 bugs/hygiene:** fixed `analyze.py`'s SCORE_COLUMNS drift; declared the
  missing `scipy` dependency; dissolved parcels by PID (8,980 rows / 8,840
  unique → 8,840/8,840, zero duplicates); wired `config.working_crs` through
  ingest (was decorative); deleted dead `clip.py`/`crs.py`; fixed README/
  `check_data.py`/CLI doc drift; trimmed `map.html` 59.1MB → 42.4MB by
  dropping unscored parcels from the parcel layer.
- **§7 performance:** vectorized `compute_confidence` (was iterrows);
  spatial-indexed `apply_exclusions`' overlap path (was O(cells×exclusions),
  verified against real data: identical 1,079-cell exclusion count, 0.67s);
  bulk-STRtree `access.py` distance computation (was a per-cell loop, had zero
  prior test coverage).

**A live example of the exact staleness problem this review warned about:**
regenerating the `results/` snapshot surfaced that the PID-dissolve fix had
been written but never actually applied — `data/processed/parcels.gpkg` was
cached from *before* the fix existed, so `ingest_parcels`'s skip-if-exists
check meant the dissolve logic never ran against it. The new manifest system
doesn't catch this class of staleness (it tracks config drift, not ingestion-
*code* changes) — worth remembering as a residual gap, not a false claim of
completeness.

**What's still open:** the §8 "soon"/"later" roadmap items — `export-snapshot`
CLI, run provenance metadata, the calibration harness (still the single
biggest open question — the ranking has never been checked against a known
site), HYDAT-based low-flow regression, civic-address ingestion, per-energy
ranked lists, PVSC join, and the rest. See §8 below, unchanged.

---

## 1. What we built (context)

- **Stage B (PID pipeline):** NSPRD parcel ingestion (local file or the public
  PLV-backed REST service), PID/AAN normalization, cell→parcel aggregation,
  land-only / lightly-built / developed typing, severance-candidate flagging,
  `ranked_parcels.csv` + a committed `results/` snapshot.
- **Scoring redesign:** hydro 40 / access 20 / open_ground 15 / wind 15 /
  elevation 10; `open_ground` replaced the saturated solar+buildable pair;
  elevation demoted to a coastal-flood penalty; conjunctive `score_allrounder`;
  `wind_worth_it` flag.
- **Real data end-to-end:** validated flow-accumulation (post-bugfix), NSTDB
  forest/water in the buildability mask, NS Coastal Program 2100 flood
  exclusion, Global Wind Atlas 100 m wind via windowed `/vsicurl/` reads.
- **Performance:** vectorized zonal stats (`src/zonal.py`) — full score run
  ~16 min → ~2 min; smoke targets (`score --limit N`, synthetic e2e test).

## 2. Post-mortem: mistakes made during the work

Recorded honestly because each one changed how the tool should be run or
reviewed in the future.

1. **`d8_flow_accumulation` fed the D8 pointer instead of a DEM** (pre-existing
   bug, but our first "fix" recommended *using* the broken raster). Symptom:
   accumulation maxed at ~45 cells; the confidence flag keyed on file
   *existence* so it also reported false confidence. Caught by inspecting the
   raster's value range — the lesson is **validate data products by their
   values, not their presence** (now enforced by `flow_accumulation_valid`).
2. **`breach_depressions_least_cost(dist=200)` is pathologically slow** —
   25+ min of CPU without finishing. Replaced with plain `breach_depressions`
   (~6 s). Lesson: time-box unfamiliar geoprocessing calls; test on a subset.
3. **A "fix" introduced a crash**: setting `None` into the boolean
   `wind_worth_it` column for excluded cells (pandas 3 raises). It shipped
   because no test exercised that column with an excluded cell — the failure
   surfaced 16 minutes into a full run. Now covered by a regression test and
   the synthetic end-to-end test.
4. **Blind long runs, twice.** Full runs were launched without a fast
   pre-flight, so failures surfaced at the end. Fixed structurally:
   `score --limit N` (~10 s), a ~2 s synthetic e2e pytest, and a working
   practice of *always* smoke-testing before a full run.
5. **Data-source claims went through three revisions:** first "NSPRD parcels
   are free, no account" (wrong — the bulk download is fee/restricted), then
   "there is no free programmatic source" (also wrong), before finding the
   public PLV-backed service (`nsgiwa2 .../PLAN_NSPRD_WM84`) that actually
   works. Lesson: verify access claims against the live endpoint before
   documenting them; the docs now record the corrected history.
6. **The first NSPRD host (`gis7`, ISD_GIS) is NS-gov-network-only** — DNS
   resolves, TCP times out. Cost several timeouts before the reachable host
   was found via the Provincial Landscape Viewer's webmap config.
7. **Underestimated run times** ("~5 min" for what was a ~16 min score),
   which compounded the blind-run problem until the vectorization fixed both.

## 3. Reliability risks (fresh-eyes review of main, ranked)

1. **Versionless skip-if-exists caching (the biggest one).** Every ingest step,
   DEM derivative, and mask build skips when its output file exists — keyed on
   nothing. Changing `study_area.bbox` or `cell_size_m` silently reuses
   old-region rasters. No `--force` flag exists; the only recourse is deleting
   `data/processed/`. **Fix:** per-output manifest (bbox, cell size, source
   mtime/URL, code version) with invalidate-on-mismatch, plus `--force`.
2. **ArcGIS "HTTP 200 + error JSON" can silently truncate the parcel fetch.**
   `fetch_nsprd_parcels` treats an empty page as end-of-data; a mid-pagination
   throttle/error body would return a partial parcel set that then gets cached
   forever by risk #1. **Fix:** detect `payload["error"]` and abort loudly.
3. **No schema-drift guards on remote services.** If the NSPRD service renames
   `OBJECTID`/PID, output degrades to blank PIDs with only a log warning.
   **Fix:** post-fetch assertion (e.g. ≥95% of parcels have an 8-digit PID).
   The GWA wind COG lives on a third-party CDN with no pin/mirror documented.
4. **"No flood zones" is indistinguishable from "flood service failed."** Both
   leave `flood.gpkg` absent → the −20 `no_flood_data` deduction applies and
   the network is re-hit every run. **Fix:** write an empty sentinel layer on
   a successful-but-empty export.
5. **Partial ingest failures are log-only.** `run_ingest` logs "MISSING" and
   carries on with exit code 0 — a cron/CI run can't detect a broken ingest.

## 4. Testing gaps and CI

126 tests, all pure-synthetic, ~3 s — good foundation, but:

- **No CI.** No `.github/` workflow; the suite is fast and network-free, so a
  ~20-line Actions workflow (`pip install -e ".[dev]" && pytest`) is cheap
  insurance. No lockfile either — `pyproject.toml` has only `>=` floors, so a
  fresh install can pull an untested geopandas/shapely major.
- Highest-value missing tests, in order:
  1. `fetch_nsprd_parcels` with mocked `requests` (pagination, mid-fetch error
     JSON, transfer-limit, offset cap).
  2. `grid_zonal_stats` randomized parity vs `rasterstats` (which is still a
     declared dependency — a perfect oracle; `zonal.py` currently has zero
     direct tests despite underpinning three scorers).
  3. `run_visualize` smoke test (705 lines, 0 tests; the `keep_cols` list is
     exactly the kind of thing that silently rots).
  4. `ingest_wind` / flood PNG-decode path against local fixtures.
  5. CLI functional tests beyond `--help` (e.g. `--layer/--service` actually
     forward).

## 5. Bugs and hygiene found on main (post-merge)

- **`analyze.py` drifted from the redesign (real bug):** `SCORE_COLUMNS` still
  lists dead `score_solar`/`score_buildable` and omits `score_open_ground`,
  `score_wind`, `score_allrounder`. Two-line fix + a guard test.
- **`scipy` is used (`zonal.py`, `dem.py`) but not declared in pyproject** —
  rides in transitively today; latent install break.
- **~120 duplicate PIDs in the ranked output** (8,980 rows, 8,840 unique).
  NSPRD serves multiple polygons per PID and the bbox clip splits others;
  fragments are ranked independently with fragment-level acreage. **Fix:**
  `dissolve(by="PID")` after clip, recompute `area_acres`. Until then, treat
  duplicate rows in `results/ranked_pids.csv` as fragments of one property.
- **No provenance in outputs** — no run date, config hash, or git SHA in the
  CSVs; the `results/` README date is hand-written. Add `run_metadata.json`.
- **`results/` artifacts aren't reproducible from repo code** — the
  maps_link column, worksheet, and curated splits were generated ad hoc in
  session. Add an `export-snapshot` CLI command.
- **Dead code:** `src/clip.py` and `src/crs.py` are imported nowhere
  (ingest reimplements both). `solar.py`/`buildable.py` are intentionally
  kept-but-disabled — fine for now.
- **Doc drift:** README still says 104 tests (126), lists solar/buildable among
  the five criteria in the intro, calls `--from-rest` "NS-network only"
  (superseded by the `nsgiwa2` fix), and `cli.py` help names the old service.
  `check_data.py` doesn't know about `wind`, `waterbodies`, or REST-ingested
  flood.
- **`config.yaml`'s `working_crs` is decorative** — ingest uses the constant,
  not the config value. Wire it through or remove the key.
- **`output/map.html` is ~59 MB** (and `scored_parcels.geojson` 107 MB) —
  folium inlines every layer. Filter parcels to a score cutoff or move layers
  to on-demand files.

## 6. Design decisions made (and their rationale)

Recorded so future-us doesn't relitigate them casually:

- **Stream buffer stays at 100 m** (not 250 m). Widening would "link" the
  visual gaps along meandering rivers but only smears the same signal wider —
  precision beats continuity for a user who dislikes false positives.
- **Hydro can score 100 on flow alone (~3.8 m head).** Known and accepted for
  now: a big flat river nearby is a real resource but overstated as a build.
  A head-weighted threshold is the flagged calibration change — do it against
  a known site, not by guesswork.
- **Hydro is rated on the summer low-flow** (8 L/s/km², confirmed by the
  LaHave gauge) — scores reflect the worst month; winter output is ~5–8×.
- **Parcel score = 0.65·cells + 0.35·acreage** — deliberately acreage-heavy
  per the owner's priorities; consequence: very large parcels ride high, so
  check `n_cells` on the giants.
- **Orchards/nurseries count as open ground** (only "TREE AREA" is excluded).
  Defensible — they're cleared — but an orchard isn't buildable without
  removal; revisit if it ever matters in practice.
- **Elevation no longer rewards mid-elevation** — it's purely a coastal-flood
  penalty, so it stops fighting the wind criterion.
- **Wind is a bonus, not a driver** (15%): NASA POWER's seasonal shape shows
  wind is nearly flat (~1.4× swing) — steadying, not deficit-covering. See
  `docs/seasonal-energy-balance.md`.

## 7. Performance leftovers (non-blocking)

- `apply_exclusions` overlap loop is O(cells × exclusion features) with no
  spatial index — worst remaining loop now that flood adds ~2k polygons.
- `compute_confidence` still iterrows (trivially vectorizable, seconds).
- `access.py` `_compute_min_distances` per-cell loop → `STRtree.query_nearest`.
- Hydro's per-cell loop is inherent (per-stream DEM sampling) — leave it.

## 8. Roadmap (prioritized)

**Now / next session — reliability quick wins (~an hour of work):**
1. Declare `scipy`; fix `analyze.py` SCORE_COLUMNS; fix README/cli doc drift.
2. Dissolve parcels by PID before scoring; regenerate the snapshot.
3. Detect ArcGIS error-JSON in pagination; PID-coverage assertion post-fetch.
4. GitHub Actions CI (pytest) + a lockfile.
5. `--force` flag + minimal manifest-based cache invalidation.

**Soon — trust and usability:**
6. `export-snapshot` command with `run_metadata.json` provenance.
7. Calibration harness: a labeled CSV of known good/bad sites → rank report
   and threshold sensitivity (weights are reasoned, not calibrated — the
   single biggest open question in the scoring).
8. Head-weighted hydro thresholds (post-calibration), and a
   drainage-dependent HYDAT low-flow regression to replace the single
   8 L/s/km² constant — the most influential scalar in the 40%-weight score.
9. Civic-address ingestion (the access scorer already looks for `civic.gpkg`;
   NS Civic Address File is open data) — cheap accuracy win.
10. Trim the map: score-cutoff parcel layer or tiled/on-demand layers.

**Later — features:**
11. Per-energy ranked lists (best-hydro / best-wind / best-solar PIDs) — the
    registry already supports it; it's an output-shape change.
12. PVSC assessment join (class + assessed value per AAN) via a bulk request —
    firms up land-only typing and supports offer pricing.
13. Owner-outreach workflow for severance candidates (letter template + PID →
    owner lookup worksheet).
14. NSPRD re-fetch diffing ("watch mode"): flag newly split/re-registered PIDs
    in high-scoring areas.
15. Region profiles (service URLs, runoff constant, FEAT_CODE filters under a
    `region:` config block) → multi-county / multi-province.
16. Zoning / minimum-lot-size layer for real severance feasibility.

## 9. Bottom line

The scoring math is in good shape post-audit and everything runs on real data.
The two things most likely to bite next are **(1) the versionless
skip-if-exists cache** silently serving stale or truncated data, and **(2) the
untested network-ingest paths that feed it**. Those, plus CI, the scipy
declaration, and the per-PID dissolve, are the highest-leverage fixes before
adding features. The biggest open *product* question is calibration — the
ranking has never been checked against a property whose off-grid quality is
independently known.
