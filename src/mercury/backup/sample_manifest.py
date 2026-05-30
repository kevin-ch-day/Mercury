"""Write sample backup manifest JSON for documentation/testing (seed)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from mercury.backup.manifest import BackupManifest, planned_backup_files
from mercury.database.planning import build_demo_backup_plan
from mercury.core.paths import OUTPUT_DIR
from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY


def write_sample_manifests(output_dir: Path | None = None) -> list[Path]:
    """Write example manifest.json files for first prod DB (full + schema-only)."""
    out = output_dir or OUTPUT_DIR
    plan = build_demo_backup_plan()
    if not plan.backup_sources:
        return []

    database = plan.backup_sources[0]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db_dir = out / "samples" / day / database
    db_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []

    for kind, dump_name in (
        (BACKUP_KIND_FULL, planned_backup_files(database, ts)[0]),
        (BACKUP_KIND_SCHEMA_ONLY, planned_backup_files(database, ts)[1]),
    ):
        manifest = BackupManifest(
            backup_id=f"sample-{database}-{kind}",
            database=database,
            backup_kind=kind,
            created_at=datetime.now(timezone.utc),
            dump_file=dump_name,
            source_role="production",
            tool_used="mariadb-dump",
            verified=False,
            notes="Sample manifest for seed/documentation only.",
        )
        path = db_dir / f"manifest_{kind}.json"
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        written.append(path)

    return written
