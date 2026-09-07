from __future__ import annotations

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app

client = TestClient(app)


def telemetry_csv(n_assets=6, cycles=8):
    rows = ["machine_id,operating_cycle,vibration,remaining_useful_life"]
    for a in range(1, n_assets + 1):
        life = cycles + a * 3
        for c in range(1, cycles + 1):
            rows.append(f"M{a},{c},{1 + a*.1 + c*.05},{life-c}")
    return "\n".join(rows)


def test_data_modes_and_data_gateway_page_are_exposed():
    modes = client.get("/api/data/modes")
    assert modes.status_code == 200
    ids = {x["id"] for x in modes.json()["modes"]}
    assert {"browser_text", "sql", "historical_replay"}.issubset(ids)
    page = client.get("/data-gateway")
    assert page.status_code == 200
    assert "Bring your own machine history" in page.text
    assert "NO SILENT MODEL TRANSFER" in page.text


def test_browser_text_ingestion_returns_capability_specific_readiness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = {
        "name": "api external test",
        "files": [{"entity_type": "telemetry", "filename": "telemetry.csv", "content": telemetry_csv()}],
        "mappings": {},
    }
    res = client.post("/api/data/ingest/text", json=body)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["readiness"]["capabilities"]["external_rul_training"]["status"] == "READY"
    assert payload["readiness"]["capabilities"]["fd001_rul_inference"]["status"] == "NOT_READY"
    ds = payload["dataset_id"]
    ready = client.get(f"/api/data/datasets/{ds}/readiness")
    assert ready.status_code == 200
    replay = client.get(f"/api/data/datasets/{ds}/replay?upto=3&limit=100")
    assert replay.status_code == 200
    assert all(row["cycle"] <= 3 for row in replay.json()["records"])


def test_browser_ingestion_refuses_telemetry_without_time_axis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = {
        "name": "bad telemetry",
        "files": [{"entity_type": "telemetry", "filename": "t.csv", "content": "asset_id,vibration\nA,1.2\n"}],
        "mappings": {},
    }
    res = client.post("/api/data/ingest/text", json=body)
    assert res.status_code == 422
    assert "cycle or timestamp" in res.json()["detail"]


def test_preview_validates_without_persisting_dataset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = {
        "name": "preview only",
        "files": [{"entity_type": "telemetry", "filename": "telemetry.csv", "content": telemetry_csv()}],
        "mappings": {},
    }
    preview = client.post("/api/data/preview/text", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "PREVIEW_VALIDATED"
    assert preview.json()["readiness"]["capabilities"]["external_rul_training"]["status"] == "READY"
    assert client.get("/api/data/datasets").json() == []
