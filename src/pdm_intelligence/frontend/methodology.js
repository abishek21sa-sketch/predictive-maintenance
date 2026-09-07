const money=x=>'$'+Math.round(Number(x)).toLocaleString();
Promise.all([fetch('/api/portfolio').then(r=>r.json()),fetch('/api/commitment-ledger?bay_capacity=2').then(r=>r.json())]).then(([p,l])=>{
  const m=p.model_metrics||{};
  document.getElementById('method-evidence').textContent=`DATA ${String(p.dataset||p.data_mode).replaceAll('_',' ')} · MODEL ${p.selected_model||'validated release model'} · ${p.asset_count} assets`;
  document.getElementById('ai-metrics').textContent=`VALIDATED BENCHMARK · RMSE ${Number(m.rmse).toFixed(2)} · MAE ${Number(m.mae).toFixed(2)} · R² ${Number(m.r2).toFixed(3)} · age-only baseline documented separately`;
  const e=l.solver_evidence;document.getElementById('or-evidence').textContent=`OPTIMIZED · ${e.solver.replace('scipy_highs_milp','HiGHS MILP')} · ${e.status} · MIP gap ${e.mip_gap??'n/a'} · objective ${money(e.objective_value)}`;
  document.getElementById('sim-evidence').textContent='SEEDED MONTE CARLO · 3 policy comparison · common random numbers · modeled consequences only';
}).catch(()=>{document.getElementById('method-evidence').textContent='Method evidence unavailable; deterministic documentation remains valid.'});
