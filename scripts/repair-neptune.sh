#!/usr/bin/env bash
# Neptune fresh-rebuild repair helper — review before running.
# Mercury never executes these steps automatically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-linuxadmin}}"

echo "Mercury Neptune repair helper"
echo "Target user: ${USER_NAME}"
echo

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-run with sudo so mount, ownership, and MariaDB grants can be applied:"
  echo "  sudo $0"
  exit 1
fi

"${ROOT}/scripts/repair-mercury-usb.sh"

echo
echo "Applying MariaDB deployment grants for ${USER_NAME}..."
mariadb <<EOF
GRANT CREATE, INSERT, UPDATE, DELETE, ALTER, INDEX,
  CREATE TEMPORARY TABLES, CREATE VIEW, CREATE ROUTINE,
  ALTER ROUTINE, EXECUTE, TRIGGER, EVENT
  ON *.* TO '${USER_NAME}'@'localhost';
FLUSH PRIVILEGES;
SHOW GRANTS FOR '${USER_NAME}'@'localhost';
EOF

echo
echo "Done. Verify with:"
echo "  sudo -u ${USER_NAME} bash -lc 'cd \"${ROOT}\" && ./run.sh doctor'"
