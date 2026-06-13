#!/usr/bin/env bash
# Mount Mercury USB, create the standard layout, fix ownership, and enable boot mount.
# Run as: sudo ./run.sh repair-usb   (or: sudo ./scripts/repair-mercury-usb.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-${USER:-}}"
USB="${MERCURY_USB_MOUNT:-/mnt/MERCURY_DATA_USB}"
LABEL="MERCURY_DATA_USB"
MOUNT_UNIT="mnt-MERCURY_DATA_USB.mount"

if [[ -z "${USER_NAME}" || "${USER_NAME}" == "root" ]]; then
  echo "Mercury USB repair must be run with sudo from your operator account:" >&2
  echo "  sudo ./run.sh repair-usb" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Mercury USB repair requires root for mount and ownership." >&2
  echo "Run: sudo ./run.sh repair-usb" >&2
  exit 1
fi

echo "Mercury USB repair"
echo "  operator: ${USER_NAME}"
echo "  mount:    ${USB}"
echo

mkdir -p "${USB}"

if ! findmnt -n "${USB}" >/dev/null 2>&1; then
  echo "Mounting Mercury USB at ${USB} ..."
  if systemctl list-unit-files "${MOUNT_UNIT}" >/dev/null 2>&1; then
    systemctl enable "${MOUNT_UNIT}" >/dev/null 2>&1 || true
    systemctl start "${MOUNT_UNIT}"
  else
    mount "LABEL=${LABEL}" "${USB}"
  fi
  findmnt "${USB}"
else
  echo "USB already mounted:"
  findmnt "${USB}"
  if systemctl list-unit-files "${MOUNT_UNIT}" >/dev/null 2>&1; then
    echo "Ensuring ${MOUNT_UNIT} is enabled for boot ..."
    systemctl enable "${MOUNT_UNIT}" >/dev/null 2>&1 || true
  fi
fi

for dir in mercury_logs mercury_backups mercury_manifests mercury_state \
  mercury_repo_backups mercury_restore_checks mercury_runbooks; do
  path="${USB}/${dir}"
  mkdir -p "${path}"
  echo "chown ${USER_NAME}:${USER_NAME} ${path}"
  chown -R "${USER_NAME}:${USER_NAME}" "${path}"
done

if [[ -d "${USB}" ]]; then
  echo "chown ${USER_NAME}:${USER_NAME} ${USB}"
  chown "${USER_NAME}:${USER_NAME}" "${USB}" 2>/dev/null || true
fi

echo
echo "Mercury USB is ready."
echo "Verify with:"
echo "  sudo -u ${USER_NAME} bash -lc 'cd \"${ROOT}\" && ./run.sh doctor'"
