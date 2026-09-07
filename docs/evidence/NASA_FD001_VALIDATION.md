# NASA C-MAPSS FD001 Validation Record

## Scope

This is a public benchmark evidence track for remaining-useful-life (RUL)
regression. NASA describes C-MAPSS as multivariate engine trajectories with
separate training and test engines, run-to-failure training histories, and
provided test RUL values. It is simulated data and is not field evidence.

Source: <https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data>

The raw archive is acquired locally under `data/raw/CMAPSSData/` and is
excluded from release artifacts. The benchmark report records input SHA-256
checksums and the selected model's evidence.

## Reproduction

```powershell
Set-Location "<repository-root>"
& ".\.venv\Scripts\python.exe" scripts\benchmark_fd001.py `
  --data-dir data\raw\CMAPSSData `
  --out artifacts\reports\fd001_benchmark.json
```

The training procedure holds out complete engine trajectories for model
selection, then refits the selected candidate on all training engines before
scoring the official test endpoints. No row-level random split is used.

## Current local result

The current run used 20,631 training rows across 100 training engines and
13,096 test rows across 100 test engines. Histogram gradient boosting was
selected from the candidate set.

| Metric | Selected model | Age-only baseline |
|---|---:|---:|
| MAE (cycles) | 14.173 | 27.585 |
| RMSE (cycles) | 19.237 | 33.335 |
| R² | 0.786 | 0.356 |
| NASA asymmetric score | 735.933 | 5,318.921 |

The report also contains asset-grouped 95% bootstrap intervals for test
metrics and empirical RUL-band coverage. The intervals quantify uncertainty
across independent engine trajectories; they do not certify calibrated
coverage, field reliability, causal benefit, or production readiness.

## Boundary

This result validates the implementation on a public simulated benchmark. It
does not transfer FD001 accuracy to MetroPT-3, another machine, or a plant.
MetroPT-3 remains a separate real-data track and its failure-horizon model
must pass its own chronological, calibration, confidence-bound, and
independent-event gates before promotion.
