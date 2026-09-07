# Mathematical, ML, and AI Methods

## Purpose and evidence boundary

This document is the technical companion to the Predictive Maintenance Intelligence Platform. It explains the mathematical definitions, model-training procedures, uncertainty calculations, decision optimization, and validation boundaries implemented in the source code.

The platform separates five evidence classes:

1. **Observed** — source telemetry, published failure windows, asset metadata, or operator-recorded workflow state.
2. **Predicted** — RUL, anomaly score, or failure-horizon risk produced by a model.
3. **Calculated** — reliability, engineering, economic, KPI, and risk-equivalent quantities derived from explicit inputs.
4. **Optimized** — an action/timing plan selected by a constrained mathematical program.
5. **Simulated** — seeded stress and lifecycle outcomes generated from modeled assumptions.

The system never treats benchmark accuracy as field accuracy, an anomaly score as failure probability, a planning transform as certified RUL, or simulated savings as realized savings.

## 1. Data semantics and leakage controls

Every model is trained against a declared dataset contract. The canonical telemetry identity is `(asset_id, cycle)` or `(asset_id, timestamp)`. Asset identity is used for grouping and traceability, never as a predictive feature.

For trajectory data, the latest row for each asset is the scoring point. Training and validation are separated by complete assets, not individual rows. This prevents neighboring measurements from one engine or compressor appearing on both sides of an evaluation split.

For chronological operational data, the time order is preserved:

```text
earlier development period → validation period → untouched future holdout
```

The gateway validates uniqueness, monotonic time, numeric ranges, required telemetry, referential integrity, and deterministic dataset fingerprints before downstream models can use the accepted data.

## 2. Remaining Useful Life mathematics

### 2.1 Target construction

For engine or asset `i` at cycle `t`, the benchmark target is:

\[
  RUL_{i,t} = \max(0, C_{i,\max} - C_{i,t})
\]

where `C_i,max` is the last observed training cycle for that asset. The benchmark pipeline optionally applies an explicit upper cap:

\[
  RUL^{cap}_{i,t} = \min(RUL_{i,t}, RUL_{max})
\]

The cap is a training-label policy for the benchmark. It is not a claim that an actual asset cannot live longer.

External datasets must provide their own explicit RUL ground truth. The gateway does not silently invent an RUL target from a generic sensor file.

### 2.2 Past-only feature engineering

Features are deterministic functions of current and historical values. A rolling mean for signal `x` over a trailing window of width `w` is:

\[
  \bar{x}_{i,t,w} = \frac{1}{n_{i,t,w}}\sum_{k \in W_{i,t,w}} x_{i,k},
  \quad k \leq t
\]

The corresponding population standard deviation is:

\[
  s_{i,t,w} = \sqrt{\frac{1}{n_{i,t,w}}\sum_{k \in W_{i,t,w}}
  (x_{i,k}-\bar{x}_{i,t,w})^2}
\]

Past-only deltas use:

\[
  \Delta x_{i,t}=x_{i,t}-x_{i,t-1}
\]

The implementation fills the first delta/rolling values with deterministic defaults rather than using future observations. Operating settings and sensor channels are retained only when they satisfy the dataset contract.

### 2.3 Candidate models and selection

The RUL candidate set is:

- Ridge regression with standardized features;
- Random Forest regression;
- Histogram Gradient Boosting regression.

The age-only baseline is a Ridge model using cycle/age alone. Candidate selection minimizes validation RMSE on complete-asset holdout groups:

\[
  \hat{m}=\arg\min_{m\in M} RMSE_{holdout}(m)
\]

The selected estimator is refit on all development assets only after selection. The final benchmark model is selected independently per C-MAPSS regime; metrics are not transferred from one regime to another.

### 2.4 Regression metrics

For `n` scored assets with truth `y_i` and prediction `\hat{y}_i`:

\[
  MAE=\frac{1}{n}\sum_i |y_i-\hat{y}_i|
\]

\[
  RMSE=\sqrt{\frac{1}{n}\sum_i (y_i-\hat{y}_i)^2}
\]

\[
  R^2=1-\frac{\sum_i(y_i-\hat{y}_i)^2}
                 {\sum_i(y_i-\bar{y})^2}
\]

The NASA asymmetric score penalizes late RUL predictions more steeply than early predictions. Let `d_i = \hat{y}_i-y_i`:

\[
  S=\sum_i
  \begin{cases}
    e^{-d_i/13}-1, & d_i<0\\
    e^{d_i/10}-1, & d_i\geq0
  \end{cases}
\]

Lower MAE, RMSE, and NASA score are better; higher R² is better. The age-only baseline is retained so that added model complexity must demonstrate measurable value.

### 2.5 Uncertainty and grouped confidence intervals

The runtime records an empirical 90% residual half-width from the asset holdout:

\[
  q_{.90}=Q_{.90}(|y_i-\hat{y}_i|)
\]

For a non-negative prediction `\hat{y}` the displayed interval is:

\[
  [\max(0,\hat{y}-q_{.90}),\hat{y}+q_{.90}]
\]

This is a holdout residual diagnostic, not a formal conformal guarantee. Model selection and interval estimation share the same holdout, so the implementation labels the result honestly.

Metric uncertainty is estimated by resampling complete asset trajectories as units. Rows from the same asset are not treated as independent observations. The report stores 95% grouped bootstrap low/median/high values for MAE, RMSE, R², the NASA score, and interval coverage.

### 2.6 Explainability

Permutation importance measures the increase in validation error after randomly permuting one feature while keeping the fitted model fixed. Positive importance means the feature contributed predictive signal on the sampled holdout. It is diagnostic importance, not a causal effect, and it does not prove that changing a sensor will change failure risk.

## 3. Operational anomaly and failure-horizon AI

The MetroPT-3 path is a separate real-data track. It does not reuse the C-MAPSS RUL model.

### 3.1 Anomaly model

An Isolation Forest is fit only on a declared February reference period. Its score distribution establishes an operational alert threshold. The anomaly score is a ranking/degradation signal; it is not a calibrated probability of failure.

### 3.2 Published-failure horizon label

For a timestamped observation `t`, the supervised label is:

\[
  y_t=1\quad\text{if }t\text{ lies in the 24-hour window before a published failure start}
\]

otherwise `y_t=0`. This is a historical label derived from published failure windows. It is not a prospective safety label or certified RUL.

### 3.3 Chronological classification validation

The failure-horizon candidate set includes Logistic Regression as a baseline and Random Forest as a nonlinear candidate. The evaluation preserves chronology and evaluates Average Precision, ROC-AUC, Brier loss, precision, recall, F1, prevalence, and event-level confidence bounds.

The promotion gate requires both chronological validation and future holdout evidence to clear declared thresholds:

- Average Precision above the period prevalence;
- ROC-AUC above chance;
- Brier loss better than a constant-prevalence predictor;
- event/block-clustered confidence bounds where independent groups exist;
- at least two independent published failure windows in each evaluation period.

If these conditions are not met, the runtime remains `PROMOTION_BLOCKED`. Strong anomaly ranking cannot substitute for a weak supervised failure-horizon model.

### 3.4 Average Precision and Brier loss

Average Precision summarizes the precision-recall curve and is informative for rare-event ranking. It must be compared with the event prevalence; a high-looking score can still be unhelpful when the base rate is low.

For predicted probability `p_i`, Brier loss is:

\[
  Brier=\frac{1}{n}\sum_i(p_i-y_i)^2
\]

The constant-prevalence comparator uses `p_i=\pi`, where `\pi` is the evaluation-period event prevalence. Lower Brier loss is better.

## 4. Reliability and industrial-engineering mathematics

### 4.1 Availability

With mean time between failures `MTBF` and mean time to repair `MTTR`:

\[
  A=\frac{MTBF}{MTBF+MTTR}
\]

The result is dimensionless and applies only to the repair-process assumptions supplied to the calculation.

### 4.2 Weibull reliability and hazard

For shape `\beta` and scale `\eta`, the survival/reliability function is:

\[
  R(t)=e^{-(t/\eta)^\beta}
\]

The hazard function is:

\[
  h(t)=\frac{\beta}{\eta}\left(\frac{t}{\eta}\right)^{\beta-1}
\]

The fitted shape is interpreted as infant mortality when `\beta<0.95`, approximately random failure near `\beta=1`, and wear-out when `\beta>1.05`. This is a screening interpretation, not a physical diagnosis without suitable failure data.

### 4.3 Maintenance economics

For failure probability `p_f`, preventive cost `C_PM`, corrective cost `C_CM`, corrective downtime `D_CM`, preventive downtime `D_PM`, and downtime cost per cycle `C_d`:

\[
  E[C_{PM}]=C_{PM}+p_fC_{CM}+
  [p_fD_{CM}+(1-p_f)D_{PM}]C_d
\]

The run-to-failure comparator is:

\[
  C_{RTF}=C_{CM}+D_{CM}C_d
\]

The displayed avoided-cost quantity is `C_RTF-E[C_PM]`. It is a modeled comparison under explicit assumptions, not a realized savings claim.

## 5. Operations-research decision mathematics

### 5.1 Intervention variables

For asset `i`, action `a`, and start cycle `t`, binary variable `x_{i,a,t}` equals one when that action starts at that time. The action set is Maintain, Inspect, and Defer where policy permits.

Exactly one permitted alternative is selected per asset:

\[
  \sum_{a,t}x_{i,a,t}=1\quad\forall i
\]

Critical assets cannot be assigned Defer. Job duration, labor, skill, bay occupancy, and part requirements are attached to each candidate.

### 5.2 Expected cost plus CVaR

For RUL scenario `s` with probability `p_s` and scenario cost `C_s(x)`, the objective is:

\[
  \min_x\;E[C(x,\xi)]+\lambda CVaR_\alpha(C(x,\xi))
\]

where:

\[
  E[C]=\sum_s p_sC_s(x)
\]

The linearized CVaR formulation introduces VaR threshold `\eta` and non-negative excess `z_s`:

\[
  z_s\geq C_s(x)-\eta,\quad z_s\geq0
\]

\[
  CVaR_\alpha=\eta+\frac{1}{1-\alpha}\sum_s p_sz_s
\]

`\lambda` expresses risk aversion. Scenario probabilities are normalized and persisted as evidence.

### 5.3 Feasibility constraints

The MILP enforces:

- bay occupancy across every cycle of a multi-cycle job;
- labor-hour capacity per cycle;
- technician-skill capacity per cycle;
- part inventory and spare-start limits;
- valid start/end placement inside the horizon;
- robust feasibility across named capacity profiles;
- maximum deferral limits and criticality policies.

Deterministic prechecks report impossible mandatory interventions. When the solver proves infeasibility, an IIS is requested when supported so the result identifies implicated constraints instead of presenting a false plan.

### 5.4 Independent oracle

Tiny instances are solved twice: once by the MILP and once by exhaustive enumeration of every legal action/timing combination. Tests require the objective values to agree. This protects the optimization implementation from silently changing its economics or constraints.

The runtime uses licensed Gurobi when available and a SciPy/HiGHS MILP fallback for reproducible local/CI validation. Solver status, objective, best bound, MIP gap, solve time, and termination reason remain part of the evidence.

## 6. Monte Carlo stress mathematics

The stress lab samples uncertain RUL, maintenance duration, bay outages, technician absence, part delays, and corrective downtime. Every policy receives the same seeded draws, known as common random numbers, so differences are less contaminated by independent random streams.

For each policy, the report includes expected cost, P95 cost, expected failures, downtime, disrupted interventions, and on-time intervention rate. These are simulated consequences of the declared assumptions.

## 7. Validation and reproducibility commands

The canonical public benchmark evidence can be regenerated locally after acquiring the public C-MAPSS files:

```powershell
\.venv\Scripts\python.exe scripts\benchmark_cmapss_regimes.py `
  --data-dir data\raw\CMAPSSData `
  --out artifacts\reports\cmapss_regime_benchmark.json `
  --subsets FD001 FD002 FD003 FD004
```

The full software checks are:

```powershell
\.venv\Scripts\ruff.exe check src scripts tests
\.venv\Scripts\python.exe -m pytest -q -W error
\.venv\Scripts\python.exe scripts\release_consistency.py
\.venv\Scripts\python.exe scripts\release_audit.py
```

The release audit deliberately excludes raw benchmark and operational files from the distributable package. The report contains metrics and checksums, not private source data.

## 8. Limitations that remain explicit

- Public C-MAPSS results demonstrate benchmark behavior, not transfer to unrelated equipment.
- MetroPT-3 is a separate real-data track and currently lacks enough independent failure events to clear the supervised promotion gate.
- Empirical residual bands and grouped bootstrap intervals do not create formal calibration guarantees.
- Planning probabilities, downtime, cost, capacity, skills, and inventory are assumptions unless supplied by a site.
- A passing local test suite does not prove managed infrastructure, security operations, availability, recovery, operator safety, or prospective field benefit.

The source of truth for implementation is the code under `src/pdm_intelligence`; the corresponding tests and evidence reports are the executable record of the claims made here.
