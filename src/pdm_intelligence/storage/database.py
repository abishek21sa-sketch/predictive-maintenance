"""Unified state-database connections for reference and managed runtimes.

The reference deployment uses SQLite.  A managed deployment uses the same
application stores through SQLAlchemy and PostgreSQL; it never falls back to
the local path when the managed declaration cannot be honored.  The small
DB-API compatibility wrapper keeps the existing store code transactionally
portable while the application is migrated away from SQLite-specific
connection behavior.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .managed_state import (
    ManagedStateRuntimeError,
    ensure_state_runtime_available,
)

_QMARK = re.compile(r"\?")
_AUTOINCREMENT = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE
)
_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", re.IGNORECASE)
_TRIGGER = re.compile(r"^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER|DROP\s+TRIGGER)", re.IGNORECASE)
STATE_SCHEMA_VERSION = "1"
STATE_MIGRATION_ID = "pdm-state-schema-v1"
STATE_MIGRATION_TABLE = "pdm_schema_migrations"
AUDIT_APPEND_ONLY_GUARD_FUNCTION = "pdm_audit_append_only_guard"
AUDIT_APPEND_ONLY_GUARDS = (
    ("audit_events", "audit_events_no_mutation"),
    ("approval_records", "approval_records_no_mutation"),
)
STATE_TABLES = (
    "assets",
    "feature_store",
    "predictions",
    "decisions",
    "audit_events",
    "approval_records",
    "external_datasets",
    "external_models",
    "work_orders",
    "cmms_work_orders",
    "cmms_inventory",
)
_SCHEMA_MIGRATION_CONTEXT: ContextVar[bool] = ContextVar(
    "pdm_schema_migration_context", default=False
)


def is_managed_backend() -> bool:
    """Return whether the process explicitly selected the managed backend."""

    import os

    return os.getenv("PDM_STATE_BACKEND", "sqlite").strip().lower() == "managed"


@contextmanager
def managed_schema_migration():
    """Authorize schema DDL for the explicit managed migration command only."""

    token = _SCHEMA_MIGRATION_CONTEXT.set(True)
    try:
        yield
    finally:
        _SCHEMA_MIGRATION_CONTEXT.reset(token)


def ensure_schema_mutation_allowed(_state_path: str | Path | None = None) -> bool:
    """Allow DDL only for migration, or verify an existing managed schema.

    Returns ``True`` when the caller may run its initializer and ``False`` when
    a managed schema was already verified and the initializer must be skipped.
    """

    if not is_managed_backend() or _SCHEMA_MIGRATION_CONTEXT.get():
        return True
    report = managed_state_schema_check()
    if report.get("status") != "PASS_MANAGED_SCHEMA":
        raise ManagedStateRuntimeError("managed_state_schema_migration_required")
    return False


def _bind_qmarks(statement: str, parameters: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    values = tuple(parameters)
    names = [f"pdm_param_{index}" for index in range(len(values))]
    if statement.count("?") != len(values):
        raise ValueError("managed SQL parameter count does not match statement placeholders")
    bound = dict(zip(names, values))
    return _QMARK.sub(lambda _match: f":{names.pop(0)}", statement), bound


class ManagedRow(Mapping[str, Any]):
    """Mapping row with the integer indexing behavior used by sqlite3.Row."""

    def __init__(self, values: Mapping[str, Any]):
        self._values = dict(values)
        self._keys = tuple(self._values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[self._keys[key]]
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def keys(self):
        return self._keys


class ManagedResult:
    """Small result facade shared by the existing state stores."""

    def __init__(self, result: Any):
        self._result = result
        self.rowcount = result.rowcount
        self.lastrowid = getattr(result, "lastrowid", None)

    def fetchone(self) -> ManagedRow | None:
        row = self._result.fetchone()
        return ManagedRow(row._mapping) if row is not None else None

    def fetchall(self) -> list[ManagedRow]:
        return [ManagedRow(row._mapping) for row in self._result.fetchall()]


class ManagedConnection:
    """DB-API-like facade over one SQLAlchemy PostgreSQL connection."""

    def __init__(self, url: str):
        from sqlalchemy import create_engine

        normalized_url = url.strip()
        if normalized_url.startswith("postgresql://"):
            normalized_url = "postgresql+psycopg://" + normalized_url[len("postgresql://"):]
        self._engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            pool_timeout=3,
            connect_args={"connect_timeout": 3},
            future=True,
        )
        self._connection = self._engine.connect()
        self._closed = False

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _value):
        # SQLAlchemy rows are always returned as ManagedRow instances.
        return None

    def execute(self, statement: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> ManagedResult:
        from sqlalchemy import text

        sql = statement.strip().rstrip(";")
        if not sql:
            raise ValueError("managed SQL statement cannot be empty")
        pragma = re.fullmatch(r"PRAGMA\s+table_info\(([^)]+)\)", sql, flags=re.IGNORECASE)
        if pragma:
            table_name = pragma.group(1).strip().strip('"`[]')
            result = self._connection.execute(
                text(
                    "SELECT 0 AS cid, column_name AS name "
                    "FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name=:table_name "
                    "ORDER BY ordinal_position"
                ),
                {"table_name": table_name},
            )
            return ManagedResult(result)
        if sql.upper().startswith("PRAGMA "):
            # SQLite performance pragmas have no PostgreSQL equivalent.  The
            # managed engine uses a bounded pool and server-side transactions.
            return ManagedResult(_EmptyResult())
        if sql.upper().startswith("BEGIN IMMEDIATE"):
            sql = "BEGIN"
        sql = _AUTOINCREMENT.sub(
            "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", sql
        )
        if isinstance(parameters, Mapping):
            bound = dict(parameters)
        else:
            sql, bound = _bind_qmarks(sql, parameters)
        if _INSERT_OR_IGNORE.match(sql):
            sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", sql, count=1, flags=re.IGNORECASE)
            if " ON CONFLICT " not in sql.upper():
                sql += " ON CONFLICT DO NOTHING"
        return ManagedResult(self._connection.execute(text(sql), bound))

    def executescript(self, script: str) -> None:
        # Repository schemas are DDL statements separated by semicolons.  Do
        # not use a server-side script endpoint; each statement is bound to
        # this connection and remains inside SQLAlchemy's transaction.
        for raw_statement in script.split(";"):
            sql = raw_statement.strip()
            if not sql or sql.startswith("--") or _TRIGGER.match(sql):
                continue
            sql = _AUTOINCREMENT.sub(
                "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", sql
            )
            self.execute(sql)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._engine.dispose()
            self._closed = True


class _EmptyResult:
    rowcount = -1
    lastrowid = None

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def connect_state(path: str | Path):
    """Open the configured state backend without permitting silent fallback."""

    if not is_managed_backend():
        local_path = Path(path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(local_path)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    ensure_state_runtime_available()
    import os

    raw_url = os.getenv("PDM_DATABASE_URL", "").strip()
    if not raw_url:
        raise ManagedStateRuntimeError("managed_database_url_missing")
    try:
        return ManagedConnection(raw_url)
    except ManagedStateRuntimeError:
        raise
    except Exception as exc:
        raise ManagedStateRuntimeError("managed_database_unavailable") from exc


def _initialize_state_stores(state_path: Path) -> None:
    """Create repository state tables for an explicit migration command."""

    from pdm_intelligence.external.catalog import ExternalCatalog
    from pdm_intelligence.governance.audit import AuditStore
    from pdm_intelligence.integrations.cmms import _ensure_schema as ensure_cmms_schema
    from pdm_intelligence.integrations.inventory import (
        _ensure_schema as ensure_inventory_schema,
    )
    from pdm_intelligence.operations.command import _ensure_work_orders
    from pdm_intelligence.storage.registry import AssetRegistry, FeatureStore
    from pdm_intelligence.storage.sqlite import SQLiteStore

    AssetRegistry(state_path)
    FeatureStore(state_path)
    SQLiteStore(state_path)
    AuditStore(state_path)
    ExternalCatalog(state_path)
    _ensure_work_orders(state_path)
    ensure_cmms_schema(state_path)
    ensure_inventory_schema(state_path)


def initialize_managed_state_schema() -> dict[str, object]:
    """Apply the repository schema and record its version explicitly.

    This mutating operation is intentionally separate from readiness checks so
    a probe cannot turn an un-migrated database into a passing deployment.
    """

    if not is_managed_backend():
        return {
            "status": "NOT_RUN",
            "reason": "managed_backend_not_selected",
            "schema_version": STATE_SCHEMA_VERSION,
            "migration_id": STATE_MIGRATION_ID,
        }
    import os

    state_path = Path(os.getenv("PDM_STATE_DB", "data/pdm.db"))
    try:
        with managed_schema_migration():
            _initialize_state_stores(state_path)
            with closing(connect_state(state_path)) as con:
                con.execute(
                    f"""CREATE TABLE IF NOT EXISTS {STATE_MIGRATION_TABLE}(
                       schema_name TEXT PRIMARY KEY,
                       schema_version TEXT NOT NULL,
                       migration_id TEXT NOT NULL,
                       applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
                )
                for table_name in STATE_TABLES:
                    con.execute(
                        f"INSERT INTO {STATE_MIGRATION_TABLE}(schema_name,schema_version,migration_id) "
                        "VALUES(?,?,?) ON CONFLICT(schema_name) DO UPDATE SET "
                        "schema_version=excluded.schema_version, migration_id=excluded.migration_id",
                        (table_name, STATE_SCHEMA_VERSION, STATE_MIGRATION_ID),
                    )
                con.commit()
    except Exception:  # noqa: BLE001 - migration output must not expose driver details
        return {
            "status": "NOT_READY",
            "reason": "managed_state_migration_failed",
            "schema_version": STATE_SCHEMA_VERSION,
            "migration_id": STATE_MIGRATION_ID,
        }
    return {
        "status": "PASS_MANAGED_MIGRATION",
        "reason": "managed_state_schema_initialized_and_version_recorded",
        "schema_version": STATE_SCHEMA_VERSION,
        "migration_id": STATE_MIGRATION_ID,
        "tables": list(STATE_TABLES),
    }


def managed_state_schema_check() -> dict[str, object]:
    """Verify managed schema, migrations, and audit guards without mutation."""

    if not is_managed_backend():
        return {
            "status": "NOT_RUN",
            "reason": "managed_backend_not_selected",
            "stores": [],
        }
    import os

    state_path = Path(os.getenv("PDM_STATE_DB", "data/pdm.db"))
    stores = (
        "asset_registry",
        "feature_store",
        "prediction_store",
        "audit_store",
        "external_catalog",
        "operator_work_orders",
        "cmms_work_orders",
        "cmms_inventory",
    )
    try:
        with closing(connect_state(state_path)) as con:
            table_names = (*STATE_TABLES, STATE_MIGRATION_TABLE)
            placeholders = ",".join("?" for _ in table_names)
            rows = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_name IN (" + placeholders + ")",
                table_names,
            ).fetchall()
            present = {row[0] for row in rows}
            missing_tables = sorted(set(table_names) - present)
            if missing_tables:
                return {
                    "status": "NOT_READY",
                    "reason": "managed_schema_or_migration_table_missing",
                    "stores": list(stores),
                    "missing_tables": missing_tables,
                    "schema_version": STATE_SCHEMA_VERSION,
                    "migration_id": STATE_MIGRATION_ID,
                }
            migration_placeholders = ",".join("?" for _ in STATE_TABLES)
            migration_rows = con.execute(
                f"SELECT schema_name,schema_version,migration_id FROM {STATE_MIGRATION_TABLE} "
                "WHERE schema_name IN (" + migration_placeholders + ")",
                STATE_TABLES,
            ).fetchall()
            migrations = {row[0]: (row[1], row[2]) for row in migration_rows}
            missing_migrations = sorted(set(STATE_TABLES) - set(migrations))
            mismatched_migrations = sorted(
                name
                for name, (version, migration_id) in migrations.items()
                if version != STATE_SCHEMA_VERSION or migration_id != STATE_MIGRATION_ID
            )
            if missing_migrations or mismatched_migrations:
                return {
                    "status": "NOT_READY",
                    "reason": "managed_schema_migration_not_verified",
                    "stores": list(stores),
                    "missing_migrations": missing_migrations,
                    "mismatched_migrations": mismatched_migrations,
                    "schema_version": STATE_SCHEMA_VERSION,
                    "migration_id": STATE_MIGRATION_ID,
                }
            function_rows = con.execute(
                "SELECT p.proname FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=current_schema() AND p.proname=?",
                (AUDIT_APPEND_ONLY_GUARD_FUNCTION,),
            ).fetchall()
            present_functions = {row[0] for row in function_rows}
            trigger_placeholders = ",".join(
                "(?, ?)" for _ in AUDIT_APPEND_ONLY_GUARDS
            )
            trigger_rows = con.execute(
                "SELECT c.relname,t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND NOT t.tgisinternal "
                "AND (c.relname,t.tgname) IN ("
                + trigger_placeholders
                + ")",
                tuple(value for pair in AUDIT_APPEND_ONLY_GUARDS for value in pair),
            ).fetchall()
            present_guards = {(row[0], row[1]) for row in trigger_rows}
            missing_audit_guards = []
            if AUDIT_APPEND_ONLY_GUARD_FUNCTION not in present_functions:
                missing_audit_guards.append(AUDIT_APPEND_ONLY_GUARD_FUNCTION)
            missing_audit_guards.extend(
                f"{table}:{trigger}"
                for table, trigger in AUDIT_APPEND_ONLY_GUARDS
                if (table, trigger) not in present_guards
            )
            if missing_audit_guards:
                return {
                    "status": "NOT_READY",
                    "reason": "managed_audit_append_only_guards_not_verified",
                    "stores": list(stores),
                    "missing_audit_guards": missing_audit_guards,
                    "schema_version": STATE_SCHEMA_VERSION,
                    "migration_id": STATE_MIGRATION_ID,
                }
    except Exception:  # noqa: BLE001 - readiness output must not expose driver details
        return {
            "status": "NOT_READY",
            "reason": "managed_store_schema_check_failed",
            "stores": list(stores),
        }
    return {
        "status": "PASS_MANAGED_SCHEMA",
        "reason": "all_stateful_store_schemas_migrations_and_audit_guards_verified_without_mutation",
        "stores": list(stores),
        "schema_version": STATE_SCHEMA_VERSION,
        "migration_id": STATE_MIGRATION_ID,
        "tables": list(STATE_TABLES),
        "audit_guard_function": AUDIT_APPEND_ONLY_GUARD_FUNCTION,
        "audit_append_only_guards": [
            f"{table}:{trigger}" for table, trigger in AUDIT_APPEND_ONLY_GUARDS
        ],
    }


__all__ = [
    "STATE_MIGRATION_ID",
    "STATE_SCHEMA_VERSION",
    "ManagedConnection",
    "ManagedRow",
    "connect_state",
    "ensure_schema_mutation_allowed",
    "initialize_managed_state_schema",
    "is_managed_backend",
    "managed_schema_migration",
    "managed_state_schema_check",
]
