# Implementation Progress

## Completed reference-platform layers

The repository contains the AI, reliability, operations-research, simulation,
decision-intelligence, real-data, UI, work-order, and BELIEF-MAINT reference
layers described in the release documentation.

## Phase K — CMMS/ERP integration boundary

Implemented:

- canonical `CMMS-WORK-ORDER.v1` work-order contract;
- canonical `CMMS-INVENTORY.v1` inventory-position contract with derived
  available stock and location-aware identity;
- status, timestamp, numeric, duplicate-ID and unknown-field validation;
- a separate `cmms_work_orders` external mirror so CMMS updates cannot rewrite
  local operator execution state;
- deterministic SHA-256 record fingerprints and batch IDs;
- idempotent replay handling;
- strict-newer revision/timestamp updates;
- stale/conflict reporting without silent overwrite;
- contract-only deterministic JSONL export;
- reconciliation and export audit linkage;
- atomic mirror plus reconciliation-audit commits when using one state DB;
- inventory reconciliation and export audit linkage;
- API endpoints and a dedicated Windows acceptance script.

Acceptance command:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cmms_erp_acceptance.ps1
```

The remaining production boundary is a site-specific SAP/Maximo/Oracle or
other CMMS connector with enterprise credentials, transport retry semantics,
dead-letter handling, and sandbox evidence. The repository now supplies the
managed PostgreSQL adapter boundary, a bounded runtime readiness probe, and a
local Compose integration harness; production HA, migrations, monitoring
retention/alert routing, recovery evidence, and site authorization remain
deployment-specific.

The readiness contract now also requires explicit non-secret declarations for
the trusted TLS ingress, metrics/log/trace/alert components, backup target,
and site connector identity/endpoint. These declarations are presence checks,
not claims that the external systems are reachable or certified. Deployment
attestations are schema-v2 records with a canonical evidence digest and an
exact release-artifact SHA-256 binding; stale, cross-environment, and
cross-artifact evidence fails closed.

Managed-state readiness now separates mutation from verification: the explicit
`scripts/managed_state_migrate.py` command records the repository schema
version, while the deployment probe only verifies all state tables and the
`pdm_schema_migrations` ledger. A readiness request cannot silently create or
upgrade production tables. Managed container readiness also verifies the
migration ledger and PostgreSQL append-only audit guards after connectivity;
the managed Compose harness exposes an opt-in one-shot migration profile. In
production authentication mode, the API automatically applies the strict
deployment-readiness gate rather than relying on a second opt-in flag.

## Phase L — Real-data model evidence hardening

The MetroPT-3 supervised promotion gate now requires point metrics, prevalence
baseline comparisons, event/block-clustered uncertainty bounds, and independent
failure-event coverage on both chronological validation and the untouched
future holdout. Positive rows are resampled by published failure event and
negative rows by contiguous 24-hour background block, so serial telemetry rows
are not treated as independent evidence. The current four-event source has one
event in validation and one in holdout; the model therefore remains explicitly
`PROMOTION_BLOCKED` until additional independent labeled failure history and
acceptable model metrics exist.

Phase 5 diagnostics are now SHA-256 bound to the trusted MetroPT runtime
manifest. Enterprise acceptance fails closed when diagnostics are missing,
stale, detached from the runtime, or backed by an integrity-failed artifact.

Synthetic development tooling now generates a deterministic 200,000+ row
canonical multi-asset fixture, verifies ingestion/replay, and keeps synthetic
model results explicitly in `SYNTHETIC_TEST_ONLY` with promotion disabled.

## Phase M — Release extraction and provenance gate

The release workflow now includes `scripts/clean_extract_validate.py`. It
checks the exact distributable ZIP for CRC integrity, safe relative paths,
duplicate or symlink members, forbidden credential/database/archive material,
raw external data, required runtime files, valid manifest JSON, and successful
temporary-directory extraction. The check is a first-class enterprise
acceptance lane and records the exact artifact SHA-256 without turning local
packaging evidence into production certification.

## Phase N — Production topology boundary

Added a vendor-neutral Kubernetes `List` template under
`deploy/kubernetes/pdm-platform.json` with production authentication and
managed-state references, digest-pinned image syntax, non-root restricted
pods, read-only root filesystem, bounded resources, startup/readiness/liveness
probes, three-replica rolling updates, disruption protection, TLS ingress, and
default-bounded network policy. Site-specific values remain replacement
markers, and `scripts/validate_production_manifest.py` plus enterprise
acceptance tests verify the secure wiring without claiming a live cluster or
external service evidence.
