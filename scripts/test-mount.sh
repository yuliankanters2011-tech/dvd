#!/usr/bin/env bash
# Root-only integration test used by CI. Mounts real ISO9660/UDF loop images
# read-only and verifies the exact mount options used by the container.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[[ $(id -u) -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
command -v genisoimage >/dev/null
command -v mkudffs >/dev/null

WORK=$(mktemp -d /tmp/dvd-mount-test-XXXX)
LOOP=""
cleanup() {
    set +e
    if [[ -n $LOOP ]]; then
        umount "$WORK/mnt" 2>/dev/null
        losetup -d "$LOOP" 2>/dev/null
    fi
    umount "$WORK/mnt" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT
mkdir -p "$WORK/src" "$WORK/mnt"
echo "hallo nas" > "$WORK/src/hallo.txt"

genisoimage -quiet -R -J -V TESTDVD -o "$WORK/test.iso" "$WORK/src"
LOOP=$(losetup --find --show --read-only "$WORK/test.iso")
python3 - "$LOOP" "$WORK/mnt" <<'PY'
import sys
from pathlib import Path
from app import drive
info = drive.DiscInfo("test", "TESTDVD", "iso9660", 1024)
drive.mount_disc(Path(sys.argv[1]), Path(sys.argv[2]), info, 0, 0)
assert (Path(sys.argv[2]) / "hallo.txt").read_text().strip() == "hallo nas"
assert drive.mount_source(Path(sys.argv[2])) == sys.argv[1]
try:
    (Path(sys.argv[2]) / "nieuw.txt").write_text("mag niet")
except OSError:
    pass
else:
    raise AssertionError("mount was writable")
drive.unmount(Path(sys.argv[2]))
PY
losetup -d "$LOOP"; LOOP=""

truncate -s 64M "$WORK/test.udf"
mkudffs --vid=TESTUDF --lvid=TESTUDF "$WORK/test.udf" >/dev/null
LOOP=$(losetup --find --show "$WORK/test.udf")
mount -t udf -o rw "$LOOP" "$WORK/mnt"
echo "hallo udf" > "$WORK/mnt/hallo.txt"
umount "$WORK/mnt"
losetup -d "$LOOP"
LOOP=$(losetup --find --show --read-only "$WORK/test.udf")
python3 - "$LOOP" "$WORK/mnt" <<'PY'
import sys
from pathlib import Path
from app import drive
info = drive.DiscInfo("test", "TESTUDF", "udf", 64 * 1024 * 1024)
drive.mount_disc(Path(sys.argv[1]), Path(sys.argv[2]), info, 0, 0)
assert (Path(sys.argv[2]) / "hallo.txt").read_text().strip() == "hallo udf"
drive.unmount(Path(sys.argv[2]))
PY
echo "Real ISO9660 + UDF read-only mount tests passed"
