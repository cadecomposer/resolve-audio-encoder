#!/usr/bin/env bash
#
# Launcher for Resolve Audio Encoder.
# Creates a local virtual environment on first run and installs the one
# optional dependency (tkinterdnd2, used for drag & drop).
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"

if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment in $VENV ..."
    python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c "import tkinterdnd2" >/dev/null 2>&1; then
    echo "Installing dependencies ..."
    "$VENV/bin/pip" install --upgrade pip >/dev/null
    "$VENV/bin/pip" install -r "$DIR/requirements.txt"
fi

exec "$VENV/bin/python" "$DIR/resolve_audio_encoder.py" "$@"
