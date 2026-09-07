# Portfolio Positioning

## One-line description
Built an end-to-end predictive maintenance decision intelligence platform that combines sensor-based RUL/anomaly models, Weibull reliability analytics, Monte Carlo lifecycle simulation and capacity-constrained MILP maintenance scheduling behind a FastAPI operations workbench.

## Engineering story
The key design choice is separating prognosis from decision-making. The predictive layer estimates degradation; reliability engineering provides lifecycle context; the MILP converts risk into resource-feasible maintenance timing; simulation quantifies policy trade-offs; the decision layer produces an explainable recommendation. Asset-level holdout validation and reproducible evidence generation are built into the repository rather than added after modeling.

## Real-operations portfolio demonstration

The strongest end-to-end demonstration is the `/operations` MetroPT-3 path:

1. verify the evidence gate reports `REAL DATA READY` after local UCI preparation;
2. choose a published failure event and inspect the historical compressor sensor trajectory before that event;
3. inspect the 24-hour failure-horizon probability and February-reference anomaly evidence;
4. compare Maintain / Inspect / Defer modeled alternatives;
5. show the Gurobi-selected intervention/timing under explicit bays/skills/parts assumptions;
6. switch to a bay outage, technician absence or compound disruption and re-solve the same historical case;
7. commit the decision to a work order and show the decision trace / lifecycle state;
8. explain that observed telemetry, predicted risk, assumed economics/resources, optimized decision and operator-recorded work-order state are deliberately different evidence classes.

This is the signature differentiator from a conventional predictive-maintenance dashboard: the real-data model output materially changes an auditable maintenance decision rather than ending at a health score.
