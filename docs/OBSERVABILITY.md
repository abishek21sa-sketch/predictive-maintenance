# Observability and Model Health

The API provides `/api/health` for service liveness and `/api/monitoring/drift` for feature-distribution monitoring. Drift uses Population Stability Index (PSI) with transparent thresholds: `<0.10` low, `0.10-0.25` moderate/watch, and `>=0.25` high/drift detected.

Each response carries an `X-Request-ID` correlation value. A valid caller-supplied
ID is preserved; malformed or overlong values are replaced with a generated ID.
The API also emits baseline security headers and disables caching for API
responses. When `PDM_TLS_TERMINATED=1` is declared for a trusted ingress
boundary, it additionally emits HSTS (`Strict-Transport-Security`); local/
reference mode does not claim transport security it does not provide.
Authenticated monitoring systems can scrape `/metrics` for
low-cardinality request counts, status codes, 5xx server errors, bounded
latency histograms, in-flight work, and process uptime. Unmatched URLs are
collapsed into one `/unmatched` series so user-controlled path values cannot
create unbounded metric labels. Request failures are logged with the same
request ID correlation value. These in-process metrics are a
development/reference instrumentation boundary; production deployments must
export them to a managed metrics system with retention, alert routing, and
trace correlation.

Deployment review additionally requires non-secret declarations for the
metrics exporter, structured log sink, trace exporter, and alert-routing
destination. The readiness report exposes only boolean presence for those
components; the operator attestation must still reference the actual managed
records.

Prediction, optimization and asset-registration write operations generate append-only audit events retrievable through `/api/audit`. This provides reproducible traceability between operational requests and system actions.

RUL predictions from the persisted Random Forest also expose p10/p90 ensemble dispersion. This is explicitly described as model-ensemble uncertainty, not a statistically calibrated coverage guarantee.
