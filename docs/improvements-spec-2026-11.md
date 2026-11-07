# Improvements spec and stress tests (2026-11)

Written 2026-11-07 after the 30 m wind review, the water-cell fix, land acres,
the 1-acre minimum and the tie-order fix. It specs the 13 improvements
suggested that day. For each: the goal, the design, what "done" means, how to
stress-test it, and the risks. Items that would change the ranking were
prototyped against the real outputs before anything was built. Their measured
effects are under each item.

Status key: **built** (in `main`, with tests) · **prototyped** (measured in
scratch, needs a decision before it ships) · **spec only** (not started).

Baseline for every comparison is `main` at `78f499b`:

- 8,085 ranked parcels.
- Tiers: 43 ≥80, 273 ≥70, 1,078 ≥60.
- 58 severance candidates.
- Median parcel score 45.38.

## Order of work

1. Ship the no-ranking-change items first: 12 (snapshot command), 13 (lint
   in CI), 11 (map text from config), 9 (parcel popup) and 8 (map colours
   and tiers). They make every later change easier to check and to show.
2. Then the labelling page (1). Calibration and every weight or band decision
   below wait on labels, so it is the main unblocker.
3. Then the ranking changes (2, 4, 3, 5). Each one ships on its own branch
   with before/after numbers, ideally once the labels exist to judge it.
4. Optional filters (6), the lighter map (7) and the shortlist view (10) come
   after that, or when a buyer needs them.

---

## 1. Labelling page (Good / Maybe / Bad)

**Status:** spec only.

**Goal.** Get about 40 parcels judged by a person, so weights and bands can be
checked against something other than intuition. Every open calibration
question (severance cut-off, band edges, the cell/size split) is blocked on
this.

**Design.**

- A private artifact page with a shared database. Each row shows one parcel:
  PID, rank, score breakdown, land vs mapped acres, and ViewPoint and Maps
  links.
- The rater picks Good, Maybe or Bad and can leave a one-line note.
- Sampling is stratified, so the labels say something about the whole list,
  not only the top:
  - 15 from the top 100;
  - 10 from ranks 100–1,000;
  - 10 from ranks 1,000+;
  - 5 deliberate edge cases: big lot ranked high on acreage alone, water-heavy
    lot, severance candidate, and parcels just above and below the 1-acre line.
- The sample is drawn with a fixed seed and kept with the labels, so it can be
  redrawn and compared later.
- `python -m src labels export` writes the sample. `python -m src labels
  report` reads the labels back and prints agreement by tier:
  - share of Good in ≥70 vs <60;
  - Spearman correlation of score against the label;
  - the parcels where score and label disagree most.

**Done when** 40 parcels are labelled and the report runs on them.

**Stress tests.**

- The sample has no duplicate PIDs and every stratum is filled.
- Redrawing with the same seed gives the same sample.
- The report copes with:
  - unlabelled rows;
  - a PID no longer in the ranking;
  - every parcel labelled the same.

**Risks.**

- One rater's taste stands in for "good". Keep the notes so each judgement
  can be read back.
- The page must not hold anything private beyond PIDs, which are public
  record. Owner names stay in ViewPoint.

## 2. Smooth step bands

**Status:** prototyped; needs a decision.

**Goal.** Remove cliff effects. Today a parcel at 49.9 land acres scores 80 on
size and one at 50.0 scores 92: 12 size points, or 4.2 parcel points. That is
enough to move a parcel hundreds of places. Wind has the same cliff at each
band floor (4.0, 4.5, 5.0 and 6.0 m/s).

**Design.**

- Keep the band tables as the anchor points and interpolate linearly between
  band floors. Interpolate size on log acres (the bands are roughly
  geometric).
- A config switch `scoring.interpolate_bands: false` keeps the step behaviour,
  so the change can be compared and rolled back.
- Scores stay in 0–100 and match the step table exactly at each band floor.

**Done when:**

- the switch exists for size and wind;
- tests pin the values at, between and beyond every edge;
- the before/after table is in this doc.

**Stress tests.**

- Values exactly on each edge and just under it (49.999… acres).
- 0 acres, NaN, inf and 1,500+ acres.
- Wind at exactly 4.0 and 6.0 m/s, NaN wind, and wind below the lowest floor.
- The result is monotone across the full range.
- Two runs are byte-identical.

**Risks.** It changes many scores by small amounts, so the top list reshuffles
a little. That is expected; check it with the labels (item 1).

**Prototype results.** Measured 2026-11-07 against `78f499b`, before the bug fixes below. The baseline was rebuilt with the repo's own functions and matched `output/` exactly. Scripts are kept outside the repo; ask for a rerun after the fixes land.

| Variant | ≥80 / ≥70 / ≥60 | Median | Top-50 kept | Top-200 kept | Spearman | Severance |
|---|---|---:|---:|---:|---:|---:|
| Today | 43 / 273 / 1,078 | 45.38 | — | — | — | 58 |
| Size, linear | 59 / 344 / 1,341 | 47.78 | 39 | 169 | 0.987 | 58 |
| Size, log-acres | 60 / 358 / 1,388 | 48.10 | 38 | 170 | 0.986 | 58 |
| Wind, linear from each floor | 43 / 283 / 1,128 | 45.80 | 49 | 196 | 0.999 | 63 |
| Size linear + wind | 61 / 357 / 1,399 | 48.21 | 39 | 170 | 0.985 | 63 |

- Interpolating only raises scores (each band floor keeps its step value).
  The tiers inflate, so they would need restating.
- The size cliff matters: 148 ranked parcels sit at 19–20 acres and each
  loses about 6.7 parcel points to the 20-acre step. The parcels entering the
  top 50 are all 15.5–19.9-acre lots with cell scores of 83–88. The ones
  leaving are 105–2,657-acre lots with cell scores of 68–71, passed rather
  than lowered.
- Wind barely matters: 60% of eligible cells are below 4.0 m/s, and wind is
  15% of the cell score.
  - Interpolating from each floor keeps the 25-point jump at 4.0 m/s
    (158 cells sit within 0.01 m/s below it).
  - Interpolating 4.0→15 up to 4.5→65 removes that jump, but lowers 3,419
    cells by up to 25 points.
- Edge values behave: exact band edges keep their value, and 0, negative or
  NaN acres give 0.

**Decision needed:** whether to smooth size (recommended, log-acres), and
whether to restate the tiers when it ships. Wind smoothing is low value.

## 3. Parcel-level hydro and road frontage

**Status:** prototyped; needs a decision.

**Goal.** Hydro credits any stream within 100 m of a cell, and access is
distance from the cell. So a parcel can score on a neighbour's stream, or on a
road it has no frontage on. A buyer needs the stream on their own land, and
deeded road frontage or a right of way.

**Design.**

- Add two parcel columns:
  - `stream_on_land`: true if a mapped stream crosses the parcel's land (the
    parcel less waterbodies);
  - `road_frontage_m`: length of the parcel boundary within 20 m of a vehicle
    road.
- Show both in the CSV and the map popup first, as information.
- Whether either one feeds the score (a penalty, or a gate on the hydro
  credit) is a decision to make once labels exist.

**Done when** both columns are exported and tested, and the measured share of
top parcels with no stream on their land is recorded here.

**Stress tests.**

- A stream that touches the boundary only.
- A stream inside a lake within the parcel (it should not count).
- Multi-part parcels.
- A parcel crossed by a road, which counts as frontage on both sides.
- Roads filtered to vehicle roads only, as access already does.

**Risks.**

- Mapped streams miss small brooks, and the road layer misses private lanes.
  Both columns can say "no" when the truth is "yes", so they must stay flags
  until checked against ViewPoint.

**Prototype results.** Measured 2026-11-07 against `78f499b`, before the bug fixes below. The baseline was rebuilt with the repo's own functions and matched `output/` exactly. Scripts are kept outside the repo; ask for a rerun after the fixes land.

Whether the stream behind a parcel's hydro score crosses the parcel's own land
(the parcel less waterbodies):

| Set | Crosses land (>20 m in) | On land, near the boundary only | Just outside (≤20 m) | No stream within 20 m |
|---|---:|---:|---:|---:|
| Top 50 | 43 | 4 | 1 | 2 |
| Top 200 | 144 | 36 | 11 | 8 |
| All ranked parcels whose top cell has hydro > 0 (2,459) | 1,252 | 588 | 187 | 432 |

- 25% of hydro-credited parcels have no mapped stream on their land.
  - The stricter test (no stream within 100 m of a credited cell touches the
    land) gives 28%. The share is about the same at every hydro score.
  - 196 of these parcels border a waterbody, so some front a river or lake.
- **Rank 1 (PID 60347168) is one of them.** Its credited streams are
  127–144 m outside the parcel, and a hydro score of 100 is worth up to about
  26 parcel points.

**Decision needed:** this is the largest single-signal issue found. The
recommendation is to ship `stream_on_land` as a column and popup line first,
then decide with labels whether hydro credit requires it.

## 4. Area-weighted cell → parcel aggregation

**Status:** prototyped; needs labels and a sliver guard.

**Goal.** Today each cell counts in full for the one parcel that holds its
centroid (or most of its land), and not at all for the neighbours it also
covers. So:

- a big parcel's score can rest on one good corner;
- a small parcel inside a good cell can get nothing;
- the shore-cell rule exists only to patch this.

**Design.** Intersect eligible cells with parcels. Parcel `cell_score` is the
area-weighted mean of the best cells, taken best-first until their area inside
the parcel reaches min(parcel land area, 3 cells' area). Tie-breaks are by
cell_id, so the result is deterministic.

**Done when** the method is behind a config switch, it is tested, and the
before/after table is here.

**Stress tests.**

- A parcel inside one cell.
- A parcel straddling four cells.
- A parcel with all its cells excluded.
- Slivers under 1 m².
- Invalid geometry.
- Runtime on all 40k parcels (it must stay under about 30 s).

**Risks.**

- More parcels gain a cell_score. The 1-acre minimum and the
  `parcel_no_assigned_candidates` flag change meaning, so the docs must be
  updated.
- The shore-cell assignment becomes redundant and should then be removed, not
  left in place.

**Prototype results.** Measured 2026-11-07 against `78f499b`, before the bug fixes below. The baseline was rebuilt with the repo's own functions and matched `output/` exactly. Scripts are kept outside the repo; ask for a rerun after the fixes land.

Rule used:
- Every overlap between an eligible cell and a parcel counts, weighted by the
  overlap area.
- Each parcel takes overlaps best-first up to min(land area, 3 cells'
  area); ties go to the bigger piece.
- Runtime is 17.8 s (2.3 s today).

| | Ranked | ≥80 / ≥70 / ≥60 | Median | Top-50 kept | Top-200 kept | Spearman | Severance |
|---|---:|---|---:|---:|---:|---:|---:|
| Today | 8,085 | 43 / 273 / 1,078 | 45.38 | — | — | — | 58 |
| Area-weighted | 20,350 | 25 / 212 / 1,260 | 39.50 | 36 | 142 | 0.954 | 54 (46 kept) |

- 27,360 more parcels get a cell score.
  - 15,095 of them stay unranked under the 1-acre minimum.
  - 12,265 become ranked: median 2.3 acres, none in the top 50.
  - 278 of those have under 5% of their area in eligible cells.
- All of the change at the top of the list comes from re-weighting, not from
  new entrants. Today's top 50 lose 5.5 cell-score points on average.
- Single-cell parcels lose their full-weight cell. For example, #7 (60554573)
  holds one 87-point cell covering 2.7 of its 16 ha and falls to #508.
- **Risk found:** long, thin parcels collect the best slivers of their
  neighbours' cells. 60555083, a shoreline strip with 624 mapped acres and
  49 land acres, rises from #1,131 to #44 by touching 110 cells. Very
  elongated parcels make up 30 of the 347 parcels whose cell score rises by
  more than 10 points, three times their share.
  - A fix would cap each piece's weight at the parcel's share of that cell,
    or require a minimum overlap per piece.

**Decision needed:** the biggest change to the top of the list of any item
here. Do it only with labels (item 1) to judge it, and with the sliver
guard.

## 5. Relative severance cut-off

**Status:** prototyped; needs a decision.

**Goal.** The severance rule uses cell_score ≥ 50. When wind moved to a 30 m
hub height the whole scale shifted down, and 23 candidates fell out with no
change on the ground. A cut-off relative to the distribution would not move
when the scale does.

**Design.** `severance.min_cell_score` becomes either a number (as today) or a
percentile string such as `"top 20%"`, worked out over ranked parcels. The
2026-11-07 decision kept 50 for now, so this ships as an option and the
default does not change.

**Done when:**

- both forms are accepted and validated;
- the log states the cut-off value it resolved to;
- tests cover both forms.

**Stress tests.**

- 0% and 100%.
- A percentile with heavy ties at the cut.
- An empty ranking.
- Malformed strings.

**Prototype results.** Measured 2026-11-07 against `78f499b`, before the bug fixes below. The baseline was rebuilt with the repo's own functions and matched `output/` exactly. Scripts are kept outside the repo; ask for a rerun after the fixes land.

- The pool is 310 parcels with 1–3 buildings and ≥40 land acres.
- Today's cut-off of 50 sits at about the top 23% of ranked cell scores.

| Rule | Candidates | Of today's 58 kept |
|---|---:|---:|
| Top 10% (≥58.5) | 21 | 21 |
| Top 20% (≥51.0) | 56 | 56 |
| Top 30% (≥47.2) | 79 | 58 |
| ≥45 | 92 | 58 |
| ≥50 (today) | 58 | 58 |
| ≥55 | 32 | 32 |

- The count is fragile: 26 of the 58 sit at 50–55, and 18 more pool parcels
  sit at 48–50.
- A top-20% rule would keep 56 of today's 58 and stay put when the scale
  shifts (it shifts again with the access and hydro fixes below).

## 6. Optional preference filters (SPEC §2.4)

**Status:** spec only.

**Goal.** SPEC §2.4 describes three buyer-specific criteria, all off by
default:

- reference-town drive time;
- water-body amenity;
- Crown land adjacency.

None of them is implemented. Crown land is ingested and shown on the map, but
never scored.

**Design.**

- Each criterion becomes a scorer in `src/scoring/` that is skipped at weight
  0. The weights are renormalised over enabled criteria, as SPEC §2.5 says.
- Drive time starts as straight-line distance, labelled as a proxy in the
  output, until routing exists.
- Crown adjacency uses `crown_land.gpkg`: a shared boundary scores 100, then
  falls off by distance as in the SPEC table.

**Done when** each criterion can be enabled from config and has tests, and the
default output is byte-identical to today.

**Stress tests.**

- All criteria enabled.
- Only one enabled.
- A missing data layer, which should log a warning and score NaN, never 0.

## 7. Parcel-first, lighter map

**Status:** spec only (size breakdown measured; the map is still 32.6 MB).

**Goal.** `output/map.html` is 32 MB, and 31.8 MB of it is GeoJSON. It is slow
to open and too big to share.

Measured 2026-11-07:

| Layer | Size | Features |
|---|---:|---:|
| Cells, five score bands | 10.5 MB | 21,030 |
| Parcels | 6.6 MB | 8,085 |
| Contours | 4.5 MB | 981 |
| Buildings | 3.9 MB | 32,501 |
| One 395-feature context layer | 3.1 MB | 395 |
| Other context layers | 3.2 MB | — |

**Design.**

- Make parcels the default view.
- Keep cells, but only those of ranked parcels' top cells, plus a switch for
  the full grid.
- Simplify geometry to about 5 m, which is enough at the zoom levels used.
- Round coordinates to 5 decimals (already done).
- Load buildings and contours only above a zoom threshold, or drop them from
  the shared map.
- Target: under 10 MB with no loss of the information a buyer needs.

**Done when** the map is under 10 MB and the popups and legend still match the
CSV.

**Stress tests.**

- Simplification must not drop small parcels or make invalid shapes.
- Open the map in a browser and click a parcel in each tier.

## 8. Colour-blind-safe scale with tiers aligned to the results

**Status:** built `25428a2`.

**Goal.** The map colours by 80/60/40/20 bands on a red-to-green ramp, which is
hard to read with red-green colour blindness. The results README reports tiers
at 80/70/60, so the map and the README use different tiers.

**Design.**

- One sequential, colour-blind-safe ramp (viridis-like).
- Tiers defined once in constants (`SCORE_TIERS = [80, 70, 60]`, plus "below
  60") and used by the legend, layer names, the README and the results
  README.
- Excluded cells stay grey.

**Done when** the legend, layer names and README all show the same tiers from
the one constant, and each colour pair passes a deuteranopia simulation check.

## 9. Parcel popup that explains the rank

**Status:** built `25428a2`.

**Goal.** A buyer looking at the map should see why a parcel ranks where it
does without opening the CSV.

**Design.** Clicking a parcel opens a popup with:

- rank and score;
- cell score vs size score, with the 0.65/0.35 split stated;
- land acres vs mapped acres, and the water share;
- n_cells;
- the top cell's criterion scores;
- parcel type, and whether it is a severance candidate;
- links to ViewPoint (PID) and Maps.

**Done when** the popup is built from the same columns as the CSV, missing
values render as "n/a" rather than "nan", and a test checks the HTML for one
parcel.

## 10. Shortlist filter view

**Status:** spec only.

**Goal.** Let a buyer narrow 8,085 parcels to a shortlist without a
spreadsheet.

**Design.**

- A static page next to the map, built from `ranked_parcels.csv`.
- Filters:
  - minimum score;
  - acreage range;
  - land-only or severance;
  - minimum hydro;
  - max distance to road.
- The current filter can be exported as CSV.
- No server; the data is embedded in the page.

**Done when** each filter produces the same rows as the equivalent pandas
query, which a test checks.

## 11. Map text from config; refresh screenshots

**Status:** formula panel built `6b398cc`; screenshots not yet retaken.

**Goal.** The map's formula panel hardcodes the weights (40/20/15/15/10), so a
config change makes the map lie. The README screenshots date from 2026-04-01,
before the 30 m wind, water and land-acres changes.

**Design.**

- Build the weight rows from `config.weights`, and omit criteria at weight 0.
- Retake the screenshots from a fresh map after items 7–9.

**Done when** a test renders the panel with non-default weights and finds
them in the HTML.

## 12. Snapshot command in the repo

**Status:** built `aca41c8`; the stale June files in `output/` are left for Graham to keep or delete.

**Goal.** `results/` is regenerated by a scratch script that lives outside the
repo, so nobody else can rebuild it. `output/` also holds stale June copies of
`top_land_only.csv`, `severance_candidates.csv`, `ranked_pids.csv`,
`shortlist_candidates.csv` and `viewpoint_worksheet.csv`. They sit next to
fresh files and look current.

**Design.**

- `python -m src snapshot [--out results]` reads `output/ranked_parcels.csv`
  and writes:
  - `ranked_pids.csv`: every ranked parcel, best first, ties by PID;
  - `top_land_only.csv`: the top 50 land-only parcels;
  - `severance_candidates.csv`: every severance candidate.
- Columns match the committed snapshot. A Maps link is built from the
  lat/lon, rounded to 6 decimals.
- It refuses to run when the export is older than `scored_parcels.gpkg` (a
  half-finished score run).

**Done when** running it on today's output reproduces the committed
`results/` byte for byte, and tests cover the edge cases below.

**Stress tests.**

- Missing `latitude`/`longitude`.
- PIDs with leading zeros.
- No land-only parcels.
- No severance candidates.
- Missing or empty `ranked_parcels.csv`, which should exit non-zero with a
  clear message.

## 13. Lint in CI

**Status:** built `ed6bf88`.

**Goal.** There is no lint. Unused imports and similar slips get past review.

**Design.**

- Add `ruff` to the dev dependencies, with a minimal rule set (pyflakes, plus
  `E9` syntax errors), and fix what it finds.
- Run `ruff check` in `.github/workflows/tests.yml`.
- Skip a type checker for now: pyright reports 98 errors in `src/`, mostly
  pandas-stub noise, and they would bury real problems.

**Done when** CI runs ruff and passes.

---

## Bug hunt (2026-11-07)

Four reviewers each read one part of the code and checked every finding
against the real data or a synthetic case before reporting it. Findings that
could not be reproduced were dropped. Each fix is its own commit with a test
that fails before it. Fixes that change the ranking carry before/after numbers.

The status column is updated as each fix lands.

**Result (2026-11-07).** Every finding is fixed except H4, which waits on
data. Each fix is its own commit with its before/after numbers. Final run on
`main` with the re-fetched parcels and the regenerated flow accumulation,
against the `78f499b` baseline:

| | Baseline | Final |
|---|---:|---:|
| Ranked parcels | 8,085 | 7,679 |
| ≥80 / ≥70 / ≥60 | 43 / 273 / 1,078 | 44 / 302 / 1,119 |
| Median score | 45.38 | 46.66 |
| Severance candidates | 58 | 74 |
| Top 50 kept | — | 29 |
| Top 200 kept | — | 138 |
| Spearman (parcels in both) | — | 0.921 |

The hydro fixes moved the ranking most (top 50 keeps 35 on their own). The
road and water-lot exclusion removed four top-50 parcels. Hydro drainage
through lakes depends on which outlet the D8 routing picks, which shifts
about 400–500 cells between equally valid routings (`tasks.md`).

### CLI, config, export, map

| # | Finding | Severity | Status |
|---|---|---|---|
| M1 | The map never drew excluded cells. They have score NaN, not None, so 18,970 of 40,000 cells were missing although the legend showed them | high | fixed `287c0e6` |
| M2 | The formula panel hardcoded the weights | medium | fixed `6b398cc` |
| M3 | Config values were not range-checked: a percent overlap threshold, a reversed or lat/lon bbox, negative weights, `cell_weight` 1.5, `top_n` 0, a geographic CRS | medium | fixed `52deee8` |
| M4 | Misspelled config keys were silently ignored, and null blocks crashed late, after `scored_cells.gpkg` had been rewritten | medium | fixed `3dc7ded` |
| M5 | A partial `confidence_deductions` block replaced the defaults; two keys did nothing | medium | fixed `140388c`, `f726f5a` |
| M6 | Stale `ranked_parcels.csv`, `ranked_eligible.csv` and Stage B files survived runs that produced none | medium | fixed `9d0166e` |
| M7 | `visualize` and `ingest-parcels` exited 0 on failure; `check-data` and `score` (missing grid) still do | medium | fixed `287c0e6`, `bd3496f`, `2259233` |
| M8 | `check-data` and ingest disagreed on which raw files exist | medium | fixed `e292003` |
| M9 | Renaming the study area marked `data/processed/` stale, and `--force` then deleted everything | medium-low | fixed `1fa8577` |
| M10 | `visualize` skipped the manifest check; `cell_id` was missing from the cell CSVs; docs named a `confidence_score` column that is called `confidence` | low | fixed `287c0e6`, `6b98d79`; docs this commit |

### Ingest

| # | Finding | Severity | Status |
|---|---|---|---|
| I1 | Parcel `area_acres` was computed after clipping to the study box, so edge parcels were scored on their inside part. 114 ranked parcels lose more than 25%; PID 60314424 holds 22.99 acres against a true 74.97 | high | fixed `3128ffc`; parcels re-fetched 2026-11-07 (top 50 keeps 49) |
| I2 | The DEM ingest used one arbitrary tile, so one HRDEM tile replaced full CDEM coverage | high (latent) | fixed `481da17` |
| I3 | NSPRD paging skipped records when the server capped pages below 1,000 | medium (latent) | fixed `1101d03` |
| I4 | Civic addresses were never ingested, though access scoring reads them (SPEC §2.2) | medium | fixed `6f04a47` |
| I5 | Layer writes were not atomic, so an interrupted write was cached for good | medium | fixed `de00cfb` |
| I6 | One self-intersecting parcel crashed `ingest-parcels` | medium (latent) | fixed `39bb703` |
| I7 | Each layer ingested one arbitrary file, so a flood shapefile could become "protected areas" | medium | fixed `fa59163` |
| I8 | The flood export assumed a square bbox | medium (latent) | fixed `774568f` (tested against a mock only) |
| I9 | Only the bbox corners were reprojected to lat/lon | low (latent) | fixed `67819eb` |
| I10 | Placeholder PID `00000006` (a road/water polygon?) ranks 4,166 as a 173-acre land-only parcel | to check | fixed `d172198` (never ranked, with road rights-of-way and water lots) |

### Scoring and aggregation

| # | Finding | Severity | Status |
|---|---|---|---|
| S1 | Access distance was measured from the cell centre, so "road within 50 m → 80" could never happen. 35% of eligible cells score below the SPEC table, and 4,295 cells get `access_unverified` with a road within 200 m | high | fixed `018530c` |
| S2 | The open-ground south bonus ignored whether there is open ground: 10,182 cells with 0% open ground score 10 | medium | fixed `14c3e46` |
| S3 | A scorer that raises scored its whole criterion 0 at full weight, silently | medium | fixed `147a04c`, `495098d` |
| S4 | SPEC §2.1 flags were never set (`coastal_low_elevation`, `solar_low_confidence`, `wind_exposure_proxy_only`), and `hydro_low_confidence` landed on 81% of cells with an undocumented −10 | medium | fixed `0ba56d0` |
| S5 | Per-cell confidence deductions ignored config, and `incomplete_land_cover_mask` was never detected | low-medium | fixed `f726f5a` |
| S6 | Parcels carried no confidence and dropped the flags of the cells they rest on (SPEC §4.4) | low-medium | fixed `018b454` |
| S7 | The elevation mean included sea pixels on shore cells | low | fixed `010fde4` |
| S8 | Exclusion reasons came out in arbitrary order | low | fixed `3717721` |
| S9 | A missing `status` column crashed scoring (API use only) | low | fixed `92acd82` |
| S10 | The band lookups (size, wind, hydro) score +inf as 0, because the top band's upper bound is exclusive | low (latent) | fixed `69b655b` |
| S11 | A cell with no wind value scores 15 (treated as 0 m/s) with no flag | low (latent; wind.tif has no gaps) | fixed `7107754` (neutral 40 plus a warning) |

### Hydro, DEM, mask

| # | Finding | Severity | Status |
|---|---|---|---|
| H1 | Drainage area was read at the single nearest stream point, which often misses the routed channel. 630 cells score 0 that should not (e.g. a 23 km² brook read as 0.03 km²) | high | fixed `0103ed0` |
| H2 | Head was max − min of unordered samples, so DEM noise counted as head (389 cells score from noise alone) | high-medium | fixed `78f2a0e` |
| H3 | The head window did not follow SPEC §3.1 (pairs 50–500 m apart, one end in the buffer) | medium | fixed `78f2a0e` |
| H4 | Drainage stopped at the study-area box. The LaHave enters at under 20 km² against ~1,250 km² at its gauge | medium | open: needs a DEM beyond the bbox (tasks.md) |
| H5 | Coastline and tidal shoreline lines were scored as streams | medium | fixed `1aa2d1c` |
| H6 | Flow was routed across the sea, so the routing check could never fail | low | fixed `c4dce58`; raster regenerated 2026-11-07 |
| H7 | Hydro dropped the DEM silently when a step failed | low | fixed `d6b8498` |
| H8 | Slope and aspect ignored DEM nodata, marking nodata as buildable | low (latent) | fixed `4ab50b0` |

Also found:
- The README map screenshots date from 2026-04-01.
- `output/` still holds June copies of `top_land_only.csv`, `severance_candidates.csv` and three other files that no current code writes.
- `data/processed/dem_filled.tif` and `flow_direction.tif` are 20 m files from an older code version.
