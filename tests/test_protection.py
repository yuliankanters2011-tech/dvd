import errno
from pathlib import Path

import pytest

from app import drive, protection


@pytest.mark.parametrize(
    "text",
    [
        "Sense Key : 0x5 Current [descriptor] ASC=0x6f ASCQ=0x3",
        "sr 0:0:0:0: [sr0] tag#0 Sense Key : 0x5 [current] ASC=0x6f ASCQ=0x3",
        "Sense Key 0x5, ASC=6f, ASCQ=03",
    ],
)
def test_recognizes_scrambled_sector_sense(text):
    assert protection.is_css_sense(text)


@pytest.mark.parametrize(
    "text",
    [
        "Input/output error",
        "Sense Key : 0x3 ASC=0x11 ASCQ=0x0",
        "Sense Key : 0x5 ASC=0x6f ASCQ=0x4",
        "Sense Key : 0x5 ASC=0x21 ASCQ=0x3",
    ],
)
def test_does_not_guess_protection_from_other_errors(text):
    assert not protection.is_css_sense(text)


def test_wrapped_sense_and_notice():
    inner = OSError(errno.EIO, "Sense Key 0x5 ASC=0x6f ASCQ=0x3")
    outer = RuntimeError("Kan bron niet lezen")
    outer.__cause__ = inner
    assert protection.is_css_error(outer)
    assert protection.is_css_error(ValueError(protection.CSS_NOTICE))
    assert not protection.is_css_error(OSError(errno.EIO, "Input/output error"))


def test_copyright_query_is_readonly_and_never_authenticates(monkeypatch):
    calls = []
    monkeypatch.setattr(
        drive.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 7
    )
    monkeypatch.setattr(drive.os, "close", lambda fd: calls.append(("close", fd)))

    def ioctl(fd, request, data, mutate):
        calls.append(("ioctl", fd, request, len(data), bytes(data[:2]), mutate))
        data[2] = b"\x01"
        return 0

    monkeypatch.setattr(drive.fcntl, "ioctl", ioctl)
    assert drive.copyright_protection(Path("/dev/sr0")) == 1
    assert calls == [
        ("open", Path("/dev/sr0"), drive.os.O_RDONLY | drive.os.O_NONBLOCK),
        ("ioctl", 7, drive.DVD_READ_STRUCT, 2056, b"\x01\x00", True),
        ("close", 7),
    ]


def test_copyright_query_unsupported_returns_none(monkeypatch):
    monkeypatch.setattr(drive.os, "open", lambda *a: 7)
    monkeypatch.setattr(drive.os, "close", lambda fd: None)

    def deny(*a):
        raise OSError(errno.ENOTTY, "not supported")

    monkeypatch.setattr(drive.fcntl, "ioctl", deny)
    assert drive.copyright_protection(Path("/dev/sr0")) is None
