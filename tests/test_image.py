import json
import threading

import pytest

from app import image
from app.drive import DiscInfo, safe_disc_name


def make_disc(tmp_path, size=4096):
    device = tmp_path / "source.iso"
    device.write_bytes(b"A" * size)
    info = DiscInfo("id", "TEST", "iso9660", size)
    return device, info, tmp_path / "library", tmp_path / "state"


def fake_ddrescue(monkeypatch, device, *, missing=0, returncode=0, truncate=0, sparse=False):
    """Fake GNU ddrescue: write a mapfile with an optional unreadable tail."""
    monkeypatch.setattr(image.shutil, "which", lambda name: "/usr/bin/ddrescue")
    calls = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            calls.append(command)
            partial, mapfile = command[-2], command[-1]
            data = bytearray(device.read_bytes())
            if truncate:
                data = data[:-truncate]
            good = len(data) - missing
            if missing:
                data[good:] = b"\x00" * missing
            with open(partial, "wb") as out:
                if sparse and missing:
                    out.write(data[:good])
                    out.truncate(len(data))
                else:
                    out.write(data)
            lines = []
            if good > 0:
                lines.append(f"0x0000 0x{good:x} +\n")
            if missing > 0:
                lines.append(f"0x{good:x} 0x{missing:x} *\n")
            if truncate:
                lines.append(f"0x{len(data):x} 0x{truncate:x} ?\n")
            with open(mapfile, "w") as out:
                out.write("".join(lines))
            self.returncode = returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image.subprocess, "Popen", FakeProcess)
    return calls


def test_mapfile_stats_and_complete(tmp_path):
    path = tmp_path / "test.mapfile"
    path.write_text("# comment\n0x0000 0x1000 +\n0x1000 0x1000 +\n")
    assert image._mapfile_stats(path, 0x2000) == (0x2000, 0)
    assert image._mapfile_complete(path, 0x2000)
    path.write_text("0x0000 0x1000 +\n0x1000 0x1000 *\n")
    assert image._mapfile_stats(path, 0x2000) == (0x1000, 0x1000)
    assert not image._mapfile_complete(path, 0x2000)
    assert not image._mapfile_complete(path, 0x1000)
    assert not image._mapfile_complete(tmp_path / "missing", 1)


@pytest.mark.parametrize(
    "text",
    [
        "0x0000 0x1000 +\n0x0000 0x1000 +\n",  # overlap
        "0x0000 0x0800 +\n0x1000 0x0800 +\n",  # gap
        "0x0000 0x1000 X\n",  # unknown status
        "0x0000 0x2000 +\n",  # past end
        "niet een getal\n",
    ],
)
def test_mapfile_rejects_unsafe_coverage(tmp_path, text):
    path = tmp_path / "bad.mapfile"
    path.write_text(text)
    with pytest.raises(image.ImageError):
        image._mapfile_stats(path, 0x1000)


def test_mapfile_accepts_all_ddrescue_statuses(tmp_path):
    path = tmp_path / "statuses.mapfile"
    path.write_text(
        "0x0000 0x0200 +\n0x0200 0x0200 *\n0x0400 0x0200 /\n0x0600 0x0200 -\n0x0800 0x0800 ?\n"
    )
    assert image._mapfile_stats(path, 0x1000) == (0x200, 0xE00)


def test_create_complete_iso_and_idempotent(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path)
    calls = fake_ddrescue(monkeypatch, device)
    result = image.create_iso(device, library, state, info, threading.Event())
    assert result.path.read_bytes() == b"A" * 4096
    assert result.complete is True and result.missing_bytes == 0
    assert not list(library.glob(".*partial*"))
    assert not list(state.glob("*.mapfile"))
    manifest = json.loads((library / f"{result.path.stem}{image.MARKER_SUFFIX}").read_text())
    assert manifest["complete"] is True and manifest["partial"] is False
    again = image.create_iso(device, library, state, info, threading.Event())
    assert again.path == result.path
    assert len(calls) == 1


def test_small_loss_is_saved_and_marked_partial(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path, size=10000)
    fake_ddrescue(monkeypatch, device, missing=40, sparse=True)  # 0.4% <= default 0.5%
    result = image.create_iso(device, library, state, info, threading.Event())
    assert result.complete is False and result.missing_bytes == 40
    assert result.path.stat().st_size == 10000
    assert result.path.read_bytes()[-40:] == b"\x00" * 40
    manifest = json.loads((library / f"{result.path.stem}{image.MARKER_SUFFIX}").read_text())
    assert manifest["partial"] is True and manifest["missing_bytes"] == 40
    assert manifest["forced"] is False


def test_heavy_loss_requires_explicit_force_and_keeps_session(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path, size=10000)
    fake_ddrescue(monkeypatch, device, missing=2000)  # 20%
    with pytest.raises(image.ImageIncompleteError) as exc:
        image.create_iso(device, library, state, info, threading.Event())
    assert exc.value.missing_bytes == 2000
    name = safe_disc_name(info.label, info.identity)
    partial = library / f".{name}.iso.partial"
    mapfile = state / f"{name}.mapfile"
    assert partial.exists() and mapfile.exists()
    assert not (library / f"{name}.iso").exists()

    result = image.create_iso(device, library, state, info, threading.Event(), force=True)
    assert result.complete is False and result.forced is True
    assert result.missing_bytes == 2000
    manifest = json.loads((library / f"{name}{image.MARKER_SUFFIX}").read_text())
    assert manifest["forced"] is True and manifest["partial"] is True
    assert not partial.exists() and not mapfile.exists()


def test_force_never_overwrites_existing_iso(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path)
    fake_ddrescue(monkeypatch, device, missing=2000)
    with pytest.raises(image.ImageIncompleteError):
        image.create_iso(device, library, state, info, threading.Event())
    name = safe_disc_name(info.label, info.identity)
    existing = library / f"{name}.iso"
    existing.write_bytes(b"bestaand")
    with pytest.raises(image.ImageError, match="bestaat al"):
        image.create_iso(device, library, state, info, threading.Event(), force=True)
    assert existing.read_bytes() == b"bestaand"


def test_cancel_keeps_partial_and_resume_completes(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path)
    monkeypatch.setattr(image.shutil, "which", lambda name: "/usr/bin/ddrescue")
    stop = threading.Event()
    name = safe_disc_name(info.label, info.identity)
    partial = library / f".{name}.iso.partial"
    mapfile = state / f"{name}.mapfile"

    class RunningProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            library.mkdir(parents=True, exist_ok=True)
            state.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(b"A" * 2048)
            mapfile.write_text("0x0000 0x0800 +\n0x0800 0x0800 ?\n")
            self.pid = 999999
            stop.set()

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image.subprocess, "Popen", RunningProcess)
    terminated = []
    monkeypatch.setattr(image, "_terminate", lambda process: terminated.append(process))
    with pytest.raises(image.ImageCancelledError) as exc:
        image.create_iso(device, library, state, info, stop)
    assert exc.value.resumable is True
    assert terminated and partial.exists() and mapfile.exists()

    stop.clear()
    fake_ddrescue(monkeypatch, device)
    result = image.create_iso(device, library, state, info, stop)
    assert result.complete is True
    assert not partial.exists() and not mapfile.exists()


def test_ddrescue_failure_keeps_session(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path)
    fake_ddrescue(monkeypatch, device, returncode=1)
    with pytest.raises(image.ImageError, match="ddrescue exit 1"):
        image.create_iso(device, library, state, info, threading.Event())
    name = safe_disc_name(info.label, info.identity)
    assert (library / f".{name}.iso.partial").exists()
    assert (state / f"{name}.mapfile").exists()


def test_truncated_file_is_not_published(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path, size=10000)
    fake_ddrescue(monkeypatch, device, truncate=40)
    with pytest.raises(image.ImageError, match="onvolledig"):
        image.create_iso(device, library, state, info, threading.Event())
    name = safe_disc_name(info.label, info.identity)
    assert not (library / f"{name}.iso").exists()
    assert (library / f".{name}.iso.partial").exists()


@pytest.mark.parametrize("force", [False, True])
def test_no_readable_data_never_published_even_forced(tmp_path, monkeypatch, force):
    device, info, library, state = make_disc(tmp_path, size=4096)
    fake_ddrescue(monkeypatch, device, missing=4096)
    with pytest.raises(image.ImageError, match="geen leesbaar blok"):
        image.create_iso(device, library, state, info, threading.Event(), force=force)
    name = safe_disc_name(info.label, info.identity)
    assert not (library / f"{name}.iso").exists()


def test_saved_partial_iso_is_idempotent(tmp_path, monkeypatch):
    device, info, library, state = make_disc(tmp_path, size=10000)
    calls = fake_ddrescue(monkeypatch, device, missing=40)
    first = image.create_iso(device, library, state, info, threading.Event())
    again = image.create_iso(device, library, state, info, threading.Event())
    assert again == first
    assert len(calls) == 1


def test_invalid_tolerance_rejected(tmp_path):
    device, info, library, state = make_disc(tmp_path)
    with pytest.raises(image.ImageError, match="ISO_MAX_MISSING_PERCENT"):
        image.create_iso(device, library, state, info, threading.Event(), max_missing_percent=101)
