# Validation Protocol — V1.0

## Data and readiness

External inputs are validated before persistence. Tests cover required identifiers, alias mapping, duplicate key rejection, temporal ordering, non-negative engineering fields, asset referential integrity, maintenance chronology, numeric sensor availability, content fingerprints, SQL credential redaction and future-blind replay.

Readiness is assessed per capability rather than with a single marketing score. Incompatible data is allowed to be accepted while an unsupported analytical capability remains `NOT_READY`.

## Artificial Intelligence

RUL validation uses complete asset IDs rather than random rows to prevent trajectory leakage. Candidate models are compared against a simple age/time baseline. Metrics include MAE, RMSE and R²; the NASA benchmark also records the asymmetric RUL score. External-model metrics apply only to the dataset/split recorded in that model manifest.

Empirical residual intervals are reported as holdout dispersion rather than formal guaranteed coverage.

## Reliability / Industrial Engineering

Analytical tests independently verify availability closure, Weibull hazard boundaries, maintenance-economics closure, FMEA/RCM mapping and maintenance KPI arithmetic. Units and assumptions are documented in `TECHNICAL_METHODS.md`.

## Operations Research

Validation requires:

- solver status interpreted explicitly;
- every selected intervention obeys allowed action domains;
- critical assets are protected from silent deferral;
- multi-cycle bay occupancy is physically feasible;
- labor, skill and parts constraints hold;
- robust capacity profiles hold simultaneously;
- CVaR is not below expected cost for the same scenario portfolio;
- tiny instances match independent exact enumeration.

Gurobi is the primary licensed solver. SciPy/HiGHS provides a reproducible CI path.

## Simulation

Monte Carlo comparisons use deterministic seeds and common random numbers across the optimized, earliest-RUL and run-to-failure policies. The test suite verifies reproducibility and the complete three-policy set.

## System / release

V1.0 acceptance requires:

1. tests pass with warnings treated as errors;
2. Python source compiles;
3. all engineering diagnostic scripts pass;
4. release/security audit passes;
5. performance sanity benchmark completes within broad engineering thresholds;
6. final wheel builds and clean-installs;
7. the exact release ZIP is clean-extracted and retested;
8. packaged UI/API endpoints return expected evidence;
9. licensed Windows Gurobi acceptance passes before project closure.

## MetroPT-3 full real-data acceptance

Real operational validation is a separate gate from structural CI tests. `scripts/phase5_acceptance.ps1` must use the complete UCI MetroPT-3 source and verify:

1. strict source row count, required 15 sensor signals, timestamps and no missing required values;
2. local source/content fingerprinting and provenance;
3. streaming feature-store construction;
4. chronological train/validation/future-holdout partitions with `shuffle=false`, event/block-clustered 95% bootstrap bounds supporting the supervised metric thresholds, and at least two independent failure windows represented in each evaluation period;
5. a simple supervised baseline and candidate comparison;
6. validation and holdout Average Precision compared with prevalence, ROC-AUC compared with chance, and Brier loss compared with a constant-prevalence predictor rather than accuracy alone;
7. February-only reference anomaly training;
8. all four published failure windows retained in the runtime evidence;
9. no fabricated MetroPT RUL and no transfer of FD001 benchmark accuracy;
10. real historical sensor evidence reaches the Operations Command case;
11. the intervention optimizer reaches an explicit optimal/feasible solver status;
12. resource disruptions materially alter the planning context/decision evidence;
13. work-order commitment closes the application workflow while remaining clearly labeled application state;
14. `/operations`, real-data status APIs and methodology evidence load;
15. the real-data-derived decision solves through the user's licensed Gurobi runtime.

The small structural fixture in the automated test suite validates implementation behavior only. The six-row MetroPT excerpt validates the parser against real source values only. Neither is reported as full real-data model evidence.
