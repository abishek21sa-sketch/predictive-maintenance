import pandas as pd

from scripts.metropt_candidate_benchmark import _add_past_only_regime_features, _feature_columns


def test_candidate_benchmark_excludes_targets_and_future_derived_labels():
    frame = pd.DataFrame(
        {
            "timestamp": ["2020-01-01"],
            "row_count": [1],
            "source_index_min": [1],
            "source_index_max": [1],
            "failure_active": [0],
            "failure_within_24h": [1],
            "failure_within_6h": [1],
            "failure_event_id": ["F01"],
            "hours_to_next_failure": [2.0],
            "safe_sensor_feature": [3.0],
        }
    )

    assert _feature_columns(frame) == ["safe_sensor_feature"]


def test_regime_feature_extension_keeps_unique_columns_and_fills_edge_values():
    frame = pd.DataFrame(
        {
            "TP2_mean": [1.0, 2.0, 3.0],
            "TP2_mean__roll6_mean": [1.0, 1.5, 2.5],
            "TP2_mean__roll36_mean": [1.0, 1.25, 2.0],
            "TP2_mean__roll6_std": [0.0, 0.1, 0.2],
            "TP2_mean__roll36_std": [0.0, 0.2, 0.4],
        }
    )

    augmented, added = _add_past_only_regime_features(frame)

    assert len(augmented.columns) == len(set(augmented.columns))
    assert len(added) == 3
    assert augmented[added].isna().sum().sum() == 0
