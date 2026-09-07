# Phase 4 Acceptance — Uncertainty-Aware Decision Intelligence

**Release:** `0.6.0`

Phase 4 deepens the predictive-maintenance decision chain without replacing the accepted Phase 1–3 foundations.

## Implemented

- intervention type + timing MILP (`maintain`, `inspect`, `defer` where allowed);
- protected critical-RUL assets that cannot be silently deferred;
- RUL scenario generation with deterministic seeds;
- expected modeled cost + CVaR tail-risk objective;
- baseline + bay/crew stress capacity profiles enforced as a robust uncertainty set;
- labor, maintenance-bay, technician-skill and part-inventory constraints;
- deterministic infeasibility precheck and Gurobi IIS reporting for proven infeasibility;
- exact enumeration oracle for tiny intervention portfolios;
- seeded stress simulation using common random numbers;
- optimized vs earliest-RUL heuristic vs run-to-failure comparison;
- Reliability Shock Lab UI and Phase 4 methodology updates;
- Phase 4 API routes and diagnostics.

## Evidence boundary

The Phase 4 objective, CVaR and stress-test results are **modeled / optimized / simulated** quantities. They are not realized maintenance savings, causal production improvement, field reliability certification or aircraft-safety claims.

## Laptop-only acceptance

The distributed Phase 4 acceptance script validates the new uncertainty-aware portfolio through the user's licensed Gurobi runtime. This is intentionally retained as an external environment acceptance check.
