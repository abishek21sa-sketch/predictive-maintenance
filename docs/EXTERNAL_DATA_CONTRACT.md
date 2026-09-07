# External Data Contract and Readiness Gate

## Purpose

The external-data layer exists to prevent a common predictive-maintenance failure mode: accepting arbitrary industrial data and silently applying a model trained for a different asset, sensor contract, operating regime or failure definition.

The architecture is:

`source → adapter → canonical schema → validation/readiness gate → versioned dataset → analytical engines`

Successful ingestion means **the data contract was accepted**. It does not mean every analytical capability is valid.

## Source modes

### Browser text ingestion

Supported: CSV, JSON, JSONL and NDJSON.

The browser reads the local file and sends its textual content to `/api/data/preview/text` and, after explicit acceptance, `/api/data/ingest/text`.

### Server-side file ingestion

Supported: CSV, JSON, JSONL, NDJSON and Parquet. Parquet requires the optional `parquet` dependency group.

The server-side path endpoint is intended for controlled local/server environments, not arbitrary public file access.

### SQL ingestion

SQL sources are read through SQLAlchemy. A table or explicit query may be supplied. Database-specific drivers are external integration requirements.

Connection URLs are **not persisted** in the dataset manifest. The manifest records only the source mode, entity type, table name where supplied, and whether a query was used.

### Historical replay

Accepted telemetry can be replayed only up to a requested cycle or timestamp. This provides a deterministic boundary for future-blind scenario reconstruction and testing.

## Canonical entities

### Asset master

Minimum:

- `asset_id`

Recommended:

- `asset_type`
- `criticality`
- `status`
- `commissioned_at`

### Telemetry

Minimum:

- `asset_id`
- `cycle` **or** `timestamp`
- at least one numeric condition/sensor signal

Optional:

- `rul` — only when genuine remaining-useful-life ground truth exists
- operating context / settings
- additional numeric condition features

Rules:

- `(asset_id, cycle)` or `(asset_id, timestamp)` must be unique.
- the time axis must increase strictly within an asset.
- blank/null asset identifiers are rejected.
- cycles must be non-negative numeric values.
- timestamps must parse successfully.
- telemetry with no usable numeric signal is rejected.

### Maintenance history

Minimum:

- `asset_id`
- `event_type`

Recommended:

- `start_time`
- `end_time`
- `downtime_hours`
- `labor_hours`
- `part`
- `cost`
- `failure`

Rules:

- negative cost/labor/downtime is rejected.
- end time cannot precede start time.
- maintenance references are checked against the asset master when one is supplied.

## Mapping

The gateway performs conservative alias inference for common source names such as:

- `machine_id → asset_id`
- `operating_cycle → cycle`
- `remaining_useful_life → rul`
- `maintenance_type → event_type`

Users can supply explicit canonical-to-source mappings when source terminology differs.

Missing fields are not fabricated merely to satisfy a downstream model.

## Readiness capabilities

The readiness engine evaluates capabilities independently.

### Asset registry

`READY` when an asset master or telemetry asset identifiers are available.

### Condition monitoring

`READY` when numeric condition/sensor signals are available.

### Bundled FD001 RUL inference

`READY` only when the accepted telemetry satisfies the complete bundled FD001 input contract:

- cycle
- 3 operating settings
- 21 C-MAPSS sensor fields

Otherwise `NOT_READY` with explicit missing fields.

### External RUL training

`READY` only when:

- an explicit `rul` target is supplied, and
- at least six independent asset trajectories exist, reserving at least two
  complete trajectories for validation and retaining at least four for training.

No RUL target is derived from undocumented assumptions.

### Maintenance KPIs / failure reliability

Readiness depends on available event timing/downtime and failure labels.

### Maintenance optimization

Can be `READY_WITH_ASSUMPTIONS` when the condition state exists but site-specific labor/parts/criticality inputs require planner assumptions.

## Dataset versioning and evidence

Accepted datasets receive a content-derived fingerprint and dataset ID. Canonical entities are persisted separately under `data/external/<dataset_id>/` and a manifest records:

- dataset ID
- content fingerprint
- source mode
- per-entity row/column counts
- per-entity hashes
- readiness results
- evidence boundary

The canonical persisted interchange is CSV so the core repository does not require a binary columnar engine merely to reopen accepted data. Parquet remains supported as an input format when its optional engine is installed.

## External model lifecycle

When external RUL training is ready:

1. telemetry is sorted by asset and cycle/time;
2. numeric condition features are detected;
3. leakage-safe historical deltas/rolling statistics are generated;
4. target-like fields such as failure-cycle/time-to-failure columns are excluded from model features;
5. complete assets, not random rows, are held out for validation;
6. a simple age/time Ridge baseline is fitted;
7. Ridge, Random Forest and Histogram Gradient Boosting candidates are evaluated;
8. the lowest-RMSE candidate is selected;
9. empirical 90% residual dispersion is recorded;
10. the explicit versioned promotion gate records RMSE/MAE baseline comparisons,
    independent validation-asset coverage, split disjointness and target-leakage
    exclusion;
11. the selected algorithm is refit on the accepted dataset and persisted with a
    model manifest; the registry rechecks the manifest and artifact hashes before
    human promotion.

The evidence class is:

`VALIDATED_ON_USER_SUPPLIED_HOLDOUT`

This does not imply prospective field validation.

## Evidence boundaries

The platform distinguishes:

- **BENCHMARK VALIDATION** — NASA C-MAPSS evidence
- **USER-SUPPLIED HOLDOUT VALIDATION** — model evaluated on held-out assets from an accepted external dataset
- **HISTORICAL REPLAY** — reconstruction using only data up to a selected point
- **PREDICTED** — model output
- **CALCULATED** — engineering arithmetic/reliability output
- **OPTIMIZED** — solver-selected feasible action
- **SIMULATED** — stochastic modeled consequence

None of these is automatically equivalent to a realized plant benefit.

## MetroPT-3 real operational specialization

MetroPT-3 does not pass through the generic RUL-training contract because its row-level telemetry does not contain a certified `rul` target. Its dedicated adapter preserves all 15 published sensor signals, validates source timing/content, builds a streaming feature store, and derives historical failure-horizon labels only from the failure windows published with the dataset.

The MetroPT readiness state is exposed separately as `metropt3_real_operations` through `/api/data/modes`:

- `NOT_READY` until the complete local UCI preparation pipeline has passed;
- `READY` only after strict source validation, feature generation, model training/evaluation and runtime evidence generation.

No synthetic fixture can promote this production readiness state. See `docs/METROPT3_REAL_OPERATIONS.md`.
