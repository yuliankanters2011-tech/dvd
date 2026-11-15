import errno
import subprocess
from pathlib import Path

import pytest

from app import drive


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_mount_options_readonly_and_owned():
    options = drive.mount_options("udf", 1024, 100)
    assert "ro," in options and "nosuid" in options and "nodev" in options
    assert "uid=1024" in options and "gid=100" in options
    assert "umask=022" in options
    iso = drive.mount_options("iso9660", 1024, 100)
    assert "mode=0444" in iso and "dmode=0555" in iso
    with pytest.raises(drive.DriveError):
        drive.mount_options("ext4", 0, 0)


def test_inspect_disc(monkeypatch):
    def fake_run(args, timeout=30):
        if args[0] == "blkid":
            return completed("DEVNAME=/dev/sr0\nTYPE=udf\nLABEL=VAKANTIE\nUUID=2026-01-02\n")
        return completed("4700000000\n")

    monkeypatch.setattr(drive, "run", fake_run)
    info = drive.inspect_disc(Path("/dev/sr0"))
    assert info.label == "VAKANTIE"
    assert info.filesystem == "udf"
    assert info.size_bytes == 4700000000
    assert "2026-01-02" in info.identity


def test_inspect_rejects_audio(monkeypatch):
    monkeypatch.setattr(drive, "run", lambda *a, **k: completed("", "no type", 2))
    with pytest.raises(drive.DriveError, match="gegevensbestandssysteem"):
        drive.inspect_disc(Path("/dev/sr0"))


def test_drive_status_ioctl(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)
    monkeypatch.setattr(drive, "_ioctl", lambda *a: 4)
    assert drive.drive_status(Path("/dev/sr0")) == "disc"
    monkeypatch.setattr(drive, "_ioctl", lambda *a: 2)
    assert drive.drive_status(Path("/dev/sr0")) == "open"


def test_drive_status_no_medium_open(monkeypatch):
    def deny(*a):
        raise OSError(errno.ENOMEDIUM, "No medium found")

    monkeypatch.setattr(drive.os, "open", deny)
    assert drive.drive_status(Path("/dev/sr0")) == "empty"


def test_drive_status_fallback(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)

    def ioctl(*a):
        raise OSError(errno.ENOTTY, "no ioctl")

    monkeypatch.setattr(drive, "_ioctl", ioctl)
    monkeypatch.setattr(drive, "run", lambda *a, **k: completed("123\n"))
    assert drive.drive_status(Path("/dev/sr0")) == "disc"


def test_safe_name():
    name = drive.safe_disc_name("../../Vakantie Foto's!", "id")
    assert ".." not in name and "/" not in name and "'" not in name
    assert name.startswith("Vakantie-Foto-s-")
    assert drive.safe_disc_name("", "id").startswith("CD-DVD-")


def test_mount_error_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(drive, "run", lambda *a, **k: completed("", "permission denied", 1))
    info = drive.DiscInfo("id", "TEST", "udf", 100)
    with pytest.raises(drive.DriveError, match="SYS_ADMIN"):
        drive.mount_disc(Path("/dev/sr0"), tmp_path / "disc", info, 1024, 100)


def test_close_tray_ioctl(monkeypatch):
    calls = []
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)
    monkeypatch.setattr(drive, "_ioctl", lambda fd, req, arg: calls.append(req) or 0)
    drive.close_tray(Path("/dev/sr0"))
    assert calls == [drive.CDROMCLOSETRAY]


def test_close_tray_fallback(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)

    def deny(*a):
        raise OSError(errno.ENOTTY, "no ioctl")

    monkeypatch.setattr(drive, "_ioctl", deny)
    calls = []
    monkeypatch.setattr(drive, "run", lambda args, timeout=20: calls.append(args) or completed())
    drive.close_tray(Path("/dev/sr0"))
    assert calls == [["eject", "-t", "/dev/sr0"]]


def test_close_tray_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)
    monkeypatch.setattr(drive, "_ioctl", lambda *a: (_ for _ in ()).throw(OSError("deny")))
    monkeypatch.setattr(drive, "run", lambda *a, **k: completed("", "failed", 1))
    with pytest.raises(drive.DriveError, match="fysieke knop"):
        drive.close_tray(Path("/dev/sr0"))


@pytest.fixture()
def control_ioctl(monkeypatch):
    calls = []
    monkeypatch.setattr(
        drive.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 7
    )
    monkeypatch.setattr(drive.os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(
        drive, "_ioctl", lambda fd, req, arg: calls.append(("ioctl", fd, req, arg)) or 0
    )
    return calls


def test_spin_down_and_up_use_readonly_nonblock_ioctl(control_ioctl):
    drive.spin_down(Path("/dev/sr0"))
    drive.spin_up(Path("/dev/sr0"))
    assert control_ioctl == [
        ("open", Path("/dev/sr0"), drive.os.O_RDONLY | drive.os.O_NONBLOCK),
        ("ioctl", 7, drive.CDROMSTOP, 0),
        ("close", 7),
        ("open", Path("/dev/sr0"), drive.os.O_RDONLY | drive.os.O_NONBLOCK),
        ("ioctl", 7, drive.CDROMSTART, 0),
        ("close", 7),
    ]


@pytest.mark.parametrize("speed", [0, 1, 8, 24])
def test_set_speed_requests_linux_cdrom_units(control_ioctl, speed):
    drive.set_speed(Path("/dev/sr0"), speed)
    assert control_ioctl[1] == ("ioctl", 7, drive.CDROM_SELECT_SPEED, speed)
    assert control_ioctl[-1] == ("close", 7)


@pytest.mark.parametrize("speed", [-1, 25, True, "8", 1.5])
def test_set_speed_rejects_invalid_values(speed):
    with pytest.raises(ValueError):
        drive.set_speed(Path("/dev/sr0"), speed)


@pytest.mark.parametrize("code", [errno.ENOSYS, errno.ENOTTY, errno.EINVAL, errno.EOPNOTSUPP])
def test_unsupported_control_is_actionable_not_fatal(monkeypatch, code):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    closed = []
    monkeypatch.setattr(drive.os, "close", closed.append)

    def deny(*a):
        raise OSError(code, "not supported")

    monkeypatch.setattr(drive, "_ioctl", deny)
    with pytest.raises(drive.DriveUnsupportedError, match="ondersteunt"):
        drive.spin_down(Path("/dev/sr0"))
    assert closed == [7]


def test_control_io_error_keeps_hardware_hint(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)

    def deny(*a):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(drive, "_ioctl", deny)
    with pytest.raises(drive.DriveError, match="usb-kabel"):
        drive.set_speed(Path("/dev/sr0"), 4)


def test_control_open_failure_is_actionable(monkeypatch):
    def deny(*a):
        raise OSError(errno.ENOENT, "No such file")

    monkeypatch.setattr(drive.os, "open", deny)
    with pytest.raises(drive.DriveError, match="Kan /dev/sr0 niet openen"):
        drive.spin_up(Path("/dev/sr0"))


def test_inspect_disc_recognizes_css_sense(monkeypatch):
    monkeypatch.setattr(
        drive,
        "run",
        lambda *a, **k: completed("", "Sense Key : 0x5 ASC=0x6f ASCQ=0x3", 2),
    )
    with pytest.raises(drive.DriveProtectedError, match="CSS-beveiligd"):
        drive.inspect_disc(Path("/dev/sr0"))


def test_mount_recognizes_css_sense(monkeypatch, tmp_path):
    monkeypatch.setattr(
        drive, "run", lambda *a, **k: completed("", "Sense Key 0x5 ASC=6f ASCQ=03", 1)
    )
    info = drive.DiscInfo("id", "TEST", "udf", 100)
    with pytest.raises(drive.DriveProtectedError, match="CSS-beveiligd"):
        drive.mount_disc(Path("/dev/sr0"), tmp_path / "disc", info, 1000, 100)
