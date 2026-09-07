from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app
from pdm_intelligence.monitoring.runtime import RuntimeMetrics


def test_api_responses_have_request_id_and_security_headers():
    response = TestClient(app).get("/api/health", headers={"X-Request-ID": "manual-check-42"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "manual-check-42"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_hsts_is_emitted_only_when_tls_termination_is_declared(monkeypatch):
    monkeypatch.setenv("PDM_TLS_TERMINATED", "1")
    response = TestClient(app).get("/api/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"

    monkeypatch.delenv("PDM_TLS_TERMINATED")
    reference_response = TestClient(app).get("/api/health")
    assert "Strict-Transport-Security" not in reference_response.headers


def test_invalid_request_id_is_replaced_and_metrics_are_scrapeable():
    client = TestClient(app)
    response = client.get("/api/health", headers={"X-Request-ID": "not valid\\nvalue"})
    metrics = client.get("/metrics")
    metrics_again = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "not valid\\nvalue"
    assert len(response.headers["X-Request-ID"]) == 32
    assert metrics.status_code == 200
    assert "pdm_http_requests_total" in metrics.text
    assert 'route="/api/health"' in metrics.text
    assert 'route="/metrics"' in metrics_again.text


def test_unmatched_paths_cannot_create_unbounded_metric_labels():
    client = TestClient(app)
    secret_like_path = "/api/not-found/customer-secret-123456789"

    response = client.get(secret_like_path)
    metrics = client.get("/metrics")

    assert response.status_code == 404
    assert 'route="/unmatched"' in metrics.text
    assert secret_like_path not in metrics.text


def test_runtime_metrics_expose_latency_buckets_and_server_errors():
    metrics = RuntimeMetrics()
    metrics.request_started()
    metrics.request_finished("GET", "/api/example", 503, 0.2)

    rendered = metrics.prometheus()

    assert 'pdm_http_server_errors_total{method="GET",route="/api/example",status_class="5xx"} 1' in rendered
    assert 'pdm_http_request_duration_seconds_bucket{method="GET",route="/api/example",le="0.1"} 0' in rendered
    assert 'pdm_http_request_duration_seconds_bucket{method="GET",route="/api/example",le="0.25"} 1' in rendered
    assert 'pdm_http_request_duration_seconds_bucket{method="GET",route="/api/example",le="+Inf"} 1' in rendered
