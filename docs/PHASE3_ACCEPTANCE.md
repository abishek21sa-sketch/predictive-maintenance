# Phase 3 Acceptance — Real Data & Model Lifecycle

**Patch release:** `0.5.1` closes the diagnostic SQLite source connection explicitly and verifies the SQL source file can be deleted immediately before temporary-directory cleanup. This fixes the Windows `WinError 32` acceptance failure observed in `0.5.0`.

## Scope

Phase 3 extends the validated predictive-maintenance computational core with a real/external data lifecycle.

### Implemented

- browser CSV/JSON/JSONL ingestion
- server-side file ingestion
- optional Parquet ingestion
- SQLAlchemy database ingestion
- canonical asset/telemetry/maintenance schemas
- alias inference and explicit field mapping
- preview-before-persist validation
- content-addressed dataset versioning
- dataset manifest and provenance boundary
- capability-specific readiness gate
- historical replay by cycle or timestamp
- external RUL training with asset-level holdout
- baseline comparison
- persisted external model artifacts/metadata
- external RUL inference
- target-like leakage-field exclusion
- Data Gateway frontend
- sample data-contract templates

### Explicitly not claimed

- universal RUL compatibility
- production live-stream connector for a specific site
- prospective field accuracy
- realized maintenance savings

## Automated gate

Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\phase3_acceptance.ps1
```

The script:

1. resolves an installed supported Python 3.12+ runtime (3.13 is preferred);
2. creates/repairs `.venv`;
3. installs dev + Gurobi dependencies;
4. runs the complete test suite;
5. reruns Phase 1 diagnostics;
6. reruns Phase 2 optimization diagnostics;
7. reruns Observatory diagnostics;
8. runs Phase 3 external-data diagnostics;
9. verifies the licensed Gurobi planner path.

## Phase 3 diagnostic evidence

`scripts/phase3_diagnostics.py` creates a deterministic synthetic generic pump fleet and verifies:

- canonical ingestion
- condition-monitoring readiness
- bundled FD001 model rejection for incompatible schema
- external-training readiness
- content fingerprinting
- evidence-boundary persistence
- future-blind historical replay
- asset-level model holdout
- external model vs simple baseline
- external inference
- uncertainty interval output
- SQL ingestion
- Data Gateway page/API availability

This diagnostic is labeled **SYNTHETIC VALIDATION FOR EXTERNAL DATA LIFECYCLE**.

The diagnostic model metrics are not real plant performance claims.
