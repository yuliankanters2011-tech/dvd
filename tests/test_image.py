import threading

from app.image import _mapfile_complete


def test_mapfile_complete(tmp_path):
    path = tmp_path / "test.mapfile"
    path.write_text("# comment\n0x0000 0x1000 +\n0x1000 0x1000 +\n")
    assert _mapfile_complete(path, 0x2000)
    path.write_text("0x0000 0x1000 +\n0x1000 0x1000 *\n")
    assert not _mapfile_complete(path, 0x2000)
    assert not _mapfile_complete(path, 0x1000)
    assert not _mapfile_complete(tmp_path / "missing", 1)


def test_create_iso_with_fake_ddrescue(tmp_path, monkeypatch):
    from app import image
    from app.drive import DiscInfo

    device = tmp_path / "source.iso"
    device.write_bytes(b"A" * 4096)
    info = DiscInfo("id", "TEST", "iso9660", 4096)
    library = tmp_path / "library"
    state = tmp_path / "state"

    def fake_which(name):
        return "/usr/bin/ddrescue"

    class FakeProcess:
        def __init__(self, command, **kwargs):
            # command: ddrescue -n -b 2048 device partial mapfile
            partial, mapfile = command[-2], command[-1]
            with open(partial, "wb") as out:
                out.write(device.read_bytes())
            with open(mapfile, "w") as out:
                out.write("0x0000 0x1000 +\n")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(image.shutil, "which", fake_which)
    monkeypatch.setattr(image.subprocess, "Popen", FakeProcess)
    result = image.create_iso(device, library, state, info, threading.Event())
    assert result.path.read_bytes() == b"A" * 4096
    assert result.path.name.endswith(".iso")
    assert not list(library.glob(".*partial*"))
    assert not list(state.glob("*.mapfile"))
    again = image.create_iso(device, library, state, info, threading.Event())
    assert again.path == result.path
