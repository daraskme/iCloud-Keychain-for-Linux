#!/usr/bin/env bash
# Launcher for the native-messaging host. The browser execs this with the extension's stdio.
# Resolve the repo's venv python so the `icp` package + its deps are importable.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m icp.vault.host
