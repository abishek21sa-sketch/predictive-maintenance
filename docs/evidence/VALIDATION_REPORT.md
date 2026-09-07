# Validation Evidence Report — V1.0 Real Operations Release Candidate

## Release status

The locked benchmark/engineering core remains validated. Phase 5 adds a real operational MetroPT-3 path and maintenance-command workflow. The **complete real-data model evidence is intentionally pending the Windows full-source acceptance gate** because the raw 208 MB UCI dataset is not redistributed in this artifact.

## Automated regression

- **78 tests** collected and passing.
- Suite passes with **warnings treated as errors**.
- Python compilation passes across source/scripts.
- Inline JavaScript syntax passes for Observatory, Data Gateway, Real Operations Command, Shock Lab and Methodology.
- Public-release audit passes with no raw NASA or MetroPT source data packaged.

## Locked AI / IE / OR evidence

### NASA C-MAPSS FD001 benchmark

Bundled historical/public benchmark evidence:

- selected release model: Histogram Gradient Boosting;
- official-test RMSE: **19.24 cycles**;
- official-test MAE: **14.17 cycles**;
- official-test R²: **0.786**;
- age-only baseline RMSE: **33.34 cycles**.

This is simulated C-MAPSS benchmark evidence, not field-aircraft accuracy.

### Synthetic CI prognostics diagnostic

Current deterministic pipeline diagnostic:

- selected model: Histogram Gradient Boosting;
- RMSE: **6.0544**;
- age/time baseline RMSE: **8.4679**;
- R²: **0.9253**;
- evidence class: `VALIDATED_ON_SYNTHETIC_BENCHMARK`.

These metrics validate the training/evaluation machinery only.

### Reliability / Industrial Engineering

Current deterministic checks include:

- availability **0.92857**;
- Weibull shape **6.6845**;
- Weibull scale **139.4910**;
- maintenance-economics reference total **14,750**;
- executable FMEA/RCM/KPI arithmetic.

### Operations Research

Base maintenance MILP exact oracle:

- exact objective: **105,146.46839828514**;
- HiGHS MILP objective: **105,146.46839828514**.

Uncertainty-aware Maintain / Inspect / Defer portfolio exact oracle:

- exact enumeration objective: **21,057.176065097694**;
- MILP objective: **21,057.176065097694**.

Current deterministic portfolio evidence:

- status `OPTIMAL`;
- expected modeled cost **672,246.59**;
- CVaR(0.90) **688,951.11**;
- action mix **8 inspect / 4 maintain / 6 defer**.

The user's prior Windows V1 gate independently returned licensed Gurobi `OPTIMAL` with MIP gap `0.0` for the locked portfolio formulation.

### Simulation

The Shock Lab retains the three intended policies under common random numbers:

- optimized intervention;
- earliest-RUL heuristic;
- run-to-failure.

Outputs remain explicitly **SIMULATED**.

## Phase 5 — real operational path

### Source evidence design

MetroPT-3 is a separate evidence track. The implementation expects the complete UCI dataset with:

- 1,516,948 operational observations;
- 15 published sensor signals;
- railway Air Production Unit compressor telemetry;
- four published company failure windows.

The source row-level telemetry is unlabeled. No certified RUL is fabricated and no C-MAPSS RUL model is applied.

### Structural preflight completed in the packaged build

`artifacts/phase5_preflight.json` reports `PASS` for:

- parser compatibility against a six-row excerpt containing real published MetroPT source values;
- all 15 source signals preserved by the parser;
- zero missing values in that real excerpt;
- explicit `NOT_READY` when the complete local MetroPT runtime is absent;
- no synthetic substitute claim;
- dynamic `metropt3_real_operations` data mode;
- packaged `/operations` UI;
- Methodology separation between NASA benchmark and MetroPT real-data tracks;
- UCI DOI/source attribution;
- no raw MetroPT data packaged;
- full Phase-5 Windows acceptance script present.

Evidence class:

`STRUCTURAL_PREFLIGHT_ONLY_NOT_FULL_REAL_DATA_VALIDATION`.

### Full real-data acceptance still required

`scripts/phase5_acceptance.ps1` must run on the user's Windows machine against the complete UCI source before the project can claim full MetroPT end-to-end validation. The gate requires:

- strict complete-source validation;
- streaming feature-store construction;
- February-reference anomaly model;
- chronological failure-horizon development / validation / future holdout;
- baseline/candidate model comparison;
- future-holdout Average Precision above prevalence;
- all four published failure events retained;
- real sensor dossier reaching `/operations`;
- no fabricated RUL;
- disruption-sensitive maintenance decision;
- work-order commitment and lifecycle state;
- licensed Gurobi `OPTIMAL` acceptance on the real-data-derived APU case.

Only after that gate may the model evidence be labeled `VALIDATED_ON_REAL_OPERATIONAL_METROPT3_HOLDOUT`.

## Release/security audit

Current public-tree audit passes for:

- no detected API/private-key patterns;
- no forbidden secret/license files;
- no redistributed raw NASA source data;
- no redistributed raw MetroPT-3 source data;
- required release/Phase-5 documentation and scripts present;
- stale pre-V1 artifacts absent;
- `.env`, `.venv`, Gurobi licenses and external data ignored.

## Performance sanity

The locked 100-asset benchmark decision path and 80-replication stress test remain within their broad local sanity thresholds. These are not production SLAs.

## Exact artifact discipline

The Phase-5 candidate is accepted for delivery only after:

1. full warnings-as-errors regression;
2. all locked Phase 1–4/V1 diagnostics;
3. release/security audit;
4. Phase-5 structural preflight;
5. Python compilation;
6. frontend JavaScript syntax checks;
7. wheel build;
8. exact ZIP packaging;
9. clean extraction of that exact ZIP;
10. repetition of the build-time gates from the extraction;
11. isolated wheel installation and HTTP smoke;
12. user Windows full-source Phase-5 acceptance.
