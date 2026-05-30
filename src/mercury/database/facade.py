"""High-level database module facade for CLI and other Mercury components."""

from mercury.database.core import DatabaseClassification, classify_database
from mercury.database.discovery import DiscoveryMode, discover, discover_demo
from mercury.database.terminal.inventory import print_classification, print_inventory
from mercury.database.terminal.pairs import print_prod_dev_pairs
from mercury.database.terminal.policy import print_policy_report
from mercury.database.core import DatabaseInventory, backup_source_names
from mercury.database.mariadb.config import MariaDbConnectionConfig, load_mariadb_config
from mercury.database.prod_dev_pairs import ProdDevPair, build_prod_dev_pairs
from mercury.database.prod_dev_pairs import orphan_dev_databases as find_orphan_dev_databases
from mercury.database.backup_planning import (
    BackupPlanDryRun,
    build_backup_plan,
    build_backup_plan_from_inventory,
    build_demo_backup_plan,
    build_discovered_backup_plan,
)
from mercury.database.policy import PolicyReport, validate_config_policy


class DatabaseService:
    """Single entry point for database discovery, classification, and planning."""

    def classify(self, name: str) -> DatabaseClassification:
        return classify_database(name)

    def discover(
        self,
        mode: DiscoveryMode = "config",
        *,
        mariadb_config: MariaDbConnectionConfig | None = None,
        connect_fn=None,
    ) -> DatabaseInventory:
        return discover(mode, mariadb_config=mariadb_config, connect_fn=connect_fn)

    def discover_demo(self) -> DatabaseInventory:
        return discover_demo()

    def discover_live(
        self,
        *,
        mariadb_config: MariaDbConnectionConfig | None = None,
        connect_fn=None,
    ) -> DatabaseInventory:
        return discover("live", mariadb_config=mariadb_config, connect_fn=connect_fn)

    def load_mariadb_config(self) -> MariaDbConnectionConfig:
        return load_mariadb_config()

    def print_inventory(self, inventory: DatabaseInventory) -> None:
        print_inventory(inventory)

    def print_classification(self, name: str) -> None:
        print_classification(name)

    def print_pairs(self, inventory: DatabaseInventory | None = None) -> None:
        print_prod_dev_pairs(inventory=inventory)

    def backup_source_names(self, inventory: DatabaseInventory) -> list[str]:
        return backup_source_names(inventory)

    def backup_plan(self, database_names: list[str]) -> BackupPlanDryRun:
        return build_backup_plan(database_names)

    def backup_plan_from_inventory(self, inventory: DatabaseInventory) -> BackupPlanDryRun:
        return build_backup_plan_from_inventory(inventory)

    def backup_plan_demo(self) -> BackupPlanDryRun:
        return build_demo_backup_plan()

    def backup_plan_discovered(self) -> BackupPlanDryRun:
        return build_discovered_backup_plan()

    def prod_dev_pairs(
        self,
        inventory: DatabaseInventory,
    ) -> list[ProdDevPair]:
        from mercury.database.core import projects_map

        return build_prod_dev_pairs(inventory.names, projects=projects_map(inventory))

    def orphan_dev_databases(
        self,
        inventory: DatabaseInventory,
        pairs: list[ProdDevPair],
    ) -> list[str]:
        return find_orphan_dev_databases(inventory.names, pairs)

    def validate_policy(self, *, use_demo_catalog: bool = False) -> PolicyReport:
        return validate_config_policy(use_demo_catalog=use_demo_catalog)

    def print_policy_report(self, report: PolicyReport) -> None:
        print_policy_report(report)


# Module-level singleton for convenience
default_service = DatabaseService()
