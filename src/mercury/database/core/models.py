"""Database inventory models."""

from pydantic import BaseModel, Field


class DatabaseRecord(BaseModel):
    name: str
    role: str
    backup_source: bool
    dev_target: bool
    manual_review: bool
    project: str | None = None
    host: str | None = None
    port: int | None = None
    config_source: str
    connected: bool = False


class DatabaseInventory(BaseModel):
    """Databases Mercury knows about (config, catalog, or live server)."""

    connection: str = "not_connected"
    mode: str = "config_and_catalog"
    primary_config: str | None = None
    entries: list[DatabaseRecord] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]
