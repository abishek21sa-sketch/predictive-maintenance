# BELIEF-MAINT Research Validation Contract

**Null hypothesis H0.** Given the same degradation-belief model, production opportunity costs, and crew capacity, BELIEF-MAINT does not reduce modeled maintenance/failure exposure cost relative to fixed-interval, current-risk ranking, or lowest-production-load scheduling.

Transition-stress experiments are common-cause degradation stress tests over the declared Markov chain. Belief probabilities are model inputs/outputs, not causal proof or calibrated field failure probabilities unless separately validated on operational reliability data.

## Common-cause Monte Carlo stress

The release evidence includes 200 deterministic-seed (`20260901`) Monte Carlo replications. Each replication draws one shared degradation-transition severity factor and applies it to every asset, representing a common environmental/process stress. The output distribution is written to `artifacts/belief_maint/common_cause_monte_carlo.csv`. This is a falsification/stress mechanism only; the shock distribution is synthetic and is not presented as a calibrated common-cause failure-frequency model.
