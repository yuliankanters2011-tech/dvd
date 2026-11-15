"""Linux optical-drive helpers. No shelling out with user-supplied strings."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .protection import CSS_NOTICE, is_css_sense

LOG = logging.getLogger("dvd.drive")

CDROM_DRIVE_STATUS = 0x5326
CDROM_MEDIA_CHANGED = 0x5325
CDROM_LOCKDOOR = 0x5329
CDROMCLOSETRAY = 0x5319
CDROMSTOP = 0x5307
CDROMSTART = 0x5308
CDROM_SELECT_SPEED = 0x5322
DVD_READ_STRUCT = 0x5390
DVD_STRUCT_COPYRIGHT = 0x01
# Size of the Linux dvd_struct union (largest member: dvd_manufact). The
# copyright query uses only the first four bytes; no key or auth ioctl is sent.
_DVD_STRUCT_SIZE = 2056
CDSL_CURRENT = 0x7FFFFFFF
CDS_DISC_OK = 4
# Linux cdrom ioctl speed units. The sr driver multiplies a non-zero value
# by 177 kB/s; 0 asks the drive for its default/maximum. This is a request,
# not a measured RPM and not a guaranteed DVD-video read rate.
DRIVE_SPEED_MAX = 24

DRIVE_STATES = {
    0: "unknown",
    1: "empty",
    2: "open",
    3: "not_ready",
    4: "disc",
}


class DriveError(RuntimeError):
    """An actionable, Dutch error message for the dashboard."""


class DriveUnsupportedError(DriveError):
    """The drive or USB bridge does not implement this control ioctl."""


class DriveProtectedError(DriveError):
    """The medium reports copy protection that DVD Bridge will not bypass."""


def copyright_protection(device: Path) -> int | None:
    """Read the drive's Copyright Protection System Type, or None if unavailable.

    This is a read-only DVD_READ_STRUCT copyright-information query. It does not
    perform DVD authentication and does not request disc/title keys. Values are
    0=none, 1=CSS/CPPM, 2=CPRM; unsupported drives/bridges simply return None.
    """
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        data = ctypes.create_string_buffer(_DVD_STRUCT_SIZE)
        data[0] = bytes([DVD_STRUCT_COPYRIGHT])
        fcntl.ioctl(fd, DVD_READ_STRUCT, data, True)
        return ord(data[2])
    except OSError as exc:
        LOG.debug("Copyright-informatie van %s niet beschikbaar: %s", device, exc)
        return None
    finally:
        os.close(fd)


@dataclass(frozen=True)
class DiscInfo:
    identity: str
    label: str
    filesystem: str
    size_bytes: int
    uuid: str = ""


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    LOG.debug("Uitvoeren: %s", " ".join(args))
    try:
        return subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DriveError(
            f"{Path(args[0]).name} reageerde niet binnen {timeout} seconden. "
            "Controleer de schijf en de usb-voeding."
        ) from exc
    except OSError as exc:
        raise DriveError(f"Kan {args[0]} niet uitvoeren: {exc}") from exc


def _ioctl(fd: int, request: int, argument: int = 0) -> int:
    return fcntl.ioctl(fd, request, argument)


def drive_status(device: Path) -> str:
    """Use the cdrom ioctl, not /sys/block/sr0/size (which lies with no disc)."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENOMEDIUM:
            return "empty"
        if exc.errno in {errno.ENOENT, errno.ENXIO}:
            return "missing"
        raise DriveError(f"Kan {device} niet openen: {exc.strerror}") from exc
    try:
        result = _ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)
        if result != 0:
            return DRIVE_STATES.get(result, "unknown")
        # CDS_NO_INFO: fall through to the conservative blockdev probe.
    except OSError as exc:
        if exc.errno == errno.ENOMEDIUM:
            return "empty"
        # Some USB bridges do not implement the status ioctl. blockdev is a
        # conservative fallback; a failed read never mounts anything.
        if exc.errno in {errno.ENOTTY, errno.EINVAL, errno.ENOSYS}:
            size = run(["blockdev", "--getsize64", str(device)], timeout=10)
            if size.returncode != 0:
                if "No medium found" in size.stderr:
                    return "empty"
                raise DriveError(f"De speler geeft geen status: {size.stderr.strip()}") from exc
            return "disc" if size.stdout.strip().isdigit() else "unknown"
        raise DriveError(f"Status van {device} mislukt: {exc.strerror}") from exc
    finally:
        os.close(fd)
    size = run(["blockdev", "--getsize64", str(device)], timeout=10)
    if size.returncode != 0:
        if "No medium found" in size.stderr:
            return "empty"
        raise DriveError(f"De speler geeft geen status: {size.stderr.strip()}")
    return "disc" if size.stdout.strip().isdigit() else "unknown"


def media_changed(device: Path) -> bool:
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        return _ioctl(fd, CDROM_MEDIA_CHANGED, CDSL_CURRENT) == 1
    except OSError:
        return False
    finally:
        os.close(fd)


def unlock_tray(device: Path) -> None:
    """Undo the kernel's mount-time door lock before attempting eject."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        _ioctl(fd, CDROM_LOCKDOOR, 0)
    except OSError:
        pass
    finally:
        os.close(fd)


def inspect_disc(device: Path) -> DiscInfo:
    result = run(["blkid", "-p", "-o", "export", str(device)], timeout=30)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    filesystem = values.get("TYPE", "").lower()
    if filesystem not in {"iso9660", "udf"}:
        detail = result.stderr.strip()
        if is_css_sense(detail):
            raise DriveProtectedError(CSS_NOTICE)
        raise DriveError(
            "Geen leesbaar gegevensbestandssysteem gevonden. Gebruik een "
            "gegevens-cd/dvd met ISO 9660 of UDF. Audio-cd's, lege schijfjes "
            "en versleutelde film-dvd's worden niet automatisch omgezet."
            + (f" ({detail})" if detail else "")
        )
    size_result = run(["blockdev", "--getsize64", str(device)], timeout=15)
    try:
        size = int(size_result.stdout.strip()) if size_result.returncode == 0 else 0
    except ValueError:
        size = 0
    label = values.get("LABEL", "").strip() or "CD-DVD"
    uuid = values.get("UUID", "")
    # UUID_SUB distinguishes some otherwise similar UDF volumes. Include the
    # filesystem and medium size as well, never the host device name.
    identity = "|".join([filesystem, uuid, values.get("UUID_SUB", ""), label, str(size)])
    return DiscInfo(identity, label, filesystem, max(0, size), uuid)


def mount_options(filesystem: str, puid: int, pgid: int) -> str:
    options = ["ro", "nosuid", "nodev", "noexec", f"uid={puid}", f"gid={pgid}"]
    if filesystem == "udf":
        options.extend(["umask=022", "iocharset=utf8"])
    elif filesystem == "iso9660":
        # ISO9660 has separate file/directory modes, not UDF's umask option.
        options.extend(["mode=0444", "dmode=0555", "iocharset=utf8"])
    else:
        raise DriveError(f"Niet-ondersteund bestandssysteem: {filesystem}")
    return ",".join(options)


def mount_disc(device: Path, target: Path, info: DiscInfo, puid: int, pgid: int) -> None:
    target.mkdir(parents=True, exist_ok=True)
    result = run(
        [
            "mount",
            "-t",
            info.filesystem,
            "-o",
            mount_options(info.filesystem, puid, pgid),
            str(device),
            str(target),
        ],
        timeout=45,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if is_css_sense(stderr):
            raise DriveProtectedError(CSS_NOTICE)
        hint = ""
        if "permission denied" in stderr.lower() or "operation not permitted" in stderr.lower():
            hint = " De container heeft SYS_ADMIN en mount-toestemming van DSM nodig."
        elif "wrong fs type" in stderr.lower() or "invalid argument" in stderr.lower():
            hint = " De NAS-kernel mist mogelijk udf/iso9660/nls_utf8-ondersteuning."
        raise DriveError(f"Aankoppelen mislukt: {stderr or 'onbekende fout'}." + hint)


def is_mounted(target: Path) -> bool:
    result = run(["mountpoint", "-q", str(target)], timeout=10)
    return result.returncode == 0


def mount_source(target: Path) -> str:
    """Return the exact mount's source, or an empty string.

    /proc/self/mountinfo is available on DSM and avoids findmnt version
    differences. Field 5 is the mountpoint; the source follows the '-' separator.
    """
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    resolved = str(target.resolve())
    for line in reversed(lines):
        fields = line.split()
        if len(fields) < 10 or fields[4] != resolved:
            continue
        try:
            separator = fields.index("-")
            return fields[separator + 2]
        except (ValueError, IndexError):
            return ""
    return ""


def unmount(target: Path) -> None:
    if not is_mounted(target):
        return
    result = run(["umount", str(target)], timeout=20)
    if result.returncode != 0 and is_mounted(target):
        raise DriveError(
            f"Loskoppelen mislukt: {result.stderr.strip()}. "
            "Sluit open bestanden op de schijf en probeer opnieuw."
        )


def safe_disc_name(label: str, identity: str) -> str:
    import hashlib
    import unicodedata

    normalized = unicodedata.normalize("NFKC", label)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(".-")
    cleaned = cleaned[:48] or "CD-DVD"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned}-{digest}"


def eject(device: Path) -> None:
    unlock_tray(device)
    result = run(["eject", str(device)], timeout=20)
    if result.returncode != 0:
        raise DriveError(
            f"Uitwerpen mislukt: {result.stderr.strip()}. "
            "Gebruik eventueel de fysieke knop op de speler."
        )


def close_tray(device: Path) -> None:
    """Close a motorised tray. Manual slimline drives may ignore this command."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise DriveError(f"Kan {device} niet openen om de lade te sluiten: {exc.strerror}") from exc
    try:
        _ioctl(fd, CDROMCLOSETRAY, CDSL_CURRENT)
    except OSError:
        result = run(["eject", "-t", str(device)], timeout=20)
        if result.returncode != 0:
            raise DriveError(
                "De lade kon niet automatisch worden gesloten. Duw de lade voorzichtig "
                "dicht of gebruik de fysieke knop op de speler."
            ) from None
    finally:
        os.close(fd)


def _control_ioctl(device: Path, request: int, argument: int, action: str) -> None:
    """Run a read-safe cdrom control ioctl and close the fd immediately.

    Keeping an fd open would itself count as drive activity. CDROMSTART/STOP
    and CDROM_SELECT_SPEED are dispatched by the kernel's cdrom layer before
    the raw-SCSI write-permission check, so the existing :r device is enough.
    """
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise DriveError(f"Kan {device} niet openen om {action}: {exc.strerror}") from exc
    try:
        _ioctl(fd, request, argument)
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.ENOTTY, errno.EINVAL, errno.EOPNOTSUPP}:
            raise DriveUnsupportedError(
                f"Deze speler of usb-brug ondersteunt {action} niet. "
                "Het dashboard blijft verder gewoon werken."
            ) from exc
        raise DriveError(
            f"Kan {action} niet uitvoeren op {device}: {exc.strerror}. "
            "Controleer de schijf, usb-kabel en voeding."
        ) from exc
    finally:
        os.close(fd)


def spin_down(device: Path) -> None:
    """Ask the drive to stop spinning. This does not cut USB bus power."""
    _control_ioctl(device, CDROMSTOP, 0, "de speler te laten slapen")


def spin_up(device: Path) -> None:
    """Ask the drive to start again; some drives also close the tray."""
    _control_ioctl(device, CDROMSTART, 0, "de speler wakker te maken")


def set_speed(device: Path, speed: int) -> None:
    """Request a read-speed limit in Linux cdrom speed units (0 = default/max)."""
    if type(speed) is not int or not 0 <= speed <= DRIVE_SPEED_MAX:
        raise ValueError(f"Draaisnelheid moet 0 t/m {DRIVE_SPEED_MAX} zijn")
    _control_ioctl(device, CDROM_SELECT_SPEED, speed, "de draaisnelheid te wijzigen")


def check_tools() -> None:
    missing = [
        tool
        for tool in ("mount", "umount", "mountpoint", "blkid", "blockdev", "eject")
        if shutil.which(tool) is None
    ]
    if missing:
        raise DriveError("Ontbrekende programma's in de image: " + ", ".join(missing))
