#!/usr/bin/env bash
# Neptune fresh-rebuild repair helper — review before running.
# Mercury never executes these steps automatically.
set -euo pipefail

USER_NAME="${SUDO_USER:-${USER:-linuxadmin}}"
USB="/mnt/MERCURY_DATA_USB"

echo "Mercury Neptune repair helper"
echo "Target user: ${USER_NAME}"
echo

if [[ "${EUID}" -ne 0 ]]; then
  echo "Re-run with sudo so ownership and MariaDB grants can be applied:"
  echo "  sudo $0"
  exit 1
fi

for dir in mercury_logs mercury_backups mercury_manifests mercury_state \
  mercury_repo_backups mercury_restore_checks mercury_runbooks; do
  path="${USB}/${dir}"
  if [[ -d "${path}" ]]; then
    echo "chown ${USER_NAME}:${USER_NAME} ${path}"
    chown -R "${USER_NAME}:${USER_NAME}" "${path}"
  fi
done

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
echo "  sudo -u ${USER_NAME} bash -lc 'cd /home/linuxadmin/GitHub/Mercury && ./run.sh doctor && ./run.sh deploy db --dry-run'"
