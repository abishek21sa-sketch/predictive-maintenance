from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pandas as pd

from .adapters import load_path, load_sql, load_text
from .catalog import ExternalCatalog
from .readiness import assess_readiness
from .schema import canonicalize, infer_mapping, summarize


@dataclass(frozen=True)
class IngestionResult:
    dataset_id: str
    name: str
    readiness: dict
    entities: dict
    manifest_path: str

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "readiness": self.readiness,
            "entities": self.entities,
            "manifest_path": self.manifest_path,
        }


def _slug(value: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return out[:36] or "dataset"


def _frame_hash(df: pd.DataFrame) -> str:
    stable = df.copy()
    stable = stable.reindex(sorted(stable.columns), axis=1)
    for c in stable.columns:
        if pd.api.types.is_datetime64_any_dtype(stable[c]):
            stable[c] = stable[c].astype(str)
    payload = stable.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return sha256(payload).hexdigest()


class DataGateway:
    def __init__(self, root: str | Path = "data/external", catalog_path: str | Path = "data/pdm.db"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = ExternalCatalog(catalog_path)

    def ingest_frames(
        self,
        name: str,
        frames: dict[str, pd.DataFrame],
        mappings: dict[str, dict[str, str]] | None = None,
        source: dict | None = None,
        units: dict[str, dict[str, str]] | None = None,
        evidence_class: str = "USER_SUPPLIED_DATA",
    ) -> IngestionResult:
        evidence_class = str(evidence_class).strip().upper()
        if not evidence_class:
            raise ValueError("evidence_class must not be blank")
        mappings = mappings or {}
        canonical: dict[str, pd.DataFrame] = {}
        for entity_type, frame in frames.items():
            if frame is None or frame.empty:
                continue
            canonical[entity_type] = canonicalize(frame, entity_type, mappings.get(entity_type))
        if "telemetry" not in canonical:
            raise ValueError("At least telemetry data is required for an external predictive-maintenance dataset")

        assets = canonical.get("assets")
        telemetry = canonical["telemetry"]
        maintenance = canonical.get("maintenance")
        telemetry_assets = set(telemetry["asset_id"].astype(str))
        if assets is not None:
            master_assets = set(assets["asset_id"].astype(str))
            unknown = sorted(telemetry_assets - master_assets)
            if unknown:
                raise ValueError(f"Telemetry references assets absent from the asset master: {unknown[:10]}")
        if maintenance is not None and assets is not None:
            unknown = sorted(set(maintenance["asset_id"].astype(str)) - set(assets["asset_id"].astype(str)))
            if unknown:
                raise ValueError(f"Maintenance history references assets absent from the asset master: {unknown[:10]}")

        fingerprints = {k: _frame_hash(v) for k, v in canonical.items()}
        fingerprint = sha256("|".join(f"{k}:{fingerprints[k]}" for k in sorted(fingerprints)).encode()).hexdigest()
        dataset_id = f"{_slug(name)}-{fingerprint[:10]}"
        dataset_dir = self.root / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        summaries = {}
        for entity_type, frame in canonical.items():
            # CSV is the canonical persisted interchange because it has no optional binary engine dependency.
            path = dataset_dir / f"{entity_type}.csv"
            save = frame.copy()
            for c in save.columns:
                if pd.api.types.is_datetime64_any_dtype(save[c]):
                    save[c] = save[c].astype(str)
            save.to_csv(path, index=False)
            summaries[entity_type] = summarize(frame, entity_type).__dict__ | {"sha256": fingerprints[entity_type]}

        readiness = assess_readiness(assets, telemetry, maintenance)
        manifest = {
            "dataset_id": dataset_id,
            "name": name,
            "created_at": datetime.now(UTC).isoformat(),
            "evidence_class": evidence_class,
            "promotion_allowed": False,
            "source": source or {"mode": "in_memory"},
            "declared_units": units or {},
            "fingerprint_sha256": fingerprint,
            "entities": summaries,
            "readiness": readiness,
            "evidence_boundary": (
                "Ingestion readiness does not transfer NASA C-MAPSS validation results to this dataset. "
                "Model compatibility and external validation are assessed separately."
                + (
                    " Synthetic data is test-only and cannot satisfy real-data model promotion or field qualification."
                    if evidence_class == "SYNTHETIC_TEST_ONLY"
                    else ""
                )
            ),
        }
        manifest_path = dataset_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        self.catalog.put_dataset(dataset_id, name, manifest, evidence_class=evidence_class)
        return IngestionResult(dataset_id, name, readiness, summaries, str(manifest_path))

    def preview_text_bundle(
        self,
        files: list[dict],
        mappings: dict[str, dict[str, str]] | None = None,
    ) -> dict:
        mappings = mappings or {}
        canonical: dict[str, pd.DataFrame] = {}
        inferred: dict[str, dict[str, str]] = {}
        source_summaries: dict[str, dict] = {}
        for item in files:
            entity = item["entity_type"]
            frame = load_text(item["content"], item["filename"])
            inferred[entity] = infer_mapping(frame.columns, entity)
            canonical[entity] = canonicalize(frame, entity, mappings.get(entity))
            source_summaries[entity] = {
                "filename": item["filename"],
                "rows": len(frame),
                "source_columns": list(frame.columns),
                "canonical_columns": list(canonical[entity].columns),
            }
        if "telemetry" not in canonical:
            raise ValueError("At least telemetry data is required for an external predictive-maintenance dataset")
        assets = canonical.get("assets")
        telemetry = canonical["telemetry"]
        maintenance = canonical.get("maintenance")
        if assets is not None:
            unknown = sorted(set(telemetry["asset_id"].astype(str)) - set(assets["asset_id"].astype(str)))
            if unknown:
                raise ValueError(f"Telemetry references assets absent from the asset master: {unknown[:10]}")
        if maintenance is not None and assets is not None:
            unknown = sorted(set(maintenance["asset_id"].astype(str)) - set(assets["asset_id"].astype(str)))
            if unknown:
                raise ValueError(f"Maintenance history references assets absent from the asset master: {unknown[:10]}")
        return {
            "status": "PREVIEW_VALIDATED",
            "inferred_mapping": inferred,
            "sources": source_summaries,
            "readiness": assess_readiness(assets, telemetry, maintenance),
            "evidence_boundary": "Preview performs validation only; no dataset is persisted until acceptance.",
        }

    def ingest_text_bundle(
        self,
        name: str,
        files: list[dict],
        mappings: dict[str, dict[str, str]] | None = None,
        units: dict[str, dict[str, str]] | None = None,
    ) -> IngestionResult:
        frames: dict[str, pd.DataFrame] = {}
        source_files = []
        for item in files:
            entity = item["entity_type"]
            if entity in frames:
                raise ValueError(f"Duplicate entity_type supplied: {entity}")
            frames[entity] = load_text(item["content"], item["filename"])
            source_files.append({"entity_type": entity, "filename": item["filename"]})
        return self.ingest_frames(name, frames, mappings, {"mode": "browser_text", "files": source_files}, units)

    def ingest_paths(
        self,
        name: str,
        files: list[dict],
        mappings: dict[str, dict[str, str]] | None = None,
        units: dict[str, dict[str, str]] | None = None,
    ) -> IngestionResult:
        frames: dict[str, pd.DataFrame] = {}
        source_files = []
        for item in files:
            entity = item["entity_type"]
            frames[entity] = load_path(item["path"])
            source_files.append({"entity_type": entity, "filename": Path(item["path"]).name, "local_path_redacted": True})
        return self.ingest_frames(name, frames, mappings, {"mode": "file_path", "files": source_files}, units)

    def ingest_sql(
        self,
        name: str,
        sources: list[dict],
        mappings: dict[str, dict[str, str]] | None = None,
        units: dict[str, dict[str, str]] | None = None,
    ) -> IngestionResult:
        frames: dict[str, pd.DataFrame] = {}
        safe_sources = []
        for src in sources:
            entity = src["entity_type"]
            frames[entity] = load_sql(src["connection_url"], src.get("table"), src.get("query"))
            # Never persist credentials/connection URLs in the manifest.
            safe_sources.append({"entity_type": entity, "table": src.get("table"), "query_supplied": bool(src.get("query"))})
        return self.ingest_frames(name, frames, mappings, {"mode": "sql", "sources": safe_sources}, units)

    def load_dataset(self, dataset_id: str) -> dict[str, pd.DataFrame]:
        dataset_dir = self.root / dataset_id
        manifest = dataset_dir / "manifest.json"
        if not manifest.exists():
            raise ValueError(f"Dataset not found: {dataset_id}")
        out = {}
        for entity in ("assets", "telemetry", "maintenance"):
            path = dataset_dir / f"{entity}.csv"
            if path.exists():
                frame = pd.read_csv(path)
                if entity == "telemetry" and "timestamp" in frame:
                    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
                if entity == "maintenance":
                    for c in ("start_time", "end_time"):
                        if c in frame:
                            frame[c] = pd.to_datetime(frame[c], utc=True)
                out[entity] = frame
        return out

    def replay(self, dataset_id: str, upto: float | str | None = None, limit: int = 1000) -> dict:
        frames = self.load_dataset(dataset_id)
        telemetry = frames["telemetry"]
        axis = "cycle" if "cycle" in telemetry else "timestamp"
        if upto is not None:
            if axis == "cycle":
                telemetry = telemetry[telemetry[axis] <= float(upto)]
            else:
                target = pd.Timestamp(upto)
                target = target.tz_localize("UTC") if target.tzinfo is None else target.tz_convert("UTC")
                telemetry = telemetry[telemetry[axis] <= target]
        telemetry = telemetry.sort_values(["asset_id", axis]).tail(max(1, min(int(limit), 10000)))
        records = telemetry.copy()
        if "timestamp" in records:
            records["timestamp"] = records["timestamp"].astype(str)
        return {
            "dataset_id": dataset_id,
            "axis": axis,
            "upto": upto,
            "records": records.to_dict("records"),
            "record_count": len(records),
            "evidence_class": "HISTORICAL_REPLAY",
        }
