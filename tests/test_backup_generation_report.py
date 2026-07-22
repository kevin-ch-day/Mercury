"""Tests for backup generation identity reporting (no re-hash)."""

from __future__ import annotations

import json
from pathlib import Path

from mercury.backup.generation_report import report_backup_generation_identities


def _write_backup(
    root: Path,
    *,
    database: str,
    day: str,
    stamp: str,
    backup_id: str,
    created_at: str,
    verified: bool = True,
    dump_bytes: bytes = b"dump",
) -> Path:
    path = root / day / database / stamp
    path.mkdir(parents=True)
    dump_name = f"{database}_{stamp}.sql.gz"
    (path / dump_name).write_bytes(dump_bytes)
    (path / "checksum.sha256").write_text(f"deadbeef  {dump_name}\n", encoding="utf-8")
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "backup_id": backup_id,
                "database": database,
                "created_at": created_at,
                "verified": verified,
                "dump_file": dump_name,
                "size_bytes": len(dump_bytes),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_generation_report_prefers_full_backup_ids(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "mercury_backups"
    _write_backup(
        root,
        database="erebus_threat_intel_prod",
        day="2026-07-22",
        stamp="20260722_141049_220",
        backup_id="erebus_threat_intel_prod-full-20260722_141049_220",
        created_at="2026-07-22T14:10:49+00:00",
    )
    _write_backup(
        root,
        database="erebus_threat_intel_prod",
        day="2026-07-22",
        stamp="20260722_164052_380",
        backup_id="erebus_threat_intel_prod-full-20260722_164052_380",
        created_at="2026-07-22T16:40:52+00:00",
    )
    monkeypatch.setattr(
        "mercury.backup.generation_report.find_latest_restore_checked_backup",
        lambda backup_root, database: root
        / "2026-07-22"
        / database
        / "20260722_141049_220",
    )
    rows = report_backup_generation_identities(
        root, databases=("erebus_threat_intel_prod",)
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.latest_written_backup_id == (
        "erebus_threat_intel_prod-full-20260722_164052_380"
    )
    assert row.latest_artifact_verified_id == (
        "erebus_threat_intel_prod-full-20260722_164052_380"
    )
    assert row.latest_manifest_stamped_id == (
        "erebus_threat_intel_prod-full-20260722_164052_380"
    )
    assert row.latest_restore_checked_id == (
        "erebus_threat_intel_prod-full-20260722_141049_220"
    )
    assert row.notes
