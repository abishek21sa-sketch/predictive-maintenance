"""Create and verify a SQLite backup without modifying the source database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fingerprint(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(path)) as con:
        integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        tables = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        counts = {
            table: int(
                con.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
            )
            for table in tables
        }
    return {"integrity_check": integrity, **{f"rows:{name}": count for name, count in counts.items()}}


def backup_database(source: str | Path, destination: str | Path) -> dict[str, object]:
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source_path}")
    if destination_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing backup: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path)) as source_con, closing(
        sqlite3.connect(destination_path)
    ) as destination_con:
        source_con.backup(destination_con)
        destination_con.commit()
    source_fingerprint = _fingerprint(source_path)
    backup_fingerprint = _fingerprint(destination_path)
    verified = source_fingerprint == backup_fingerprint and backup_fingerprint["integrity_check"] == "ok"
    return {
        "status": "PASS_REFERENCE_ONLY" if verified else "FAIL",
        "source": str(source_path),
        "destination": str(destination_path),
        "source_fingerprint": source_fingerprint,
        "backup_fingerprint": backup_fingerprint,
        "evidence_class": "REFERENCE_LOCAL_BACKUP_RESTORE_NOT_DR_EVIDENCE",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup and verify a local SQLite state database")
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--output", default="artifacts/backup_restore.json")
    args = parser.parse_args()
    try:
        result = backup_database(args.source, args.destination)
    except (FileExistsError, FileNotFoundError, sqlite3.Error) as exc:
        result = {"status": "FAIL", "reason": str(exc)}
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS_REFERENCE_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
