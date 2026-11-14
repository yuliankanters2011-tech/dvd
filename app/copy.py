"""Optional, non-destructive import of disc files to a writable NAS folder."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .drive import DiscInfo, safe_disc_name

LOG = logging.getLogger("dvd.copy")
CHUNK = 1024 * 1024
MARKER = ".dvd-bridge-import.json"


class CopyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CopyResult:
    destination: Path
    files: int
    bytes_copied: int
    skipped: int


Progress = Callable[[int, int, int], None]


def tree_size(root: Path) -> tuple[int, int]:
    """Count only regular files; never follow a symlink off the disc."""
    total = 0
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
                count += 1
    return total, count


def _owned(path: Path, puid: int, pgid: int, directory: bool = False) -> None:
    os.chmod(path, 0o755 if directory else 0o644)
    os.chown(path, puid, pgid)


def import_disc(
    source: Path,
    library: Path,
    info: DiscInfo,
    puid: int,
    pgid: int,
    stop: threading.Event,
    progress: Progress | None = None,
) -> CopyResult:
    """Copy into a hidden staging directory, then atomically publish it.

    Existing imports are never overwritten. Reinserting the same disc is a
    no-op. Removing its marker (or the entire import) permits a fresh import.
    Symlinks, devices and FIFOs on a disc are skipped, not dereferenced.
    """
    if not source.is_dir():
        raise CopyError("De schijf is niet aangekoppeld")
    library.mkdir(parents=True, exist_ok=True)
    name = safe_disc_name(info.label, info.identity)
    destination = library / name
    marker = destination / MARKER
    if destination.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if previous.get("identity") == info.identity and previous.get("complete") is True:
                return CopyResult(
                    destination,
                    int(previous.get("files", 0)),
                    int(previous.get("bytes", 0)),
                    0,
                )
        except (OSError, ValueError, TypeError):
            pass
        raise CopyError(
            f"Er bestaat al een map {name}, maar deze is geen volledige import. "
            "Verplaats die map eerst; bestaande bestanden worden niet overschreven."
        )

    total, _count = tree_size(source)
    free = shutil.disk_usage(library).free
    # Reserve 16 MiB for metadata and a small safety buffer.
    if free < total + 16 * 1024 * 1024:
        raise CopyError("Onvoldoende vrije ruimte op de NAS voor deze import")

    staging = library / f".{name}.partial-{os.getpid()}"
    if staging.exists():
        raise CopyError(f"Er staat al een onvoltooide importmap: {staging.name}")
    staging.mkdir(mode=0o755)
    copied = 0
    files = 0
    skipped = 0
    last_report = 0.0
    try:
        _owned(staging, puid, pgid, True)
        for directory, dirs, filenames in os.walk(source, followlinks=False):
            if stop.is_set():
                raise CopyError("Import afgebroken omdat de container stopt")
            relative = Path(directory).relative_to(source)
            out_dir = staging / relative
            out_dir.mkdir(mode=0o755, exist_ok=True)
            _owned(out_dir, puid, pgid, True)
            keep_dirs = []
            for dirname in dirs:
                if (Path(directory) / dirname).is_symlink():
                    skipped += 1
                else:
                    keep_dirs.append(dirname)
            dirs[:] = keep_dirs
            for filename in filenames:
                if stop.is_set():
                    raise CopyError("Import afgebroken omdat de container stopt")
                src = Path(directory) / filename
                mode = src.lstat().st_mode
                if not stat.S_ISREG(mode):
                    skipped += 1
                    continue
                dst = out_dir / filename
                size = src.stat().st_size
                if shutil.disk_usage(library).free < size + 16 * 1024 * 1024:
                    raise CopyError("De NAS is tijdens het kopiëren vol geraakt")
                with src.open("rb") as reader, dst.open("xb") as writer:
                    while chunk := reader.read(CHUNK):
                        if stop.is_set():
                            raise CopyError("Import afgebroken omdat de container stopt")
                        writer.write(chunk)
                        copied += len(chunk)
                        now = time.monotonic()
                        if progress and now - last_report >= 0.5:
                            progress(copied, total, files)
                            last_report = now
                _owned(dst, puid, pgid)
                files += 1
        manifest = {
            "schema": 1,
            "complete": True,
            "identity": info.identity,
            "label": info.label,
            "files": files,
            "bytes": copied,
            "skipped_special_entries": skipped,
        }
        temporary_marker = staging / f"{MARKER}.tmp"
        temporary_marker.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _owned(temporary_marker, puid, pgid)
        temporary_marker.replace(staging / MARKER)
        # A second disc with the same metadata must not overwrite this import.
        if destination.exists():
            raise CopyError("De doelmap is aangemaakt tijdens het kopiëren; niets overschreven")
        os.rename(staging, destination)
        if progress:
            progress(copied, total, files)
        LOG.info("Import klaar: %s (%d bestanden)", destination, files)
        return CopyResult(destination, files, copied, skipped)
    except BaseException:
        # Only our own newly created staging directory is removed.
        try:
            shutil.rmtree(staging)
        except OSError:
            LOG.exception("Onvoltooide importmap kon niet worden opgeruimd: %s", staging)
        raise
