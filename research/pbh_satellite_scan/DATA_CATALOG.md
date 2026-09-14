# Data catalog strategy

This branch uses GitHub Actions as the batch-compute and catalog layer for the PBH/satellite search.

## Principle

Raw and intermediate datasets can be large. They do not all need to be inspected manually. Each analysis job should:

1. download or restore the public source data;
2. normalize it into a machine-readable table;
3. run the relevant anomaly/control tests;
4. emit detailed native outputs;
5. feed the important rows into `catalog_results.py`;
6. upload the detailed outputs as a GitHub Actions artifact;
7. expose a compact Markdown summary for human review.

## Current datasets

### Zenodo 18441938 — LAGEOS/LAGEOS-II observable series

- Native files: `O_LG1`, `O_LG2`
- Scope: ~30 years, seven-day cadence
- Scanner: `scan_lageos.py`
- Native output: `output/lageos_candidates.csv`
- Catalog event type: `published_residual_anomaly`

### ILRS v70 weekly SP3 precise-orbit products

- Satellites: LAGEOS I and II
- Centers currently used in the broad exact-boundary control: ASI, DGFI, ESA, GFZ
- Focused comparison additionally supports BKG, ILRSA, ILRSB, JCET and NSGF
- Scanner: `scan_2018_boundaries.py`
- Native output: `output/boundary_scan_2018/boundary_consensus.csv`
- Catalog event type: `weekly_orbit_boundary_discontinuity`

### ILRS CRD normal-point observations

- Current focused interval: Jan-Mar 2018
- Probe: `probe_ilrs_2018.py`
- Purpose: station/pass/normal-point coverage and network-composition checks
- These are supporting measurements rather than standalone anomaly scores.

### February 2018 candidate geometry

- Target: LAGEOS I around the 2018-02-04 weekly orbit boundary
- Analyzer: `analyze_feb2018_candidate.py`
- Tests: RTN decomposition, center-direction agreement, Earth-umbra clearance
- Catalog event type: `targeted_candidate_geometry`

## Catalog outputs

`catalog_results.py` produces:

- `output/catalog/events.csv`
- `output/catalog/events.json`
- `output/catalog/CATALOG_SUMMARY.md`

Every normalized event gets a stable hash ID, dataset provenance, satellite, epoch, metric, value, number of independent sources, status and native source-file pointer.

Scores from physically different datasets are **not** collapsed into one universal score. A robust residual z-score and a position discontinuity in metres are fundamentally different measurements. Ranking is therefore done inside each dataset/event type, while corroboration across independent datasets is tracked explicitly.

## Intended expansion

The same catalog can accept additional long-lived spacecraft and tracking systems later: more SLR satellites, GNSS precise orbits, deep-space Doppler/range residuals, planetary ephemeris residuals, pulsar timing, or other public precision-trajectory products. Each new source only needs a small adapter that emits the common event schema.
