import json

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app


def test_phase5_real_operations_routes_exist_and_do_not_fake_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("PDM_METROPT_ROOT", str(tmp_path / "missing-metropt"))
    with TestClient(app) as client:
        page = client.get("/operations")
        status = client.get("/api/real-data/metropt/status")
        modes = client.get("/api/data/modes")
    assert page.status_code == 200
    assert "One real compressor" in page.text
    assert status.status_code == 200
    assert status.json()["status"] == "NOT_READY"
    assert status.json()["real_data"] is True
    assert "No synthetic substitute" in status.json()["claim_boundary"]
    metropt = next(x for x in modes.json()["modes"] if x["id"] == "metropt3_real_operations")
    assert metropt["status"] == "NOT_READY"
    assert metropt["raw_data_packaged"] is False


def test_prepared_real_data_with_failed_model_gate_is_not_reported_ready(monkeypatch, tmp_path):
    root = tmp_path / "prepared-metropt"
    (root / "derived").mkdir(parents=True)
    (root / "derived" / "metropt_runtime.json").write_text(json.dumps({
        "dataset": {"raw_validation": {"row_count": 100, "first_timestamp": "2020-01-01", "last_timestamp": "2020-01-02"}},
        "model": {"selected_model": "logistic_baseline", "quality_gate": {"passed": False, "promotion_allowed": False}},
        "feature_store": {"rows": 10},
        "claim_boundary": "Observed evidence only.",
    }), encoding="utf-8")
    monkeypatch.setenv("PDM_METROPT_ROOT", str(root))
    with TestClient(app) as client:
        status = client.get("/api/real-data/metropt/status")
        modes = client.get("/api/data/modes")
    assert status.status_code == 200
    assert status.json()["status"] == "MODEL_GATE_BLOCKED"
    assert status.json()["source_status"] == "READY"
    assert status.json()["model_status"] == "PROMOTION_BLOCKED"
    metropt = next(x for x in modes.json()["modes"] if x["id"] == "metropt3_real_operations")
    assert metropt["status"] == "MODEL_GATE_BLOCKED"
    assert metropt["source_status"] == "READY"
    assert metropt["model_status"] == "PROMOTION_BLOCKED"


def test_methodology_separates_metropt_real_track_from_nasa_benchmark():
    with TestClient(app) as client:
        page = client.get("/methodology")
    assert page.status_code == 200
    assert "REAL OPERATIONAL DATA" in page.text
    assert "MetroPT-3 stays a separate evidence track" in page.text
    assert "does not fabricate" in page.text
    assert "no NASA RUL transfer" in page.text


def test_operations_page_exposes_work_order_lifecycle_controls():
    with TestClient(app) as client:
        page = client.get("/operations")
    assert page.status_code == 200
    assert "COMMIT SELECTED INTERVENTION" in page.text
    assert "IN_PROGRESS" in page.text
    assert "COMPLETED" in page.text
    assert "/api/operations/work-orders/" in page.text


def test_operations_page_exposes_separate_source_and_model_readiness_contract():
    with TestClient(app) as client:
        page = client.get("/operations")
    assert page.status_code == 200
    assert "HISTORICAL DATA READY" in page.text
    assert "MODEL GATE BLOCKED" in page.text
    assert "historical/testing only" in page.text
