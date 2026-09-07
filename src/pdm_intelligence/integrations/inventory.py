"""Canonical CMMS/ERP inventory exchange and reconciliation boundary."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any

from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

from .cmms import (
    _canonical_json,
    _digest,
    _nonnegative_integer,
    _text,
    _timestamp,
    _utc_now,
    _version_decision,
)

CONTRACT_VERSION = "CMMS-INVENTORY.v1"

_CONTRACT_FIELDS = (
    "source_system",
    "part_id",
    "description",
    "location",
    "on_hand",
    "reserved",
    "available",
    "unit_cost",
    "external_revision",
    "source_updated_at",
)

CMMS_INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS cmms_inventory(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_system TEXT NOT NULL,
  part_id TEXT NOT NULL,
  description TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  on_hand REAL NOT NULL,
  reserved REAL NOT NULL,
  available REAL NOT NULL,
  unit_cost REAL NOT NULL,
  external_revision INTEGER,
  source_updated_at TEXT,
  source_fingerprint TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at TEXT NOT NULL,
  last_batch_id TEXT NOT NULL DEFAULT '',
  UNIQUE(source_system, part_id, location)
);
CREATE INDEX IF NOT EXISTS ix_cmms_inventory_source
  ON cmms_inventory(source_system, updated_at DESC);
"""


def _quantity(value: Any, field: str, *, allow_zero: bool = True) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not isfinite(result) or (result < 0 if allow_zero else result <= 0):
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{field} must be finite and {qualifier}")
    return result


@dataclass(frozen=True)
class CanonicalInventory:
    source_system: str
    part_id: str
    description: str
    location: str
    on_hand: float
    reserved: float
    unit_cost: float
    external_revision: int | None = None
    source_updated_at: str | None = None

    @property
    def available(self) -> float:
        return float(self.on_hand - self.reserved)

    @property
    def source_fingerprint(self) -> str:
        return _digest(_canonical_json(self.to_dict(include_fingerprint=False)))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        result = {field: getattr(self, field) for field in _CONTRACT_FIELDS if field != "available"}
        result["available"] = self.available
        result["contract_version"] = CONTRACT_VERSION
        if include_fingerprint:
            result["source_fingerprint"] = self.source_fingerprint
        return result


def canonicalize_inventory(
    record: Mapping[str, Any] | CanonicalInventory,
    *,
    source_system: str,
) -> CanonicalInventory:
    """Validate and normalize one inventory position to the v1 contract."""

    if isinstance(record, CanonicalInventory):
        if record.source_system != source_system:
            raise ValueError("record source_system does not match the reconciliation source_system")
        return record
    if not isinstance(record, Mapping):
        raise ValueError("each inventory position must be an object")  # noqa: TRY004
    requested_source = _text(source_system, "source_system", max_length=80)
    source = _text(record.get("source_system", requested_source), "source_system", max_length=80)
    if source != requested_source:
        raise ValueError("record source_system does not match the reconciliation source_system")
    on_hand = _quantity(record.get("on_hand"), "on_hand")
    reserved = _quantity(record.get("reserved"), "reserved")
    if reserved > on_hand:
        raise ValueError("reserved cannot exceed on_hand")
    return CanonicalInventory(
        source_system=requested_source,
        part_id=_text(record.get("part_id"), "part_id", max_length=120),
        description=_text(record.get("description"), "description", max_length=240),
        location=_text(record.get("location", record.get("warehouse_location", "")), "location", required=False, max_length=120) or "",
        on_hand=on_hand,
        reserved=reserved,
        unit_cost=_quantity(record.get("unit_cost"), "unit_cost"),
        external_revision=_nonnegative_integer(
            record.get("external_revision"), "external_revision", required=False
        ),
        source_updated_at=_timestamp(record.get("source_updated_at"), "source_updated_at"),
    )


def validate_inventory_records(
    records: Iterable[Mapping[str, Any] | CanonicalInventory],
    *,
    source_system: str,
) -> list[CanonicalInventory]:
    normalized_source = _text(source_system, "source_system", max_length=80)
    result = [canonicalize_inventory(record, source_system=normalized_source) for record in records]
    if not result:
        raise ValueError("at least one inventory position is required")
    seen: set[tuple[str, str]] = set()
    for record in result:
        identity = (record.part_id, record.location)
        if identity in seen:
            raise ValueError(f"duplicate inventory position in batch: {record.part_id}@{record.location or 'default'}")
        seen.add(identity)
    return result


def batch_id_for_inventory(records: Iterable[CanonicalInventory]) -> str:
    material = [
        record.to_dict()
        for record in sorted(records, key=lambda item: (item.source_system, item.part_id, item.location))
    ]
    return "INV-" + _digest(_canonical_json(material))[:16].upper()


def inventory_jsonl(
    records: Iterable[Mapping[str, Any] | CanonicalInventory],
    *,
    source_system: str,
    batch_id: str | None = None,
) -> tuple[str, str]:
    normalized = validate_inventory_records(records, source_system=source_system)
    resolved_batch_id = batch_id or batch_id_for_inventory(normalized)
    lines = []
    for record in sorted(normalized, key=lambda item: (item.source_system, item.part_id, item.location)):
        payload = record.to_dict()
        payload["batch_id"] = resolved_batch_id
        lines.append(_canonical_json(payload) + "\n")
    return "".join(lines), resolved_batch_id


def export_inventory_jsonl(
    records: Iterable[Mapping[str, Any] | CanonicalInventory],
    path: str | Path,
    *,
    source_system: str,
    batch_id: str | None = None,
) -> Path:
    content, _ = inventory_jsonl(records, source_system=source_system, batch_id=batch_id)
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
        con.executescript(CMMS_INVENTORY_SCHEMA)
        columns = {row[1] for row in con.execute("PRAGMA table_info(cmms_inventory)")}
        if "last_batch_id" not in columns:
            con.execute("ALTER TABLE cmms_inventory ADD COLUMN last_batch_id TEXT NOT NULL DEFAULT ''")
        con.commit()


def _record_params(record: CanonicalInventory, now: str, batch_id: str) -> tuple[Any, ...]:
    return (
        record.source_system,
        record.part_id,
        record.description,
        record.location,
        record.on_hand,
        record.reserved,
        record.available,
        record.unit_cost,
        record.external_revision,
        record.source_updated_at,
        record.source_fingerprint,
        _canonical_json(record.to_dict()),
        now,
        batch_id,
    )


def reconcile_inventory(
    records: Iterable[Mapping[str, Any] | CanonicalInventory],
    db_path: str | Path,
    *,
    source_system: str,
    actor: str = "cmms_sync",
    audit_store: Any | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Reconcile inventory positions without duplicate rows or stale overwrites."""

    normalized = validate_inventory_records(records, source_system=source_system)
    resolved_batch_id = batch_id or batch_id_for_inventory(normalized)
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
                "SELECT * FROM cmms_inventory WHERE source_system=? AND part_id=? AND location=?",
                (record.source_system, record.part_id, record.location),
            ).fetchone()
            if existing is None:
                con.execute(
                    """INSERT INTO cmms_inventory(
                       source_system,part_id,description,location,on_hand,reserved,available,unit_cost,
                       external_revision,source_updated_at,source_fingerprint,payload,last_synced_at,last_batch_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _record_params(record, now, resolved_batch_id),
                )
                counts["inserted"] += 1
                outcome = "inserted"
                reason = "new_external_inventory_position"
            elif existing["source_fingerprint"] == record.source_fingerprint:
                counts["skipped"] += 1
                outcome = "skipped"
                reason = "idempotent_replay"
            else:
                decision = _version_decision(record, existing)
                if decision == "newer":
                    con.execute(
                        """UPDATE cmms_inventory SET
                           description=?,on_hand=?,reserved=?,available=?,unit_cost=?,external_revision=?,
                           source_updated_at=?,source_fingerprint=?,payload=?,updated_at=?,last_synced_at=?,last_batch_id=?
                           WHERE source_system=? AND part_id=? AND location=?""",
                        (
                            record.description,
                            record.on_hand,
                            record.reserved,
                            record.available,
                            record.unit_cost,
                            record.external_revision,
                            record.source_updated_at,
                            record.source_fingerprint,
                            _canonical_json(record.to_dict()),
                            now,
                            now,
                            resolved_batch_id,
                            record.source_system,
                            record.part_id,
                            record.location,
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
                "part_id": record.part_id,
                "location": record.location,
                "status": outcome,
                "reason": reason,
                "source_fingerprint": record.source_fingerprint,
            })

        if audit_store is not None:
            audit_path = getattr(audit_store, "path", None)
            if audit_path is None or Path(audit_path).resolve() != Path(db_path).resolve():
                raise ValueError("audit_store must use the same database path for atomic linkage")
            claim_boundary = (
                "CMMS inventory reconciliation records external stock state; it does not prove "
                "physical stock or parts availability without a site-controlled count process."
            )
            audit_event_id = audit_store.append_in_transaction(
                con,
                "cmms_inventory_reconciliation",
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
        "claim_boundary": "CMMS inventory reconciliation records external stock state; it does not prove physical stock or parts availability without a site-controlled count process.",
    }
    if audit_event_id is not None:
        summary["audit_event_id"] = audit_event_id
    return summary


def list_cmms_inventory(
    db_path: str | Path,
    *,
    source_system: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_schema(db_path)
    query = "SELECT * FROM cmms_inventory"
    params: tuple[Any, ...] = ()
    if source_system:
        query += " WHERE source_system=?"
        params = (source_system,)
    query += " ORDER BY source_system, part_id, location"
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
    "CanonicalInventory",
    "batch_id_for_inventory",
    "canonicalize_inventory",
    "export_inventory_jsonl",
    "inventory_jsonl",
    "list_cmms_inventory",
    "reconcile_inventory",
    "validate_inventory_records",
]
