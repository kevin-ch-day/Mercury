# Mercury HDD lifecycle menu

Operator storage lifecycle is a first-class main-menu workflow.

## Main menu

```text
[1] Mercury HDD and Storage
```

Symbolic ID: `MAIN_STORAGE` / `ACTION_HDD_STORAGE` (rendered number comes from `main_menu_hint()`).

## Primary HDD menu (four actions)

| Key | Symbolic ID | Role |
| --- | --- | --- |
| `[1]` | `STORAGE_RECOMMENDED_ACTION` | State-dependent recommended wizard |
| `[2]` | `STORAGE_STATUS_VALIDATE` | Combined status + validation |
| `[3]` | `STORAGE_CHANGE_MODE` | Reconnect / operating mode |
| `[4]` | `STORAGE_MAINTENANCE` | Cleanup + advanced tools |

Invalid actions are hidden. Safe disconnect and reconnect are never buried under Advanced.

## Recommended action `[1]` by state

| State | Label |
| --- | --- |
| Writer enabled | Prepare HDD for safe disconnect |
| Writes disabled, package verified / ready | Safe disconnect Mercury HDD |
| Preparing or blocked | Recheck disconnect blockers |
| Detached | Reconnect or inspect Mercury HDD |
| Read-only | Continue destination validation |
| Package unverified | Verify destination package |
| Identity mismatch | Diagnose attached storage |

## Safety (unchanged)

Device resolution by UUID, legacy USB rejection, sudo OS prompts only, holder checks, flush, normal unmount, UUID post-unmount confirmation, parent re-resolution before power-off, exact `RESTORE MERCURY WRITES` confirmation, destination read-only mode.

See also: [destination_package_and_detach.md](destination_package_and_detach.md).
