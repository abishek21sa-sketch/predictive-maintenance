from pdm_intelligence.reliability.analysis import bathtub_hazard
from pdm_intelligence.reliability.fmea import prioritize_failure_modes, turbofan_fd001_fmea
from pdm_intelligence.reliability.kpis import compute_maintenance_kpis


def test_fmea_rcm_and_kpis():
    modes = prioritize_failure_modes(turbofan_fd001_fmea())
    assert modes[0]["rpn"] >= modes[-1]["rpn"]
    assert modes[0]["rcm_strategy"] == "condition_based_maintenance"
    k = compute_maintenance_kpis([
        {"planned":True,"emergency":False,"on_schedule":True,"duration":2,"cost":100},
        {"planned":False,"emergency":True,"duration":5,"cost":500},
    ], operating_cycles=100)
    assert k.planned_maintenance_percentage == 50
    assert k.schedule_compliance == 100
    curve = bathtub_hazard([0,50,150])
    assert len(curve["total"]) == 3
