# Phase 2 Acceptance — Maintenance Intervention Planner

## Scope
Phase 2 converts the locked prognostics/reliability/OR core into a project-specific maintenance planning product. The signature capability is the asset-by-time Maintenance Intervention Planner.

## Decision workflow
`Detect -> Prognose -> Plan -> Stress-test -> Approve`

The UI does not represent modeled benefits as realized outcomes. The planning scenario objectives are explicitly modeled expected costs.

## Implemented in Phase 2
- asset x time intervention horizon;
- risk/RUL-prioritized maintenance queue;
- resource-constrained schedule generation;
- bay, labor, skill and part-capacity exposure;
- maintenance-duration occupancy;
- baseline, conservative and accelerated-degradation scenario comparison;
- solver status/objective/gap evidence surfaced through the planner API;
- intervention evidence panel for RUL, anomaly, planner interval, resource needs and rationale;
- dedicated planner/scenario APIs;
- Windows Phase-2 acceptance and startup scripts.

## Automated evidence
- 25/25 tests pass in the source release candidate.
- `scripts/phase2_diagnostics.py` returns overall PASS.
- Baseline planner is mathematically feasible and capacity-bounded.
- HiGHS status is OPTIMAL with zero reported MIP gap in the Phase-2 diagnostic instance.
- Frontend JavaScript syntax passes `node --check` in the build environment.
- API endpoints `/api/health`, `/api/planner/horizon`, `/api/planner/scenarios`, and `/` return HTTP 200 in smoke validation.

## External acceptance boundary
The build environment does not have a licensed Gurobi runtime. The user already validated Gurobi 13.0.2 and their academic license in Phase 1. Phase 2 acceptance rechecks the actual planner model with `solver='gurobi'` on the user's Windows laptop.
