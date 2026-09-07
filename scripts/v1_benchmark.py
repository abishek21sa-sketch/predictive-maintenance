from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from pdm_intelligence.api.main import release_snapshot
from pdm_intelligence.decision.intelligence import (
    build_decision_intelligence_plan,
    stress_test_decision_intelligence,
)

ROOT = Path(__file__).resolve().parents[1]


def timed(fn):
    start = perf_counter()
    value = fn()
    return value, perf_counter() - start


def main() -> None:
    assets = release_snapshot()['assets']
    plan, plan_s = timed(lambda: build_decision_intelligence_plan(
        assets, solver='highs', n_rul_scenarios=7, seed=4401
    ))
    stress, stress_s = timed(lambda: stress_test_decision_intelligence(
        assets, solver='highs', n_rul_scenarios=5, plan_seed=4401,
        n_simulations=80, simulation_seed=9917,
    ))
    checks = {
        'plan_under_15_seconds': plan_s < 15.0,
        'stress_80_under_30_seconds': stress_s < 30.0,
        'plan_optimal': plan['solver_evidence']['status'] == 'OPTIMAL',
        'stress_three_policies': len(stress['stress_test']['results']) == 3,
    }
    result = {
        'release': '1.0.0',
        'overall': 'PASS' if all(checks.values()) else 'FAIL',
        'evidence_class': 'PERFORMANCE_SANITY_NOT_CAPACITY_CERTIFICATION',
        'checks': checks,
        'timings_seconds': {'decision_plan': plan_s, 'stress_test_80': stress_s},
        'asset_count': len(assets),
        'solver': plan['solver_evidence']['solver'],
        'claim_boundary': 'Timings are local release sanity measurements, not production SLA guarantees.',
    }
    out = ROOT / 'artifacts' / 'v1_performance.json'
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    if result['overall'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
