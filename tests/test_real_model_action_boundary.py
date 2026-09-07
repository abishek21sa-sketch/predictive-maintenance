from fastapi.testclient import TestClient

from pdm_intelligence.api import main
from pdm_intelligence.operations import command


def test_commit_requires_promoted_model_when_production_boundary_is_enabled(monkeypatch, tmp_path):
    blocked_case = {
        "status": "READY",
        "model_status": "PROMOTION_BLOCKED",
    }
    monkeypatch.setattr(command, "build_metropt_operations_case", lambda *args, **kwargs: blocked_case)

    try:
        command.commit_metropt_work_order(
            tmp_path / "metropt",
            tmp_path / "state.db",
            require_promoted_model=True,
        )
    except command.ModelPromotionBlockedError as exc:
        assert "production work-order commitment is disabled" in str(exc)
    else:
        raise AssertionError("blocked real-data model must not be committed in production mode")


def test_production_case_endpoint_rejects_blocked_model(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv("PDM_API_KEYS", "ops:admin:runtime-key")
    monkeypatch.setenv("PDM_SECRET_PROVIDER", "test-secret-manager")
    monkeypatch.setattr(
        main,
        "build_metropt_operations_case",
        lambda *args, **kwargs: {"status": "READY", "model_status": "PROMOTION_BLOCKED"},
    )

    response = TestClient(main.app).get(
        "/api/operations/metropt/case",
        headers={"X-API-Key": "runtime-key"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "REAL_MODEL_PROMOTION_BLOCKED"
