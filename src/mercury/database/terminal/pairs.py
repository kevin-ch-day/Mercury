"""Display prod→dev database pairs."""

from mercury import output
from mercury.database.discovery import discover_demo
from mercury.database.core import projects_map
from mercury.database.prod_dev_pairs import build_prod_dev_pairs, orphan_dev_databases


def print_prod_dev_pairs(*, inventory=None) -> None:
    if inventory is None:
        inventory = discover_demo()
    names = inventory.names
    pairs = build_prod_dev_pairs(names, projects=projects_map(inventory))
    orphans = orphan_dev_databases(names, pairs)

    output.heading("Production to development pairs")
    for pair in pairs:
        project = f" [{pair.project}]" if pair.project else ""
        dev_label = pair.expected_dev if pair.dev_listed else f"MISSING ({pair.expected_dev})"
        output.item(f"{pair.prod}{project}")
        output.item(f"-> {dev_label}", indent=2)
        output.item(pair.sync_notes, indent=2)

    if orphans:
        output.heading("Dev databases without prod in inventory")
        for name in orphans:
            output.item(name)

    output.write()
    output.write("Seed mode: sync not executed. Backup and verify prod before any dev sync.")
