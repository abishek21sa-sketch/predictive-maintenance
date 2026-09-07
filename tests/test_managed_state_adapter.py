import pytest

from pdm_intelligence.storage import database
from pdm_intelligence.storage.managed_state import (
    ManagedStateRuntimeError,
    state_runtime_status,
)
from pdm_intelligence.storage.registry import AssetRegistry


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _FakeManagedConnection:
    def __init__(self, *, migration_rows, audit_guard_function_rows=None, audit_guard_rows=None):
        self.migration_rows = migration_rows
        self.audit_guard_function_rows = audit_guard_function_rows or []
        self.audit_guard_rows = audit_guard_rows or []
        self.commit_called = False

    def execute(self, statement, params=()):
        if "FROM information_schema.tables" in statement:
            return _FakeResult(
                [(table_name,) for table_name in (*database.STATE_TABLES, database.STATE_MIGRATION_TABLE)]
            )
        if f"SELECT schema_name,schema_version,migration_id FROM {database.STATE_MIGRATION_TABLE}" in statement:
            return _FakeResult(self.migration_rows)
        if "FROM pg_proc" in statement:
            return _FakeResult(self.audit_guard_function_rows)
        if "FROM pg_trigger" in statement:
            return _FakeResult(self.audit_guard_rows)
        raise AssertionError(f"unexpected read-only readiness SQL: {statement}")

    def commit(self):
        self.commit_called = True

    def close(self):
        return None


def test_managed_runtime_requires_explicit_postgresql_adapter(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")

    report = state_runtime_status()

    assert report["status"] == "MANAGED_ADAPTER_CONFIGURED"
    assert report["ready"] is True
    assert report["adapter"] == "sqlalchemy_postgresql"


def test_managed_connection_failure_is_safe_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")

    def fail(_url):
        raise RuntimeError("driver detail must not escape")

    monkeypatch.setattr(database, "ManagedConnection", fail)
    with pytest.raises(ManagedStateRuntimeError, match="managed_database_unavailable"):
        database.connect_state(tmp_path / "must-not-be-used.db")
    assert not (tmp_path / "must-not-be-used.db").exists()


def test_managed_sql_compatibility_preserves_qmark_parameters_and_rows():
    row = database.ManagedRow({"id": 7, "status": "COMMITTED"})

    assert row[0] == 7
    assert row["status"] == "COMMITTED"
    assert dict(row) == {"id": 7, "status": "COMMITTED"}
    sql, params = database._bind_qmarks("SELECT ? AS id, ? AS status", (7, "COMMITTED"))
    assert sql == "SELECT :pdm_param_0 AS id, :pdm_param_1 AS status"
    assert params == {"pdm_param_0": 7, "pdm_param_1": "COMMITTED"}


def test_managed_schema_check_fails_when_migration_ledger_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_STATE_DB", str(tmp_path / "ignored.db"))
    connection = _FakeManagedConnection(migration_rows=[])
    monkeypatch.setattr(database, "connect_state", lambda _state_path: connection)

    report = database.managed_state_schema_check()

    assert report["status"] == "NOT_READY"
    assert report["reason"] == "managed_schema_migration_not_verified"
    assert set(report["missing_migrations"]) == set(database.STATE_TABLES)
    assert connection.commit_called is False


def test_managed_schema_check_verifies_schema_without_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_STATE_DB", str(tmp_path / "ignored.db"))
    connection = _FakeManagedConnection(
        migration_rows=[
            (table_name, database.STATE_SCHEMA_VERSION, database.STATE_MIGRATION_ID)
            for table_name in database.STATE_TABLES
        ],
        audit_guard_function_rows=[(database.AUDIT_APPEND_ONLY_GUARD_FUNCTION,)],
        audit_guard_rows=list(database.AUDIT_APPEND_ONLY_GUARDS),
    )
    monkeypatch.setattr(database, "connect_state", lambda _state_path: connection)

    report = database.managed_state_schema_check()

    assert report["status"] == "PASS_MANAGED_SCHEMA"
    assert "without_mutation" in report["reason"]
    assert report["migration_id"] == database.STATE_MIGRATION_ID
    assert connection.commit_called is False


def test_managed_schema_check_fails_when_audit_guards_are_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_STATE_DB", str(tmp_path / "ignored.db"))
    connection = _FakeManagedConnection(
        migration_rows=[
            (table_name, database.STATE_SCHEMA_VERSION, database.STATE_MIGRATION_ID)
            for table_name in database.STATE_TABLES
        ]
    )
    monkeypatch.setattr(database, "connect_state", lambda _state_path: connection)

    report = database.managed_state_schema_check()

    assert report["status"] == "NOT_READY"
    assert report["reason"] == "managed_audit_append_only_guards_not_verified"
    assert set(report["missing_audit_guards"]) == {
        database.AUDIT_APPEND_ONLY_GUARD_FUNCTION,
        "audit_events:audit_events_no_mutation",
        "approval_records:approval_records_no_mutation",
    }
    assert connection.commit_called is False


def test_managed_runtime_rejects_implicit_schema_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setattr(
        database,
        "managed_state_schema_check",
        lambda: {"status": "NOT_READY", "reason": "migration_missing"},
    )

    with pytest.raises(ManagedStateRuntimeError, match="managed_state_schema_migration_required"):
        AssetRegistry(tmp_path / "must-not-be-created.db")

    assert not (tmp_path / "must-not-be-created.db").exists()


def test_managed_runtime_uses_verified_schema_without_running_initializer(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setattr(
        database,
        "managed_state_schema_check",
        lambda: {"status": "PASS_MANAGED_SCHEMA"},
    )

    registry = AssetRegistry(tmp_path / "already-migrated.db")

    assert registry.path == tmp_path / "already-migrated.db"
    assert not registry.path.exists()


def test_schema_mutation_authorization_is_scoped_to_explicit_migration_context(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setattr(
        database,
        "managed_state_schema_check",
        lambda: {"status": "NOT_READY"},
    )

    with database.managed_schema_migration():
        assert database.ensure_schema_mutation_allowed() is True

    with pytest.raises(ManagedStateRuntimeError, match="managed_state_schema_migration_required"):
        database.ensure_schema_mutation_allowed()


def test_all_managed_store_initializers_skip_ddl_after_schema_verification(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setattr(
        database,
        "managed_state_schema_check",
        lambda: {"status": "PASS_MANAGED_SCHEMA"},
    )

    from pdm_intelligence.external.catalog import ExternalCatalog
    from pdm_intelligence.governance.audit import AuditStore
    from pdm_intelligence.integrations.cmms import _ensure_schema as ensure_cmms_schema
    from pdm_intelligence.integrations.inventory import (
        _ensure_schema as ensure_inventory_schema,
    )
    from pdm_intelligence.operations.command import _ensure_work_orders
    from pdm_intelligence.storage.sqlite import SQLiteStore

    state_path = tmp_path / "must-remain-absent.db"
    AssetRegistry(state_path)
    database_path_objects = (
        ExternalCatalog,
        AuditStore,
        SQLiteStore,
    )
    for store_type in database_path_objects:
        store_type(state_path)
    _ensure_work_orders(state_path)
    ensure_cmms_schema(state_path)
    ensure_inventory_schema(state_path)

    assert not state_path.exists()
