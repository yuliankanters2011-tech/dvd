#!/usr/bin/env bash
# Called by DSM Task Scheduler at boot. Keeps the existing .env intact.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
if [[ -f .env ]]; then
    # Export the existing installer settings without rewriting the file.
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi
exec bash scripts/prepare-host.sh --no-env >>"$ROOT/boot-prepare.log" 2>&1