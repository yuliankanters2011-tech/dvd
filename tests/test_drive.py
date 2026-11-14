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
