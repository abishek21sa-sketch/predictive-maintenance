# Predictive Maintenance Intelligence Platform

**Release:** `1.0.0`  
**Signature product:** **Reliability Observatory + Real Operations Command + Data Gateway + Reliability Shock Lab**

A production-oriented predictive-maintenance decision intelligence platform that couples **AI prognostics**, **Reliability / Industrial Engineering**, **Operations Research**, and **stochastic simulation** into auditable maintenance intervention decisions.

The platform is deliberately not a generic failure-score dashboard. Its decision chain is:

`data → validated state → prognostics / failure-horizon risk → reliability consequence → uncertainty-aware optimization → disruption stress → committed maintenance work order`

## Problem

Maintenance teams need to decide more than whether an asset looks unhealthy. They need to know:

- which assets are degrading and how uncertain the prognosis is;
- the reliability and economic consequence of waiting;
- whether to maintain, inspect, or defer;
- whether the maintenance organization can execute the plan with available bays, labor, skills and parts;
- how sensitive the decision is to RUL error and resource disruption;
- which outputs are observed, predicted, calculated, optimized, or simulated.

## System architecture

```text
NASA C-MAPSS benchmark ───────────────┐
generic CSV/JSON/SQL ── readiness ───┼─→ AI evidence ─→ engineering consequence ─┐
MetroPT-3 real telemetry ────────────┘                                          │
       │                                                                        ▼
       └─→ strict source validation → streaming features → anomaly + 24h risk → CVaR Maintain/Inspect/Defer MILP
                                                                                 │
                                      ┌──────────────────────────────────────────┴─────────┐
                                      ▼                                                    ▼
                              Reliability Shock Lab                              Real Operations Command
                              policy stress simulation                           dossier / disruptions /
                                                                                 work-order commitment
```

The three data paths remain evidence-isolated: NASA benchmark performance is never transferred to unrelated user data or the MetroPT-3 compressor.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/TECHNICAL_METHODS.md`](docs/TECHNICAL_METHODS.md), [`docs/MATH_ML_AI_METHODS.md`](docs/MATH_ML_AI_METHODS.md), and [`docs/NEXT_LEVEL_READINESS.md`](docs/NEXT_LEVEL_READINESS.md).

## Artificial Intelligence

### Bundled NASA C-MAPSS FD001 benchmark

The release contains a persisted RUL model and anomaly model for the NASA C-MAPSS FD001 benchmark. Model selection uses complete-asset holdout rather than random row splitting, and the selected model must beat an age-only baseline.

**Historical/public benchmark evidence bundled with the release:**

| Metric | Selected model | Age-only baseline |
|---|---:|---:|
| RMSE | **19.24 cycles** | **33.34 cycles** |
| MAE | **14.17 cycles** | **27.59 cycles** |
| R² | **0.786** | **0.356** |
| NASA asymmetric score | **735.93** | **5,318.92** |

The bundled runtime model is the histogram-gradient-boosting candidate selected
by asset-holdout RMSE. The official test metrics above are untouched benchmark
evidence; they are not used for model selection.

These are benchmark results on simulated C-MAPSS turbofan degradation data. They are **not** field accuracy claims for real aircraft or unrelated industrial equipment.

### MetroPT-3 real operational track

The repository also implements a distinct real-data AI path for the **MetroPT-3 railway Air Production Unit compressor**. The public source contains 1,516,948 operational observations and 15 analogue/digital sensor signals. The raw dataset is acquired locally and is not committed to the repository.

MetroPT-3 does not supply a certified row-level RUL target. The platform therefore does **not** fabricate RUL or reuse the NASA model. Instead it implements:

- strict full-source integrity validation;
- chunk-streamed 10-minute engineering features;
- an Isolation Forest trained only on the February reference period;
- a 24-hour published-failure-horizon classifier;
- chronological development → June validation → July/August holdout separation;
- Logistic Regression baseline versus Random Forest candidate;
- Average Precision, Brier score, precision, recall, F1 and ROC-AUC;
- direct coupling of the selected real-data risk output into maintenance intervention optimization.

Full real-data evidence is earned only when `scripts/phase5_acceptance.ps1` succeeds against the complete locally acquired UCI source. Structural/synthetic CI fixtures cannot promote that readiness state. See [`docs/METROPT3_REAL_OPERATIONS.md`](docs/METROPT3_REAL_OPERATIONS.md).

### External-data RUL lifecycle

When a user dataset supplies explicit RUL ground truth and enough independent asset trajectories, the platform can train a dataset-specific model using:

- Ridge regression;
- Random Forest regression;
- Histogram Gradient Boosting regression;
- an age/time-only baseline;
- complete-asset holdout validation;
- MAE / RMSE / R²;
- empirical 90% residual-interval evidence.

A user dataset never inherits NASA benchmark performance merely because it can be ingested.

### Synthetic development fixture

When public or site data is not yet available, generate a deterministic 200,000+ row multi-asset
fixture for local development and UI/pipeline testing:

```powershell
$env:PYTHONPATH = ".\\.venv\\Lib\\site-packages;.\\src"
& ".\\.venv\\Scripts\\python.exe" scripts\\generate_synthetic_dataset.py --rows 200000 --assets 1000
& ".\\.venv\\Scripts\\python.exe" scripts\\synthetic_acceptance.py --rows 200000 --assets 1000
```

The fixture contains canonical asset, telemetry and maintenance files and is marked
`SYNTHETIC_TEST_ONLY`. It cannot clear MetroPT real-data model promotion or prospective field
qualification. Add `--model-check` to `synthetic_acceptance.py` for the heavier end-to-end RUL
training check. The optional model check uses a deterministic 20,000-row, all-asset sample by
default so the full 200,000-row data path is exercised without making local validation unbounded.

## Industrial / Reliability Engineering

Executable engineering methods include:

- MTBF and MTTR;
- availability `A = MTBF / (MTBF + MTTR)`;
- Weibull reliability and hazard;
- bathtub-regime interpretation;
- FMEA risk prioritization;
- RCM action mapping;
- preventive / corrective / inspection economics;
- maintenance KPIs;
- asset criticality and intervention prioritization.

The core equations are implemented in code and independently tested. See [`docs/TECHNICAL_METHODS.md`](docs/TECHNICAL_METHODS.md).

## Operations Research

The primary decision model is a **Gurobi-first MILP**. A SciPy/HiGHS path is retained for reproducible CI and environments without a Gurobi runtime.

The V1.0 portfolio model jointly chooses **intervention type and timing**:

- maintain;
- inspect;
- defer when permitted.

The objective minimizes modeled expected lifecycle cost plus CVaR tail risk:

`min E[C(x, ξ)] + λ · CVaRα(C(x, ξ))`

Constraints cover:

- exactly one permitted intervention choice per selected asset;
- critical-RUL deferral protection;
- multi-cycle maintenance-bay occupancy;
- labor-hour capacity;
- technician/skill capacity;
- part inventory;
- intervention timing and horizon feasibility;
- baseline and bay/crew stress capacity profiles;
- portfolio-level deferral limits.

Solver status, objective, best bound, MIP gap, solve time and termination reason are treated as engineering evidence. Gurobi infeasibility can produce an IIS rather than a fabricated plan.

A tiny exact-enumeration oracle independently verifies the CVaR MILP objective.

## Simulation

The **Reliability Shock Lab** compares three policies under the same stochastic draws:

1. optimized intervention portfolio;
2. earliest-RUL heuristic;
3. run-to-failure.

Stressors include RUL error, maintenance-duration variation, bay outage, technician absence, part delay and corrective downtime. Common random numbers reduce comparison noise and fixed seeds make the benchmark reproducible.

All resulting costs, failures and downtime are explicitly labeled **SIMULATED** rather than realized savings.

## Real / external data readiness

The Data Gateway implements:

`source → adapter → canonical schema → validation/readiness gate → versioned dataset → replay/model lifecycle`

Supported V1.0 modes:

- bundled NASA benchmark;
- browser CSV / JSON / JSONL / NDJSON upload;
- server-side CSV / JSON / optional Parquet ingestion;
- SQL database ingestion through SQLAlchemy;
- historical replay by cycle or timestamp.

Accepted datasets are content-fingerprinted and persisted separately from the NASA release. SQL credentials and absolute source paths are not stored in dataset manifests.

Readiness is capability-specific:

- `READY`
- `READY_WITH_ASSUMPTIONS`
- `PARTIAL`
- `NOT_READY`

For example, generic pump telemetry can be ready for condition monitoring while remaining `NOT_READY` for the bundled FD001 RUL model.

See [`docs/EXTERNAL_DATA_CONTRACT.md`](docs/EXTERNAL_DATA_CONTRACT.md).

## CMMS / ERP exchange boundary

The reference integration layer implements vendor-neutral
`CMMS-WORK-ORDER.v1` and `CMMS-INVENTORY.v1` contracts. It validates external
work orders and inventory positions, stores them in separate mirrors,
fingerprints records for idempotent replay, updates only strictly newer
revisions, reports stale/conflicting inputs without silent overwrite, and
emits contract-only JSONL with audit linkage.

Use the dedicated Windows acceptance gate:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cmms_erp_acceptance.ps1
```

The API routes are `/api/integrations/cmms/reconcile`,
`/api/integrations/cmms/work-orders`, `/api/integrations/cmms/export`,
`/api/integrations/cmms/inventory/reconcile`, and
`/api/integrations/cmms/inventory/export`.
These records describe exchange state; they do not prove field execution.

## Product surfaces

### Reliability Observatory — `/`

- **Fleet Orbit** — radial fleet prognostic urgency field;
- **Case Lens** — one-asset RUL / risk / anomaly evidence;
- **Decision Field** — Maintain / Inspect / Defer alternatives;
- **Maintenance Clock** — radial resource-constrained schedule.

### Data Gateway — `/data-gateway`

- File Airlock;
- Database Port;
- Dataset Vault;
- Replay Tunnel;
- Model Forge.

### Reliability Shock Lab — `/stress-lab`

- uncertainty-aware intervention plan;
- expected modeled cost and CVaR;
- Maintain / Inspect / Defer mix;
- three-policy stochastic stress comparison.

### Real Operations Command — `/operations`

- hard evidence gate: real source READY or explicit NOT READY;
- MetroPT-3 historical sensor dossier and published failure-event navigation;
- 24-hour failure-horizon probability and February-reference anomaly evidence;
- Maintain / Inspect / Defer intervention workbench;
- Gurobi decision + timing under explicitly assumed shop resources/economics;
- disruption re-solves for bay outage, technician absence, part delay and duration inflation;
- auditable decision trace;
- commit-to-work-order loop with operator-recorded lifecycle state.

### Executable Methodology — `/methodology`

Explains the implemented Data → AI → Reliability → OR → Simulation chain and the evidence boundary between predicted, calculated, optimized and simulated outputs.

## Windows quick start

Windows is the primary local acceptance target. The final real-operations gate is:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\phase5_acceptance.ps1
```

This downloads MetroPT-3 from UCI when needed, validates the complete source, builds its local feature/model evidence, runs the full warning-clean regression suite and Phase-5 diagnostics, then verifies the real-data-derived decision through licensed Gurobi. Raw MetroPT data remains local and gitignored.

Start the application with the policy-safe wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

Open:

- `http://127.0.0.1:8001`
- `http://127.0.0.1:8001/data-gateway`
- `http://127.0.0.1:8001/operations`
- `http://127.0.0.1:8001/stress-lab`
- `http://127.0.0.1:8001/methodology`
- `http://127.0.0.1:8001/docs`

The prior `scripts/v1_acceptance.ps1` remains the stable benchmark/release-core gate; Phase-5 acceptance adds the complete real-operational evidence requirement on top.

## Standard development setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX: source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -W error
python scripts/v1_diagnostics.py
python scripts/release_audit.py
python scripts/v1_benchmark.py
uvicorn pdm_intelligence.api.main:app --host 127.0.0.1 --port 8001
```

Licensed Gurobi path:

```bash
python -m pip install -e '.[dev,gurobi]'
```

Optional Parquet support:

```bash
python -m pip install -e '.[parquet]'
```

No Gurobi license file, WLS credential, database password or API key belongs in the repository.

## Reproducibility and validation

V1.0 provides:

- deterministic seeds for AI diagnostics, RUL scenarios and Monte Carlo stress simulation;
- content hashes for accepted external datasets;
- asset-level train/validation separation;
- AI baseline comparison;
- mathematical reliability tests;
- exact-enumeration OR oracles;
- solver feasibility/status checks;
- simulation reproducibility checks;
- API and frontend smoke tests;
- strict warning-as-error tests;
- security/secret audit;
- exact-ZIP clean-extraction validation;
- installable wheel validation;
- strict full-source MetroPT-3 validation and provenance;
- chronological real-data holdout evaluation;
- real-data → OR → work-order integration diagnostics.

See [`docs/evidence/VALIDATION_REPORT.md`](docs/evidence/VALIDATION_REPORT.md).

## Evidence language

The system deliberately distinguishes:

- **OBSERVED / USER-SUPPLIED** — source records;
- **CALCULATED** — reliability and maintenance mathematics;
- **PREDICTED** — model outputs;
- **OPTIMIZED** — solver-selected decisions;
- **SIMULATED** — stochastic policy consequences;
- **VALIDATED** — only where a defined validation protocol passed.

It does not claim realized savings, causal production improvement, certified aircraft safety, or live production reliability.

## Release boundary and limitations

The release is designed to be complete for the agreed **non-live-data portfolio boundary**, including an end-to-end real operational historical-data path. It does not claim:

- automatic universal RUL prediction for arbitrary equipment;
- live streaming telemetry without a site-specific connector;
- production CMMS/ERP credentials/connectivity;
- enterprise OIDC/IAM tenant integration;
- certified aircraft safety performance;
- prospective/live industrial field validation;
- realized downtime or maintenance-cost savings.

Those are deployment/field-validation responsibilities, not hidden implementation claims.

## Repository safety

- `.env` is gitignored; `.env.example` contains no secrets;
- Gurobi license files and WLS credentials are excluded;
- SQL source credentials are never persisted to accepted dataset manifests;
- raw NASA source data is not redistributed in the release;
- local generated databases, external datasets (including raw MetroPT-3), external trained models and build caches are ignored;
- release audit scans public text for common credential/private-key patterns and private Windows paths.

## License

Code in this repository is released under the MIT License. Dataset/model artifacts retain their source-data provenance and should be used in accordance with the originating dataset terms. See [`LICENSE`](LICENSE) and [`data/SOURCE_MANIFEST.json`](data/SOURCE_MANIFEST.json).

## BELIEF-MAINT — Markov Belief-State Maintenance Scheduling

The V1 platform now includes `BELIEF-MAINT`, a distinct maintenance-timing layer that propagates latent health belief across `healthy -> degraded -> critical -> failed` states and solves a crew-constrained MILP over maintenance timing and production opportunity cost.

This is deliberately different from the existing RUL/CVaR intervention planner: BELIEF-MAINT does not treat one point prediction as a deterministic failure date. The bundled deterministic reference compares the optimized belief-state schedule against fixed-interval, current-risk-rank, and lowest-production-load heuristics.

Run the evidence gates:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) "src")
python scripts\belief_maint_evidence.py
python scripts\belief_maint_product_evidence.py
```

Windows acceptance:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows_belief_maint_acceptance.ps1
```

Operator workspace: `http://127.0.0.1:8001/belief-maint`

The bundled transition matrix and benchmark are synthetic engineering-validation inputs. They are not calibrated field degradation probabilities, causal claims, or guaranteed failure-prevention outcomes; every released schedule remains human-review gated.


## Signature algorithm
See [`docs/SIGNATURE_ALGORITHM.md`](docs/SIGNATURE_ALGORITHM.md) for the governed BELIEF-MAINT formulation and validation contract.
