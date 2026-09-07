# Predictive Maintenance Intelligence Platform — V1.0

V1.0 is the pre-public portfolio release combining the validated benchmark/engineering core with a separate real operational evidence track and an operations-command workflow.

## Release capabilities

- NASA C-MAPSS FD001 RUL/anomaly benchmark package with explicit simulated-benchmark evidence boundaries;
- generic external-data gateway for CSV/JSON/server-file/SQL/historical replay;
- content-addressed dataset vault and capability-specific readiness;
- external RUL model lifecycle with asset-level holdout when genuine RUL targets exist;
- **MetroPT-3 real operational compressor pipeline** with local UCI acquisition, strict full-source validation, streaming features, February-reference anomaly detection and chronological 24-hour failure-horizon evaluation;
- no silent NASA RUL transfer and no fabricated MetroPT row-level RUL;
- Weibull, hazard, MTBF/MTTR/availability, FMEA/RCM, maintenance economics and KPIs;
- Gurobi-first maintain/inspect/defer MILP with timing, resources, parts, criticality, CVaR and robust capacity profiles;
- exact-enumeration OR verification and infeasibility/IIS support;
- seeded three-policy Reliability Shock Lab with common random numbers;
- Reliability Observatory, Data Gateway, **Real Operations Command**, Shock Lab and executable Methodology interfaces;
- real-data decision trace from observed compressor telemetry to predicted risk, assumed/calculated consequence, optimized intervention and auditable work-order commitment;
- disruption re-solves for bay outage, technician absence, part delay, duration inflation and compound scenarios;
- work-order lifecycle state with an explicit application-state evidence boundary;
- managed PostgreSQL adapter boundary with a parameterized Compose integration harness and bounded `/api/health/ready` runtime probe;
- global API authentication boundary with explicit public liveness probes and route-level permission enforcement;
- least-privilege permissions for prediction, optimization, anomaly analysis, data ingestion, model training, asset registration and work-order lifecycle actions;
- SHA-256-bound MetroPT runtime evidence with fail-closed artifact verification and corrected diagnostics that distinguish a blocked model gate from a broken acceptance run;
- bounded runtime observability with request correlation, Prometheus latency histograms, explicit 5xx counters, and unmatched-route cardinality protection;
- strict deployment declarations for TLS ingress identity, observability components, backup target, and site connector identity/endpoint;
- retained non-raw MetroPT candidate benchmark evidence with label/target exclusion checks;
- Windows-first acceptance, Docker/CI, installable wheel, strict warning-as-error validation and public-release audit.

## Real-data acceptance boundary

The public repository does not redistribute the MetroPT-3 raw CSV. Full real operational evidence is accepted only after `scripts/phase5_acceptance.ps1` downloads/uses the complete UCI source, validates all 1,516,948 observations and 15 source signals, builds/evaluates the chronological model evidence, and passes the licensed Gurobi real-data decision gate.

Structural test fixtures are not substitutes for this acceptance.

## Claim boundary

Observed, benchmark, predicted, calculated/assumed, optimized, simulated and operator-recorded workflow state remain explicitly separated. The release does not claim universal equipment transfer, live railway/site deployment, realized savings, certified aircraft/compressor safety or prospective field performance.
