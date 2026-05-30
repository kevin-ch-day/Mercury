"""Centralized planned backup paths (dry-run; no files written in seed)."""

from datetime import datetime, timezone

from pydantic import BaseModel

from mercury.core.safety import BACKUP_KIND_FULL, BACKUP_KIND_SCHEMA_ONLY

MANIFEST_FILENAME = "manifest.json"
CHECKSUM_FILENAME = "checksum.sha256"
REPORT_FILENAME = "backup_report.md"
TOOL_FAMILY = "mariadb-dump/mysqldump logical backup"


class BackupLayoutPaths(BaseModel):
    """Planned on-disk layout for one database backup run."""

    database: str
    date: str
    timestamp: str
    directory: str
    full_dump_file: str
    schema_dump_file: str
    manifest_file: str = MANIFEST_FILENAME
    checksum_file: str = CHECKSUM_FILENAME
    report_file: str = REPORT_FILENAME

    def full_dump_path(self) -> str:
        return f"{self.directory}{self.full_dump_file}"

    def schema_dump_path(self) -> str:
        return f"{self.directory}{self.schema_dump_file}"

    def manifest_path(self) -> str:
        return f"{self.directory}{self.manifest_file}"

    def checksum_path(self) -> str:
        return f"{self.directory}{self.checksum_file}"

    def report_path(self) -> str:
        return f"{self.directory}{self.report_file}"

    def future_schema_hint(self) -> str:
        """Line shown in schema-plan output."""
        return f"backups/{self.date}/{self.database}/{self.schema_dump_file}"

    def future_full_hint(self) -> str:
        return f"backups/{self.date}/{self.database}/{self.full_dump_file}"


def default_date(now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    return instant.strftime("%Y-%m-%d")


def default_timestamp(now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    # Include milliseconds so rapid batch backups do not collide on filenames.
    return instant.strftime("%Y%m%d_%H%M%S") + f"_{instant.microsecond // 1000:03d}"


def planned_backup_directory(database: str, date: str | None = None) -> str:
    """Relative path: backups/YYYY-MM-DD/<database>/"""
    day = date or default_date()
    return f"backups/{day}/{database}/"


def planned_dump_filename(database: str, kind: str, timestamp: str | None = None) -> str:
    ts = timestamp or default_timestamp()
    if kind == BACKUP_KIND_SCHEMA_ONLY:
        return f"{database}_{ts}.schema.sql.gz"
    return f"{database}_{ts}.sql.gz"


def build_backup_layout(
    database: str,
    *,
    date: str | None = None,
    timestamp: str | None = None,
    now: datetime | None = None,
) -> BackupLayoutPaths:
    """Build consistent planned paths for backup / schema-plan / manifest-preview."""
    day = date or default_date(now)
    ts = timestamp or default_timestamp(now)
    directory = planned_backup_directory(database, day)
    return BackupLayoutPaths(
        database=database,
        date=day,
        timestamp=ts,
        directory=directory,
        full_dump_file=planned_dump_filename(database, BACKUP_KIND_FULL, ts),
        schema_dump_file=planned_dump_filename(database, BACKUP_KIND_SCHEMA_ONLY, ts),
    )


def list_standard_filenames(database: str, timestamp: str | None = None) -> list[str]:
    """All artifact filenames in a backup folder."""
    ts = timestamp or default_timestamp()
    layout = build_backup_layout(database, timestamp=ts, date=default_date())
    return [
        layout.full_dump_file,
        layout.schema_dump_file,
        layout.manifest_file,
        layout.checksum_file,
        layout.report_file,
    ]
