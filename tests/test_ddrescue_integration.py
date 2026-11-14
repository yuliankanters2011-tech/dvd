"""Tests with the real ddrescue binary; no optical drive is required."""

import json
import shutil
import subprocess
import threading

import pytest

from app import image
from app.drive import DiscInfo, safe_disc_name

SIZE = 64 * 1024


@pytest.fixture()
def ddrescue():
    binary = shutil.which("ddrescue")
    if binary is None:
        pytest.skip("ddrescue is nodig voor deze integratietest")
    return binary


@pytest.fixture()
def disc(tmp_path):
    source = tmp_path / "source.iso"
    data = bytes(range(256)) * (SIZE // 256)
    source.write_bytes(data)
    library = tmp_path / "library"
    state = tmp_path / "state"
    library.mkdir()
    state.mkdir()
    info = DiscInfo("real-ddrescue-test", "TEST", "iso9660", SIZE)
    return source, library, state, info, data


def session_paths(library, state, info):
    name = safe_disc_name(info.label, info.identity)
    return library / f".{name}.iso.partial", state / f"{name}.mapfile"


def run_ddrescue(binary, source, partial, mapfile, *options):
    subprocess.run(
        [binary, "-n", "-b", str(image.SECTOR), *options, str(source), str(partial), str(mapfile)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_real_ddrescue_publishes_complete_iso(disc, ddrescue):
    source, library, state, info, data = disc
    result = image.create_iso(source, library, state, info, threading.Event())
    assert result.complete is True
    assert result.missing_bytes == 0
    assert result.path.read_bytes() == data
    partial, mapfile = session_paths(library, state, info)
    assert not partial.exists() and not mapfile.exists()
    marker = library / f"{result.path.stem}{image.MARKER_SUFFIX}"
    assert json.loads(marker.read_text())["complete"] is True


def test_real_ddrescue_partial_session_resumes(disc, ddrescue):
    source, library, state, info, data = disc
    partial, mapfile = session_paths(library, state, info)
    run_ddrescue(ddrescue, source, partial, mapfile, "--size=32768")
    assert partial.stat().st_size == SIZE // 2
    assert image._mapfile_stats(mapfile, SIZE) == (SIZE // 2, SIZE // 2)

    result = image.create_iso(source, library, state, info, threading.Event())
    assert result.complete is True
    assert result.path.read_bytes() == data
    assert not partial.exists() and not mapfile.exists()


def test_real_ddrescue_finished_session_reuses_existing_partial(disc, ddrescue):
    source, library, state, info, data = disc
    partial, mapfile = session_paths(library, state, info)
    run_ddrescue(ddrescue, source, partial, mapfile)
    assert image._mapfile_complete(mapfile, SIZE)
    inode = partial.stat().st_ino
    # Change the test source after rescuing. Publishing must use the partial,
    # not create a new image from the (now different) source contents.
    source.write_bytes(b"\x00" * SIZE)

    result = image.create_iso(source, library, state, info, threading.Event())
    assert result.complete is True
    assert result.path.read_bytes() == data
    assert result.path.stat().st_ino == inode
    assert not partial.exists() and not mapfile.exists()
