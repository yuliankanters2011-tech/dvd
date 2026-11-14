import json
import os
import threading

import pytest

from app.copy import MARKER, CopyCancelledError, CopyError, import_disc, tree_size
from app.drive import DiscInfo, safe_disc_name


def make_disc(tmp_path):
    source = tmp_path / "disc"
    (source / "sub").mkdir(parents=True)
    (source / "hallo.txt").write_bytes(b"hallo nas")
    (source / "sub" / "foto.bin").write_bytes(bytes(range(256)) * 4)
    (source / "link").symlink_to("/etc/passwd")
    os.mkfifo(source / "fifo")
    return source


def info():
    return DiscInfo("iso9660|uuid|label|1024", "TESTDVD", "iso9660", 1024)


def test_import_copies_regular_files_only(tmp_path):
    source = make_disc(tmp_path)
    library = tmp_path / "library"
    result = import_disc(source, library, info(), os.getuid(), os.getgid(), threading.Event())
    assert result.files == 2
    assert result.skipped == 2
    assert (result.destination / "hallo.txt").read_bytes() == b"hallo nas"
    assert (result.destination / "sub" / "foto.bin").read_bytes() == bytes(range(256)) * 4
    assert not (result.destination / "link").exists()
    manifest = json.loads((result.destination / MARKER).read_text())
    assert manifest["complete"] is True
    assert tree_size(source) == (1024 + 9, 2)


def test_import_idempotent(tmp_path):
    source = make_disc(tmp_path)
    library = tmp_path / "library"
    stop = threading.Event()
    first = import_disc(source, library, info(), os.getuid(), os.getgid(), stop)
    second = import_disc(source, library, info(), os.getuid(), os.getgid(), stop)
    assert first.destination == second.destination
    assert len(list(library.iterdir())) == 1


def test_import_does_not_overwrite_unknown_folder(tmp_path):
    source = make_disc(tmp_path)
    library = tmp_path / "library"
    existing = library / safe_disc_name(info().label, info().identity)
    existing.mkdir(parents=True)
    (existing / "belangrijk.txt").write_text("niet wissen")
    with pytest.raises(CopyError, match="bestaande bestanden"):
        import_disc(source, library, info(), os.getuid(), os.getgid(), threading.Event())
    assert (existing / "belangrijk.txt").read_text() == "niet wissen"


def test_stop_removes_staging(tmp_path):
    source = make_disc(tmp_path)
    library = tmp_path / "library"
    stop = threading.Event()
    stop.set()
    with pytest.raises(CopyCancelledError, match="geannuleerd"):
        import_disc(source, library, info(), os.getuid(), os.getgid(), stop)
    assert list(library.iterdir()) == []
