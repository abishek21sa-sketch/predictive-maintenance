from pathlib import Path


def test_observatory_links_to_data_gateway_and_gateway_is_not_dashboard_clone():
    root = Path(__file__).resolve().parents[1] / "src" / "pdm_intelligence" / "frontend"
    home = (root / "index.html").read_text(encoding="utf-8")
    gateway = (root / "data_gateway.html").read_text(encoding="utf-8")
    assert "/data-gateway" in home
    assert "FILE AIRLOCK" in gateway
    assert "DATABASE PORT" in gateway
    assert "REPLAY TUNNEL" in gateway
    assert "MODEL FORGE" in gateway
    assert "NO SILENT MODEL TRANSFER" in gateway
    assert "six KPI cards" not in gateway.lower()


def test_methodology_explains_external_data_readiness_gate():
    root = Path(__file__).resolve().parents[1] / "src" / "pdm_intelligence" / "frontend"
    methods = (root / "methodology.html").read_text(encoding="utf-8")
    assert "DATA READINESS GATE" in methods
    assert "valid dataset can still leave RUL inference NOT READY" in methods
    assert "/api/data/datasets" in methods
