# BELIEF-MAINT — Markov Belief-State Maintenance Scheduling

BELIEF-MAINT adds a latent degradation decision layer to Predictive Maintenance Intelligence. It is intentionally distinct from the repository's existing RUL, anomaly, CVaR intervention, simulation, and maintenance-planning capabilities.

## Decision question

When scarce maintenance crew capacity cannot service every asset immediately, **which asset should be maintained in which cycle when health is uncertain and production opportunity cost varies over time?**

## State model

Each asset has a belief vector over four states:

`healthy -> degraded -> critical -> failed`

The failed state is absorbing. The row-stochastic transition matrix propagates the belief with `b[t+1] = b[t] P`. These transitions are engineering assumptions/evidence inputs, not causal proof.

## Optimization

Binary `x[a,t]` equals one when asset `a` is scheduled in cycle `t`.

Each candidate cycle carries:

- preventive maintenance cost,
- expected failure-exposure cost from the propagated failed-state belief,
- production opportunity cost for taking that asset down in that cycle.

The MILP chooses exactly one cycle per asset and enforces cycle-level crew capacity.

## Validation

The deterministic reference benchmark compares BELIEF-MAINT against:

1. fixed-interval maintenance,
2. current-risk ranking,
3. lowest-production-load scheduling.

A transition-stress sensitivity study increases deterioration/failure movement while preserving a valid absorbing Markov chain.

## Evidence boundary

The bundled reference is synthetic formulation validation. BELIEF-MAINT does not assert calibrated real-asset degradation probabilities, causal transition dynamics, or guaranteed failure prevention. `AUTHORIZED` means the modeled decision passed evidence and feasibility checks; work orders still require maintenance-engineering review.
