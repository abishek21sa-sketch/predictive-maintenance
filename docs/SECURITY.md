# Security Boundary

The reference release is secure-by-configuration rather than pretending to provide a site-specific identity provider. Local development runs without credentials. Setting `PDM_API_KEYS` enables API-key authentication, and every `/api/*` route is authenticated except the non-sensitive `/api/health` and `/api/health/ready` liveness probes. Individual routes still enforce their role/permission requirements. This establishes a clean boundary for replacement with OIDC/API-gateway identity in deployment.

Protected write paths emit append-only, SHA-256 hash-chained audit events to
the configured state backend. SQLite is the local reference backend; managed
PostgreSQL installs database-enforced append-only audit guards, and the managed
readiness probe verifies that the guard function and both mutation-blocking
triggers are present. `/api/audit/verify` validates the chain and
`/api/audit/export` emits a verified chronological JSONL export. Secrets are environment variables and
are not stored in source control. Production deployments should terminate TLS
at the ingress/reverse proxy, rotate credentials, use a managed secret store,
restrict database/file permissions, and map the authorization dependency to
enterprise IAM.

`GET /api/health/production-readiness` exposes a non-secret deployment gate for
production authentication, managed state, TLS termination, observability,
backup/restore evidence, a site connector, change control, and prospective
field qualification. It fails closed
for the local/reference configuration and requires a structured non-secret
deployment attestation plus a bounded managed-database connectivity probe. The
attestation is bound to the running release ID, deployment environment, and
exact `PDM_RELEASE_ARTIFACT_SHA256`, so it cannot be reused for a different
release artifact. Its evidence class is
`CONFIGURATION_AND_ATTESTATION_NOT_DEPLOYMENT_CERTIFICATION`; passing the
declarations and attestation does not certify infrastructure, compliance,
availability, model performance, or field safety. See
`docs/DEPLOYMENT_EVIDENCE.md` for the contract.

Prospective field qualification is a separate release-bound evidence file with
completed follow-up, reconciled outcomes, safety review, operator acceptance,
and non-secret references; historical or synthetic evidence cannot satisfy it.

The readiness gate also requires non-secret declarations for the trusted TLS
ingress identity, metrics exporter, structured log sink, trace exporter,
alert-routing destination, backup target, and site connector ID/endpoint. The
report exposes only declaration presence and never echoes endpoint values.

Container orchestration can be configured with
`PDM_REQUIRE_PRODUCTION_READINESS=1`. In that mode `/api/health/ready` fails
closed when the deployment gate is blocked, preventing a workload from entering
service while it is only reference-ready. `PDM_AUTH_MODE=production` enables
the same strict behavior automatically; local/reference mode keeps the bounded
runtime probe behavior unless the flag is explicitly set.

The managed-state declaration is fail-closed. The managed PostgreSQL adapter is
selected explicitly and all stateful stores use it. A separate migration
command records the repository schema version in `pdm_schema_migrations`; the
readiness probe verifies that ledger without mutating the database. Normal
managed-runtime store initialization also refuses to run schema DDL; it only
uses a verified existing schema, and otherwise fails closed until the explicit
migration command completes. If the adapter, driver, schema ledger, or database is unavailable, stateful endpoints
return a controlled 503 and do not execute against the local fallback store. A
connectivity and schema probe still does not certify HA, migrations, recovery,
or site deployment.

Production identity readiness requires every declared principal entry to be
valid, unique, and backed by at least 32 characters of key material, plus a
declared secret-rotation policy and a fresh rotation test timestamp; the
readiness report exposes only non-secret counts and policy results. Production
mode also fails closed when a real-data model has not passed its
promotion gate: MetroPT operational cases and work-order commitments return a
controlled conflict instead of treating historical/testing evidence as an
approved production decision. Local/reference mode can still exercise that
workflow for explicitly labeled testing.

MetroPT runtime evidence binds its derived feature store, model artifacts, and
holdout evidence to a root-bound SHA-256 manifest. The runtime JSON is also
bound to a separate sidecar digest, allowing changes to quality-gate or
provenance fields to be detected. A declared artifact or runtime-manifest that
is missing, modified, or outside the runtime root is rejected from the trusted
runtime path. These local digests are corruption/tamper detection evidence, not
a signed provenance system or an immutable artifact store.

API responses include request correlation IDs plus baseline browser/API
security headers. When TLS termination is explicitly declared with
`PDM_TLS_TERMINATED=1`, responses also include HSTS for the trusted ingress
boundary. The authenticated `/metrics` endpoint exposes operational counters,
bounded latency buckets, and 5xx totals but intentionally excludes request
bodies, credentials, asset values, and arbitrary URL identifiers.

External models are registered as candidates with artifact/manifest hashes and
cannot be promoted unless their explicit versioned gate proves that both holdout
RMSE and MAE beat the age/time baseline, at least two independent validation
assets were held out, target leakage was excluded, the registered hashes still
match, and an authorized human supplies a rationale. Set
`PDM_REQUIRE_APPROVED_MODELS=1` for a deployment scoring boundary. This local
registry is not a substitute for an enterprise model registry or immutable
managed retention.

The CMMS/ERP integration requires the `ingest:data` permission and records the
reconciliation batch ID and per-record outcome in the audit chain. Mirror
changes and their reconciliation audit event are committed atomically when
they share the state database. The reference implementation is not a
replacement for a managed immutable log service or enterprise OIDC/SSO
deployment. Export responses also carry an SHA-256 content digest that is
recorded in the corresponding audit event, so receivers can verify the exact
newline-delimited payload they accepted.

This release does not claim aircraft safety certification, regulatory approval, or production identity-provider integration.
