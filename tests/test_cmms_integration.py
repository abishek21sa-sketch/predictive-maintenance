from __future__ import annotations

import json
import sqlite3
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app
from pdm_intelligence.governance.audit import AuditStore
from pdm_intelligence.integrations.cmms import (
    CONTRACT_VERSION,
    export_work_orders_jsonl,
    list_cmms_work_orders,
    reconcile_work_orders,
    validate_work_orders,
    work_orders_jsonl,
)
from pdm_intelligence.integrations.inventory import (
    CONTRACT_VERSION as INVENTORY_CONTRACT_VERSION,
)
from pdm_intelligence.integrations.inventory import (
    export_inventory_jsonl,
    inventory_jsonl,
    list_cmms_inventory,
    reconcile_inventory,
    validate_inventory_records,
)


def _record(**overrides):
    record = {
        "work_order_id": "WO-1001",
        "asset_id": "ENG-034",
        "status": "released",
        "planned_start": "2026-09-01T10:00:00Z",
        "duration_hours": 6,
        "required_skill": "mechanic",
        "part_id": "apu-service-kit",
        "part_qty": 1,
        "action": "maintain",
        "external_revision": 1,
        "source_updated_at": "2026-09-01T09:00:00Z",
    }
    record.update(overrides)
    return record


def _inventory(**overrides):
    record = {
        "part_id": "KIT-1",
        "description": "APU service kit",
        "on_hand": 12,
        "reserved": 3,
        "unit_cost": 8500,
        "location": "HUB-A",
        "external_revision": 1,
        "source_updated_at": "2026-09-01T09:00:00Z",
    }
    record.update(overrides)
    return record


class _FailingAudit:
    def __init__(self, path):
        self.path = path

    def append_in_transaction(self, *args, **kwargs):
        raise RuntimeError("audit sink unavailable")


def test_canonical_contract_normalizes_status_and_is_deterministic():
    first = validate_work_orders([_record()], source_system="maximo")
    second = validate_work_orders([_record()], source_system="maximo")
    assert first[0].status == "OPEN"
    assert first[0].source_fingerprint == second[0].source_fingerprint
    assert first[0].to_dict()["contract_version"] == CONTRACT_VERSION


def test_contract_rejects_invalid_or_duplicate_external_records():
    with pytest.raises(ValueError, match="Unsupported work-order status"):
        validate_work_orders([_record(status="unknown")], source_system="maximo")
    with pytest.raises(ValueError, match="duplicate work_order_id"):
        validate_work_orders([_record(), _record()], source_system="maximo")
    with pytest.raises(ValueError, match="ISO-8601"):
        validate_work_orders([_record(planned_start="tomorrow")], source_system="maximo")


def test_reconciliation_is_idempotent_and_audited(tmp_path):
    db_path = tmp_path / "cmms.db"
    audit = AuditStore(db_path)
    first = reconcile_work_orders(
        [_record()], db_path, source_system="maximo", actor="sync-service", audit_store=audit
    )
    replay = reconcile_work_orders(
        [_record()], db_path, source_system="maximo", actor="sync-service", audit_store=audit
    )
    assert first["counts"] == {"inserted": 1, "updated": 0, "skipped": 0, "conflicts": 0, "stale": 0}
    assert replay["counts"] == {"inserted": 0, "updated": 0, "skipped": 1, "conflicts": 0, "stale": 0}
    assert replay["batch_id"] == first["batch_id"]
    assert len(list_cmms_work_orders(db_path, source_system="maximo")) == 1
    assert audit.verify_chain()["valid"] is True
    assert audit.list()[0]["event_type"] == "cmms_work_order_reconciliation"


def test_reconciliation_rolls_back_mirror_when_audit_linkage_fails(tmp_path):
    db_path = tmp_path / "cmms.db"

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        reconcile_work_orders(
            [_record()], db_path, source_system="maximo", audit_store=_FailingAudit(db_path)
        )
    assert list_cmms_work_orders(db_path, source_system="maximo") == []


def test_audit_verification_reports_corrupt_payload(tmp_path):
    db_path = tmp_path / "audit.db"
    audit = AuditStore(db_path)
    audit.append("test_event", "tester", {"safe": True}, subject="case-1")
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TRIGGER audit_events_no_update")
        con.execute("UPDATE audit_events SET payload=? WHERE id=1", ("not-json",))
        con.commit()
    finally:
        con.close()
    result = audit.verify_chain()
    assert result["valid"] is False
    assert any(item["reason"] == "payload_not_valid_json" for item in result["errors"])


def test_inventory_reconciliation_rolls_back_mirror_when_audit_linkage_fails(tmp_path):
    db_path = tmp_path / "inventory.db"
    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        reconcile_inventory(
            [_inventory()], db_path, source_system="sap", audit_store=_FailingAudit(db_path)
        )
    assert list_cmms_inventory(db_path, source_system="sap") == []


def test_reconciliation_updates_only_newer_versions_and_never_overwrites_conflicts(tmp_path):
    db_path = tmp_path / "cmms.db"
    reconcile_work_orders([_record()], db_path, source_system="sap")
    updated = reconcile_work_orders(
        [_record(status="scheduled", external_revision=2, source_updated_at="2026-09-01T10:00:00Z")],
        db_path,
        source_system="sap",
    )
    stale = reconcile_work_orders(
        [_record(status="cancelled", external_revision=1)], db_path, source_system="sap"
    )
    conflict = reconcile_work_orders(
        [_record(status="cancelled", external_revision=2)], db_path, source_system="sap"
    )
    current = list_cmms_work_orders(db_path, source_system="sap")[0]
    assert updated["counts"]["updated"] == 1
    assert stale["counts"]["stale"] == 1
    assert conflict["counts"]["conflicts"] == 1
    assert current["status"] == "COMMITTED"
    assert current["external_revision"] == 2


def test_unversioned_conflict_is_reported_without_overwrite(tmp_path):
    db_path = tmp_path / "cmms.db"
    base = _record(external_revision=None, source_updated_at=None)
    reconcile_work_orders([base], db_path, source_system="oracle")
    result = reconcile_work_orders(
        [_record(status="completed", external_revision=None, source_updated_at=None)],
        db_path,
        source_system="oracle",
    )
    assert result["status"] == "PARTIAL_CONFLICT"
    assert result["counts"]["conflicts"] == 1
    assert list_cmms_work_orders(db_path, source_system="oracle")[0]["status"] == "OPEN"


def test_export_is_deterministic_and_contract_only(tmp_path):
    records = [_record(work_order_id="WO-1002", connection_url="postgresql://secret"), _record()]
    content_one, batch_id = work_orders_jsonl(records, source_system="sap")
    content_two, same_batch_id = work_orders_jsonl(list(reversed(records)), source_system="sap")
    output = tmp_path / "exchange" / "work-orders.jsonl"
    export_work_orders_jsonl(records, output, source_system="sap", batch_id=batch_id)
    lines = [json.loads(line) for line in content_one.splitlines()]
    assert content_one == content_two
    assert batch_id == same_batch_id
    assert output.read_text(encoding="utf-8") == content_one
    assert [line["work_order_id"] for line in lines] == ["WO-1001", "WO-1002"]
    assert all(line["contract_version"] == CONTRACT_VERSION for line in lines)
    assert "secret" not in content_one
    assert "connection_url" not in content_one


def test_inventory_contract_calculates_available_and_rejects_invalid_stock():
    first = validate_inventory_records([_inventory()], source_system="sap")
    assert first[0].available == 9
    assert first[0].to_dict()["contract_version"] == INVENTORY_CONTRACT_VERSION
    with pytest.raises(ValueError, match="reserved cannot exceed on_hand"):
        validate_inventory_records([_inventory(reserved=13)], source_system="sap")
    with pytest.raises(ValueError, match="duplicate inventory position"):
        validate_inventory_records([_inventory(), _inventory()], source_system="sap")


def test_inventory_reconciliation_is_idempotent_versioned_and_audited(tmp_path):
    db_path = tmp_path / "inventory.db"
    audit = AuditStore(db_path)
    first = reconcile_inventory(
        [_inventory()], db_path, source_system="sap", actor="sync-service", audit_store=audit
    )
    replay = reconcile_inventory(
        [_inventory()], db_path, source_system="sap", actor="sync-service", audit_store=audit
    )
    newer = reconcile_inventory(
        [_inventory(on_hand=20, reserved=4, external_revision=2)], db_path, source_system="sap", audit_store=audit
    )
    stale = reconcile_inventory(
        [_inventory(on_hand=2, reserved=1, external_revision=1)], db_path, source_system="sap", audit_store=audit
    )
    current = list_cmms_inventory(db_path, source_system="sap")[0]
    assert first["counts"]["inserted"] == 1
    assert replay["counts"]["skipped"] == 1
    assert newer["counts"]["updated"] == 1
    assert stale["counts"]["stale"] == 1
    assert current["on_hand"] == 20
    assert current["available"] == 16
    assert audit.verify_chain()["valid"] is True


def test_inventory_export_is_deterministic_and_secret_free(tmp_path):
    records = [_inventory(part_id="KIT-2", connection_url="postgresql://secret"), _inventory()]
    content_one, batch_id = inventory_jsonl(records, source_system="oracle")
    content_two, same_batch_id = inventory_jsonl(list(reversed(records)), source_system="oracle")
    output = tmp_path / "exchange" / "inventory.jsonl"
    export_inventory_jsonl(records, output, source_system="oracle", batch_id=batch_id)
    assert content_one == content_two
    assert batch_id == same_batch_id
    assert output.read_text(encoding="utf-8") == content_one
    assert "secret" not in content_one
    assert "connection_url" not in content_one


def test_api_reconciliation_rbac_audit_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("PDM_API_KEYS", "engineer:data-engineer:engineer-key,viewer:viewer-key")
    monkeypatch.setenv("PDM_STATE_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    request = {"source_system": "maximo", "records": [_record()]}

    assert client.post(
        "/api/integrations/cmms/reconcile", json=request, headers={"X-API-Key": "viewer-key"}
    ).status_code == 403
    reconciled = client.post(
        "/api/integrations/cmms/reconcile", json=request, headers={"X-API-Key": "engineer-key"}
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["counts"]["inserted"] == 1
    assert reconciled.json()["audit_event_id"] > 0

    listing = client.get(
        "/api/integrations/cmms/work-orders?source_system=maximo",
        headers={"X-API-Key": "viewer-key"},
    )
    assert listing.status_code == 200
    assert listing.json()["records"][0]["contract_version"] == CONTRACT_VERSION

    exported = client.post(
        "/api/integrations/cmms/export", json=request, headers={"X-API-Key": "engineer-key"}
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    assert exported.headers["x-audit-event-id"].isdigit()
    assert exported.headers["x-exchange-content-sha256"] == sha256(exported.content).hexdigest()
    assert json.loads(exported.text)["batch_id"] == reconciled.json()["batch_id"]

    inventory_request = {"source_system": "maximo", "records": [_inventory()]}
    inventory_reconciled = client.post(
        "/api/integrations/cmms/inventory/reconcile",
        json=inventory_request,
        headers={"X-API-Key": "engineer-key"},
    )
    assert inventory_reconciled.status_code == 200
    assert inventory_reconciled.json()["counts"]["inserted"] == 1
    inventory_listing = client.get(
        "/api/integrations/cmms/inventory?source_system=maximo",
        headers={"X-API-Key": "viewer-key"},
    )
    assert inventory_listing.status_code == 200
    assert inventory_listing.json()["records"][0]["available"] == 9
    inventory_exported = client.post(
        "/api/integrations/cmms/inventory/export",
        json=inventory_request,
        headers={"X-API-Key": "engineer-key"},
    )
    assert inventory_exported.status_code == 200
    assert inventory_exported.headers["x-contract-version"] == INVENTORY_CONTRACT_VERSION
    assert inventory_exported.headers["x-exchange-content-sha256"] == sha256(inventory_exported.content).hexdigest()
    assert inventory_exported.headers["x-audit-event-id"].isdigit()
