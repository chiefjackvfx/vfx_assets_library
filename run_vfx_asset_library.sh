#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || {
    printf 'Error: could not locate the ShotBox Assets project directory.\n' >&2
    exit 1
}

cd -- "$SCRIPT_DIR" || {
    printf 'Error: could not open the ShotBox Assets project directory: %s\n' "$SCRIPT_DIR" >&2
    exit 1
}

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -e "$SCRIPT_DIR/.venv" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
        printf 'Error: Python 3.11 or newer is required, but python3 was not found.\n' >&2
        exit 1
    fi

    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        printf 'Error: Python 3.11 or newer is required. Found: ' >&2
        python3 --version >&2
        exit 1
    fi

    printf 'Creating the ShotBox Assets virtual environment...\n'
    if ! python3 -m venv "$SCRIPT_DIR/.venv"; then
        printf 'Error: could not create .venv. Ensure the Python venv module is installed.\n' >&2
        exit 1
    fi
elif [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'Error: .venv exists but does not contain an executable at %s.\n' "$VENV_PYTHON" >&2
    printf 'Repair or remove .venv, then run this launcher again.\n' >&2
    exit 1
fi

if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf 'Error: the .venv interpreter must be Python 3.11 or newer. Found: ' >&2
    "$VENV_PYTHON" --version >&2
    exit 1
fi

printf 'Synchronizing ShotBox Assets dependencies...\n'
if ! "$VENV_PYTHON" -m pip install -e "$SCRIPT_DIR"; then
    printf 'Error: dependency installation failed. Check the output above and your network connection.\n' >&2
    exit 1
fi

printf 'Starting ShotBox Assets...\n'
"$VENV_PYTHON" "$SCRIPT_DIR/run_vfx_asset_library.py" "$@"
exit_code=$?

if (( exit_code != 0 )); then
    printf 'Error: ShotBox Assets exited with status %d.\n' "$exit_code" >&2
fi

exit "$exit_code"
