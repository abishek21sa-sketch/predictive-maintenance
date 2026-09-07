"""Canonical CMMS/ERP work-order exchange and reconciliation boundary.

The local SQLite mirror is deliberately separate from the application's
operator work-order ledger. External systems may replay, reorder, or revise a
work order; none of those behaviors should silently rewrite local execution
state. This module therefore provides a strict canonical contract, monotonic
revision checks, deterministic fingerprints, and a safe JSONL export surface.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

CONTRACT_VERSION = "CMMS-WORK-ORDER.v1"
WORK_ORDER_STATUSES = frozenset({"OPEN", "COMMITTED", "IN_PROGRESS", "COMPLETED", "CANCELLED"})

_STATUS_ALIASES = {
    "NEW": "OPEN",
    "CREATED": "OPEN",
    "RELEASED": "OPEN",
    "PLANNED": "OPEN",
    "READY": "COMMITTED",
    "ASSIGNED": "COMMITTED",
    "SCHEDULED": "COMMITTED",
    "INPROGRESS": "IN_PROGRESS",
    "IN_PROGRESS": "IN_PROGRESS",
    "CLOSED": "COMPLETED",
    "CANCELED": "CANCELLED",
    "VOID": "CANCELLED",
}

_CONTRACT_FIELDS = (
    "source_system",
    "work_order_id",
    "asset_id",
    "status",
    "planned_start",
    "duration_hours",
    "required_skill",
    "part_id",
    "part_qty",
    "action",
    "scheduled_cycle",
    "decision_id",
    "approval_id",
    "external_revision",
    "source_updated_at",
)

CMMS_WORK_ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS cmms_work_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_system TEXT NOT NULL,
  work_order_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  status TEXT NOT NULL,
  planned_start TEXT NOT NULL,
  duration_hours REAL NOT NULL,
  required_skill TEXT NOT NULL,
  part_id TEXT NOT NULL,
  part_qty INTEGER NOT NULL,
  action TEXT,
  scheduled_cycle INTEGER,
  decision_id TEXT,
  approval_id TEXT,
  external_revision INTEGER,
  source_updated_at TEXT,
  source_fingerprint TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at TEXT NOT NULL,
  last_batch_id TEXT NOT NULL DEFAULT '',
  UNIQUE(source_system, work_order_id)
);
CREATE INDEX IF NOT EXISTS ix_cmms_work_orders_source
  ON cmms_work_orders(source_system, updated_at DESC);
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, field: str, *, required: bool = True, max_length: int = 240) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be text")  # noqa: TRY004
    result = str(value).strip()
    if required and not result:
        raise ValueError(f"{field} is required")
    if len(result) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    return result or None


def _timestamp(value: Any, field: str, *, required: bool = False) -> str | None:
    result = _text(value, field, required=required, max_length=80)
    if result is None:
        return None
    normalized = result[:-1] + "+00:00" if result.endswith("Z") else result
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    return parsed.isoformat(timespec="seconds")


def _timestamp_value(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _positive_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite and greater than zero")
    return result


def _nonnegative_integer(value: Any, field: str, *, required: bool = True) -> int | None:
    if value is None and not required:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not isfinite(number) or not number.is_integer() or number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return int(number)


def _status(value: Any) -> str:
    raw = _text(value, "status", max_length=40)
    normalized = raw.upper().replace("-", "_").replace(" ", "_")
    normalized = _STATUS_ALIASES.get(normalized, normalized)
    if normalized not in WORK_ORDER_STATUSES:
        raise ValueError(f"Unsupported work-order status {raw}")
    return normalized


@dataclass(frozen=True)
class CanonicalWorkOrder:
    source_system: str
    work_order_id: str
    asset_id: str
    status: str
    planned_start: str
    duration_hours: float
    required_skill: str
    part_id: str
    part_qty: int
    action: str | None = None
    scheduled_cycle: int | None = None
    decision_id: str | None = None
    approval_id: str | None = None
    external_revision: int | None = None
    source_updated_at: str | None = None

    @property
    def source_fingerprint(self) -> str:
        return _digest(_canonical_json(self.to_dict(include_fingerprint=False)))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        result = {field: getattr(self, field) for field in _CONTRACT_FIELDS}
        result["contract_version"] = CONTRACT_VERSION
        if include_fingerprint:
            result["source_fingerprint"] = self.source_fingerprint
        return result


def canonicalize_work_order(
    record: Mapping[str, Any] | CanonicalWorkOrder,
    *,
    source_system: str,
) -> CanonicalWorkOrder:
    """Validate and normalize one external work order to the v1 contract."""

    if isinstance(record, CanonicalWorkOrder):
        if record.source_system != source_system:
            raise ValueError("record source_system does not match the reconciliation source_system")
        return record
    if not isinstance(record, Mapping):
        raise ValueError("each work order must be an object")  # noqa: TRY004
    source = _text(record.get("source_system", source_system), "source_system", max_length=80)
    requested_source = _text(source_system, "source_system", max_length=80)
    if source != requested_source:
        raise ValueError("record source_system does not match the reconciliation source_system")
    return CanonicalWorkOrder(
        source_system=requested_source,
        work_order_id=_text(record.get("work_order_id"), "work_order_id", max_length=120),
        asset_id=_text(record.get("asset_id"), "asset_id", max_length=120),
        status=_status(record.get("status")),
        planned_start=_timestamp(record.get("planned_start"), "planned_start", required=True),
        duration_hours=_positive_number(record.get("duration_hours"), "duration_hours"),
        required_skill=_text(record.get("required_skill"), "required_skill", max_length=120),
        part_id=_text(record.get("part_id"), "part_id", max_length=120),
        part_qty=_nonnegative_integer(record.get("part_qty"), "part_qty"),
        action=_text(record.get("action"), "action", required=False, max_length=120),
        scheduled_cycle=_nonnegative_integer(record.get("scheduled_cycle"), "scheduled_cycle", required=False),
        decision_id=_text(record.get("decision_id"), "decision_id", required=False, max_length=120),
        approval_id=_text(record.get("approval_id"), "approval_id", required=False, max_length=120),
        external_revision=_nonnegative_integer(
            record.get("external_revision"), "external_revision", required=False
        ),
        source_updated_at=_timestamp(record.get("source_updated_at"), "source_updated_at"),
    )


def validate_work_orders(
    records: Iterable[Mapping[str, Any] | CanonicalWorkOrder],
    *,
    source_system: str,
) -> list[CanonicalWorkOrder]:
    """Validate a batch and reject duplicate external IDs before persistence."""

    normalized_source = _text(source_system, "source_system", max_length=80)
    result = [canonicalize_work_order(record, source_system=normalized_source) for record in records]
    if not result:
        raise ValueError("at least one work order is required")
    seen: set[str] = set()
    for record in result:
        if record.work_order_id in seen:
            raise ValueError(f"duplicate work_order_id in batch: {record.work_order_id}")
        seen.add(record.work_order_id)
    return result


def batch_id_for_work_orders(records: Iterable[CanonicalWorkOrder]) -> str:
    material = [
        record.to_dict(include_fingerprint=True)
        for record in sorted(records, key=lambda item: (item.source_system, item.work_order_id))
    ]
    return "CMMS-" + _digest(_canonical_json(material))[:16].upper()


def work_orders_jsonl(
    records: Iterable[Mapping[str, Any] | CanonicalWorkOrder],
    *,
    source_system: str,
    batch_id: str | None = None,
) -> tuple[str, str]:
    normalized = validate_work_orders(records, source_system=source_system)
    resolved_batch_id = batch_id or batch_id_for_work_orders(normalized)
    lines = []
    for record in sorted(normalized, key=lambda item: (item.source_system, item.work_order_id)):
        payload = record.to_dict()
        payload["batch_id"] = resolved_batch_id
        lines.append(_canonical_json(payload) + "\n")
    return "".join(lines), resolved_batch_id


def export_work_orders_jsonl(
    records: Iterable[Mapping[str, Any] | CanonicalWorkOrder],
    path: str | Path,
    *,
    source_system: str,
    batch_id: str | None = None,
) -> Path:
    """Write a deterministic, contract-only JSONL exchange file."""

    content, _ = work_orders_jsonl(records, source_system=source_system, batch_id=batch_id)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def _ensure_schema(db_path: str | Path) -> None:
    if not ensure_schema_mutation_allowed(db_path):
        return
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_state(path)) as con:
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(CMMS_WORK_ORDER_SCHEMA)
        columns = {row[1] for row in con.execute("PRAGMA table_info(cmms_work_orders)")}
        if "last_batch_id" not in columns:
            con.execute("ALTER TABLE cmms_work_orders ADD COLUMN last_batch_id TEXT NOT NULL DEFAULT ''")
        con.commit()


def _version_decision(record: CanonicalWorkOrder, existing: sqlite3.Row) -> str:
    incoming_revision = record.external_revision
    existing_revision = existing["external_revision"]
    if incoming_revision is not None or existing_revision is not None:
        if incoming_revision is not None and existing_revision is None:
            return "newer"
        if incoming_revision is None:
            return "conflict_missing_revision"
        if incoming_revision > int(existing_revision):
            return "newer"
        if incoming_revision < int(existing_revision):
            return "stale"
        return "conflict_same_revision"

    incoming_time = _timestamp_value(record.source_updated_at)
    existing_time = _timestamp_value(existing["source_updated_at"])
    if incoming_time is not None or existing_time is not None:
        if incoming_time is not None and existing_time is None:
            return "newer"
        if incoming_time is None:
            return "conflict_missing_timestamp"
        if incoming_time > existing_time:
            return "newer"
        if incoming_time < existing_time:
            return "stale"
        return "conflict_same_timestamp"
    return "conflict_no_version"


def _record_params(record: CanonicalWorkOrder, now: str, batch_id: str) -> tuple[Any, ...]:
    return (
        record.source_system,
        record.work_order_id,
        record.asset_id,
        record.status,
        record.planned_start,
        record.duration_hours,
        record.required_skill,
        record.part_id,
        record.part_qty,
        record.action,
        record.scheduled_cycle,
        record.decision_id,
        record.approval_id,
        record.external_revision,
        record.source_updated_at,
        record.source_fingerprint,
        _canonical_json(record.to_dict()),
        now,
        batch_id,
    )


def reconcile_work_orders(
    records: Iterable[Mapping[str, Any] | CanonicalWorkOrder],
    db_path: str | Path,
    *,
    source_system: str,
    actor: str = "cmms_sync",
    audit_store: Any | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile an external batch without duplicate inserts or silent overwrites."""

    normalized = validate_work_orders(records, source_system=source_system)
    resolved_batch_id = batch_id or batch_id_for_work_orders(normalized)
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "conflicts": 0, "stale": 0}
    outcomes: list[dict[str, Any]] = []
    audit_event_id: int | None = None
    now = _utc_now()
    _ensure_schema(db_path)
    with closing(connect_state(db_path)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("BEGIN IMMEDIATE")
        for record in normalized:
            existing = con.execute(
                "SELECT * FROM cmms_work_orders WHERE source_system=? AND work_order_id=?",
                (record.source_system, record.work_order_id),
            ).fetchone()
            if existing is None:
                con.execute(
                    """INSERT INTO cmms_work_orders(
                       source_system,work_order_id,asset_id,status,planned_start,duration_hours,
                       required_skill,part_id,part_qty,action,scheduled_cycle,decision_id,approval_id,
                       external_revision,source_updated_at,source_fingerprint,payload,last_synced_at,last_batch_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _record_params(record, now, resolved_batch_id),
                )
                counts["inserted"] += 1
                outcome = "inserted"
                reason = "new_external_work_order"
            elif existing["source_fingerprint"] == record.source_fingerprint:
                counts["skipped"] += 1
                outcome = "skipped"
                reason = "idempotent_replay"
            else:
                decision = _version_decision(record, existing)
                if decision == "newer":
                    con.execute(
                        """UPDATE cmms_work_orders SET
                           asset_id=?,status=?,planned_start=?,duration_hours=?,required_skill=?,part_id=?,
                           part_qty=?,action=?,scheduled_cycle=?,decision_id=?,approval_id=?,external_revision=?,
                           source_updated_at=?,source_fingerprint=?,payload=?,updated_at=?,last_synced_at=?,last_batch_id=?
                           WHERE source_system=? AND work_order_id=?""",
                        (
                            record.asset_id,
                            record.status,
                            record.planned_start,
                            record.duration_hours,
                            record.required_skill,
                            record.part_id,
                            record.part_qty,
                            record.action,
                            record.scheduled_cycle,
                            record.decision_id,
                            record.approval_id,
                            record.external_revision,
                            record.source_updated_at,
                            record.source_fingerprint,
                            _canonical_json(record.to_dict()),
                            now,
                            now,
                            resolved_batch_id,
                            record.source_system,
                            record.work_order_id,
                        ),
                    )
                    counts["updated"] += 1
                    outcome = "updated"
                    reason = "strictly_newer_external_version"
                elif decision == "stale":
                    counts["stale"] += 1
                    outcome = "stale"
                    reason = "older_external_version_ignored"
                else:
                    counts["conflicts"] += 1
                    outcome = "conflict"
                    reason = decision
            outcomes.append({
                "work_order_id": record.work_order_id,
                "status": outcome,
                "reason": reason,
                "source_fingerprint": record.source_fingerprint,
            })

        if audit_store is not None:
            audit_path = getattr(audit_store, "path", None)
            if audit_path is None or Path(audit_path).resolve() != Path(db_path).resolve():
                raise ValueError("audit_store must use the same database path for atomic linkage")
            claim_boundary = (
                "CMMS reconciliation records external exchange state; it does not prove "
                "that maintenance occurred in the field."
            )
            audit_event_id = audit_store.append_in_transaction(
                con,
                "cmms_work_order_reconciliation",
                actor,
                {
                    "contract_version": CONTRACT_VERSION,
                    "source_system": normalized[0].source_system,
                    "batch_id": resolved_batch_id,
                    "counts": counts,
                    "outcomes": outcomes,
                    "claim_boundary": claim_boundary,
                },
                subject=resolved_batch_id,
            )
        con.commit()

    summary = {
        "status": "APPLIED" if counts["conflicts"] == 0 else "PARTIAL_CONFLICT",
        "contract_version": CONTRACT_VERSION,
        "source_system": normalized[0].source_system,
        "batch_id": resolved_batch_id,
        "counts": counts,
        "outcomes": outcomes,
        "conflict_policy": "Older or unversioned conflicting records never overwrite the existing external mirror.",
        "claim_boundary": "CMMS reconciliation records external exchange state; it does not prove that maintenance occurred in the field.",
    }
    if audit_event_id is not None:
        summary["audit_event_id"] = audit_event_id
    return summary


def list_cmms_work_orders(
    db_path: str | Path,
    *,
    source_system: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_schema(db_path)
    query = "SELECT * FROM cmms_work_orders"
    params: tuple[Any, ...] = ()
    if source_system:
        query += " WHERE source_system=?"
        params = (source_system,)
    query += " ORDER BY source_system, work_order_id"
    with closing(connect_state(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = json.loads(row["payload"])
        item.update({
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_synced_at": row["last_synced_at"],
            "last_batch_id": row["last_batch_id"],
        })
        result.append(item)
    return result


__all__ = [
    "CONTRACT_VERSION",
    "WORK_ORDER_STATUSES",
    "CanonicalWorkOrder",
    "batch_id_for_work_orders",
    "canonicalize_work_order",
    "export_work_orders_jsonl",
    "list_cmms_work_orders",
    "reconcile_work_orders",
    "validate_work_orders",
    "work_orders_jsonl",
]
