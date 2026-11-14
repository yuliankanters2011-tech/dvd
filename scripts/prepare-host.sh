#!/usr/bin/env bash
# Prepare a Synology/Linux host for DVD Bridge. Run with sudo.
# It only changes a dedicated bind mount, never makes /volume1 itself shared.
set -euo pipefail

SHARE_PATH=${SHARE_PATH:-/volume1/dvd}
DEVICE=${DEVICE:-}
WEB_PORT=${WEB_PORT:-8080}
COPY_FILES=${COPY_FILES:-false}
AUTO_EJECT=${AUTO_EJECT:-false}
ISO_MAX_MISSING_PERCENT=${ISO_MAX_MISSING_PERCENT:-0.5}
MEDIA_ROOT=${MEDIA_ROOT:-/volume1/video}
WRITE_ENV=1

usage() {
    cat <<'EOF'
Gebruik: sudo bash scripts/prepare-host.sh [opties]

  --share PAD       Gedeelde NAS-map (standaard /volume1/dvd)
  --device APPARAAT Optische speler, bijvoorbeeld /dev/sr0 (standaard: automatisch)
  --port POORT      Webpoort (standaard 8080)
  --copy            Kopieer nieuwe schijven automatisch naar library
  --auto-eject      Werp uit na een geslaagde automatische kopie of live-dvd-conversie
  --media-root PAD  Jellyfin-map (standaard /volume1/video; maakt Films/Muziek)
  --no-env          Schrijf geen .env-bestand
  -h, --help        Toon deze hulp

Maak de gedeelde map bij voorkeur eerst in DSM: Configuratiescherm >
Gedeelde map > Maken > naam dvd. Deze container maakt geen echte USB-stick.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --share) SHARE_PATH=${2:?Ontbrekend pad}; shift 2 ;;
        --device) DEVICE=${2:?Ontbrekend apparaat}; shift 2 ;;
        --port) WEB_PORT=${2:?Ontbrekende poort}; shift 2 ;;
        --copy) COPY_FILES=true; shift ;;
        --auto-eject) AUTO_EJECT=true; shift ;;
        --media-root) MEDIA_ROOT=${2:?Ontbrekend pad}; shift 2 ;;
        --no-env) WRITE_ENV=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Onbekende optie: $1" >&2; usage >&2; exit 2 ;;
    esac
done

fail() { echo "FOUT: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[[ $(id -u) -eq 0 ]] || fail "Start met sudo: sudo bash scripts/prepare-host.sh"
[[ $(uname -s) == Linux ]] || fail "Deze container heeft een Linux-kernel nodig"
[[ $SHARE_PATH =~ ^/volume[0-9]+/[A-Za-z0-9._/-]+$ ]] || fail "Gebruik een pad zoals /volume1/dvd (geen spaties of ..)"
[[ $SHARE_PATH != *..* ]] || fail "Het pad mag geen .. bevatten"
[[ $MEDIA_ROOT =~ ^/volume[0-9]+/[A-Za-z0-9._/-]+$ ]] || fail "Gebruik een Jellyfin-pad zoals /volume1/video"
[[ $MEDIA_ROOT != *..* ]] || fail "Het Jellyfin-pad mag geen .. bevatten"
if ! [[ $WEB_PORT =~ ^[0-9]+$ ]] || (( WEB_PORT < 1024 || WEB_PORT > 65535 )); then
    fail "Poort moet 1024 t/m 65535 zijn"
fi
[[ $COPY_FILES == true || $COPY_FILES == false ]] || fail "COPY_FILES moet true of false zijn"
[[ $AUTO_EJECT == true || $AUTO_EJECT == false ]] || fail "AUTO_EJECT moet true of false zijn"
if ! [[ $ISO_MAX_MISSING_PERCENT =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    fail "ISO_MAX_MISSING_PERCENT moet een getal van 0 t/m 100 zijn"
fi
if ! awk -v n="$ISO_MAX_MISSING_PERCENT" 'BEGIN { exit !(n >= 0 && n <= 100) }'; then
    fail "ISO_MAX_MISSING_PERCENT moet een getal van 0 t/m 100 zijn"
fi

command -v docker >/dev/null || fail "Docker/Container Manager is niet geïnstalleerd"
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "Docker Compose ontbreekt (installeer Container Manager)"
fi

# Load modules only when the host actually ships them. Never download or
# compile kernel drivers for an unknown DSM model.
for module in cdrom sr_mod udf isofs nls_utf8; do
    if ! grep -qw "$module" /proc/modules && ! grep -qw "$module" /proc/filesystems; then
        modprobe "$module" >/dev/null 2>&1 || true
    fi
done

if [[ -z $DEVICE ]]; then
    candidates=()
    for node in /dev/sr[0-9]*; do
        [[ -b $node ]] && candidates+=("$node")
    done
    if (( ${#candidates[@]} == 0 )); then
        cat >&2 <<'EOF'
FOUT: Geen /dev/sr0 gevonden.
1. Sluit de usb-cd/dvd-speler rechtstreeks aan en geef hem voldoende voeding.
2. Controleer in SSH: ls -l /dev/sr* en dmesg | tail -50
3. Synology moet cdrom/sr_mod ondersteunen. Sommige DSM-modellen missen deze
   drivers; Docker kan dat niet repareren. Compileer geen willekeurige drivers.
EOF
        exit 1
    fi
    (( ${#candidates[@]} == 1 )) || fail "Meerdere spelers gevonden: ${candidates[*]}. Kies met --device /dev/srX"
    DEVICE=${candidates[0]}
fi
[[ $DEVICE =~ ^/dev/sr[0-9]+$ ]] || fail "DEVICE moet /dev/sr0, /dev/sr1, enz. zijn"
[[ -b $DEVICE ]] || fail "$DEVICE is geen blokkoppelpunt. Sluit de speler aan"

info "Speler: $DEVICE"
info "NAS-map: $SHARE_PATH"
mkdir -p "$SHARE_PATH/disc" "$SHARE_PATH/library"

# A self-bind isolates propagation from the rest of the volume. Docker's
# rshared bind can then send the child disc mount back to this exact path.
if ! awk -v p="$SHARE_PATH" '$5 == p {found=1} END {exit !found}' /proc/self/mountinfo; then
    mount --bind "$SHARE_PATH" "$SHARE_PATH"
fi
if ! awk -v p="$SHARE_PATH" '$5 == p {for(i=7;i<NF;i++) if($i ~ /^shared:/) ok=1} END {exit !ok}' /proc/self/mountinfo; then
    mount --make-rshared "$SHARE_PATH"
fi
awk -v p="$SHARE_PATH" '$5 == p {for(i=7;i<NF;i++) if($i ~ /^shared:/) ok=1} END {exit !ok}' /proc/self/mountinfo \
    || fail "De map is niet shared. Ondersteunt deze DSM-kernel mount-propagation?"

# Prefer the sudo caller's DSM account; fall back to the folder owner.
PUID=${SUDO_UID:-$(stat -c '%u' "$SHARE_PATH")}
PGID=${SUDO_GID:-$(stat -c '%g' "$SHARE_PATH")}
chown "$PUID:$PGID" "$SHARE_PATH/disc" "$SHARE_PATH/library"
chmod 0755 "$SHARE_PATH/disc" "$SHARE_PATH/library"

FILMS_PATH="$MEDIA_ROOT/Films"
MUSIC_PATH="$MEDIA_ROOT/Muziek"
mkdir -p "$FILMS_PATH" "$MUSIC_PATH"
chown "$PUID:$PGID" "$FILMS_PATH" "$MUSIC_PATH"
chmod 0755 "$FILMS_PATH" "$MUSIC_PATH"
info "Jellyfin-films: $FILMS_PATH"
info "Jellyfin-muziek: $MUSIC_PATH"

if [[ $WRITE_ENV == 1 ]]; then
    ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    ENV_FILE="$ROOT/.env"
    if [[ -e $ENV_FILE ]]; then
        backup="$ENV_FILE.backup.$(date +%Y%m%d%H%M%S)"
        cp "$ENV_FILE" "$backup"
        info "Bestaande .env bewaard als $backup"
    fi
    cat > "$ENV_FILE" <<EOF
# Gemaakt door scripts/prepare-host.sh
SHARE_PATH=$SHARE_PATH
DEVICE=$DEVICE
WEB_PORT=$WEB_PORT
WEB_BIND=0.0.0.0
PUID=$PUID
PGID=$PGID
POLL_SECONDS=5
COPY_FILES=$COPY_FILES
AUTO_EJECT=$AUTO_EJECT
ISO_MAX_MISSING_PERCENT=$ISO_MAX_MISSING_PERCENT
IMAGE_NAME=${IMAGE_NAME:-ghcr.io/yuliankanters2011-tech/dvd}
IMAGE_TAG=${IMAGE_TAG:-latest}
FILMS_PATH=$FILMS_PATH
MUSIC_PATH=$MUSIC_PATH
CONVERT_THREADS=2
CONVERT_CRF=20
CONVERT_PRESET=veryfast
CONVERT_AUDIO_BITRATE=192
EOF
    chown "$PUID:$PGID" "$ENV_FILE"
    chmod 0644 "$ENV_FILE"
    info ".env geschreven"
fi

"${COMPOSE[@]}" config --quiet
info "Host is voorbereid. Start daarna:"
echo "    sudo ${COMPOSE[*]} up -d --build"
echo "    Dashboard: http://<NAS-IP>:$WEB_PORT"
echo "    Bestanden: $SHARE_PATH/disc"
echo "Na een NAS-herstart dit script opnieuw uitvoeren (zie README voor Taakplanner)."