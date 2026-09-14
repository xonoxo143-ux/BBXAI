# PBH satellite anomaly scan

Branch-local research experiment for looking for impulsive, unexplained orbital perturbations that could be consistent with a compact dark object flyby.

## First automated target

The first pass uses the public LAGEOS/LAGEOS II supporting dataset from Zenodo record `18441938`, released with the 2026 SaToR-G Lorentz-invariance analysis.

LAGEOS is useful because it is passive: no thrusters, fuel, heaters, or active attitude control. That removes many false-positive mechanisms present in deep-space probes.

## What the scanner does

For every numeric residual series it can parse, it ranks:

- robust value outliers;
- unusually large one-step changes;
- local mean change-points over several window sizes;
- same-epoch coincidences across independent files/channels.

The output is a ranked candidate list, not a physical detection claim.

## Critical limitation

The published LAGEOS residuals were produced from independent seven-day orbit arcs with separately fitted initial state vectors. A true instantaneous velocity kick near an arc boundary can therefore be absorbed into the next arc's fitted starting state.

This scan is useful for identifying odd arcs and building the anomaly-vetting machinery. A decisive PBH search ultimately needs raw SLR normal-point data reprocessed with a continuous trajectory model that does not permit unexplained state-vector resets.

## Run

```bash
python scan_zenodo.py --record 18441938 --workdir work --outdir output
```

The GitHub Actions workflow runs the same scan and uploads `output/` as an artifact.
