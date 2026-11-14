#!/usr/bin/env bash
# Read-only diagnostics for DVD Bridge on Synology/Linux.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

section() { printf '\n== %s ==\n' "$1"; }
ok() { printf 'OK   %s\n' "$1"; }
warn() { printf 'LET OP %s\n' "$1"; }

section "Host"
uname -a
if [[ -r /etc/synoinfo.conf ]]; then
    grep -E '^(company|title|upnpmodelname|unique)=' /etc/synoinfo.conf || true
fi

section "Docker"
if command -v docker >/dev/null; then
    docker version --format 'Client {{.Client.Version}} / server {{.Server.Version}}' || warn "Docker daemon niet bereikbaar (sudo nodig?)"
    if docker compose version >/dev/null 2>&1; then
        docker compose version
        COMPOSE=(docker compose)
    elif command -v docker-compose >/dev/null; then
        docker-compose version
        COMPOSE=(docker-compose)
    else
        warn "Compose ontbreekt"
        COMPOSE=()
    fi
else
    warn "Docker ontbreekt"
    COMPOSE=()
fi

section "Optische speler"
found=0
for node in /dev/sr[0-9]*; do
    if [[ -b $node ]]; then
        found=1
        ls -l "$node"
        if command -v lsblk >/dev/null; then lsblk -no NAME,TYPE,SIZE,MODEL "$node" || true; fi
    fi
done
(( found )) || warn "Geen /dev/srX gevonden. Controleer usb-voeding en DSM-kernelmodules cdrom/sr_mod."
for module in cdrom sr_mod udf isofs nls_utf8; do
    if grep -qw "$module" /proc/modules || grep -qw "$module" /proc/filesystems; then
        ok "$module geladen/ingebouwd"
    else
        warn "$module niet zichtbaar (mogelijk niet beschikbaar op dit DSM-model)"
    fi
done

section "Mount-propagation"
SHARE_PATH=${SHARE_PATH:-/volume1/dvd}
if [[ -r .env ]]; then
    # shellcheck disable=SC1090
    source <(grep -E '^(SHARE_PATH|DEVICE|WEB_PORT)=' .env)
fi
if awk -v p="$SHARE_PATH" '$5 == p {for(i=7;i<NF;i++) if($i ~ /^shared:/) ok=1} END {exit !ok}' /proc/self/mountinfo; then
    ok "$SHARE_PATH is shared"
else
    warn "$SHARE_PATH is niet shared. Voer sudo bash scripts/prepare-host.sh opnieuw uit."
fi
awk -v p="$SHARE_PATH" '$5 == p || index($5, p "/") == 1 {print}' /proc/self/mountinfo || true

section "Container"
if ((${#COMPOSE[@]})); then
    "${COMPOSE[@]}" ps || true
    "${COMPOSE[@]}" logs --tail=40 dvd || true
    if "${COMPOSE[@]}" exec -T dvd ffmpeg -version >/dev/null 2>&1; then
        ok "FFmpeg aanwezig in de container"
    else
        warn "FFmpeg ontbreekt of de container draait niet. Pull/build de nieuwe image."
    fi
    if "${COMPOSE[@]}" exec -T dvd ffprobe -version >/dev/null 2>&1; then
        ok "ffprobe aanwezig in de container"
    else
        warn "ffprobe ontbreekt of de container draait niet."
    fi
fi

section "Jellyfin-mappen"
if [[ -r .env ]]; then
    # shellcheck disable=SC1090
    source <(grep -E '^(FILMS_PATH|MUSIC_PATH)=' .env)
fi
for folder in "${FILMS_PATH:-/volume1/video/Films}" "${MUSIC_PATH:-/volume1/video/Muziek}"; do
    if [[ -d $folder && -w $folder ]]; then
        ok "$folder bestaat en is schrijfbaar"
    else
        warn "$folder ontbreekt of is niet schrijfbaar. Voer prepare-host.sh uit."
    fi
done

section "Dashboard"
WEB_PORT=${WEB_PORT:-8080}
if command -v curl >/dev/null; then
    if curl --fail --silent --max-time 3 "http://127.0.0.1:$WEB_PORT/healthz"; then
        echo
    else
        warn "Dashboard reageert niet op poort $WEB_PORT"
    fi
fi

cat <<'EOF'

Veelvoorkomende oorzaken:
- Geen /dev/sr0: DSM ondersteunt de optische usb-speler niet, of de speler krijgt te weinig voeding.
- Mount permission denied: Container Manager blokkeert SYS_ADMIN/mount; controleer compose en DSM-versie.
- Lege NAS-map: mount-propagation staat niet aan of de container gebruikt een ander SHARE_PATH.
- wrong fs type: kernel mist udf/iso9660/nls_utf8, of de schijf is geen gegevensschijf.
- FFmpeg ontbreekt: de container draait nog op een oude image zonder ffmpeg-pakket.
- Jellyfin-map niet schrijfbaar: controleer FILMS_PATH/MUSIC_PATH en voer prepare-host.sh uit.
EOF