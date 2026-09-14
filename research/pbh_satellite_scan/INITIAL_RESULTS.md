# Initial LAGEOS anomaly scan results

Date of scan: 2026-09-13/14 UTC

This note preserves the first actual results from the `pbh-satellite-scan` branch. Nothing here is a primordial-black-hole detection. The goal is to identify epochs worth reprocessing from lower-level Satellite Laser Ranging (SLR) observations.

## Data scanned

The first automated pass uses the public `O_LG1` and `O_LG2` supporting observables from Zenodo record `18441938`, released with the 2026 SaToR-G LAGEOS/LAGEOS-II analysis. Each series contains 1,474 seven-day samples spanning roughly three decades.

The first column is MJD at seven-day spacing. The second is the published LAGEOS observable residual series used by the paper. The scanner ranks robust value outliers, one-step changes, and local change-points.

Important limitation: the source orbit determination divides the data into independent seven-day arcs and fits a new initial state vector for every arc. Therefore a genuine instantaneous velocity impulse can be partly or completely absorbed by a later arc's fitted initial state. These residuals are a candidate-finding layer, not a decisive impulse test.

## Strongest impulse-like candidate

### LAGEOS I — early February 2018

The most striking one-step event in the full LAGEOS-I series occurs across the samples:

- MJD 58144 (2018-01-26): +3.7208
- MJD 58151 (2018-02-02): -27.7576
- MJD 58158 (2018-02-09): +15.3524
- MJD 58165 (2018-02-16): +5.7332

The one-week change from MJD 58151 to 58158 is +43.1100 in the published observable units. It is the largest absolute one-week change in the entire LAGEOS-I series. The preceding change, -31.4784, is the second largest.

The automated robust scores are:

- 2018-02-02: value-z 8.12; first-difference z 5.89
- 2018-02-09: first-difference z 8.04; local change-point score 7.80; combined ranking score 9.75

LAGEOS II does not show a corresponding large excursion in the same weeks. Its values are +6.5704 on MJD 58151 and +5.8684 on MJD 58158.

This makes the event interesting for a *single-satellite* close-encounter search, but it also makes common Earth-orientation/tracking-network disturbances less compelling as an explanation.

### Initial mundane checks

- The apparent February-2018 start dates seen in some EDC pages refer to full-rate submissions, not the normal-point data used in precise orbit determination. Normal-point tracking from Mendeleevo-2 and Irkutsk predates 2018, so a newly-added station is not currently a valid explanation for the spike.
- NOAA space-weather reports describe 2 February 2018 as geomagnetically quiet and 9 February as quiet-to-unsettled. There was no major geomagnetic storm coincident with the event.
- LAGEOS is passive, so there is no maneuver, thruster firing, fuel leak, heater state, or commanded attitude event to invoke.

Still to test before this event can be called unexplained:

- Earth-shadow/eclipse geometry and thermal recoil;
- station-specific range/time biases during the relevant passes;
- data gaps or unusual station weighting;
- agreement between independent orbit-determination solutions;
- raw normal-point residuals within each seven-day window;
- whether a continuous trajectory with a free impulsive delta-v fits significantly better than the standard disconnected-arc solution.

## Other high-ranking structures

### LAGEOS I — September/October 2004

A multi-week structure appears around MJD 53251-53279:

- 2004-09-03: -16.4680
- 2004-09-10: +4.5848
- 2004-09-17: +11.1080
- 2004-09-24: +18.9704
- 2004-10-01: -11.2372

The final one-week change is -30.2076, the third-largest absolute one-week change in the LAGEOS-I series. Because the disturbance persists over several arcs, it is less cleanly impulse-shaped than the 2018 event and is especially susceptible to slowly varying thermal/force-model errors.

### Cross-satellite same-epoch hit — MJD 50451 (1997-01-03)

This is the only exact-epoch event above the scanner's score threshold in both files:

- LAGEOS I: +5.4272
- LAGEOS II: -16.6028

A common epoch in both satellites is interesting, but it is also more vulnerable to a common orbit-model, Earth-orientation, or network systematic. It is therefore not automatically stronger evidence for a compact flyby.

## Current ranking for follow-up

1. **LAGEOS I, 2018-02-02 to 2018-02-09** — highest priority. Largest one-week jump in ~30 years and no corresponding LAGEOS-II spike.
2. **LAGEOS I, 2004-09-03 to 2004-10-01** — strong multi-week excursion, probably more vulnerable to slowly varying force-model effects.
3. **LAGEOS I + II, 1997-01-03** — cross-satellite exact-epoch anomaly; useful as a test of common-mode systematics.
4. **LAGEOS II, 2010-01-08** — one of the stronger LAGEOS-II one-step events and worth a separate pass.

## Next analysis layer

The next useful step is not simply another outlier detector. It is to retrieve the raw ILRS normal points and independent precise-orbit products around the top epochs, then test a continuous trajectory with and without a free instantaneous velocity impulse. A candidate should only survive if it improves independent station data and independent orbit solutions without coinciding with eclipse/thermal transitions, station biases, data gaps, or known geophysical-model errors.
