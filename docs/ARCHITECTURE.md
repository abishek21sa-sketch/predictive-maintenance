# Architecture Decision Record — Predictive Maintenance Intelligence

## Context

The repository must transform degradation evidence into operational maintenance decisions while keeping data provenance, AI, Reliability Engineering, Operations Research, simulation and evidence boundaries independently testable. It also must support genuine external operational data without pretending that a NASA C-MAPSS model is universally transferable.

## System architecture

```text
  ┌────────────────────────────── DATA SOURCES ───────────────────────────────┐
  │ NASA C-MAPSS benchmark | user CSV/JSON | SQL | MetroPT-3 real telemetry │
  └──────────────────────────────────┬────────────────────────────────────────┘
                                     │
                                     ▼
                    source adapters / local acquisition
                                     │
                ┌────────────────────┴────────────────────┐
                ▼                                         ▼
    canonical external-data contracts         MetroPT-3 source validator
    asset | telemetry | maintenance            15 signals | timestamps
                │                                         │
                ▼                                         ▼
       capability readiness gate                 streaming feature store
                │                                         │
        ┌───────┴─────────┐                  ┌────────────┴─────────────┐
        ▼                 ▼                  ▼                          ▼
 bundled FD001 RUL   user RUL lifecycle   February anomaly        24h failure-horizon
 benchmark model     when true RUL exists reference model         chronological model
        └──────────┬──────┘                  └────────────┬─────────────┘
                   └──────────────────────┬───────────────┘
                                          ▼
                         reliability / maintenance consequence
                                          │
                                          ▼
                     Maintain / Inspect / Defer CVaR MILP
                           Gurobi primary | HiGHS fallback
                                          │
                            ┌─────────────┴─────────────┐
                            ▼                           ▼
                 Monte Carlo Shock Lab          Real Operations Command
                 three-policy comparison        sensor dossier / resources
                                                disruption re-solves
                                                work-order decision trace
```

## Evidence-track isolation

There are deliberately three analytical paths:

1. **NASA benchmark path** — simulated FD001 run-to-failure data with RUL benchmark evidence.
2. **Generic external-data path** — canonical user data, capability readiness, replay and dataset-specific RUL training only when genuine RUL labels exist.
3. **MetroPT-3 real-operations path** — real railway-compressor telemetry with published failure windows, anomaly detection and a chronological failure-horizon model. It does not reuse the NASA RUL model.

The paths converge only after they have produced evidence appropriate to their own source contract.

## Component decisions

| Component | Technology | Why it fits |
|---|---|---|
| Core services | Python | Shared numerical/reliability/optimization ecosystem and Windows portability |
| API | FastAPI | Validated typed service boundary; no value in a rewrite for diversity alone |
| External DB adapter | SQLAlchemy | Vendor-neutral batch database contract |
| State/catalog/work orders | SQLite / managed PostgreSQL | SQLite reference workflow; explicit SQLAlchemy adapter for managed deployment |
| Generic canonical interchange | CSV | Reopenable without a mandatory binary engine |
| Optional columnar input | pandas + PyArrow extra | Efficient Parquet ingestion when available |
| Real-data preparation | pandas chunk streaming | Processes 1.5M MetroPT rows without requiring all raw samples in memory |
| Prognostics | scikit-learn | Transparent baseline/candidate comparison and reproducible classical ML |
| Primary OR | Gurobi | Resource-constrained MILP, exact status/bounds/gap and IIS capability |
| CI OR fallback | SciPy/HiGHS | Independent reproducible solver path |
| Simulation | NumPy Monte Carlo | Seeded transparent stochastic stress tests |
| Frontend | purpose-built HTML/SVG/JS | Distinct radial Observatory plus a separate operations-command interaction |

## Real-data storage boundary

- raw MetroPT-3 remains under `data/external/metropt3/` and is gitignored;
- derived feature/model/runtime artifacts are produced locally under the same ignored runtime tree;
- the public repository contains acquisition/validation code, not the 208 MB raw dataset;
- accepted generic user datasets and user-trained models likewise remain outside tracked source.

## Operations state

A recommendation can be committed to the SQLite work-order ledger. The ledger stores the source timestamp, model/decision trace, selected action, resource scenario and evidence boundary. Work-order lifecycle status is operator-recorded application state and is never retroactively presented as historical MetroPT maintenance evidence.

## Security boundary

- no API keys or database passwords are committed;
- no Gurobi license/credential material is committed;
- SQL connection URLs are not persisted in accepted manifests;
- raw third-party datasets are excluded from the release artifact;
- local development can run without an API key; deployments can configure `PDM_API_KEYS` externally.

## Deployment evolution

SQLite is intentional for reproducible local/reference acceptance. Managed
PostgreSQL state is available through the explicit SQLAlchemy adapter, with
all stateful stores sharing one fail-closed connection boundary and a schema
initialization check. Live streaming, site-specific CMMS write-back and
enterprise IAM remain deployment integrations, not hidden V1 claims.
