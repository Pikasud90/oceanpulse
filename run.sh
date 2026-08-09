#!/usr/bin/env bash
# OceanPulse launcher for macOS and Linux.
# Creates a virtual environment, installs dependencies, then starts the app.
# All arguments are passed straight through to run.py.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10 or newer is required but was not found."
    echo "  macOS:  brew install python@3.12   (or download from python.org)"
    echo "  Linux:  sudo apt install python3 python3-venv"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (one time only)..."
    if ! "$PYTHON" -m venv .venv 2>/dev/null; then
        echo "ERROR: could not create a virtual environment."
        echo "On Debian and Ubuntu the venv module ships separately:"
        echo "  sudo apt install python3-venv"
        exit 1
    fi
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Reinstall only when the requirements file is newer than the last install.
STAMP=".venv/.requirements-stamp"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
    echo "Installing dependencies (this takes about a minute the first time)..."
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    touch "$STAMP"
fi

exec python run.py "$@"
