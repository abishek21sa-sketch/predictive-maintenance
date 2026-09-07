# NASA C-MAPSS Multi-Regime Validation Record

## Scope

This evidence track evaluates the same leakage-safe RUL pipeline separately on
FD001, FD002, FD003, and FD004. NASA C-MAPSS is simulated benchmark data; the
results are not transferred to MetroPT-3 or field equipment.

## Reproduction

The raw archive must be acquired locally and is excluded from release artifacts.

```powershell
Set-Location "<repository-root>"
& ".\.venv\Scripts\python.exe" scripts\benchmark_cmapss_regimes.py `
  --data-dir data\raw\CMAPSSData `
  --out artifacts\reports\cmapss_regime_benchmark.json
```

Each regime performs model selection using complete-engine holdout data, then
scores its untouched official test engines. No row-level random split is used,
and metrics are not pooled across regimes for model promotion.

The generated report records train/test row counts, independent engine counts,
model choice, MAE/RMSE/R²/NASA score, asset-grouped bootstrap intervals,
empirical RUL-band coverage, and source checksums.

## Boundary

FD001–FD004 broaden public simulated benchmark coverage across operating
conditions and fault regimes. They do not establish MetroPT failure-horizon
quality, prospective field benefit, causal safety, or production readiness.
