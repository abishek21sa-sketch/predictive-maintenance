# API Contract

FastAPI exposes interactive OpenAPI documentation at `/docs`.

## Platform / benchmark

- `GET /api/health` — service liveness/version.
- `GET /api/health/ready` — bounded runtime readiness for container
  orchestration. In managed mode it checks the configured PostgreSQL adapter;
  it does not certify production deployment. Set
  `PDM_REQUIRE_PRODUCTION_READINESS=1` in a strict deployment to make this
endpoint return `503` until the structured production-readiness gate passes.
Setting `PDM_AUTH_MODE=production` enables that strict readiness behavior
automatically; `PDM_REQUIRE_PRODUCTION_READINESS=1` remains available for
non-production environments that need the same gate during deployment review.
- `GET /api/portfolio` — bundled release metrics, reliability and simulation evidence.
- `GET /api/assets` — bundled fleet snapshot.
- `GET /api/decisions` — bundled explainable decisions.
- `POST /api/predict` — bundled C-MAPSS-format runtime scoring.

## Reliability / decision engineering

- `GET /api/reliability/fmea`
- `POST /api/reliability/kpis`
- `POST /api/optimize`
- `POST /api/optimize/stochastic`
- `GET /api/casebook`
- `GET /api/assets/{asset_id}/dossier`
- `GET /api/assets/{asset_id}/intervention-options`
- `GET /api/commitment-ledger`
- `GET /api/planner/horizon`
- `GET /api/planner/scenarios`

## External data gateway

### Discover modes

- `GET /api/data/modes`

### Browser text lifecycle

- `POST /api/data/preview/text` — map/validate/profile without persisting.
- `POST /api/data/ingest/text` — accept and version validated CSV/JSON/JSONL text inputs.

Request bundle entities are `assets`, `telemetry`, and `maintenance`; telemetry is required.

### Server-side file lifecycle

- `POST /api/data/ingest/path`

Supports CSV/JSON and optional Parquet when the Parquet extra is installed.

### SQL lifecycle

- `POST /api/data/ingest/sql`

Reads tables/queries using SQLAlchemy. Connection URLs are not saved to manifests.

### Dataset catalog/readiness/replay

- `GET /api/data/datasets`
- `GET /api/data/datasets/{dataset_id}/readiness`
- `GET /api/data/datasets/{dataset_id}/replay?upto=...&limit=...`

### Model compatibility

- `POST /api/data/datasets/{dataset_id}/predict/fd001`

This endpoint refuses an external dataset unless the complete bundled FD001 feature contract is satisfied.

## External model lifecycle

- `POST /api/models/external/rul/train`
- `GET /api/models/external`
- `POST /api/models/external/rul/predict`
- `GET /api/models/registry` — returns candidate/approved/retired model lifecycle records.
- `POST /api/models/registry/{model_id}/promote` — requires approval permission,
  a passed validation gate, and a human rationale; it never executes scoring.

External RUL training requires explicit RUL ground truth and at least six independent asset trajectories. The training gate reserves at least two complete trajectories for validation, requires the selected model to beat the recorded baseline on RMSE and MAE, and records an explicit human-promotion boundary. Metrics belong to that external model/dataset evidence stream only.

## Registry / governance / monitoring

- `GET /api/registry/assets`
- `POST /api/registry/assets`
- `POST /api/monitoring/drift`
- `GET /api/health/production-readiness` — returns the fail-closed,
  non-secret deployment prerequisite report. `NOT_READY` is expected for the
  local/reference configuration; `READY_FOR_DEPLOYMENT_REVIEW` still is not a
  production certification. The report includes a separate
  `prospective_field_validation` gate; historical, benchmark, synthetic, and
  local-smoke evidence cannot satisfy it.
- `GET /api/audit` — requires the `auditor` or `admin` role when identity is configured.
- `GET /api/audit/verify` — validates the append-only hash chain.
- `GET /api/audit/export` — exports verified chronological JSONL with chain metadata.

In local development, the explicit local-admin principal is used. In
production, set `PDM_AUTH_MODE=production` and configure named principals with
`PDM_API_KEYS` using `subject:role:key`; all API routes require a valid
`X-API-Key` except the non-sensitive `/api/health` and `/api/health/ready`
liveness probes. Requests fail closed when identity is missing or invalid.

For `PDM_STATE_BACKEND=managed`, the application does not auto-create or alter
state tables during API traffic. Run `scripts/managed_state_migrate.py` first;
otherwise stateful requests fail closed with
`MANAGED_STATE_RUNTIME_UNAVAILABLE` and the migration-readiness reason.
Managed container readiness also returns `503` until the explicitly applied
schema migration, migration ledger, and append-only audit guards are verified;
the application does not treat database connectivity alone as readiness.

Set `PDM_REQUIRE_APPROVED_MODELS=1` in a protected deployment to prevent
candidate external models from scoring before human promotion.

## Phase 5 — Real Operations Command

### `GET /api/real-data/metropt/status`
Returns separate source, artifact-integrity, and model readiness for the local MetroPT-3 track. `source_status=READY` means the complete local preparation pipeline generated runtime evidence and its declared artifacts verify by SHA-256. `status=READY` and `model_status=PROMOTION_READY` additionally require the supervised validation and future-holdout Average Precision, ROC-AUC, calibration, event/block-clustered 95% bootstrap-bound, and independent-event quality gates. A prepared dataset with a failed quality gate returns `MODEL_GATE_BLOCKED`; an artifact mismatch returns `RUNTIME_INTEGRITY_BLOCKED`. Both remain unavailable for production model approval. When absent, the endpoint returns `NOT_READY` and an explicit preparation command; no synthetic data is substituted.

### `GET /api/operations/metropt/case`
Query parameters:

- `at` — optional historical timestamp;
- `scenario` — `baseline`, `bay_outage`, `technician_absent`, `part_delay`, `duration_plus_50`, or `compound_disruption`;
- `bays` — maintenance-bay assumption.

Returns observed historical sensor evidence, predicted 24-hour failure-horizon risk, anomaly evidence, assumed planning economics/resources, intervention alternatives, Gurobi/HiGHS decision evidence and current work-order state.

When `PDM_AUTH_MODE=production` (or
`PDM_REQUIRE_PROMOTED_REAL_DATA_MODEL=1`), the endpoint returns HTTP 409 until
the real-data supervised model promotion gate passes. Historical/testing mode
remains explicitly labeled and cannot be used as a production approval.

### `POST /api/operations/metropt/work-orders/commit`
Commits the currently re-solved intervention decision to the local work-order ledger with its decision trace. This is application state, not evidence that historical field maintenance occurred.

Production mode rejects the commit with HTTP 409 while the real-data model
promotion gate is blocked.

### `GET /api/operations/work-orders`
Lists committed work orders.

### `PATCH /api/operations/work-orders/{work_order_id}/status`
Advances an application work order through valid lifecycle transitions. Supported states are `COMMITTED`, `IN_PROGRESS`, `COMPLETED`, and `CANCELLED`. Invalid transitions return HTTP 422.

## CMMS / ERP exchange boundary

- `GET /api/integrations/cmms/work-orders?source_system=...` — reads the
  canonical external work-order mirror.
- `POST /api/integrations/cmms/reconcile` — reconciles a batch using the
  `CMMS-WORK-ORDER.v1` contract. The request contains `source_system` and a
  `records` array. The endpoint is idempotent for replayed fingerprints,
  updates only strictly newer revisions, reports stale/conflicting records,
  and links each batch to the audit chain.
- `POST /api/integrations/cmms/export` — emits deterministic contract-only
  JSONL. The response includes `X-Contract-Version`, `X-Exchange-Batch-ID`,
  `X-Exchange-Content-SHA256`, and `X-Audit-Event-ID`; unknown input fields
  are excluded.
- `GET /api/integrations/cmms/inventory?source_system=...` — reads the
  canonical inventory mirror.
- `POST /api/integrations/cmms/inventory/reconcile` — reconciles
  `CMMS-INVENTORY.v1` positions with deterministic replay and revision rules.
- `POST /api/integrations/cmms/inventory/export` — emits safe deterministic
  inventory JSONL with the same exchange, content-digest, and audit headers.

These records represent external exchange state. They do not prove that a
technician performed maintenance in the field.
