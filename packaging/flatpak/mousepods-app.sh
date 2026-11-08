#!/bin/sh
# Flatpak entry point: writable data outside the read-only /app, then the desktop app.
export MOUSEPODS_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mousepods"
mkdir -p "$MOUSEPODS_DATA_DIR"
exec python3 /app/share/mousepods/mousepods-app "$@"
