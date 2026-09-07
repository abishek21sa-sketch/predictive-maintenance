import json
from pathlib import Path

from pdm_intelligence.data.synthetic import (
    SYNTHETIC_EVIDENCE_CLASS,
    generate_external_fixture,
    write_external_fixture,
)
from pdm_intelligence.external.gateway import DataGateway


def test_external_fixture_is_deterministic_and_has_canonical_shape(tmp_path: Path):
    first = generate_external_fixture(n_rows=240, n_assets=12, seed=7)
    second = generate_external_fixture(n_rows=240, n_assets=12, seed=7)

    assert all(first[name].equals(second[name]) for name in first)
    assert len(first["telemetry"]) == 240
    assert first["telemetry"]["asset_id"].nunique() == 12
    assert {"asset_id", "cycle", "timestamp", "rul", "vibration_rms"}.issubset(
        first["telemetry"].columns
    )
    assert len(first["maintenance"]) == 12

    manifest = write_external_fixture(first, tmp_path / "fixture", seed=7)
    assert manifest["evidence_class"] == SYNTHETIC_EVIDENCE_CLASS
    assert manifest["promotion_allowed"] is False
    assert Path(tmp_path / "fixture" / "manifest.json").exists()


def test_gateway_preserves_synthetic_evidence_boundary(tmp_path: Path):
    frames = generate_external_fixture(n_rows=240, n_assets=12, seed=11)
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    result = gateway.ingest_frames(
        "synthetic",
        frames,
        source={"mode": "synthetic_fixture", "seed": 11},
        evidence_class=SYNTHETIC_EVIDENCE_CLASS,
    )

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["evidence_class"] == SYNTHETIC_EVIDENCE_CLASS
    assert manifest["promotion_allowed"] is False
    assert "cannot satisfy real-data model promotion" in manifest["evidence_boundary"]
