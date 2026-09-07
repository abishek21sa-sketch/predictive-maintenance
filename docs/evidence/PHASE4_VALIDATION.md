# Phase 4 Validation Evidence

Generated/revalidated during the `0.6.0` release process.

Required gates:

- full automated regression suite;
- Phase 1 mathematical/AI/OR diagnostics;
- Phase 2 maintenance-planning regression;
- Observatory regression;
- Phase 3 external-data lifecycle regression;
- Phase 4 CVaR MILP vs exact enumeration oracle;
- reproducible common-random-number stress simulation;
- API and Stress Lab smoke;
- exact release ZIP clean-extraction validation;
- licensed Gurobi Phase 4 acceptance on the user's Windows environment.

The exact numerical results are emitted by `scripts/phase4_diagnostics.py`; they are intentionally reproducible from the packaged code rather than copied as unverifiable prose.
