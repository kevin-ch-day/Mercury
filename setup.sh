#!/usr/bin/env bash
# First-run bootstrap for a fresh Mercury clone.
#
# This script intentionally does not install OS packages, mount storage, alter
# MariaDB, or enable services. It prepares the local Python environment only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON_BIN="${MERCURY_PYTHON:-python3}"
VENV="$ROOT/.venv"
STAMP="$VENV/.mercury-sync-stamp"
INIT_CONFIG=0
SKIP_INSTALL=0
SYSTEM_SITE_PACKAGES=0

usage() {
  cat <<'EOF'
Usage: ./setup.sh [options]

Prepare a newly cloned Mercury checkout for safe local use.

Options:
  --init-config             Create missing config/*.toml files from examples.
  --skip-install            Do not install or refresh Python dependencies.
  --system-site-packages    Create a new virtualenv with system packages visible.
  -h, --help                Show this help.

Environment:
  MERCURY_PYTHON=/path/to/python3.12-or-newer

This bootstrap never mounts storage, invokes sudo, installs OS packages,
changes MariaDB, writes backups, or enables services. Review config/local.toml
and mount the HDD before running live Mercury operations.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-config) INIT_CONFIG=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --system-site-packages) SYSTEM_SITE_PACKAGES=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is required to identify this checkout." >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: Python not found: $PYTHON_BIN" >&2
  echo "Install Python 3.12 or newer, then rerun with MERCURY_PYTHON=/path/to/python3." >&2
  exit 1
fi

PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Error: Mercury requires Python 3.12 or newer; found $PYTHON_VERSION." >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  venv_args=()
  if [[ "$SYSTEM_SITE_PACKAGES" -eq 1 ]]; then
    venv_args+=(--system-site-packages)
  fi
  echo "Creating virtual environment: $VENV"
  "$PYTHON_BIN" -m venv "${venv_args[@]}" "$VENV"
elif [[ "$SYSTEM_SITE_PACKAGES" -eq 1 ]]; then
  echo "Note: --system-site-packages only applies when creating a new .venv."
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  echo "Installing Mercury and its local test/database dependencies..."
  "$VENV/bin/pip" install -e ".[mariadb,dev]"
  touch "$STAMP"
else
  echo "Skipping dependency installation by request."
fi

if [[ ! -x "$VENV/bin/mercury" ]]; then
  echo "Error: Mercury is not installed in $VENV." >&2
  echo "Rerun without --skip-install once Python package access is available." >&2
  exit 1
fi

if [[ "$INIT_CONFIG" -eq 1 ]]; then
  echo "Initializing only missing local configuration files..."
  "$VENV/bin/mercury" config init
fi

cat <<'EOF'

Mercury bootstrap complete.

Next safe steps on a new host:
  1. Mount MERCURY_DATA_V2 at /mnt/MERCURY_DATA_V2.
  2. Run ./setup.sh --init-config if config/local.toml is not present.
  3. Review config/local.toml; do not copy source-host credentials.
  4. Run ./run.sh doctor, then ./run.sh db ping.
  5. Use ./run.sh transfer receive before any restore or deploy action.
EOF
