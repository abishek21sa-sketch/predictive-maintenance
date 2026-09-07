import pytest

from pdm_intelligence.data.cmapss import CMAPSS_COLUMNS, add_training_rul, validate
from pdm_intelligence.data.synthetic import generate_cmapss_fixture


def test_fixture_schema_and_rul():
    df = generate_cmapss_fixture(n_units=5, min_cycles=20, max_cycles=30)
    assert list(df.columns) == CMAPSS_COLUMNS
    labeled = add_training_rul(df, cap=None)
    assert labeled.groupby("unit_id").tail(1)["rul"].eq(0).all()


def test_validation_rejects_null():
    df = generate_cmapss_fixture(n_units=3, min_cycles=10, max_cycles=12)
    df.loc[0, "sensor_01"] = None
    with pytest.raises(ValueError): validate(df)
