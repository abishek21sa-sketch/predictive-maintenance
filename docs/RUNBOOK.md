# Operations Runbook — Predictive Maintenance Intelligence

## Windows release acceptance

Before either acceptance gate, stop the local server with `Ctrl+C` in the
terminal running Uvicorn. The gates rebuild `.venv`; an active Python process
can lock native files such as `httptools` or `gurobipy` and prevent a clean
rebuild. The scripts prefer Python 3.13, then 3.12, because those versions
have the required scientific-computing wheels on the supported Windows path.

Base V1 engineering gate:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\v1_acceptance.ps1
```

CMMS/ERP boundary gate and evidence artifact:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cmms_erp_acceptance.ps1
```

This writes `artifacts/cmms_erp_acceptance.json` only after the contract,
reconciliation, audit, authorization, and safe-export tests pass.

Full **real-operations** gate:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase5_acceptance.ps1
```

The Phase-5 gate downloads the public MetroPT-3 archive from UCI when it is not already present, validates the complete source, builds the streaming feature store, trains/evaluates the real-data models, runs Phase-5 diagnostics, and verifies the resulting maintenance case with licensed Gurobi.

Raw MetroPT-3 data stays local under `data/external/metropt3/`.

## Enterprise readiness checks

Run the bounded local/reference acceptance pack after code changes:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\enterprise_acceptance.ps1
```

The report is written to `artifacts/enterprise_acceptance.json`. `-Full` adds
the complete warning-clean suite. `-Strict` intentionally returns nonzero until
real deployment evidence is supplied.

Production containers automatically use the strict readiness gate when
`PDM_AUTH_MODE=production`; do not rely on the local/reference readiness result
for a production deployment.

The deployment gate requires a non-secret evidence attestation in addition to
environment declarations. The attestation references real enterprise records;
it must never contain credentials:

```powershell
$env:PDM_SECRET_PROVIDER = "<approved-secret-manager>"
$env:PDM_DEPLOYMENT_EVIDENCE_FILE = "C:\path\to\deployment-evidence.json"
$env:PDM_READINESS_PROBE_DATABASE = "1"
\.venv\Scripts\python.exe scripts\deployment_evidence.py --input $env:PDM_DEPLOYMENT_EVIDENCE_FILE
```

See `docs/DEPLOYMENT_EVIDENCE.md` for the required seven-control schema.

Managed-state preflight validates a supported PostgreSQL SQLAlchemy URL without
printing credentials. `--skip-connect` is configuration-only; the normal form
performs the bounded live probe. Prefer `PDM_DATABASE_URL` from a secret manager:

```powershell
\.venv\Scripts\python.exe scripts\managed_state_preflight.py --skip-connect
\.venv\Scripts\python.exe scripts\managed_state_preflight.py
\.venv\Scripts\python.exe scripts\managed_state_migrate.py
```

The migration command creates the managed schema and audit protections. The
readiness probe is read-only and will remain blocked if either the migration
ledger or the PostgreSQL append-only audit guards are missing.

For a local adapter integration harness, copy `.env.managed.example` to
`.env.managed`, replace the placeholder password locally, and run:

```powershell
docker compose -f docker-compose.managed.yml --env-file .env.managed up --build
```

The harness uses local authentication and a single PostgreSQL container. Apply
the schema explicitly before starting the platform so container readiness also
verifies the migration ledger and append-only audit guards:

```powershell
docker compose -f docker-compose.managed.yml --env-file .env.managed --profile migration run --rm --build migrate
docker compose -f docker-compose.managed.yml --env-file .env.managed up --build
```

The migration service is opt-in and one-shot; the normal platform service never
auto-migrates production state. The harness is useful for verifying managed
adapter wiring and `/api/health/ready`, but it is not production infrastructure
or disaster-recovery evidence. Do not use the example file as a production
secret source.

For the production Kubernetes topology, start from
`deploy/kubernetes/pdm-platform.json`. Render the replacement markers through
the approved GitOps/deployment system; keep `PDM_API_KEYS` and
`PDM_DATABASE_URL` in the pre-created `pdm-runtime-secrets` secret and keep the
non-secret deployment attestation in the mounted ConfigMap. Validate the
rendered file before review:

```powershell
\.venv\Scripts\python.exe scripts\validate_production_manifest.py `
  --manifest .\rendered\pdm-platform.json --strict
```

Use the cluster's server-side dry run and diff before applying. The migration
Job is suspended in the template by default; only an approved change should
unsuspend it, verify the migration ledger and append-only audit guards, and
then roll out the application. Verify the rollout through the trusted TLS
ingress and require `/api/health/production-readiness` to report ready before
accepting traffic. Cluster, secret-manager, ingress, recovery, connector, and
change-ticket evidence remains deployment-specific.

The local backup/restore check never overwrites an existing destination:

```powershell
\.venv\Scripts\python.exe scripts\backup_restore.py --source data\pdm.db --destination artifacts\backups\pdm.db
```

For a running local or staging API, execute a bounded load smoke test:

```powershell
\.venv\Scripts\python.exe scripts\load_smoke.py --url http://127.0.0.1:8001/api/health
```

External model training registers a candidate model. Set
`PDM_REQUIRE_APPROVED_MODELS=1` in a protected deployment so only models
promoted through `/api/models/registry/{model_id}/promote` can score.

## Startup

Because many Windows installations block unsigned local PowerShell scripts, the robust invocation is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

The Windows local application serves on `http://127.0.0.1:8001` by default.

To use another local port, pass it to the launcher:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1 -Port 8001
```

The corresponding application URL is `http://127.0.0.1:8001`.

Primary surfaces:

- `/` — Reliability Observatory;
- `/data-gateway` — generic external-data lifecycle;
- `/operations` — MetroPT-3 Real Operations Command;
- `/stress-lab` — benchmark stochastic policy comparison;
- `/methodology` — evidence/method chain;
- `/docs` — API schema.

## Prepare MetroPT-3 manually

```powershell
.\.venv\Scripts\python.exe scripts\prepare_metropt3.py --download
```

For an already downloaded source:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_metropt3.py --csv-path "C:\path\to\MetroPT3(AirCompressor).csv"
```

Strict mode expects the complete UCI dataset. `--allow-partial` exists only for developer-path diagnostics and must never be used as full real-data evidence.

## Standard development setup

```bash
python -m venv .venv
python -m pip install -e '.[dev]'
python -m pytest -W error
python scripts/v1_diagnostics.py
python scripts/release_audit.py
python scripts/v1_benchmark.py
uvicorn pdm_intelligence.api.main:app --host 127.0.0.1 --port 8001
```

Licensed Gurobi path:

```bash
python -m pip install -e '.[dev,gurobi]'
```

## Failure handling

- **MetroPT source absent:** `/operations` remains `NOT_READY`; no synthetic substitute is displayed.
- **MetroPT source mismatch:** preparation fails before model training.
- **Schema/readiness failure:** reject unsupported analysis; never fabricate fields.
- **Model incompatibility:** keep capability `NOT_READY`; never transfer NASA model claims.
- **MILP infeasibility:** return precheck/IIS evidence; never violate capacity silently.
- **Gurobi unavailable:** HiGHS remains available for reproducible development unless the acceptance gate explicitly requires Gurobi.
- **External database error:** credentials are not persisted; return a bounded integration error.
- **Work-order completion:** recorded status is application state, not evidence of historical field execution.
