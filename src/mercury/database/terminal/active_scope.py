"""Display the active Mercury database scope snapshot."""

from mercury.terminal import screen as display_screen
from mercury.database.mariadb.active_scope import ActiveScopeReport
from mercury.database.core import role_env_label


def print_active_scope_report(report: ActiveScopeReport, *, compact: bool = False) -> None:
    display_screen.write_fields(
        {
            "Access mode": report.access_mode,
            "Databases": report.database_count,
            "Present": report.present_count,
            "Missing": report.missing_count,
        }
    )
    display_screen.write_blank()
    display_screen.write_compact_table(
        ["DATABASE", "ROLE", "STATUS", "TABLES", "VIEWS", "SIZE", "SYNC ROLE"],
        [
            [
                row.name,
                role_env_label(row.role),
                row.status_label,
                str(row.table_count),
                str(row.view_count),
                row.size_label,
                row.sync_role,
            ]
            for row in report.rows
        ],
        min_col_widths=[28, 8, 8, 6, 6, 10, 12],
        max_col_widths=[36, 8, 8, 6, 6, 12, 14],
    )
    if not compact:
        display_screen.write_blank()
        for note in report.notes:
            display_screen.write_summary(note)
