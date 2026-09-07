# Engineering Risk Register

| Risk | Impact | Current control | Next production control |
|---|---|---|---|
| Sim-to-real gap | High | Explicit dataset/model-card boundary | Field validation and transfer learning |
| RUL uncertainty miscalibration | High | Scenario-based stochastic scheduler | Conformal/predictive intervals |
| Cost-model misspecification | High | Assumption register | CMMS/ERP cost calibration |
| Sensor drift | High | Anomaly detection + FMEA item | Drift monitoring and recalibration |
| Over-maintenance | Medium | Early-life-loss penalty + simulation optimization | Economic threshold calibration |
| Resource infeasibility | High | Hard MILP capacity constraints | Skills, shifts, parts and bay constraints |
| Model/data drift | High | Reproducible benchmark metadata | Automated drift gates and retraining policy |
| Human trust | Medium | Decision rationale + model drivers | Approval workflow and reason codes |
