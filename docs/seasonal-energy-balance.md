# Seasonal energy balance — hydro, solar, wind in Nova Scotia

Why a parcel with all three matters. This note captures the seasonal profiles of
the three renewable sources the tool scores, the data behind them, and how they
complement each other across the year. It's the reasoning behind weighting
hydro heavily and treating wind as a steadying bonus rather than a primary
driver.

## TL;DR

In Nova Scotia the three sources are **seasonally complementary**, which is what
makes a hydro + solar (+ wind) hybrid viable for year-round off-grid supply:

- **Hydro** is the winter heavy-lifter — it swings ~8× across the year and peaks
  in late winter/spring, exactly when solar is weakest.
- **Solar** is the summer workhorse — ~3× swing, opposite to hydro.
- **Wind** is a near-flat baseload (~1.4× swing) with a gentle winter lean; it
  smooths the shoulder seasons rather than surging to cover a deficit.

The residual weak spot for the combination is the **September–October shoulder**
(solar fading, hydro not yet recharged) — a battery + occasional generator's job.

## Monthly profiles (each as % of its own annual average)

| Month | Hydro | Solar | Wind |
|------:|------:|------:|-----:|
| Jan | 134 | 60 | 112 |
| Feb | 113 | 80 | 114 |
| Mar | 155 | 110 | 113 |
| Apr | **207** | 120 | 102 |
| May | 107 | 130 | 90 |
| Jun | 58 | 130 | 86 |
| Jul | 33 | 135 | **79** |
| Aug | **25** | 125 | 82 |
| Sep | 29 | 105 | 92 |
| Oct | 60 | 80 | 105 |
| Nov | 128 | 50 | 110 |
| Dec | 151 | **45** | 114 |
| **annual mean** | ~35 m³/s | — | ~5.2 m/s (50 m) |

Read it as: in the worst solar months (Dec ~45%, Jan ~60%), hydro is at
134–151% and wind ~112–114%. In peak solar (Jul ~135%), hydro is at its
weakest (33%) — but you don't need it then.

## Data sources & method

- **Hydro:** Water Survey of Canada **HYDAT station 01EF001 — LaHave River at
  West Northfield**, monthly mean discharge, 1915–2024 (110 years), via the
  ECCC GeoMet OGC API (`hydrometric-monthly-mean`). Station drainage ≈ 1,250 km².
  Long-term annual mean ≈ 35 m³/s.
- **Wind:** **NASA POWER** monthly climatology of 50 m wind speed (`WS50M`,
  community `RE`) at the study centroid (≈ 44.42 N, −64.50 W). Annual mean
  ≈ 5.2 m/s at 50 m.
- **Solar:** *approximate* typical Nova Scotia fixed-tilt PV monthly profile
  (winter ≈ half of summer). Not site-specific — illustrative of the shape only.

Charts for this analysis are generated interactively in-session; the tables
above are the durable record so the analysis is reproducible.

## Caveats (don't over-read these curves)

1. **Shape vs. magnitude (hydro).** The *seasonal shape* (snowmelt + rain
   regime) applies broadly to the candidate streams, but absolute flow scales
   with drainage area. **Small streams are flashier** — their Aug–Sep lows are
   harsher than the big LaHave's, and some may nearly stop. Size any micro-hydro
   on the dry-month flow, not the annual mean.
2. **The model already uses the summer low.** Hydro scoring uses a conservative
   low-flow specific runoff of **8 L/s/km²**, which this gauge confirms
   (8.7 m³/s ÷ 1,250 km² ≈ 7 L/s/km² in August). So hydro scores reflect the
   *worst* month; real winter output runs ~5–8× higher (Apr ÷ Aug ≈ 8.3×) —
   the candidates are even better in winter than their score implies.
3. **NASA POWER understates absolute wind.** POWER is a coarse (~50 km) 50 m
   product; its ~5.2 m/s here is lower than the Global Wind Atlas 100 m data the
   tool scores with (~7.2 m/s, 250 m). **Use GWA for "how windy is this parcel,"
   and the POWER curve only for "which months."**
4. **Solar curve is approximate** — a generic NS PV profile, for illustration.

## Implication for scoring

- Hydro carries the heaviest weight (40%) because it's the scarce,
  hard-to-replicate source *and* the one that covers the hard (winter) months.
- Wind is a modest, conditional contributor — steady and winter-leaning, but it
  doesn't swing enough to be a primary driver; scored from GWA, treated as a
  bonus.
- A parcel strong on all three (high `score_allrounder`) is genuinely
  well-positioned for year-round supply — that's the profile to prioritise.
