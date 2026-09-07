# Next-Level Readiness Roadmap

This roadmap states what is already implemented in the reference release and what must be completed before a controlled site deployment. It is intentionally written as an engineering checklist rather than a marketing score.

## Current release baseline

The repository currently provides:

- a FastAPI application with Observatory, Data Gateway, Real Operations, Stress Lab, and methodology surfaces;
- dataset-specific contracts, readiness states, fingerprints, replay, and provenance boundaries;
- RUL candidate selection with complete-asset holdout validation;
- independent C-MAPSS FD001–FD004 benchmark evidence with grouped uncertainty intervals;
- a separate MetroPT-3 anomaly/failure-horizon track with chronological evaluation and fail-closed promotion;
- Weibull, hazard, availability, FMEA/RCM, economics, and KPI calculations;
- Maintain/Inspect/Defer MILP scheduling with capacity, skills, parts, CVaR, stress profiles, and an exact small-instance oracle;
- seeded Monte Carlo policy comparison with common random numbers;
- authenticated API boundaries, request correlation, metrics, local audit linkage, and CMMS exchange contracts;
- release packaging, clean extraction, secret/raw-data audit, deployment templates, and automated tests.

## Critical work before site deployment

### 1. Data and evidence

- Obtain a site-approved telemetry contract with asset identity, time semantics, units, quality rules, retention, and ownership.
- Acquire enough independent failure and maintenance events for meaningful chronological evaluation; do not treat rows from one event as independent evidence.
- Reconcile sensor timestamps, maintenance records, failure definitions, and asset master identifiers.
- Establish a versioned data-quality report for missingness, duplicates, drift, late arrival, out-of-order data, and sensor health.
- Keep public benchmark, synthetic fixture, and site-operational evidence in separate namespaces and reports.

### 2. ML and AI quality

- Complete the MetroPT or site-specific supervised evaluation with at least the declared independent-event minimum in validation and future holdout.
- Compare against prevalence, constant-probability, age/time, and operational heuristic baselines.
- Report calibration, precision-recall behavior at operating thresholds, false-alert burden, missed-event analysis, and subgroup/asset-family behavior.
- Add a locked test set, model version, feature schema, training data fingerprint, and reproducible training command for every promotion.
- Define drift thresholds for feature distributions, prediction distributions, missingness, and event rates.
- Define retraining triggers, rollback procedure, shadow mode, champion/challenger evaluation, and human override behavior.
- Keep model explanations tied to evidence; permutation importance is not a causal explanation.

### 3. Reliability, IE, and math governance

- Replace illustrative costs, downtime, repair durations, capacities, skills, parts, and criticality with site-approved values and units.
- Validate Weibull/survival assumptions against appropriate failure and censoring treatment.
- Run sensitivity and stress analysis over cost, RUL error, capacity, delay, and criticality parameters.
- Document why each risk transform is appropriate for the operating context.
- Maintain independent analytical or enumeration oracles for every high-consequence optimization rule.
- Define safety constraints and forbidden automated actions; the planner must not issue unsafe work without human authorization.
- Quantify uncertainty calibration separately from ranking accuracy and avoid converting empirical bands into guarantees.

### 4. Application and architecture

- Move application state from local SQLite to a managed multi-user database with migrations, connection limits, pooling, and tested rollback.
- Define API versioning, backward compatibility, request limits, idempotency keys, timeout budgets, and error contracts.
- Run load, soak, failover, and recovery tests against the intended topology.
- Deploy immutable container images by digest and record the exact release artifact hash.
- Separate development, test, staging, and production environments with controlled configuration promotion.
- Define SLOs for availability, latency, ingestion freshness, scoring completion, and work-order exchange.

### 5. Security and privacy

- Use an enterprise identity provider or equivalent production authentication, with least-privilege RBAC and service identities.
- Store API keys and database credentials in an approved secret manager with rotation policy and a tested rotation event.
- Enable trusted TLS termination, network segmentation, ingress restrictions, and secure headers.
- Run dependency scanning, static analysis, secret scanning, image scanning, SBOM generation, and signed-artifact verification.
- Complete a threat model covering data ingestion, model artifacts, work-order actions, connectors, logs, and operator accounts.
- Establish data retention, deletion, access review, privacy classification, and incident-notification procedures.

### 6. Observability and operations

- Route metrics, structured logs, traces, and alerts to managed systems owned by the operating team.
- Monitor request errors, latency, queue depth, model version, data freshness, drift, solver failures, infeasibility, and connector reconciliation.
- Create on-call ownership, alert severity definitions, escalation paths, incident runbooks, and post-incident review.
- Test backup/restore and disaster recovery with measured RPO/RTO rather than a configuration declaration.
- Record deployment, rollback, configuration, model, and data-contract changes in an approved change system.

### 7. CMMS/ERP and human workflow

- Implement a site-specific connector with HTTPS endpoint, authentication, idempotent retry behavior, reconciliation, and dead-letter handling.
- Map local asset, work-order, parts, technician, priority, and status vocabularies.
- Confirm that a committed recommendation requires human approval before execution.
- Reconcile accepted, rejected, modified, cancelled, and completed work orders back into the audit trail.
- Train operators and record acceptance testing, override reasons, and safety escalation procedures.

### 8. Prospective qualification

- Bind the candidate release, environment, model, data contract, and artifact digest to a site-owned study.
- Define success criteria before observation begins.
- Run shadow or advisory mode before any operational action.
- Capture follow-up outcomes, false alerts, missed events, downtime, operator overrides, safety review, and reconciliation completeness.
- Obtain formal operator acceptance and a non-secret qualification attestation.

## Delivery sequence

### Stage A — reproducible reference release

Run the local lint, warning-clean tests, benchmark evidence, release-consistency, packaging, extraction, and audit commands. Publish only source, reports, templates, and documentation; exclude raw operational data and secrets.

### Stage B — controlled pilot

Use a site-owned dataset and identity boundary. Run advisory-only recommendations, verify data quality and connector reconciliation, measure alert burden, and collect prospective outcomes without automatic actuation.

### Stage C — production promotion review

Require passing model-quality evidence, deployment attestations, security review, recovery evidence, change approval, connector acceptance, operator acceptance, and prospective qualification. A failed gate keeps the capability unavailable for production promotion.

## Evidence artifacts to keep for every release

- release manifest and artifact SHA-256;
- source/data/model fingerprints;
- training configuration and candidate comparison;
- locked validation and future-holdout metrics;
- calibration and uncertainty report;
- solver evidence and optimization assumptions;
- test, lint, security, package, and extraction results;
- deployment, backup/restore, observability, connector, and change attestations;
- prospective qualification report and operator acceptance.

The current repository provides templates and fail-closed checks for these artifacts. Site-specific values and attestations must be supplied by the organization that operates the system.
