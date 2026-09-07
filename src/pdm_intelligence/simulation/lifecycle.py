from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class PolicySimulation:
    policy: str
    expected_total_cost: float
    expected_failures: float
    expected_downtime_cycles: float
    preventive_actions: float

    def to_dict(self):
        return asdict(self)


def compare_policies(rul_predictions, n_simulations: int = 1000, seed: int = 42):
    """Monte Carlo comparison of run-to-failure vs predictive maintenance policies."""
    rng = np.random.default_rng(seed)
    rul = np.maximum(np.asarray(rul_predictions, dtype=float), 1.0)
    results = []
    for policy in ("run_to_failure", "predictive"):
        costs, fails, downtime, preventive = [], [], [], []
        for _ in range(n_simulations):
            realized = np.maximum(1.0, rng.normal(rul, np.maximum(2.0, 0.20 * rul)))
            if policy == "run_to_failure":
                fail = np.ones_like(realized)
                prev = np.zeros_like(realized)
                down = rng.uniform(3, 9, size=realized.size)
            else:
                threshold = np.maximum(8.0, 0.35 * rul)
                fail = (realized < threshold).astype(float)
                prev = 1.0 - fail
                down = fail * rng.uniform(3, 9, size=realized.size) + prev * rng.uniform(0.5, 1.5, size=realized.size)
            cost = fail * 30000 + prev * 5000 + down * 1200
            costs.append(cost.sum()); fails.append(fail.sum()); downtime.append(down.sum()); preventive.append(prev.sum())
        results.append(PolicySimulation(policy, float(np.mean(costs)), float(np.mean(fails)), float(np.mean(downtime)), float(np.mean(preventive))))
    return results


def simulate_predictive_policy(rul_predictions, threshold_fraction: float, n_simulations: int = 500, seed: int = 42) -> PolicySimulation:
    rng = np.random.default_rng(seed)
    rul = np.maximum(np.asarray(rul_predictions, dtype=float), 1.0)
    costs=[]; fails=[]; downtime=[]; preventive=[]
    for _ in range(n_simulations):
        realized = np.maximum(1.0, rng.normal(rul, np.maximum(2.0, 0.20*rul)))
        threshold = np.maximum(5.0, threshold_fraction*rul)
        fail=(realized < threshold).astype(float); prev=1.0-fail
        down=fail*rng.uniform(3,9,size=rul.size)+prev*rng.uniform(0.5,1.5,size=rul.size)
        cost=fail*30000+prev*5000+down*1200
        costs.append(cost.sum()); fails.append(fail.sum()); downtime.append(down.sum()); preventive.append(prev.sum())
    return PolicySimulation(
        f"predictive_threshold_{threshold_fraction:.2f}", float(np.mean(costs)), float(np.mean(fails)),
        float(np.mean(downtime)), float(np.mean(preventive))
    )


def optimize_predictive_threshold(rul_predictions, candidates=(0.20,0.30,0.40,0.50,0.60), n_simulations: int = 500, seed: int = 42):
    """Simulation optimization over an interpretable preventive-maintenance threshold."""
    trials=[simulate_predictive_policy(rul_predictions,float(t),n_simulations,seed+i) for i,t in enumerate(candidates)]
    best=min(trials,key=lambda x:x.expected_total_cost)
    return {"best_threshold_fraction": float(best.policy.rsplit('_',1)[1]), "best": best.to_dict(), "trials":[t.to_dict() for t in trials]}
