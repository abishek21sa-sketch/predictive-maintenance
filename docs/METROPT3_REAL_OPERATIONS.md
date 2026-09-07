# MetroPT-3 Real Operations Evidence Track

## Purpose

MetroPT-3 is the platform's **real operational evidence track**. It is intentionally separate from the NASA C-MAPSS FD001 benchmark. C-MAPSS remains useful for reproducible RUL benchmarking, but its model and accuracy claims are never transferred to the MetroPT-3 compressor.

Source: UCI Machine Learning Repository dataset 791, DOI `10.24432/C5VW3R`, licensed CC BY 4.0. The source describes compressor Air Production Unit telemetry from a metro train in an operational context, with 1,516,948 observations and 15 analogue/digital sensor signals.

The raw third-party dataset is not committed to this repository. It is acquired locally by the acceptance/preparation workflow.

## Acquisition

Windows:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_metropt3.py --download
```

Portable Python:

```bash
python scripts/prepare_metropt3.py --download
```

The downloader retrieves the public UCI archive, extracts `MetroPT3(AirCompressor).csv`, and records source provenance. The raw CSV stays under `data/external/metropt3/`, which is gitignored.

The generated runtime evidence contains a root-bound SHA-256 manifest for the
derived feature store, failure-horizon model, anomaly model, and holdout
evidence. The runtime JSON itself is bound by a separate sidecar digest, so
changes to quality-gate or provenance fields are detected during loading. A
declared artifact or runtime-manifest mismatch is not eligible for production
use. This local digest is corruption/tamper detection evidence, not a signed
provenance system or an immutable artifact store.

## Full-source validation gate

Strict real-data preparation refuses promotion unless it can verify:

- exactly 1,516,948 source rows;
- required source index and timestamp fields;
- all 15 published sensor signals;
- no missing/non-numeric values in required source fields;
- monotonic source timestamps;
- expected source time range;
- source SHA-256 and file size;
- actual median timestamp interval derived from the data rather than assumed from metadata.

The UCI page contains inconsistent textual sampling-frequency descriptions in different sections. The implementation therefore derives timing from source timestamps and records the measured median interval instead of hard-coding a claimed frequency.

## Failure-event evidence

The row-level telemetry is unlabeled. UCI publishes company failure reports separately. The implementation preserves four reported high-stress air-leak windows:

| Event | Start | End | Evidence |
|---|---|---|---|
| F01 | 2020-04-18 00:00 | 2020-04-18 23:59 | published company failure report |
| F02 | 2020-05-29 23:30 | 2020-05-30 06:00 | published company failure report |
| F03 | 2020-06-05 10:00 | 2020-06-07 14:30 | published company failure report |
| F04 | 2020-07-15 14:30 | 2020-07-15 19:00 | published company failure report |

Derived labels such as `failure_within_24h` are calculated only from these published timestamps. They are **derived historical labels**, not source row labels and not certified RUL ground truth.

## Streaming feature store

The 200+ MB source is processed in chunks. Ten-minute engineering buckets are built from sufficient statistics so the application does not need to load 1.5 million rows into memory at once.

The feature store contains:

- mean / standard deviation / min / max for the 7 analogue signals;
- duty cycle for the 8 digital signals;
- pressure-gap and motor-load derived features;
- source-index range for provenance;
- published-failure active / 6-hour / 24-hour derived event context.

Chunk-boundary aggregation is combined mathematically rather than treating each chunk as an independent time series.

## Real-data AI design

### Anomaly model

An Isolation Forest learns only from the February reference period. Its alert threshold is the 99th percentile of the February reference anomaly score. This is a **reference-deviation signal**, not a calibrated failure probability.

### 24-hour failure-horizon model

The supervised target asks whether the compressor is within 24 hours before a published failure start. It does **not** claim a continuous certified RUL.

Candidate evaluation is strictly chronological:

- model development before 2020-06-01;
- June validation for candidate/threshold selection;
- July onward untouched holdout for final historical evaluation;
- no random row shuffle.

A simple logistic-regression model on analogue means is the baseline. Random
Forest and histogram gradient boosting on the complete engineering feature
vector are candidates, and the exploratory candidate lab also evaluates
unweighted and past-only regime-normalized variants. A candidate is promoted
only when the selected model clears every validation and future-holdout gate;
no candidate is marked promoted merely because it improves one metric or is
selected for investigation.

Metrics include Average Precision, Brier score, precision, recall, F1, ROC-AUC and prevalence. Threshold selection uses validation data, not the final holdout. The promotion gate also records event/block-clustered 95% bootstrap bounds: positive rows are resampled by published failure event and negative rows by contiguous 24-hour background block. Their lower/upper limits must support the same AP, ROC-AUC and Brier requirements before a split is considered sufficient; a split without at least two independent groups fails closed.


The Phase 5 diagnostic command reports `PASS` when this gate is evaluated
correctly and remains fail-closed; a blocked model gate is retained as a
`BLOCKED_EXTERNAL` enterprise lane rather than misclassified as a test-runner
failure.

Candidate investigations can be reproduced without changing runtime artifacts:

```powershell
.\.venv\Scripts\python.exe scripts\metropt_candidate_benchmark.py --root data\external\metropt3 --output artifacts\metropt_candidate_benchmark.json
```

For a bounded single-candidate experiment, add for example
`--only logistic_regime_normalized_unweighted`. This lab never writes model
artifacts; the latest evidence remains exploratory until the production
preparation path records a passing gate.

This command reports exploratory candidate metrics on the same partitions,
records a non-raw evidence artifact, and keeps the production promotion
decision in `prepare_metropt3.py`. The artifact is evidence of candidate
investigation, not a promotion approval.

Evidence class after successful full-data preparation:

`VALIDATED_ON_REAL_OPERATIONAL_METROPT3_HOLDOUT`

This remains **historical validation**, not prospective field validation.

## Decision coupling

The real-data model output feeds the same engineering decision stack rather than stopping at a probability card:

`OBSERVED telemetry → PREDICTED 24h failure risk → CALCULATED maintenance consequence → OPTIMIZED intervention/timing → COMMITTED work order`

The optimizer receives a risk-equivalent scheduling deadline derived from the 24-hour probability. The API and UI explicitly label this as a **decision transform, not RUL**.

Because MetroPT-3 does not publish a complete maintenance-shop resource roster, the following inputs are explicit scenario assumptions:

- preventive / failure / downtime economics;
- maintenance bays;
- technician skill availability;
- service and inspection kits;
- three shop-backlog jobs used to create resource contention.

They are labeled `ASSUMED_PLANNING_CONTEXT` / `ASSUMED_SHOP_BACKLOG` and never presented as observed MetroPT fields.

## Operations Command workspace

`/operations` provides:

- source-readiness/evidence gate;
- published failure-event navigation;
- real historical sensor dossier;
- real-data model and anomaly evidence;
- Maintain / Inspect / Defer alternatives;
- Gurobi-selected action and timing;
- resource board;
- disruption re-solves for bay outage, technician absence, part delay, duration inflation and compound disruption;
- decision trace;
- work-order commitment and operator-recorded lifecycle state.

A work-order status transition is application state. Marking a work order `IN_PROGRESS` or `COMPLETED` does not assert that the corresponding historical MetroPT maintenance event occurred.

## Claim boundary

The platform may claim, after the full acceptance gate passes:

- the raw dataset is genuine MetroPT-3 operational telemetry;
- source integrity and schema were locally validated;
- a time-aware model was historically evaluated on an untouched future holdout;
- the model output materially feeds maintenance optimization;
- the intervention model is resource-feasible under its stated assumptions;
- work-order state is auditable application state.

It may not claim:

- certified compressor RUL;
- live railway deployment;
- causal effect of the recommended intervention;
- realized downtime/cost savings;
- that assumed shop resources or economics were observed in MetroPT-3.
