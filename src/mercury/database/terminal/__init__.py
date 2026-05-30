"""Database CLI terminal output — inventory, inspect, ping, policy, pairs, stats."""

from mercury.database.terminal.access import print_platform_access
from mercury.database.terminal.discover_menu import (
    build_discover_menu_fields,
    print_discover_menu,
)
from mercury.database.terminal.inspect import (
    print_database_inspect,
    print_database_inspect_menu,
)
from mercury.database.terminal.inventory import (
    print_classification,
    print_inventory,
    print_readonly_discovery_plan,
)
from mercury.database.terminal.pairs import print_prod_dev_pairs
from mercury.database.terminal.ping import print_server_probe
from mercury.database.terminal.policy import print_policy_report
from mercury.database.terminal.stats import print_database_stats

__all__ = [
    "build_discover_menu_fields",
    "print_classification",
    "print_database_inspect",
    "print_database_inspect_menu",
    "print_database_stats",
    "print_discover_menu",
    "print_inventory",
    "print_platform_access",
    "print_policy_report",
    "print_prod_dev_pairs",
    "print_readonly_discovery_plan",
    "print_server_probe",
]
