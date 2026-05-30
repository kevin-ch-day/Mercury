"""Platform database catalog (reference names and project metadata)."""

from pydantic import BaseModel

# ObsidianDroid uses gecko_research_database_* in this platform.


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
        name="gecko_research_database_prod",
        project="ObsidianDroid",
        description="Android malware ML/research pipeline database (production).",
    ),
    CatalogEntry(
        name="gecko_research_database_dev",
        project="ObsidianDroid",
        description="Disposable dev target.",
    ),
    CatalogEntry(
        name="droid_threat_intel_db_prod",
        project="DroidThreatIntel",
        description="Threat intel production database on dev server (manual review for catalog alignment).",
    ),
    CatalogEntry(
        name="droid_threat_intel_db_dev",
        project="DroidThreatIntel",
        description="Disposable dev target for droid_threat_intel_db_prod.",
    ),
    CatalogEntry(
        name="proofpoint_cti_db_dev",
        project="ProofpointCTI",
        description="Dev database without matching prod in inventory; manual review.",
    ),
    CatalogEntry(
        name="_restorecheck_erebus_threat_intel_prod_20260530",
        project="Mercury",
        description="Temporary restore-check database; not a backup source.",
    ),
    CatalogEntry(
        name="random_test_db",
        project="Test",
        description="Unknown naming pattern; manual review required.",
    ),
]

PLATFORM_DATABASES: list[str] = [entry.name for entry in PLATFORM_CATALOG]

CATALOG_BY_NAME: dict[str, CatalogEntry] = {entry.name: entry for entry in PLATFORM_CATALOG}
