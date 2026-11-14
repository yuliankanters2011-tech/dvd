"""Create a mountable ISO image from a data disc, without overwriting files."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .drive import DiscInfo, safe_disc_name

LOG = logging.getLogger("dvd.image")
MARKER_SUFFIX = ".dvd-bridge.json"


class ImageError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageResult:
    path: Path
    size_bytes: int


Progress = Callable[[int, int], None]


def _mapfile_complete(path: Path, expected_size: int) -> bool:
    """GNU ddrescue can exit 0 with unreadable blocks; inspect the mapfile."""
    covered = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 3:
                return False
            _pos, size, status = parts
            if status != "+":
                return False
            covered += int(size, 0)
    except (OSError, ValueError):
        return False
    return covered == expected_size


def create_iso(
    device: Path,
    library: Path,
    state_dir: Path,
    info: DiscInfo,
    stop: threading.Event,
    progress: Progress | None = None,
) -> ImageResult:
    """Use GNU ddrescue's first pass; stop on read errors rather than hide them.

    The completed image can be mounted by DSM File Station as a virtual drive.
    It is still read-only, and copy protection is not removed.
    """
    if info.size_bytes <= 0:
        raise ImageError("De grootte van de schijf is niet bekend")
    if shutil.which("ddrescue") is None:
        raise ImageError("ddrescue ontbreekt in de containerimage")
    library.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    name = safe_disc_name(info.label, info.identity)
    destination = library / f"{name}.iso"
    marker = library / f"{name}{MARKER_SUFFIX}"
    if destination.exists() or marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if (
                previous.get("identity") == info.identity
                and previous.get("complete") is True
                and destination.stat().st_size == info.size_bytes
            ):
                return ImageResult(destination, info.size_bytes)
        except (OSError, ValueError, TypeError):
            pass
        raise ImageError(
            f"{destination.name} bestaat al. Verplaats het bestand en het "
            "bijbehorende .json-bestand eerst; er wordt niets overschreven."
        )
    free = shutil.disk_usage(library).free
    if free < info.size_bytes + 32 * 1024 * 1024:
        raise ImageError("Onvoldoende vrije ruimte op de NAS voor een ISO-bestand")

    partial = library / f".{name}.iso.partial-{os.getpid()}"
    mapfile = state_dir / f"{name}.mapfile"
    log_path = state_dir / f"{name}.ddrescue.log"
    if partial.exists() or mapfile.exists():
        raise ImageError(
            "Er staat al een onvoltooide ISO-sessie. Verplaats het verborgen "
            ".partial-bestand in library en de .mapfile in de state-volume eerst."
        )
    command = ["ddrescue", "-n", "-b", "2048", str(device), str(partial), str(mapfile)]
    LOG.info("ISO maken van %s naar %s", device, destination)
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command, stdout=log_file, stderr=subprocess.STDOUT, text=True
            )
            last_report = 0.0
            while process.poll() is None:
                if stop.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise ImageError("ISO maken afgebroken omdat de container stopt")
                now = time.monotonic()
                if progress and now - last_report >= 1:
                    try:
                        done = partial.stat().st_size
                    except OSError:
                        done = 0
                    progress(min(done, info.size_bytes), info.size_bytes)
                    last_report = now
                stop.wait(0.5)
            returncode = process.returncode
        if returncode != 0:
            raise ImageError(
                f"ISO maken mislukt (ddrescue exit {returncode}). De schijf kan "
                f"beschadigd of beveiligd zijn. Details: {log_path.name} in de state-volume."
            )
        actual = partial.stat().st_size
        if actual != info.size_bytes or not _mapfile_complete(mapfile, info.size_bytes):
            raise ImageError(
                f"ISO is niet volledig: {actual} van {info.size_bytes} bytes gelezen. "
                "Er zijn mogelijk onleesbare blokken; het partiele bestand is niet bewaard."
            )
        manifest = {
            "schema": 1,
            "complete": True,
            "identity": info.identity,
            "label": info.label,
            "bytes": actual,
        }
        temporary = library / f".{name}.manifest-{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if destination.exists() or marker.exists():
            temporary.unlink(missing_ok=True)
            raise ImageError("Het doelbestand is intussen aangemaakt; niets overschreven")
        os.rename(partial, destination)
        os.rename(temporary, marker)
        mapfile.unlink(missing_ok=True)
        if progress:
            progress(actual, actual)
        LOG.info("ISO klaar: %s (%d bytes)", destination, actual)
        return ImageResult(destination, actual)
    except BaseException:
        partial.unlink(missing_ok=True)
        # This first-pass implementation does not resume sessions. Keep only
        # the log so a retry is not blocked by an orphaned mapfile.
        mapfile.unlink(missing_ok=True)
        raise
