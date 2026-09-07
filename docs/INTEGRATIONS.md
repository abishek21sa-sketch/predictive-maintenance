# Integration Boundary — V1.0 / CMMS-WORK-ORDER.v1

V1.0 is complete for file/batch/database/historical-replay workflows. The
CMMS/ERP reference boundary now includes a validated canonical work-order
contract and a local reconciliation mirror. Continuous live-event streaming
and a site-specific production connector remain outside the reference
platform boundary.

## External data adapters

The Data Gateway accepts browser text uploads, server-side files, optional Parquet files and SQLAlchemy database sources. Every source is mapped into canonical asset, telemetry and maintenance contracts before readiness is assessed.

Connection URLs and credentials are not persisted in dataset manifests.

## CMMS / ERP batch exchange

`pdm_intelligence.integrations.batch` continues to validate vendor-neutral
work-order and spare-parts extracts and can export recommendation records for
downstream loading. `pdm_intelligence.integrations.cmms` adds the stricter
`CMMS-WORK-ORDER.v1` exchange contract with these required fields:

`source_system`, `work_order_id`, `asset_id`, `status`, `planned_start`,
`duration_hours`, `required_skill`, `part_id`, and `part_qty`.

Optional linkage fields include `action`, `scheduled_cycle`, `decision_id`,
`approval_id`, `external_revision`, and `source_updated_at`. Status aliases
from common CMMS systems are normalized to `OPEN`, `COMMITTED`,
`IN_PROGRESS`, `COMPLETED`, or `CANCELLED`. Unknown statuses, invalid
timestamps, negative quantities, non-positive durations, and duplicate IDs in a
batch are rejected before persistence.

### Reconciliation guarantees

- External records are stored in `cmms_work_orders`, separate from the local
  operator execution ledger.
- A deterministic SHA-256 source fingerprint makes a replay of the same
  record a `skipped` operation rather than a duplicate insert.
- An existing record changes only when the incoming external revision is
  strictly newer, or when a newer source update timestamp is available and no
  revision is supplied.
- Older records are reported as `stale`; conflicting records without a
  monotonic version are reported as `conflict` and never overwrite the mirror.
- Every reconciliation batch is linked to the append-only audit chain by a
  deterministic `CMMS-...` batch ID.
- When audit linkage is enabled, the mirror mutation and reconciliation audit
  event commit in the same local transaction; a different audit database is
  rejected rather than creating an unaudited mirror update.

The reference API exposes:

- `POST /api/integrations/cmms/reconcile` — data-engineer/admin permission;
  returns counts, per-record outcomes, batch ID and audit event ID.
- `GET /api/integrations/cmms/work-orders` — lists the canonical mirror.
- `POST /api/integrations/cmms/export` — data-engineer/admin permission;
  returns contract-only deterministic JSONL with batch, audit, and
  `X-Exchange-Content-SHA256` integrity headers.

The export surface intentionally ignores unknown input keys and does not
persist or emit connection URLs, credentials, tokens, or vendor-specific
payloads.

## CMMS / ERP inventory exchange

Inventory positions use the `CMMS-INVENTORY.v1` contract. Required fields are
`source_system`, `part_id`, `description`, `on_hand`, `reserved`, and
`unit_cost`; `location`, `external_revision`, and `source_updated_at` provide
optional location and version linkage. The contract calculates `available` as
`on_hand - reserved`, rejects negative quantities and `reserved > on_hand`,
and treats `(source_system, part_id, location)` as the external identity.

Inventory positions are mirrored in `cmms_inventory` and follow the same
fingerprint, replay, strictly-newer revision, stale, conflict, and audit
semantics as work orders. The API exposes:

- `POST /api/integrations/cmms/inventory/reconcile`;
- `GET /api/integrations/cmms/inventory`;
- `POST /api/integrations/cmms/inventory/export`.

Both export endpoints audit the content digest and return it in
`X-Exchange-Content-SHA256`, allowing a receiving system to verify the exact
newline-delimited byte stream it accepted.

Inventory values describe an external stock feed. They are not proof of a
physical count, reservation fulfillment, or parts availability without a
site-controlled inventory process.

A real site can map SAP PM, IBM Maximo, Oracle Maintenance or another CMMS/ERP into the canonical contracts without rewriting prognostics, reliability, optimization or simulation logic.

## Live integration future boundary

A production live connector should implement:

`site source → authenticated connector → canonical contract → readiness/quality gate → analytical services`

Site-specific work includes message-bus/API credentials, unit/semantic mapping, retention, observability, cybersecurity, failure semantics and prospective validation. V1.0 does not pretend those deployment-specific concerns are universal.
