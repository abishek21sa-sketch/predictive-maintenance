from __future__ import annotations

from pathlib import Path

import pandas as pd

WORK_ORDER_COLUMNS={"work_order_id","asset_id","status","planned_start","duration_hours","required_skill","part_id","part_qty"}
INVENTORY_COLUMNS={"part_id","description","on_hand","reserved","unit_cost"}

def _load(path: str | Path, required: set[str]) -> pd.DataFrame:
    df=pd.read_csv(path)
    missing=sorted(required-set(df.columns))
    if missing: raise ValueError(f"Missing required columns: {missing}")
    return df

def import_work_orders_csv(path: str | Path) -> pd.DataFrame:
    df=_load(path,WORK_ORDER_COLUMNS); df["asset_id"]=df["asset_id"].astype(int); return df

def import_inventory_csv(path: str | Path) -> pd.DataFrame:
    df=_load(path,INVENTORY_COLUMNS); df["available"]=(df["on_hand"]-df["reserved"]).clip(lower=0); return df

def export_recommendations_csv(records: list[dict], path: str | Path) -> Path:
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True); pd.DataFrame(records).to_csv(target,index=False); return target
