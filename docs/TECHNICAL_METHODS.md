# Technical Methods — Predictive Maintenance Intervention Intelligence

This document records the computational methods implemented in the repository. Evidence labels distinguish historical/public benchmark validation, synthetic validation, mathematical verification, optimized decisions, and simulated outcomes.

## A. Artificial Intelligence

### Remaining Useful Life prognostics

**Task.** Estimate cycles of remaining useful life (RUL) from engine operating settings, sensor trajectories, engineered rolling/degradation features, and current asset age.

**Models.** Ridge regression, Random Forest regression, and histogram gradient boosting are trained as candidate prognostic models. Selection is by asset-group holdout RMSE. The selected algorithm is refit only after candidate evaluation. An age-only Ridge model is retained as the explicit baseline.

**Features.** C-MAPSS operating settings, sensor channels and deterministic trajectory features from `src/pdm_intelligence/features/engineering.py`. Unit identity is never used as a predictive feature.

**Target.** Training RUL is computed as `max_observed_cycle_for_asset - current_cycle`, capped only where configured by the benchmark pipeline.

**Validation.** Asset IDs, rather than individual rows, are separated between development and validation to prevent measurements from the same engine leaking across the split. The public FD001 release evidence is historical/public benchmark evidence; CI diagnostics use a deterministic C-MAPSS-shaped synthetic fixture and are labeled synthetic validation.

**Metrics.** MAE, RMSE, R², and the NASA asymmetric RUL score. Complexity is accepted only when the selected model improves upon the age-only baseline.

**Uncertainty.** The validation workflow records the 90th percentile of absolute validation residuals and its empirical coverage. This is an empirical holdout residual band, not a formal coverage-guaranteed conformal interval. The runtime exposes tree-ensemble dispersion when the bundled model supports it; otherwise it exposes the asset-holdout residual band. Both are explicitly labeled model uncertainty rather than calibrated predictive probability.

**Decision use.** RUL and uncertainty feed reliability consequence estimation and maintenance-window optimization. They are parameters to the decision engine; they do not directly become maintenance orders.

### Anomaly/degradation signal

The anomaly engine provides an independent degradation signal from sensor state. It is used for inspection escalation and decision confidence, not mislabeled as failure probability.

## B. Industrial Engineering

### Availability

`A = MTBF / (MTBF + MTTR)`

Units: MTBF and MTTR are in consistent cycle/time units; availability is dimensionless. Implemented in `src/pdm_intelligence/reliability/analysis.py` and mathematically checked in `tests/test_phase1_math.py`.

Operational interpretation: the long-run fraction of time/cycles an asset is expected to be available under the assumed repair process.

### Weibull reliability and hazard

For shape `β` and scale `η`, hazard is implemented as

`h(t) = (β/η)(t/η)^(β-1)`.

The fitted shape is used to classify infant-mortality, approximately random-failure, and wear-out regimes. The test suite verifies the analytical boundary `h(η)=β/η`.

### Maintenance economics

Expected intervention economics are implemented in `src/pdm_intelligence/reliability/economics.py`:

`E[C] = C_PM + p_f C_CM + [p_f D_CM + (1-p_f) D_PM] C_d`

where `C_PM` is preventive cost, `C_CM` corrective cost, `p_f` failure probability, `D` downtime cycles, and `C_d` downtime cost per cycle. The cost equation is independently closed in a unit test.

### FMEA / RCM / maintenance KPIs

FMEA risk prioritization, RCM action mapping, MTBF, MTTR and availability are executable modules rather than documentation-only labels. Their purpose is to translate prognostic state into maintenance consequences and policy choices.

## C. Operations Research

### Maintenance intervention scheduling MILP

**Decision variables.** Binary `x[i,t] = 1` when asset `i` starts maintenance at cycle `t`.

**Objective.** Minimize expected preventive, failure-risk, life-loss, lateness, and maintenance-downtime cost. A scenario-weighted version forms the stochastic expected-cost objective under alternative RUL realizations.

**Constraints.**

- exactly one start time per asset;
- maintenance-bay occupancy across every cycle touched by a multi-cycle job;
- labor-hour capacity per cycle;
- technician-skill capacity per cycle;
- spare-start capacity per cycle;
- total part-inventory availability;
- valid horizon placement so a job finishes inside the planning horizon;
- binary domains.

**Solvers.** Gurobi is the intended primary licensed solver. The repository includes an automatic SciPy/HiGHS MILP fallback for reproducible CI and environments without Gurobi. Gurobi license files or credentials are never stored in the repository.

**Solver evidence.** `solve_maintenance` returns solver name, status, objective, best bound/MIP gap when exposed by the solver, solve time, and termination reason. A no-incumbent or infeasible solve is not presented as an optimized schedule.

**Independent validation.** `enumerate_small_instance` exactly enumerates tiny schedules. `tests/test_phase1_math.py` verifies the MILP objective equals the exact enumeration optimum and separately validates physical resource occupancy.

**Simulation optimization.** Monte Carlo lifecycle simulation compares run-to-failure and predictive intervention policies under uncertain realized RUL. Fixed seeds make benchmark runs reproducible. Simulated cost/downtime outcomes are labeled simulations, not realized savings.


### Uncertainty-aware intervention portfolio

V1.0 uses the richer portfolio MILP in `src/pdm_intelligence/optimization/intervention.py` for the primary maintain / inspect / defer decision.

**Decision variables.** Binary alternatives choose one action for each selected asset: maintain at cycle `t`, inspect at cycle `t`, or defer when the asset is not inside the protected critical-RUL threshold. The choice and the timing are therefore optimized jointly.

**Objective.** For RUL scenario `ξ`, each candidate action has modeled lifecycle cost `C(x,ξ)`. The portfolio objective is

`min E[C(x,ξ)] + λ CVaR_α(C(x,ξ))`.

The CVaR linearization uses a free VaR threshold `η` and non-negative scenario excess variables `z_s`, with `z_s ≥ C_s(x)-η`. `λ` is the explicit risk-aversion coefficient. Scenario probabilities are normalized and recorded.

**Robust capacity uncertainty set.** The same intervention selection must remain feasible under every named capacity profile. The benchmark decision-intelligence path currently uses a baseline profile plus a discrete bay/crew stress profile. Constraints cover multi-cycle bay occupancy, labor hours, technician skills and total part inventory.

**Criticality and deferral.** Assets at or below the configured critical RUL threshold cannot be assigned the defer alternative. A portfolio-level maximum deferral count is also enforced.

**Infeasibility.** Deterministic prechecks identify impossible mandatory interventions before solve. When Gurobi itself proves infeasibility, the implementation computes an IIS and reports the implicated constraint names rather than presenting a plan.

**Independent oracle.** `enumerate_intervention_small_instance` enumerates every alternative/timing combination for tiny instances and evaluates the same expected-cost + CVaR objective. V1.0 tests require the MILP optimum to equal this independent exact result.



### Maintenance stress simulation

`src/pdm_intelligence/simulation/stress.py` evaluates the optimized intervention portfolio against an earliest-RUL heuristic and run-to-failure. All policies receive the **same random draws** (common random numbers) for RUL error, maintenance-duration variation, bay outages, technician absence, part delays and corrective downtime. This reduces comparison noise and makes the policy comparison reproducible for a fixed seed.

Reported quantities include expected and 95th-percentile modeled cost, expected failures, expected downtime, disrupted interventions and on-time intervention rate. These are explicitly **SIMULATED** consequences and are never described as realized savings or causal production improvement.

## D. External Data & Model Lifecycle

### Canonical data gateway

The external-data path is implemented in `src/pdm_intelligence/external/` and is deliberately separated from the bundled NASA release path.

`source → adapter → canonical schema → validation/readiness gate → versioned dataset → model/replay services`

Adapters currently cover browser text, server-side files and SQLAlchemy database sources. Accepted datasets are content-fingerprinted and persisted under a dataset-specific directory. Connection URLs/credentials are never written to the dataset manifest.

### Capability-specific readiness

Readiness is not a single marketing score. Each capability is independently assigned `READY`, `READY_WITH_ASSUMPTIONS`, `PARTIAL`, or `NOT_READY` using the fields actually present.

A generic industrial telemetry dataset can therefore be valid for condition monitoring while remaining incompatible with the bundled FD001 model.

### External RUL learning

**Task.** Train a dataset-specific RUL regressor when an external dataset supplies explicit RUL ground truth.

**Inputs/features.** Numeric condition signals, cycle or elapsed-time state, and deterministic past-only deltas / rolling mean / rolling standard deviation. Asset identity and target-like fields are excluded from the feature set.

**Target.** User-supplied canonical `rul`. The gateway does not fabricate this target from undocumented business rules.

**Candidate models.** Ridge, Random Forest regression, Histogram Gradient Boosting regression.

**Baseline.** A simple age/time Ridge model using cycle or elapsed hours only.

**Training procedure.** Complete assets are split into training and validation groups. Individual rows from the same asset cannot appear in both sides of the validation split.

**Selection.** Lowest validation RMSE among candidate models.

**Metrics.** MAE, RMSE, R².

**Uncertainty.** The persisted model records the 90th percentile of absolute validation residuals and empirical coverage on the asset holdout. This is labeled an empirical residual interval rather than a formal calibrated guarantee.

**Evidence class.** `VALIDATED_ON_USER_SUPPLIED_HOLDOUT`.

**Decision use.** A compatible external model can supply RUL estimates to the same reliability/maintenance-decision chain, but deployment appropriateness still requires engineering review and prospective validation.

### Historical replay

Replay uses the accepted canonical ordering axis (`cycle` or `timestamp`) and returns only records at or before the requested boundary. This supports future-blind historical reconstruction and scenario testing.

### Data-quality mathematics and invariants

Executable checks include:

- uniqueness of `(asset_id, cycle)` or `(asset_id, timestamp)`;
- strictly increasing time axis within each asset;
- non-negative numeric RUL/cycle/cost/labor/downtime values where applicable;
- no maintenance end time before start time;
- referential integrity against the asset master when supplied;
- non-empty numeric telemetry signal set;
- deterministic content fingerprints for identical accepted datasets.

## E. MetroPT-3 Real Operational AI and Decision Coupling

### Source and target semantics

MetroPT-3 is handled as a separate real-data evidence track. The source supplies 15 compressor sensor signals and timestamps; the published failure reports supply failure-event windows. The implementation **does not create a row-level RUL target** and does not apply the C-MAPSS RUL model.

The real-data supervised target is instead:

`y_t = 1` when timestamp `t` lies in the 24 hours preceding a published failure start; otherwise `0`.

This is a derived historical failure-horizon label. It is not certified RUL and is not a prospective safety label.

### Streaming feature engineering

The raw source is read in chunks and combined into 10-minute buckets. Analogue signals retain mean, variance-derived standard deviation, minimum and maximum; digital signals retain duty cycles. Source-index ranges preserve traceability. Pressure-gap and motor-load features are deterministic functions of present/past source values.

### Chronological model validation

**Reference anomaly model.** Isolation Forest is fit only on the February reference period. The operational alert threshold is derived from the February score distribution.

**Failure-horizon baseline.** Logistic Regression on the analogue mean features.

**Failure-horizon candidate.** Random Forest classification on the complete engineered feature vector.

**Temporal split.** No random shuffling is permitted:

- training/development data precede June 2020;
- June is the validation period for candidate promotion and threshold choice;
- July onward is held out until final historical evaluation.

**Metrics.** Average Precision, Brier score, precision, recall, F1, ROC-AUC and prevalence. The candidate is promoted only when it improves validation Average Precision and does not exceed the configured Brier degradation tolerance.

**Evidence class after full-source acceptance.** `VALIDATED_ON_REAL_OPERATIONAL_METROPT3_HOLDOUT`.

### Industrial Engineering interpretation

The real failure-horizon probability is translated into intervention economics and a planning urgency signal. Because MetroPT-3 does not contain a complete maintenance-shop economic/resource contract, preventive cost, failure consequence, downtime cost, bay count, technician availability, parts and shop backlog are explicit assumptions.

The 24-hour probability is transformed to a **risk-equivalent planning deadline** for scheduling. This is deliberately labeled a decision transform and **not RUL**.

### Operations Research coupling

The MetroPT APU case enters the same Maintain / Inspect / Defer CVaR MILP used by the validated decision engine. The target APU's risk evidence comes from real telemetry/model output; shop backlog and resource availability are assumption-scoped inputs. The resulting action and start cycle are therefore labeled:

`OPTIMIZED_WITH_REAL_RISK_AND_ASSUMED_RESOURCE_CONTEXT`.

The Disruption Studio changes only the planning assumptions—bay outage, technician absence, part delay, duration inflation or compound disruption—then re-solves the same historical real-data case. Observed telemetry never changes merely because the user selects a scenario.

### Work-order commitment

A selected intervention can be persisted to the local work-order ledger with the complete decision trace. Allowed application-state transitions are:

`COMMITTED → IN_PROGRESS → COMPLETED`

with cancellation allowed from non-terminal states. These statuses are operator-recorded workflow state. They do not assert that an equivalent intervention historically occurred in the MetroPT-3 source.

## F. Evidence and release gates

The full real-operations evidence claim requires `scripts/phase5_acceptance.ps1` to complete against the **complete locally acquired UCI source**, including strict source validation, chronological holdout evidence, operations/API diagnostics and licensed Gurobi acceptance. CI structural fixtures and the six-row real-source excerpt validate code paths only and are not substitutes for the full-data gate.
