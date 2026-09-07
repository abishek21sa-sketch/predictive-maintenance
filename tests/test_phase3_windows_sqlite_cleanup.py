from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def _load_diagnostics_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "phase3_diagnostics.py"
    spec = importlib.util.spec_from_file_location("phase3_diagnostics", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_phase3_sqlite_fixture_releases_handle_before_cleanup(tmp_path):
    module = _load_diagnostics_module()
    db = tmp_path / "source.db"
    frame = pd.DataFrame({"asset_id": ["A-1"], "cycle": [1], "sensor_x": [2.5]})
    module._write_sqlite_fixture(db, frame)
    assert db.exists()
    # Phase-3 diagnostics now requires immediate deletion before TemporaryDirectory cleanup.
    # On Windows this is the contract that catches retained SQLite/SQLAlchemy handles.
    db.unlink()
    assert not db.exists()
