#!/usr/bin/env bash
# Mercury launcher — use from repo root: ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"
STAMP="$VENV/.mercury-sync-stamp"
SKIP_SYNC="${MERCURY_SKIP_SYNC:-0}"

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
  "$VENV/bin/pip" install -q -e ".[mariadb,dev]"
  touch "$STAMP"
fi

if [[ $# -eq 0 ]]; then
  exec "$VENV/bin/mercury" menu
fi

exec "$VENV/bin/mercury" "$@"
