# Phase 2B — Product Identity Rebuild Acceptance

Phase 2B preserves the validated prognostics/reliability/optimization core and replaces the rejected dashboard/timeline product layer.

## Product identity

The primary interaction is now **Fleet Casebook → Asset Dossier → Intervention Lab → Commitment Ledger**.

- Fleet Casebook ranks open maintenance cases.
- Asset Dossier focuses on one asset's prognostic evidence.
- Intervention Lab compares Maintain / Inspect / Defer modeled alternatives.
- Commitment Ledger reconciles candidate work against resource constraints through the maintenance MILP.

The former numbered workflow strip and asset×time Gantt centerpiece are intentionally removed.

## Evidence semantics

The degradation fingerprint is explicitly labeled `DERIVED_DIAGNOSTIC_VISUALIZATION_NOT_OBSERVED_SENSOR_HISTORY`. It must never be described as reconstructed sensor history. The survival trace is a modeled prognostic visualization. Intervention alternative costs are modeled expected costs, not realized savings.

## Laptop acceptance

Run `scripts\phase2_acceptance.ps1`, start the app with `scripts\start_windows.ps1`, and visually verify the Casebook/Dossier/Lab/Ledger workflow. Licensed Gurobi remains the primary laptop solver acceptance path.
