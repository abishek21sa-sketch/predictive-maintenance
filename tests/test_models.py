from pdm_intelligence.data.cmapss import add_training_rul
from pdm_intelligence.data.synthetic import generate_cmapss_fixture
from pdm_intelligence.models.rul import grouped_bootstrap_regression_metrics, train_rul_models
from pdm_intelligence.models.runtime import load_release_models, score_trajectory


def test_rul_model_beats_age_baseline():
    df = add_training_rul(generate_cmapss_fixture(n_units=24, min_cycles=55, max_cycles=90, seed=7))
    r = train_rul_models(df, validation_fraction=0.25, seed=7)
    assert r.metrics["rmse"] < r.baseline_metrics["rmse"]
    assert r.metrics["r2"] > 0.5


def test_grouped_bootstrap_reports_asset_level_uncertainty():
    evidence = grouped_bootstrap_regression_metrics(
        [10.0, 12.0, 20.0, 24.0],
        [11.0, 11.0, 18.0, 25.0],
        ["asset-a", "asset-a", "asset-b", "asset-b"],
        seed=7,
        iterations=25,
        residual_half_width=3.0,
    )

    assert evidence["status"] == "PASS"
    assert evidence["bootstrap_unit"] == "asset_trajectory"
    assert evidence["group_count"] == 2
    assert evidence["iterations"] == 25
    assert set(evidence["intervals"]) == {"mae", "rmse", "r2", "nasa_score", "interval_coverage"}


def test_bundled_runtime_matches_current_model_lineage_and_exposes_uncertainty():
    _, _, metadata = load_release_models()
    assert metadata["selected_model"] == "hist_gradient_boosting"
    assert metadata["uncertainty_method"] == "asset_holdout_empirical_residual_band_90"

    fixture = generate_cmapss_fixture(n_units=2, min_cycles=12, max_cycles=14, seed=9)
    predictions = score_trajectory(fixture.to_dict("records"))

    assert len(predictions) == 2
    assert all(row["uncertainty_method"] == metadata["uncertainty_method"] for row in predictions)
    assert all(row["rul_p90"] >= row["rul_p10"] for row in predictions)
