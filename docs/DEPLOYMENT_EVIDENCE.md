# Deployment Evidence Attestation

The production-readiness endpoint requires both runtime declarations and a
non-secret operator attestation before it can report
`READY_FOR_DEPLOYMENT_REVIEW`. This closes the gap between setting environment
variables and recording that a deployment control was actually reviewed.

The attestation is not a production certificate. Its `evidence_ref` values
must point to the real secret-manager, ingress, monitoring, recovery, CMMS/ERP,
and change-control records held by the deploying organization. Do not put API
keys, passwords, tokens, private keys, or credential-bearing URLs in the file.
`attested_at` and every `verified_at` must be timezone-aware RFC3339 timestamps,
must not be more than 30 days old, and may not be materially in the future.
Every `evidence_ref` must be a URI pointing to an operator-held record; file and
data URIs, embedded credentials, and secret-bearing query parameters are
rejected. The file must include the calculated `evidence_digest`; a missing or
altered digest is rejected. The runtime also requires a current timezone-aware
`PDM_RESTORE_TESTED_AT` and an HTTPS CMMS/ERP endpoint without embedded
credentials or secret-bearing query parameters.

Prospective field qualification is a separate gate. It cannot be satisfied by
MetroPT-3, NASA C-MAPSS, synthetic stress runs, a local work-order record, or a
CMMS exchange fixture. Set `PDM_FIELD_QUALIFICATION_EVIDENCE_FILE` only to a
site-owned, release-bound attestation after follow-up is complete, outcomes are
reconciled, safety review is recorded, and an operator accepts the result. The
validator requires non-secret references to the underlying records, a
canonical digest, a fingerprint of the evaluated dataset, and an exact match
to the running release, environment, and artifact SHA-256. Embedded secrets
and impossible outcome counts are rejected.

Required environment declarations include:

```powershell
$env:PDM_SECRET_PROVIDER = "<approved-secret-manager>"
$env:PDM_API_KEYS = "<secret-manager-injected-principals-with-32-character-minimum-keys>"
$env:PDM_SECRET_ROTATION_POLICY = "<approved-rotation-policy>"
$env:PDM_SECRET_ROTATION_TESTED_AT = "<recent-rfc3339-test-time>"
$env:PDM_STATE_BACKEND = "managed"
$env:PDM_MANAGED_STATE_ADAPTER = "sqlalchemy"
$env:PDM_READINESS_PROBE_DATABASE = "1"
$env:PDM_RELEASE_ID = "1.0.0"
$env:PDM_DEPLOYMENT_ENVIRONMENT = "production"
$env:PDM_CHANGE_TICKET_STATUS = "approved"
$env:PDM_DEPLOYMENT_EVIDENCE_FILE = "C:\path\to\deployment-evidence.json"
$env:PDM_FIELD_QUALIFICATION_EVIDENCE_FILE = "C:\path\to\field-qualification.json"
$env:PDM_RELEASE_ARTIFACT_SHA256 = "<lowercase-sha256-of-deployed-release-zip>"
$env:PDM_TLS_INGRESS_ID = "prod-ingress-pdm"
$env:PDM_OBSERVABILITY_ENABLED = "1"
$env:PDM_METRICS_EXPORTER = "prometheus"
$env:PDM_LOG_SINK = "managed-log-service"
$env:PDM_TRACE_EXPORTER = "otlp"
$env:PDM_ALERT_ROUTING = "sre-on-call"
$env:PDM_BACKUP_POLICY = "daily"
$env:PDM_BACKUP_TARGET = "managed-object-storage"
$env:PDM_RESTORE_TESTED_AT = "2026-09-05T12:00:00Z"
$env:PDM_CMMS_CONNECTOR_MODE = "site"
$env:PDM_CMMS_CONNECTOR_ID = "site-cmms-primary"
$env:PDM_CMMS_CONNECTOR_ENDPOINT = "https://cmms.example.invalid/api"
```

The component declarations are identifiers or endpoints only; they do not
replace the operator evidence attestation or prove reachability. Readiness
reports only whether each declaration is present and never echoes endpoint
values.

Validate the file before a deployment review:

```powershell
\.venv\Scripts\python.exe scripts\deployment_evidence.py --input C:\path\to\deployment-evidence.json
```

Prepare and validate the separate prospective field-qualification boundary:

```powershell
\.venv\Scripts\python.exe scripts\field_qualification_evidence.py `
  --write-template C:\path\to\field-qualification.json
\.venv\Scripts\python.exe scripts\field_qualification_evidence.py `
  --input C:\path\to\field-qualification.json
```

The template is intentionally invalid until a real site study is complete;
the validator returns exit code `2` for missing, pending, stale, tampered, or
incomplete evidence.

Start from the repository's unverified template when preparing a site-owned
attestation:

```powershell
\.venv\Scripts\python.exe scripts\deployment_evidence.py --write-template C:\path\to\deployment-evidence.json
```

Replace every placeholder only with non-secret references, change each control
to `verified` after the real control is checked, and then run the validation
command above. The template is deliberately invalid until an operator performs
those checks. The attestation `release_id` and `environment` must exactly match
`PDM_RELEASE_ID` and `PDM_DEPLOYMENT_ENVIRONMENT`, and its `artifact_sha256`
must exactly match `PDM_RELEASE_ARTIFACT_SHA256`; a stale, cross-environment, or
cross-artifact attestation is rejected. Change control also requires
`PDM_CHANGE_TICKET_STATUS=approved`.

The file must use `PDM-DEPLOYMENT-EVIDENCE.v2` and include every control:

```json
{
  "schema_version": "PDM-DEPLOYMENT-EVIDENCE.v2",
  "release_id": "1.0.0",
  "environment": "production",
  "artifact_sha256": "<lowercase-sha256-of-deployed-release-zip>",
  "attested_by": "platform-release-owner",
  "attested_at": "2026-09-05T12:00:00Z",
  "controls": {
    "production_auth": {"status": "verified", "owner": "security", "verified_at": "...", "evidence_ref": "vault://prod/pdm/auth"},
    "managed_state_backend": {"status": "verified", "owner": "platform", "verified_at": "...", "evidence_ref": "change://CHG-123/database"},
    "tls_termination": {"status": "verified", "owner": "network", "verified_at": "...", "evidence_ref": "ingress://prod/pdm/tls"},
    "observability": {"status": "verified", "owner": "sre", "verified_at": "...", "evidence_ref": "monitoring://prod/pdm"},
    "backup_and_restore": {"status": "verified", "owner": "sre", "verified_at": "...", "evidence_ref": "dr://prod/pdm/restore-test"},
    "site_connector": {"status": "verified", "owner": "operations", "verified_at": "...", "evidence_ref": "cmms://site/work-orders"},
    "change_control": {"status": "verified", "owner": "release", "verified_at": "...", "evidence_ref": "change://CHG-123"}
  }
}
```

After the controls are completed, calculate and include `evidence_digest` as
the SHA-256 digest of the canonical JSON payload with the digest field omitted.
The validator prints the calculated value without printing the attestation
contents.

The application reports the calculated evidence digest, the bound release
artifact digest, and control names only.
It does not echo file contents or credentials, and the generated report is
excluded from release packages.

The field-qualification attestation uses `PDM-FIELD-QUALIFICATION.v1` and
contains the site/study identifiers, completed observation period, asset and
outcome counts, evaluated-data fingerprint, explicit safety/operator acceptance, aggregate qualification
metrics, and non-secret evidence references. Validate it with the same
deployment package validator before setting the environment variable; an
attestation is evidence for review, not an independent production certificate.

For deployment automation, run the fail-closed preflight command. Exit code
`0` means `READY_FOR_DEPLOYMENT_REVIEW`; exit code `2` means that a blocker
remains. The JSON output contains only redacted configuration evidence and is
run-specific, so it is excluded from release packages.

```powershell
\.venv\Scripts\python.exe scripts\production_preflight.py
```

## Managed-state boundary

`PDM_MANAGED_STATE_ADAPTER=sqlalchemy` selects the repository's managed
PostgreSQL state adapter. Every stateful store uses the same adapter boundary;
there is no local SQLite fallback when `PDM_STATE_BACKEND=managed` is set. The
adapter opens a bounded SQLAlchemy connection and stateful requests fail closed
with a controlled `503 MANAGED_STATE_RUNTIME_UNAVAILABLE` if the driver, URL,
or database is unavailable.

The bounded probe verifies the repository's existing state tables and migration
ledger and reports that result separately from connectivity. It never creates
tables or records migrations. Before the probe, run the explicit migration
command against the managed database:

```powershell
\.venv\Scripts\python.exe scripts\managed_state_migrate.py
```

The readiness probe never creates tables or updates migration records; it only
verifies the existing schema, `pdm_schema_migrations` ledger, and the managed
PostgreSQL append-only audit function/triggers for `audit_events` and
`approval_records`. It is not a deployment certificate: production still needs controlled migrations,
HA/replication, backup and restore evidence, database permissions, and the
non-secret operator attestation described above. Install the managed database
driver with `pip install ".[managed]"` (or the equivalent locked deployment
build) before selecting this backend.

The application store initializers follow the same boundary: when the managed
backend is selected, normal API traffic does not run schema or trigger DDL. It
performs the read-only schema verification and fails closed with
`managed_state_schema_migration_required` until the explicit migration has
completed.

## Container deployment boundary

The production image installs the managed PostgreSQL driver and uses
`/api/health/ready` as its container healthcheck. The default
`docker-compose.yml` remains a local SQLite reference service. Use
`docker-compose.managed.yml` with a locally supplied `.env.managed` only as a
bounded adapter integration harness; it starts a single PostgreSQL container
and is intentionally not a production topology. CI validates both Compose
files, while actual production still requires the managed database, secret
manager, ingress, observability, disaster recovery, connector, and
change-control evidence listed above.
