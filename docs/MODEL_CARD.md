# Model Card — FD001 RUL Release

## Purpose
Estimate remaining useful life for the NASA C-MAPSS FD001 turbofan benchmark and feed a separate maintenance decision layer.

## Training and evaluation
Training RUL is capped at 125 cycles. Model selection uses asset-ID holdout rather than random row splitting. The selected algorithm is refit on all training assets after holdout selection and then evaluated on the official FD001 test endpoints. Metrics and hashes are stored under `artifacts/reports` and `data/SOURCE_MANIFEST.json`.

## Explainability
Permutation importance is computed on a bounded held-out asset sample. The release metadata stores the top model drivers. Explainability describes model sensitivity and must not be interpreted as causal failure physics.

## Limitations
- FD001 contains one operating condition and one fault mode and therefore does not represent the full variability of industrial fleets.
- C-MAPSS is simulated data, not field telemetry.
- The failure-risk mapping used by the decision engine is an interpretable monotonic policy function, not a field-calibrated probability model.
- Maintenance cost coefficients are engineering assumptions for optimization demonstrations.
- FMEA severity/occurrence/detection ratings are illustrative repository assumptions, not NASA annotations.
- A production deployment would require site-specific calibration, sensor governance, cybersecurity, human approval rules and prospective validation.

## MetroPT-3 real-data models

MetroPT-3 is a separate model card scope from FD001. The row-level UCI telemetry is not treated as certified RUL ground truth. The real-data workflow trains:

- a February-reference Isolation Forest for anomaly evidence;
- a 24-hour failure-horizon classifier derived from the failure windows published with MetroPT-3.

The supervised split is chronological: pre-June development, June validation, July onward holdout. Logistic Regression is the baseline and Random Forest plus histogram gradient boosting are complex candidates. Promotion requires the selected model to beat the prevalence baseline in both validation and the untouched future holdout, with event/block-clustered 95% bootstrap bounds supporting the metric comparisons and at least two independent published failure windows represented in each evaluation period. Positive rows are resampled by failure event and negative rows by contiguous 24-hour background block; row-level resampling is not treated as independent evidence. Candidate selection and threshold selection occur on validation data only. Anomaly-score ranking metrics are reported separately and are not calibrated failure probabilities.

Full model metrics are intentionally **not hard-coded into this repository before full-source acceptance**. They are generated locally into the MetroPT runtime manifest by `scripts/prepare_metropt3.py` and accepted by `scripts/phase5_acceptance.ps1`.

Evidence class after that gate: `VALIDATED_ON_REAL_OPERATIONAL_METROPT3_HOLDOUT`.

This is historical real-data validation, not prospective field deployment or certified RUL.
