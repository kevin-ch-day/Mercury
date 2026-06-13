#!/usr/bin/env bash
# Mercury launcher — use from repo root: ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"
STAMP="$VENV/.mercury-sync-stamp"
SKIP_SYNC="${MERCURY_SKIP_SYNC:-0}"
PIP_INSTALL_ARGS=(-e ".[mariadb,dev]")

bootstrap_help() {
  cat <<'EOF'
Mercury bootstrap failed while installing Python dependencies.

Mercury can still run without re-syncing when the existing virtualenv already
contains the mercury console script:
  MERCURY_SKIP_SYNC=1 ./run.sh

If Fedora is temporarily offline or DNS/PyPI is unavailable, use the Fedora
package fallback and a system-site-packages virtualenv:
  sudo dnf install python3-hatchling python3-pydantic python3-rich python3-typer python3-pymysql python3-pytest mariadb
  rm -rf .venv
  python3 -m venv --system-site-packages .venv
  .venv/bin/pip install --no-build-isolation -e ".[mariadb,dev]"

Mercury's Fedora-local MariaDB socket setup belongs in config/local.toml:
  [mariadb]
  host = "127.0.0.1"
  port = 3306
  user = "root"
  use_client = true
  unix_socket = "/var/lib/mysql/mysql.sock"

USB backup root example:
  /mnt/MERCURY_DATA_USB/mercury_backups
EOF
}

require_mercury() {
  if [[ -x "$VENV/bin/mercury" ]]; then
    return 0
  fi
  echo "Mercury is not installed in $VENV." >&2
  if [[ "$SKIP_SYNC" == "1" ]]; then
    echo "MERCURY_SKIP_SYNC=1 was requested, so no install attempt was made." >&2
  fi
  bootstrap_help >&2
  exit 1
}

if [[ ! -d "$VENV" ]]; then
  echo "Creating virtual environment in .venv ..."
  "$PYTHON" -m venv "$VENV"
fi

needs_install() {
  [[ "$SKIP_SYNC" == "1" ]] && return 1
  [[ ! -x "$VENV/bin/mercury" ]] && return 0
  [[ ! -f "$STAMP" ]] && return 0
  [[ "pyproject.toml" -nt "$STAMP" ]] && return 0
  [[ "src" -nt "$STAMP" ]] && return 0
  return 1
}

if needs_install; then
  echo "Syncing Mercury virtualenv ..."
  if ! install_output="$("$VENV/bin/pip" install "${PIP_INSTALL_ARGS[@]}" 2>&1)"; then
    printf '%s\n' "$install_output" >&2
    echo >&2
    bootstrap_help >&2
    exit 1
  fi
  touch "$STAMP"
fi

require_mercury

if [[ "${1:-}" == "repair-usb" ]]; then
  REPAIR_SCRIPT="$ROOT/scripts/repair-mercury-usb.sh"
  if [[ ! -f "$REPAIR_SCRIPT" ]]; then
    echo "Missing repair script: $REPAIR_SCRIPT" >&2
    exit 1
  fi
  chmod +x "$REPAIR_SCRIPT"
  if [[ "${EUID}" -eq 0 ]]; then
    exec "$REPAIR_SCRIPT"
  fi
  exec sudo "$REPAIR_SCRIPT"
fi

if [[ $# -eq 0 ]]; then
  exec "$VENV/bin/mercury" menu
fi

exec "$VENV/bin/mercury" "$@"
