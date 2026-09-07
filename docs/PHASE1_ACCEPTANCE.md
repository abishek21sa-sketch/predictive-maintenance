# Phase 1 Laptop Acceptance

Phase 1 is the computational engineering core. No frontend acceptance is required yet.

On Windows PowerShell, from the clean-extracted repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\phase1_acceptance.ps1
```

Acceptance is successful when:

1. Pytest reports all tests passing.
2. `phase1_diagnostics.py` reports `"overall": "PASS"`.
3. The final line includes `GUROBI_ACCEPTANCE_PASS`.

If Gurobi package installation or licensing is the only failure, send the exact terminal output. Do not change source code or solver configuration manually.
