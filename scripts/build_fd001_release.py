from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import Ridge

from pdm_intelligence.data.cmapss import add_training_rul, load_rul, load_trajectory
from pdm_intelligence.decision.engine import decide, failure_risk_from_rul
from pdm_intelligence.models.anomaly import AnomalyDetector
from pdm_intelligence.models.rul import (
    nasa_asymmetric_score,
    predict_latest,
    regression_metrics,
    train_rul_models,
)
from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    optimize_maintenance_stochastic,
)
from pdm_intelligence.reliability.analysis import fit_reliability
from pdm_intelligence.reliability.fmea import prioritize_failure_modes, turbofan_fd001_fmea
from pdm_intelligence.simulation.lifecycle import compare_policies, optimize_predictive_threshold


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-dir',default='data/raw')
    ap.add_argument('--model-dir',default='artifacts/models')
    ap.add_argument('--out',default='artifacts/reports/fd001_release_evidence.json')
    ap.add_argument('--snapshot-out',default='artifacts/reports/fd001_operational_snapshot.json')
    ap.add_argument('--bundled-model-dir',default='src/pdm_intelligence/release')
    ap.add_argument('--bundled-snapshot-out',default='src/pdm_intelligence/release/fd001_operational_snapshot.json')
    ap.add_argument('--seed',type=int,default=42)
    args=ap.parse_args()
    d=Path(args.data_dir)
    train=add_training_rul(load_trajectory(d/'train_FD001.txt'),cap=125)
    test=load_trajectory(d/'test_FD001.txt')
    true=load_rul(d/'RUL_FD001.txt').to_numpy(float)

    trained=train_rul_models(train,validation_fraction=0.25,seed=args.seed)
    pred=predict_latest(trained.model,trained.feature_columns,test).sort_values('unit_id')
    yhat=pred.predicted_rul.to_numpy(float)
    official=regression_metrics(true,yhat); official['nasa_score']=nasa_asymmetric_score(true,yhat)

    anomaly=AnomalyDetector(seed=args.seed).fit(train)
    latest_test=test.sort_values(['unit_id','cycle']).groupby('unit_id').tail(1).sort_values('unit_id').copy()
    latest_test['anomaly_score']=anomaly.score(latest_test).values
    merged=pred.merge(latest_test[['unit_id','anomaly_score']],on='unit_id')
    merged['failure_risk']=merged.predicted_rul.map(failure_risk_from_rul)

    asset_rows = list(merged.itertuples())
    inputs=[AssetMaintenanceInput(int(r.unit_id),float(r.predicted_rul),float(r.failure_risk)) for r in asset_rows]
    scenarios=[{int(r.unit_id):max(1.0,float(r.predicted_rul)*f) for r in asset_rows} for f in (0.8,1.0,1.2)]
    schedule=optimize_maintenance_stochastic(inputs,scenarios,[0.2,0.6,0.2],horizon=60,capacity_per_cycle=2)
    slots={s.asset_id:s.cycle for s in schedule}
    decisions=[decide(int(r.unit_id),int(r.cycle),float(r.predicted_rul),float(r.anomaly_score),slots[int(r.unit_id)]).to_dict() for r in asset_rows]

    lives=train.groupby('unit_id')['cycle'].max().to_numpy()
    reliability=fit_reliability(lives).to_dict()
    sim=[x.to_dict() for x in compare_policies(yhat,n_simulations=500,seed=args.seed)]
    sim_opt=optimize_predictive_threshold(yhat,n_simulations=300,seed=args.seed)
    baseline_model = Ridge(alpha=1.0).fit(train[['cycle']], train['rul'])
    baseline_pred = np.maximum(0.0, baseline_model.predict(latest_test[['cycle']]))
    baseline_metrics = regression_metrics(true, baseline_pred)
    baseline_metrics['nasa_score'] = nasa_asymmetric_score(true, baseline_pred)

    model_dir=Path(args.model_dir); model_dir.mkdir(parents=True,exist_ok=True)
    joblib.dump(trained.model,model_dir/'fd001_rul_model.joblib',compress=3)
    joblib.dump(anomaly,model_dir/'fd001_anomaly_model.joblib',compress=3)
    (model_dir/'fd001_model_metadata.json').write_text(json.dumps({
        'dataset':'NASA C-MAPSS FD001','selected_model':trained.selected_model,
        'features':trained.feature_columns,'top_features':trained.top_features,
        'asset_holdout_metrics':trained.metrics,'official_test_metrics':official,'seed':args.seed,
        'residual_interval_90': trained.residual_interval_90,
        'interval_coverage_90': trained.interval_coverage_90,
        'uncertainty_method': 'asset_holdout_empirical_residual_band_90',
    },indent=2),encoding='utf-8')

    bundled_model_dir = Path(args.bundled_model_dir) if args.bundled_model_dir else None
    if bundled_model_dir is not None and bundled_model_dir.resolve() != model_dir.resolve():
        bundled_model_dir.mkdir(parents=True, exist_ok=True)
        for name in ('fd001_rul_model.joblib', 'fd001_anomaly_model.joblib', 'fd001_model_metadata.json'):
            shutil.copy2(model_dir / name, bundled_model_dir / name)

    action_counts={}
    for x in decisions: action_counts[x['action']]=action_counts.get(x['action'],0)+1
    payload={
        'dataset':'NASA C-MAPSS FD001',
        'official_test_metrics':official,
        'asset_holdout_metrics':trained.metrics,
        'official_test_baseline_metrics': baseline_metrics,
        'selected_model':trained.selected_model,
        'top_features':trained.top_features,
        'reliability':reliability,
        'fmea':prioritize_failure_modes(turbofan_fd001_fmea()),
        'maintenance_schedule':[s.__dict__ for s in schedule],
        'decision_action_counts':action_counts,
        'decisions':decisions,
        'policy_simulation':sim,
        'simulation_optimization':sim_opt,
        'uncertainty': {
            'method': 'asset_holdout_empirical_residual_band_90',
            'half_width_cycles': trained.residual_interval_90,
            'validation_coverage_90': trained.interval_coverage_90,
            'claim_boundary': 'Empirical residual evidence; not a formal conformal coverage guarantee.',
        },
        'model_artifacts':['artifacts/models/fd001_rul_model.joblib','artifacts/models/fd001_anomaly_model.joblib','artifacts/models/fd001_model_metadata.json'],
        'seed':args.seed,
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,indent=2),encoding='utf-8')

    assets = [
        {
            'unit_id': int(r.unit_id),
            'cycle': int(r.cycle),
            'predicted_rul': float(r.predicted_rul),
            'anomaly_score': float(r.anomaly_score),
            'failure_risk': float(r.failure_risk),
            'decision': decision,
        }
        for r, decision in zip(asset_rows, decisions)
    ]

    snapshot = {
        'data_mode': 'validated_fd001_release_snapshot',
        'dataset': payload['dataset'],
        'selected_model': payload['selected_model'],
        'asset_count': len(assets),
        'model_metrics': payload['official_test_metrics'],
        'reliability': payload['reliability'],
        'top_features': payload['top_features'],
        'simulation': payload['policy_simulation'],
        'simulation_optimization': payload['simulation_optimization'],
        'uncertainty': payload['uncertainty'],
        'assets': assets,
        'decisions': decisions,
    }
    snapshot_out = Path(args.snapshot_out)
    snapshot_out.parent.mkdir(parents=True, exist_ok=True)
    snapshot_out.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
    bundled_snapshot_out = Path(args.bundled_snapshot_out) if args.bundled_snapshot_out else None
    if bundled_snapshot_out is not None and bundled_snapshot_out.resolve() != snapshot_out.resolve():
        bundled_snapshot_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_out, bundled_snapshot_out)
    print(json.dumps({
        'official_test_metrics':official,
        'selected_model':trained.selected_model,
        'decision_action_counts':action_counts,
        'schedule_size':len(schedule),
        'artifacts':payload['model_artifacts'],
    },indent=2))

if __name__=='__main__': main()
