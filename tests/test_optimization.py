from collections import Counter

from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput, optimize_maintenance


def test_milp_respects_capacity_and_assigns_all_assets():
    assets = [AssetMaintenanceInput(i, 5 + i, 0.8 - i*0.03) for i in range(1, 8)]
    result = optimize_maintenance(assets, horizon=10, capacity_per_cycle=2)
    assert len(result) == len(assets)
    counts = Counter(x.cycle for x in result)
    assert max(counts.values()) <= 2
    assert all(1 <= x.cycle <= 10 for x in result)
