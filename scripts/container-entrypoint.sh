#!/bin/sh
set -eu

if [ "$(id -u)" != "0" ]; then
    echo "DVD Bridge must start as root inside its container to mount the disc." >&2
    exit 1
fi

mkdir -p "${SHARE_DIR:-/srv/dvd}" "${STATE_DIR:-/var/lib/dvd}"

# DSM's AppArmor profile varies by model/version. Mount permission failures
# are reported by the app, not hidden by silently enabling privileged mode.
exec "$@"