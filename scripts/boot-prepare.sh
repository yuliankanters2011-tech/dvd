#!/usr/bin/env bash
# Called by DSM Task Scheduler at boot. Keeps the existing .env intact.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
exec bash scripts/prepare-host.sh --no-env >>"$ROOT/boot-prepare.log" 2>&1