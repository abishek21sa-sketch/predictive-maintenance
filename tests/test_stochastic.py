from collections import Counter

from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    optimize_maintenance_stochastic,
)
from pdm_intelligence.simulation.lifecycle import optimize_predictive_threshold


def test_stochastic_schedule_and_simulation_optimization():
    assets=[AssetMaintenanceInput(i,10+i,0.5) for i in range(1,7)]
    scenarios=[{i:8+i for i in range(1,7)},{i:12+i for i in range(1,7)}]
    s=optimize_maintenance_stochastic(assets,scenarios,[0.4,0.6],horizon=10,capacity_per_cycle=2)
    assert len(s)==6 and max(Counter(x.cycle for x in s).values()) <= 2
    opt=optimize_predictive_threshold([12,20,35],candidates=(0.2,0.4),n_simulations=30,seed=1)
    assert opt["best_threshold_fraction"] in (0.2,0.4)
