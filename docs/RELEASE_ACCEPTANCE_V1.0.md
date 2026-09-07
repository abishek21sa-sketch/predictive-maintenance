# Release Acceptance — V1.0

V1.0 uses two nested acceptance gates.

## 1. Stable engineering-core gate

`scripts/v1_acceptance.ps1` verifies the previously locked benchmark/engineering release:

1. clean Windows Python environment;
2. bounded dependencies;
3. warning-as-error regression tests;
4. Phase 1–4 engineering diagnostics;
5. V1 integration diagnostics;
6. secret/public-release audit;
7. performance sanity benchmark;
8. licensed Gurobi portfolio acceptance.

Required markers:

```text
GUROBI_V1_PASS
V1_RELEASE_ACCEPTANCE_PASS
```

## 2. Full real-operations gate

`scripts/phase5_acceptance.ps1` is required before the project is finally closed. It:

1. runs the complete warning-clean regression suite;
2. acquires the public UCI MetroPT-3 archive if the source is absent;
3. validates the complete real source (row count, all 15 sensor signals, timestamps, missing values, fingerprint/provenance);
4. builds the chunk-streamed feature store;
5. trains/evaluates the February-reference anomaly model and chronological 24-hour failure-horizon model;
6. requires a future holdout and a meaningful metric gate versus prevalence;
7. runs the Real Operations Command diagnostic;
8. verifies disruption sensitivity and the work-order closed loop;
9. verifies the real-data-derived APU decision on the licensed Gurobi runtime.

Required markers:

```text
METROPT3_REAL_DATA_PREPARATION_PASS
PHASE5_REAL_OPERATIONS_DIAGNOSTICS_PASS
GUROBI_PHASE5_REAL_DATA_PASS OPTIMAL ...
PHASE5_REAL_OPERATIONS_ACCEPTANCE_PASS
```

## Final browser acceptance

After the Phase-5 gate:

1. `/` — Reliability Observatory loads;
2. `/data-gateway` — generic external-data lifecycle loads;
3. `/operations` — evidence gate reports `REAL DATA READY`, real sensor history and model evidence appear, and published failure-event navigation works;
4. change `/operations` disruption scenario (for example baseline → compound) and verify the optimization evidence responds;
5. commit the selected intervention and verify the work order appears; optionally advance it to `IN_PROGRESS` / `COMPLETED` while recognizing those are application states;
6. `/stress-lab` — three benchmark policies still work;
7. `/methodology` — NASA benchmark and MetroPT real-operational evidence are visibly separated.

## External validation boundary

The Phase-5 gate is historical real-data validation and local decision-system validation. It is not prospective/live railway deployment, causal maintenance-effect validation, or proof of realized savings.
