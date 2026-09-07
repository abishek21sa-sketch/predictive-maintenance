import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app

c = TestClient(app)
home = c.get('/')
methods = c.get('/methodology')
cases = c.get('/api/casebook?limit=6')
aid = cases.json()[0]['asset_id']
dossier = c.get(f'/api/assets/{aid}/dossier')
options = c.get(f'/api/assets/{aid}/intervention-options')
infeasible = c.get('/api/commitment-ledger?scenario=accelerated_degradation&bay_capacity=1')
clock = c.get('/api/commitment-ledger?scenario=accelerated_degradation&bay_capacity=2')
checks = {
    'observatory_home': home.status_code == 200 and 'FLEET ORBIT' in home.text,
    'frontend_assets_bundled': 'data-bundled="observatory-css"' in home.text and 'data-bundled="observatory-runtime"' in home.text and '/static/styles.css' not in home.text,
    'html_cache_disabled': home.headers.get('cache-control') == 'no-store, max-age=0' and methods.headers.get('cache-control') == 'no-store, max-age=0',
    'old_dashboard_composition_removed': 'ASSET × TIME' not in home.text and 'Asset Intervention Desk' not in home.text,
    'case_lens_api': dossier.status_code == 200,
    'three_intervention_paths': options.status_code == 200 and len(options.json()['options']) == 3,
    'schedule_clock_infeasibility_detected': infeasible.status_code == 422,
    'schedule_clock_plan_feasible': clock.status_code == 200 and clock.json()['feasibility']['feasible'],
    'methodology_page': methods.status_code == 200 and 'OPERATIONS RESEARCH' in methods.text and 'RELIABILITY ENGINEERING' in methods.text,
    'honest_evidence_language': 'NOT CLAIMED' in methods.text and 'modeled alternatives, not realized outcomes' in home.text,
}
out = {'phase': '2C', 'overall': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks, 'selected_asset': aid, 'solver': clock.json().get('solver_evidence') if clock.status_code == 200 else None}
print(json.dumps(out, indent=2))
raise SystemExit(0 if out['overall'] == 'PASS' else 1)
