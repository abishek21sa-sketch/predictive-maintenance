import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path = Path(os.getenv("PDM_PROJECT_ROOT", Path.cwd()))
    model_dir: Path = Path(os.getenv("PDM_MODEL_DIR", "artifacts/models"))
    report_dir: Path = Path(os.getenv("PDM_REPORT_DIR", "artifacts/reports"))
    database_path: Path = Path(os.getenv("PDM_DATABASE_PATH", "data/pdm.db"))
    random_seed: int = int(os.getenv("PDM_RANDOM_SEED", "42"))
    rul_cap: int = int(os.getenv("PDM_RUL_CAP", "125"))
    maintenance_horizon: int = int(os.getenv("PDM_MAINTENANCE_HORIZON", "30"))


settings = Settings()
