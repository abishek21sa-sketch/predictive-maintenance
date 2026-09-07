"""Tamper-evident audit and human approval persistence.

SQLite is retained as the local reference implementation, but the application
layer provides the properties a production adapter must preserve: append-only
tables, chained hashes, verification, deterministic approval IDs, and a
portable JSONL export.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pdm_intelligence.storage.database import (
    AUDIT_APPEND_ONLY_GUARD_FUNCTION,
    AUDIT_APPEND_ONLY_GUARDS,
    connect_state,
    ensure_schema_mutation_allowed,
    is_managed_backend,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _event_hash(
    event_type: str,
    actor: str,
    subject: str | None,
    payload_sha256: str,
    created_at: str,
    previous_hash: str,
) -> str:
    return _digest(
        _canonical_json(
            {
                "actor": actor,
                "created_at": created_at,
                "event_type": event_type,
                "payload_sha256": payload_sha256,
                "previous_hash": previous_hash,
                "subject": subject or "",
            }
        )
    )


class AuditStore:
    """Append-only, hash-chained local audit trail for system actions."""

    def __init__(self, path: str | Path = "data/pdm.db"):
        if not ensure_schema_mutation_allowed(path):
            self.path = Path(path)
            return
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_state(self.path)) as con:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute(
                """CREATE TABLE IF NOT EXISTS audit_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_type TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  subject TEXT,
                  payload TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  previous_hash TEXT NOT NULL DEFAULT '',
                  payload_sha256 TEXT NOT NULL DEFAULT '',
                  event_hash TEXT NOT NULL DEFAULT '')"""
            )
            con.execute(
                """CREATE TABLE IF NOT EXISTS approval_records(
                  approval_id TEXT PRIMARY KEY,
                  decision_id TEXT NOT NULL,
                  action TEXT NOT NULL,
                  approver TEXT NOT NULL,
                  approver_role TEXT NOT NULL,
                  rationale TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(decision_id, action, approver))"""
            )
            self._ensure_audit_columns(con)
            self._backfill_hash_chain(con)
            con.executescript(
                """CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                   BEFORE UPDATE ON audit_events
                   BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
                 CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                   BEFORE DELETE ON audit_events
                   BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
                 CREATE TRIGGER IF NOT EXISTS approval_records_no_update
                   BEFORE UPDATE ON approval_records
                   BEGIN SELECT RAISE(ABORT, 'approval_records is append-only'); END;
                 CREATE TRIGGER IF NOT EXISTS approval_records_no_delete
                   BEFORE DELETE ON approval_records
                   BEGIN SELECT RAISE(ABORT, 'approval_records is append-only'); END;"""
            )
            if is_managed_backend():
                self._ensure_managed_append_only_guards(con)
            con.commit()

    @staticmethod
    def _ensure_managed_append_only_guards(con: Any) -> None:
        """Install database-enforced append-only guards for PostgreSQL."""

        con.execute(
            f"""CREATE OR REPLACE FUNCTION {AUDIT_APPEND_ONLY_GUARD_FUNCTION}()
               RETURNS trigger LANGUAGE plpgsql AS $pdm$
               BEGIN
                 RAISE EXCEPTION 'audit records are append-only';
               END;
               $pdm$"""
        )
        for table, trigger in AUDIT_APPEND_ONLY_GUARDS:
            con.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
            con.execute(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION {AUDIT_APPEND_ONLY_GUARD_FUNCTION}()"
            )

    @staticmethod
    def _ensure_audit_columns(con: sqlite3.Connection) -> None:
        columns = {row[1] for row in con.execute("PRAGMA table_info(audit_events)")}
        for name in ("previous_hash", "payload_sha256", "event_hash"):
            if name not in columns:
                con.execute(f"ALTER TABLE audit_events ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _backfill_hash_chain(con: sqlite3.Connection) -> None:
        rows = con.execute(
            "SELECT id,event_type,actor,subject,payload,created_at,previous_hash,payload_sha256,event_hash "
            "FROM audit_events ORDER BY id"
        ).fetchall()
        if not rows or all(row[8] and row[7] for row in rows):
            return
        previous_hash = ""
        for row in rows:
            try:
                payload_json = _canonical_json(json.loads(row[4]))
            except (TypeError, json.JSONDecodeError):
                payload_json = row[4]
            payload_sha256 = _digest(payload_json)
            event_hash = _event_hash(row[1], row[2], row[3], payload_sha256, row[5], previous_hash)
            con.execute(
                "UPDATE audit_events SET previous_hash=?,payload_sha256=?,event_hash=? WHERE id=?",
                (previous_hash, payload_sha256, event_hash, row[0]),
            )
            previous_hash = event_hash

    def append(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        subject: str | None = None,
    ) -> int:
        """Append one event and link it to the previous event hash."""

        with closing(connect_state(self.path)) as con:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("BEGIN IMMEDIATE")
            event_id = self.append_in_transaction(con, event_type, actor, payload, subject=subject)
            con.commit()
            return event_id

    def append_in_transaction(
        self,
        con: sqlite3.Connection,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        subject: str | None = None,
    ) -> int:
        """Append to a caller-owned transaction without committing it.

        Integration boundaries use this method to commit the mirrored state
        and its audit event atomically. The caller owns rollback/commit.
        """

        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type and actor are required")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("event_type and actor are required")
        payload_json = _canonical_json(payload)
        payload_sha256 = _digest(payload_json)
        created_at = _utc_now()
        previous_hash_row = con.execute(
            "SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        previous_hash = (previous_hash_row[0] if previous_hash_row else "") or ""
        event_hash = _event_hash(
            event_type, actor, subject, payload_sha256, created_at, previous_hash
        )
        cur = con.execute(
            "INSERT INTO audit_events(event_type,actor,subject,payload,created_at,previous_hash,payload_sha256,event_hash) "
            "VALUES(?,?,?,?,?,?,?,?) RETURNING id",
            (event_type, actor, subject, payload_json, created_at, previous_hash, payload_sha256, event_hash),
        )
        inserted = cur.fetchone()
        if inserted is None:
            raise RuntimeError("audit event insert did not return an identifier")
        return int(inserted[0])

    @staticmethod
    def _decode_event(row: sqlite3.Row | tuple) -> dict[str, Any]:
        values = dict(row) if isinstance(row, sqlite3.Row) or hasattr(row, "keys") else {
            "id": row[0],
            "event_type": row[1],
            "actor": row[2],
            "subject": row[3],
            "payload": row[4],
            "created_at": row[5],
            "previous_hash": row[6],
            "payload_sha256": row[7],
            "event_hash": row[8],
        }
        values["payload"] = json.loads(values["payload"])
        return values

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 10_000)
        with closing(connect_state(self.path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id,event_type,actor,subject,payload,created_at,previous_hash,payload_sha256,event_hash "
                "FROM audit_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._decode_event(row) for row in rows]

    def verify_chain(self) -> dict[str, Any]:
        """Verify event payload hashes, links, and event hashes in order."""

        with closing(connect_state(self.path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id,event_type,actor,subject,payload,created_at,previous_hash,payload_sha256,event_hash "
                "FROM audit_events ORDER BY id"
            ).fetchall()
        errors: list[dict[str, Any]] = []
        previous_hash = ""
        for row in rows:
            try:
                payload_json = _canonical_json(json.loads(row["payload"]))
            except (TypeError, json.JSONDecodeError):
                errors.append({"id": row["id"], "reason": "payload_not_valid_json"})
                payload_json = row["payload"]
            expected_payload_hash = _digest(payload_json)
            expected_event_hash = _event_hash(
                row["event_type"],
                row["actor"],
                row["subject"],
                expected_payload_hash,
                row["created_at"],
                previous_hash,
            )
            if row["previous_hash"] != previous_hash:
                errors.append({"id": row["id"], "reason": "previous_hash_mismatch"})
            if row["payload_sha256"] != expected_payload_hash:
                errors.append({"id": row["id"], "reason": "payload_hash_mismatch"})
            if row["event_hash"] != expected_event_hash:
                errors.append({"id": row["id"], "reason": "event_hash_mismatch"})
            previous_hash = row["event_hash"]
        return {"valid": not errors, "checked_events": len(rows), "head_hash": previous_hash, "errors": errors}

    def export_jsonl(self, limit: int = 10_000) -> str:
        """Export the verified event representation in chronological JSONL order."""

        limit = min(max(int(limit), 1), 100_000)
        with closing(connect_state(self.path)) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT id,event_type,actor,subject,payload,created_at,previous_hash,payload_sha256,event_hash "
                "FROM audit_events ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return "".join(_canonical_json(self._decode_event(row)) + "\n" for row in rows)

    def decision_exists(self, decision_id: str) -> bool:
        with closing(connect_state(self.path)) as con:
            return con.execute(
                "SELECT 1 FROM audit_events WHERE event_type=? AND subject=? LIMIT 1",
                ("belief_maint_decision", decision_id),
            ).fetchone() is not None

    def record_approval(
        self,
        decision_id: str,
        action: str,
        approver: str,
        approver_role: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Record an idempotent human approval or rejection for a decision."""

        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        if not rationale.strip():
            raise ValueError("rationale is required")
        approval_id = "APR-" + _digest(f"{decision_id}|{action}|{approver}")[:16].upper()
        with closing(connect_state(self.path)) as con:
            con.execute("PRAGMA busy_timeout=5000")
            cur = con.execute(
                "INSERT OR IGNORE INTO approval_records(approval_id,decision_id,action,approver,approver_role,rationale,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (approval_id, decision_id, action, approver, approver_role, rationale.strip(), _utc_now()),
            )
            created = cur.rowcount == 1
            row = con.execute(
                "SELECT approval_id,decision_id,action,approver,approver_role,rationale,created_at "
                "FROM approval_records WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            con.commit()
        result = {
            "approval_id": row[0],
            "decision_id": row[1],
            "action": row[2],
            "approver": row[3],
            "approver_role": row[4],
            "rationale": row[5],
            "created_at": row[6],
            "idempotent_replay": not created,
        }
        if created:
            self.append("decision_approval", approver, result, subject=decision_id)
        return result

    def list_approvals(self, decision_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT approval_id,decision_id,action,approver,approver_role,rationale,created_at "
            "FROM approval_records"
        )
        params: tuple[Any, ...] = ()
        if decision_id:
            query += " WHERE decision_id=?"
            params = (decision_id,)
        query += " ORDER BY created_at DESC"
        with closing(connect_state(self.path)) as con:
            rows = con.execute(query, params).fetchall()
        return [
            {
                "approval_id": row[0],
                "decision_id": row[1],
                "action": row[2],
                "approver": row[3],
                "approver_role": row[4],
                "rationale": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]
