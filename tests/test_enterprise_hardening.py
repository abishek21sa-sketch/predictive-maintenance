from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app
from pdm_intelligence.governance.audit import AuditStore
from pdm_intelligence.integrations.batch import import_inventory_csv, import_work_orders_csv
from pdm_intelligence.monitoring.drift import feature_drift_report
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput, optimize_maintenance


def test_resource_constrained_optimizer_respects_labor_and_spares():
    items=[AssetMaintenanceInput(i,10+i,0.4,labor_hours=8,spare_units=1,required_skill="mechanic",part_id="P1") for i in range(1,5)]
    schedule=optimize_maintenance(items,horizon=4,capacity_per_cycle=4,labor_hours_per_cycle=8,spares_per_cycle=1,skill_capacity_per_cycle={"mechanic":1},part_inventory={"P1":4})
    counts={}
    for x in schedule: counts[x.cycle]=counts.get(x.cycle,0)+1
    assert max(counts.values()) == 1


def test_drift_monitor_detects_shift():
    ref=pd.DataFrame({"sensor":[0,0,0,0,1,1,1,1,2,2]*10})
    cur=pd.DataFrame({"sensor":[10,11,12,13,14,15,16,17,18,19]*10})
    report=feature_drift_report(ref,cur,["sensor"])
    assert report["status"] == "drift_detected"


def test_audit_store_round_trip(tmp_path: Path):
    store=AuditStore(tmp_path/"audit.db")
    event_id=store.append("decision","planner",{"action":"inspect"},"ENG-001")
    rows=store.list()
    assert rows[0]["id"] == event_id and rows[0]["payload"]["action"] == "inspect"


def test_batch_integration_contracts(tmp_path: Path):
    wo=tmp_path/"wo.csv"; inv=tmp_path/"inv.csv"
    pd.DataFrame([{"work_order_id":"W1","asset_id":1,"status":"open","planned_start":"2026-01-01","duration_hours":4,"required_skill":"mechanic","part_id":"P1","part_qty":1}]).to_csv(wo,index=False)
    pd.DataFrame([{"part_id":"P1","description":"bearing","on_hand":5,"reserved":2,"unit_cost":50}]).to_csv(inv,index=False)
    assert int(import_work_orders_csv(wo).iloc[0].asset_id) == 1
    assert int(import_inventory_csv(inv).iloc[0].available) == 3


def test_api_hardening_endpoints():
    client=TestClient(app)
    assert client.get('/api/health').json()['version'] == '1.0.0'
    payload={"reference":[{"x":0},{"x":0},{"x":1},{"x":1},{"x":2}],"current":[{"x":10},{"x":11},{"x":12},{"x":13},{"x":14}],"columns":["x"]}
    assert client.post('/api/monitoring/drift',json=payload).status_code == 200
