from pdm_intelligence.decision.engine import decide
from pdm_intelligence.domain import Action


def test_critical_rul_triggers_maintenance():
    d = decide(1, 100, 5.0, 0.9, scheduled_cycle=1)
    assert d.action == Action.MAINTAIN_NOW
    assert d.priority == 1
