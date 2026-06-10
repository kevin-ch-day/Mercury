"""Platform database catalog for the current active Mercury milestone."""

from pydantic import BaseModel

class CatalogEntry(BaseModel):
    name: str
    project: str
    description: str


PLATFORM_CATALOG: list[CatalogEntry] = [
    CatalogEntry(
        name="erebus_threat_intel_prod",
        project="Erebus",
        description="VirusTotal enrichment, malware catalog, family/type authority (production).",
    ),
    CatalogEntry(
        name="erebus_threat_intel_dev",
        project="Erebus",
        description="Disposable dev target; sync from prod after verified backup.",
    ),
    CatalogEntry(
        name="android_permission_intel",
        project="Platform",
        description="Shared Android permission authority (Erebus, ScytaleDroid, ObsidianDroid, Iapetus).",
    ),
    CatalogEntry(
        name="scytaledroid_core_prod",
        project="ScytaleDroid",
        description="APK static/dynamic analysis database (production).",
    ),
    CatalogEntry(
        name="scytaledroid_core_dev",
        project="ScytaleDroid",
        description="Disposable dev target.",
    ),
    CatalogEntry(
        name="obsidiandroid_core_prod",
        project="ObsidianDroid",
        description="Malware ML/research database (production; backup-only for this milestone).",
    ),
]

PLATFORM_DATABASES: list[str] = [entry.name for entry in PLATFORM_CATALOG]

CATALOG_BY_NAME: dict[str, CatalogEntry] = {entry.name: entry for entry in PLATFORM_CATALOG}
