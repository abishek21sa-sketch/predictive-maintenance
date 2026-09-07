from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Action(str, Enum):
    MONITOR = "monitor"
    INSPECT = "inspect"
    PLAN_MAINTENANCE = "plan_maintenance"
    MAINTAIN_NOW = "maintain_now"


@dataclass(frozen=True)
class AssetPrediction:
    asset_id: int
    cycle: int
    rul_cycles: float
    anomaly_score: float
    failure_risk: float


@dataclass(frozen=True)
class MaintenanceDecision:
    asset_id: int
    action: Action
    priority: int
    recommended_cycle: int
    expected_cost: float
    rationale: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        return data
