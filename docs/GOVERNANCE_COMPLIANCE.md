# Governance Compliance Traceability — Predictive Maintenance

This file records the current RC2 engineering-release evidence against the supplied Portfolio Engineering Governance Pack and Signature Algorithms & Mathematical Foundations standard.

## Mandatory engineering-release controls

- **Project-specific signature algorithm:** `BELIEF-MAINT` in the documented project-native decision/math package.
- **Fresh signature-module line coverage:** **98%** (required threshold: >=90%).
- **Regression evidence:** 97/97 tests.
- **Research/evidence gate:** 10/10 formulation; 7/7 product integration; 10 sensitivity + 200 common-cause Monte Carlo replications.
- **Explicit objective, variables, constraints, operational decision and claim boundary:** documented in [`docs/BELIEF_MAINT.md`](BELIEF_MAINT.md).
- **Baseline/counterfactual, ablation and sensitivity evidence:** present in machine-readable artifacts under `artifacts/`.
- **Evidence classes:** observational/ingested, predicted, simulated, optimized and realized evidence are not conflated.
- **AI/agent role:** explanatory, predictive or analytical support only; the mathematical optimizer/control model remains the decision engine.
- **Human/evidence gate:** optimization recommendations are governed and do not autonomously execute external operational actions.
- **API/service exposure:** signature decision is exposed through the project service boundary.
- **Dedicated operator UI:** signature inputs, constraints/trade-offs, evidence status and baseline/counterfactual context are visible in the project UI.
- **Windows acceptance command:** `.\scripts\windows_belief_maint_acceptance.ps1`. Execution on the user's Windows machine is still required before final promotion.
- **README traceability:** README links the canonical `docs/SIGNATURE_ALGORITHM.md`.
- **Clean packaging:** RC2 packaging excludes `.env`, `.git`, virtual environments, caches/bytecode and stale nested archives.

## Explicit null hypotheses

- H0-P1: BELIEF-MAINT does not reduce modeled maintenance/failure/opportunity cost versus fixed-interval, current-risk-rank, or lowest-load scheduling.
- H0-P2: Probabilistic degradation belief does not materially change maintenance timing relative to deterministic current-risk ordering.

These are falsification targets, not claims that current evidence establishes causal or field superiority.

## Research-upgrade path

The engineering-release gate is intentionally separate from publication-grade research. Literature-positioning, stronger theoretical guarantees, broad public-benchmark studies, solver-scaling/warm-start studies, and field/causal validation remain **research-upgrade work unless explicitly evidenced in this repository**. The release does not backfill or imply those claims.
