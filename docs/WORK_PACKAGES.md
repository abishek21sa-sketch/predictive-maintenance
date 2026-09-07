# Work Package Register

Every work package below records objective, design, implementation, validation, risk and documentation evidence.

## WP00 — Repository Foundation
**Objective:** establish reproducible production-oriented project structure. **Design:** installable Python package, isolated domain modules, CI, Docker and typed API boundary. **Implementation:** `pyproject.toml`, `src/`, tests, Dockerfile, Compose, CI workflow. **Validation:** package import, compile check and test suite. **Risks:** dependency/version drift. **Documentation:** README, Runbook, Architecture. **Status:** Validated.

## WP01 — Data Contract and Ingestion
**Objective:** ingest C-MAPSS without silent schema corruption. **Design:** canonical 26-field contract with unit/cycle monotonicity and null checks. **Implementation:** `data/cmapss.py`, NASA downloader, source manifest. **Validation:** schema/RUL tests plus FD001 row/unit counts and hashes. **Risks:** source availability and upstream format change. **Documentation:** Dataset, Source Manifest. **Status:** Validated.

## WP02 — Asset Registry and Feature Store
**Objective:** provide persistent asset identity and versioned feature access. **Design:** SQLite reference backend or explicit managed PostgreSQL adapter through one state-connection boundary. **Implementation:** `storage/registry.py`, `storage/database.py`. **Validation:** round-trip registry/feature-store test plus managed adapter fail-closed tests. **Risks:** managed HA, migration and capacity evidence remain deployment-specific. **Documentation:** Architecture. **Status:** Validated reference and managed adapter boundary.

## WP03 — Reliability Engineering
**Objective:** quantify lifecycle behavior and failure-mode priority. **Design:** Weibull/MTBF/MTTR/availability/hazard plus bathtub model and FMEA→RCM screening. **Implementation:** `reliability/analysis.py`, `fmea.py`, `kpis.py`. **Validation:** physical-range tests and FMEA/KPI tests. **Risks:** illustrative FMEA ratings and simulated lifetimes. **Documentation:** Model Card, Assumptions. **Status:** Validated baseline.

## WP04 — Feature Engineering
**Objective:** extract degradation information without future-life leakage. **Design:** current/past sensor deltas and rolling statistics only. **Implementation:** `features/engineering.py`. **Validation:** exercised in asset-group holdout and official FD001 test. **Risks:** fixed rolling window. **Documentation:** Validation Protocol. **Status:** Validated.

## WP05 — RUL and Anomaly AI
**Objective:** estimate RUL and detect abnormal sensor states. **Design:** benchmark Ridge, Random Forest and HistGradientBoosting; Isolation Forest anomaly detector; asset-level model selection. **Implementation:** `models/rul.py`, `models/anomaly.py`. **Validation:** holdout benchmark, official FD001 test, NASA score, permutation importance. **Risks:** sim-to-real and statistical coverage calibration. Runtime Random-Forest ensemble p10/p90 dispersion is exposed but not misrepresented as calibrated coverage. **Documentation:** Model Card, Validation Report, Observability. **Status:** Validated.

## WP06 — Maintenance Optimization
**Objective:** turn prognosis into feasible maintenance timing. **Design:** deterministic MILP and shared first-stage stochastic schedule under RUL scenarios with maintenance-slot, labor-hour, spare-unit, skill-capacity and part-inventory constraints. **Implementation:** `optimization/maintenance.py`. **Validation:** assignment/capacity tests and 100-asset FD001 release schedule. **Risks:** resource/cost parameters require site calibration. **Documentation:** Assumptions, Risk Register. **Status:** Validated baseline.

## WP07 — Lifecycle Simulation and Simulation Optimization
**Objective:** compare maintenance policies and optimize interpretable policy thresholds. **Design:** seeded Monte Carlo lifecycle simulation plus threshold search. **Implementation:** `simulation/lifecycle.py`. **Validation:** deterministic tests and release evidence. **Risks:** assumed failure/cost distributions. **Documentation:** Validation Report, Assumptions. **Status:** Validated baseline.

## WP08 — Decision Engine
**Objective:** generate explainable maintenance actions rather than raw predictions. **Design:** RUL/risk/anomaly policy combined with optimized slot timing. **Implementation:** `decision/engine.py`. **Validation:** critical-RUL action test and 100-asset release decision distribution. **Risks:** risk mapping not field-calibrated. **Documentation:** Model Card, Risk Register. **Status:** Validated baseline.

## WP09 — REST API and Persistence Boundary
**Objective:** expose platform capabilities to operational applications. **Design:** FastAPI with validated release snapshot, fallback CI fixture, registry and optimization endpoints. **Implementation:** `api/main.py`, storage modules. **Validation:** TestClient health test and HTTP smoke test. **Risks:** site-specific OIDC/IAM mapping remains deployment-specific; configurable API-key authorization and audit boundary are implemented. **Documentation:** API Contract, Runbook. **Status:** Validated baseline.

## WP10 — Planner Frontend
**Objective:** provide an operational maintenance workbench, not a standalone KPI dashboard. **Design:** API-backed fleet queue centered on actions, RUL, risk, anomaly, reliability, model drivers and policy economics. **Implementation:** `src/pdm_intelligence/frontend/`. **Validation:** frontend HTTP response and API contract smoke. **Risks:** site-specific identity-provider UX remains deployment-specific. **Documentation:** README, Runbook. **Status:** Validated baseline.

## WP11 — Deployment, CI and Evidence
**Objective:** make results reproducible and releaseable. **Design:** Docker deployment, CI tests, evidence JSON, model metadata and source hashes. **Implementation:** Dockerfile, Compose, GitHub Actions, `artifacts/`, evidence docs. **Validation:** compile/tests/API smoke and wheel build. **Risks:** environment-specific binary serialization compatibility. **Documentation:** Runbook, Validation Report. **Status:** Validated.

## WP12 — Enterprise Hardening and Integration Boundary
**Objective:** make the non-real-time reference release operationally complete without claiming site deployment. **Design:** configurable API-key authorization boundary, append-only audit events, PSI feature-drift monitoring, Random-Forest ensemble uncertainty band, vendor-neutral CMMS/ERP batch contracts, and resource-aware optimization with labor/spares/skills/part inventory. **Implementation:** `security/`, `governance/`, `monitoring/`, `integrations/`, runtime/API/optimization extensions. **Validation:** enterprise-hardening automated tests plus package/API smoke. **Risks:** API keys are an integration boundary, not a substitute for site OIDC; ensemble interval is not calibrated coverage. **Documentation:** Security, Integrations, Observability, Release Acceptance. **Status:** Validated.

## WP13 — Real Operating Environment Integration
**Objective:** connect the validated platform to an actual asset fleet and enterprise systems. **Design/Implementation:** intentionally excluded from this release because it requires real telemetry, site credentials, enterprise IAM, CMMS/ERP endpoints and field maintenance outcomes. **Validation:** requires prospective field acceptance. **Risks:** safety, data drift, organizational adoption and site integration. **Documentation:** Risk Register and Release Acceptance. **Status:** External deployment boundary; not falsely claimed complete.

## WP14 — Critical/High Production Controls
**Objective:** make the remaining repository-owned production controls executable and auditable. **Design:** non-secret managed-state preflight and bounded connectivity, all-stateful-store SQLAlchemy/PostgreSQL wiring, schema initialization verification, structured deployment evidence attestation, hash-registered candidate models with human promotion, explicit drift insufficiency, verified local backup/restore, bounded load smoke, hardened container boundary, and CI enforcement. **Implementation:** `storage/managed_state.py`, `storage/database.py`, `security/deployment_evidence.py`, `models/model_registry.py`, `scripts/managed_state_preflight.py`, `scripts/deployment_evidence.py`, `scripts/backup_restore.py`, `scripts/load_smoke.py`, enterprise acceptance, CI and release audit. **Validation:** focused enterprise-control tests, full warning-clean suite, Ruff, compile, Docker Compose config and release audit. **Risks:** managed infrastructure, enterprise IAM, site connectors, DR, capacity certification and prospective field evidence remain external. **Documentation:** Enterprise Acceptance, Deployment Evidence, Security and Runbook. **Status:** Repository-owned adapter and controls implemented; deployment evidence external.

## WP15 — Real-Data Quality Boundary
**Objective:** prevent a historical MetroPT-3 model from being promoted when future-holdout ranking quality is below the prevalence baseline. **Design:** chronological supervised failure-horizon gate remains authoritative; anomaly ranking quality and published event detection are reported separately as reference-deviation evidence, never as calibrated failure probability. **Implementation:** real-data model evidence and Phase-5 diagnostics. **Validation:** full MetroPT-3 source validation, future holdout, anomaly ranking metrics and four-event detection. **Risks:** four published failure windows are insufficient for a production-quality prospective claim; new labeled site history and prospective validation remain required. **Documentation:** MetroPT-3 Real Operations, Model Card, Enterprise Acceptance. **Status:** Gate enforced; current historical supervised gate remains blocked.
