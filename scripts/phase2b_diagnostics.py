import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app

c=TestClient(app)
casebook=c.get('/api/casebook?limit=8')
assert casebook.status_code==200 and len(casebook.json())==8
aid=casebook.json()[0]['asset_id']
dossier=c.get(f'/api/assets/{aid}/dossier')
options=c.get(f'/api/assets/{aid}/intervention-options')
ledger=c.get('/api/commitment-ledger?bay_capacity=2')
checks={
 'case_selection_api':casebook.status_code==200,
 'asset_dossier_api':dossier.status_code==200,
 'intervention_options_api':options.status_code==200 and len(options.json()['options'])==3,
 'commitment_optimization_feasible':ledger.status_code==200 and ledger.json()['feasibility']['feasible'],
 'honest_trace_label':dossier.json()['diagnostic_trace_class']=='DERIVED_DIAGNOSTIC_VISUALIZATION_NOT_OBSERVED_SENSOR_HISTORY',
}
import json

out={'phase':'2B-regression','overall':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'selected_asset':aid,'solver':ledger.json()['solver_evidence']}
print(json.dumps(out,indent=2))
raise SystemExit(0 if out['overall']=='PASS' else 1)
