"""Create a mountable ISO image from a data disc, without overwriting files.

Small amounts of unreadable data can be tolerated automatically. Heavily
damaged discs pause for an explicit choice: the user can still save a clearly
marked damaged ISO, or cancel. Cancelled/interrupted sessions keep their hidden
partial file and ddrescue mapfile so a later attempt with the same disc can
resume.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .drive import DiscInfo, safe_disc_name

LOG = logging.getLogger("dvd.image")
MARKER_SUFFIX = ".dvd-bridge.json"
DEFAULT_MAX_MISSING_PERCENT = 0.5
SECTOR = 2048


class ImageError(RuntimeError):
    pass


class ImageCancelledError(ImageError):
    """The user/service stopped ISO creation; resumable files are kept."""

    def __init__(self, message: str, *, resumable: bool = False) -> None:
        super().__init__(message)
        self.resumable = resumable


class ImageIncompleteError(ImageError):
    """Too much data is missing; the dashboard must ask before publishing."""

    def __init__(
        self,
        message: str,
        *,
        missing_bytes: int,
        expected_bytes: int,
        rescued_bytes: int,
        allowed_bytes: int,
    ) -> None:
        super().__init__(message)
        self.missing_bytes = missing_bytes
        self.expected_bytes = expected_bytes
        self.rescued_bytes = rescued_bytes
        self.allowed_bytes = allowed_bytes


@dataclass(frozen=True)
class ImageResult:
    path: Path
    size_bytes: int
    missing_bytes: int = 0
    expected_bytes: int = 0
    complete: bool = True
    forced: bool = False


Progress = Callable[[int, int], None]


def _mapfile_stats(path: Path, expected_size: int) -> tuple[int, int]:
    """Return (rescued_bytes, missing_bytes) from a GNU ddrescue mapfile.

    Mapfile statuses: '+' finished, '*' non-trimmed, '/' non-scraped,
    '-' bad-sector, '?' non-tried. Anything not finished counts as missing.
    The mapfile must cover the whole input exactly once; otherwise it is not
    safe to publish an ISO.
    """
    covered = 0
    rescued = 0
    blocks: list[tuple[int, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ImageError(f"Kan het ddrescue-kaartbestand niet lezen: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ImageError("Het ddrescue-kaartbestand is beschadigd")
        pos_text, size_text, status = parts
        try:
            pos = int(pos_text, 0)
            size = int(size_text, 0)
        except ValueError as exc:
            raise ImageError("Het ddrescue-kaartbestand bevat ongeldige getallen") from exc
        if pos < 0 or size < 0 or pos + size > expected_size:
            raise ImageError("Het ddrescue-kaartbestand valt buiten de schijfgrootte")
        if status not in {"+", "-", "*", "/", "?"}:
            raise ImageError("Het ddrescue-kaartbestand bevat een onbekende status")
        blocks.append((pos, size, status))
    blocks.sort(key=lambda block: block[0])
    for pos, size, status in blocks:
        if pos != covered:
            raise ImageError("Het ddrescue-kaartbestand bevat een gat of overlapping")
        covered += size
        if status == "+":
            rescued += size
    if covered != expected_size:
        raise ImageError(
            f"Het ddrescue-kaartbestand dekt niet de hele schijf: "
            f"{covered} van {expected_size} bytes"
        )
    return rescued, expected_size - rescued


def _mapfile_complete(path: Path, expected_size: int) -> bool:
    """GNU ddrescue can exit 0 with unreadable blocks; inspect the mapfile."""
    try:
        _rescued, missing = _mapfile_stats(path, expected_size)
    except ImageError:
        return False
    return missing == 0


def _mapfile_rescued(path: Path, expected_size: int) -> int | None:
    try:
        rescued, _missing = _mapfile_stats(path, expected_size)
    except ImageError:
        return None
    return rescued


def _allowed_missing(expected_size: int, max_missing_percent: float) -> int:
    return int(expected_size * max_missing_percent / 100)


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait(timeout=5)


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    if destination.exists():
        raise ImageError(f"{destination.name} bestaat al; er wordt niets overschreven")
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ImageError(f"{destination.name} bestaat al; er wordt niets overschreven") from exc


def create_iso(
    device: Path,
    library: Path,
    state_dir: Path,
    info: DiscInfo,
    stop: threading.Event,
    progress: Progress | None = None,
    max_missing_percent: float = DEFAULT_MAX_MISSING_PERCENT,
    force: bool = False,
) -> ImageResult:
    """Use GNU ddrescue and publish complete or damaged ISO files.

    A lightly damaged ISO is published automatically. A heavily damaged ISO
    raises ImageIncompleteError unless force=True, after the user explicitly
    chooses to continue. Existing files are never overwritten. Cancelled or
    interrupted sessions keep their partial file/mapfile for resume.
    """
    if info.size_bytes <= 0:
        raise ImageError("De grootte van de schijf is niet bekend")
    if not 0 <= max_missing_percent <= 100:
        raise ImageError("ISO_MAX_MISSING_PERCENT moet tussen 0 en 100 liggen")
    if shutil.which("ddrescue") is None:
        raise ImageError("ddrescue ontbreekt in de containerimage")
    library.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    name = safe_disc_name(info.label, info.identity)
    expected = info.size_bytes
    destination = library / f"{name}.iso"
    marker = library / f"{name}{MARKER_SUFFIX}"
    if destination.exists() or marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if (
                isinstance(previous, dict)
                and previous.get("identity") == info.identity
                and destination.stat().st_size == expected
                and previous.get("expected_bytes", expected) == expected
            ):
                missing = previous.get("missing_bytes", 0)
                if previous.get("complete") is True and type(missing) is int and missing == 0:
                    return ImageResult(destination, expected, 0, expected, True, False)
                if (
                    previous.get("complete") is False
                    and previous.get("partial") is True
                    and type(missing) is int
                    and 0 < missing <= expected
                ):
                    return ImageResult(
                        destination,
                        expected,
                        missing,
                        expected,
                        False,
                        previous.get("forced") is True,
                    )
        except (OSError, ValueError, TypeError):
            pass
        raise ImageError(
            f"{destination.name} bestaat al. Verplaats het bestand en het "
            "bijbehorende .json-bestand eerst; er wordt niets overschreven."
        )

    partial = library / f".{name}.iso.partial"
    mapfile = state_dir / f"{name}.mapfile"
    log_path = state_dir / f"{name}.ddrescue.log"
    if partial.is_symlink() or mapfile.is_symlink():
        raise ImageError("Een onvoltooide ISO-sessie bevat een symlink; verplaats die eerst")
    if partial.exists() or mapfile.exists():
        if not (partial.exists() and mapfile.exists()):
            raise ImageError(
                "Er staat een onvoltooide ISO-sessie zonder compleet paar. Verplaats het "
                "verborgen .partial-bestand in library en de .mapfile in de state-volume eerst."
            )
        if partial.stat().st_size > expected:
            raise ImageError("Het onvoltooide ISO-bestand is groter dan de schijf")
        LOG.info("Onvoltooide ISO-sessie gevonden; hervatten: %s", partial)

    existing = partial.stat().st_size if partial.exists() else 0
    free = shutil.disk_usage(library).free
    if free < max(0, expected - existing) + 32 * 1024 * 1024:
        raise ImageError("Onvoldoende vrije ruimte op de NAS voor een ISO-bestand")
    if stop.is_set():
        raise ImageCancelledError("ISO maken geannuleerd")

    command = ["ddrescue", "-n", "-b", str(SECTOR), str(device), str(partial), str(mapfile)]
    LOG.info("ISO maken van %s naar %s%s", device, destination, " (geforceerd)" if force else "")
    process: subprocess.Popen | None = None
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            last_report = 0.0
            while process.poll() is None:
                if stop.is_set():
                    _terminate(process)
                    raise ImageCancelledError(
                        "ISO maken geannuleerd. Het deelresultaat is bewaard; "
                        "klik opnieuw op ISO maken om te hervatten.",
                        resumable=partial.exists() and mapfile.exists(),
                    )
                now = time.monotonic()
                if progress and now - last_report >= 1:
                    rescued = _mapfile_rescued(mapfile, expected)
                    if rescued is None:
                        try:
                            rescued = partial.stat().st_size
                        except OSError:
                            rescued = 0
                    progress(min(rescued, expected), expected)
                    last_report = now
                stop.wait(0.5)
            returncode = process.returncode
        if stop.is_set():
            raise ImageCancelledError(
                "ISO maken geannuleerd. Het deelresultaat is bewaard; "
                "klik opnieuw op ISO maken om te hervatten.",
                resumable=partial.exists() and mapfile.exists(),
            )
        if returncode != 0:
            raise ImageError(
                f"ISO maken mislukt (ddrescue exit {returncode}). De schijf kan "
                f"beschadigd of beveiligd zijn. Details: {log_path.name} in de state-volume. "
                "Een deelresultaat blijft bewaard om later te hervatten."
            )
        rescued, missing = _mapfile_stats(mapfile, expected)
        actual = partial.stat().st_size
        if actual > expected:
            raise ImageError("Het ISO-bestand is groter dan de schijf; niets gepubliceerd")
        if actual < expected:
            raise ImageError(
                f"Het ISO-bestand is onvolledig: {actual} van {expected} bytes. "
                "Het deelresultaat en de mapfile zijn bewaard; probeer opnieuw om te hervatten."
            )
        if missing > 0 and rescued <= 0:
            raise ImageError("Er is geen leesbaar blok gevonden; geen ISO opgeslagen")
        allowed = expected if force else _allowed_missing(expected, max_missing_percent)
        if missing > allowed:
            raise ImageIncompleteError(
                f"ISO is sterk beschadigd: {missing} van {expected} bytes onleesbaar "
                f"({missing * 100 / expected:.2f}%). Automatisch maximum: {allowed} bytes. "
                "Het deelresultaat is bewaard. Kies 'Toch doorgaan' om een beschadigde "
                "ISO te bewaren, of annuleer het opslaan.",
                missing_bytes=missing,
                expected_bytes=expected,
                rescued_bytes=rescued,
                allowed_bytes=allowed,
            )
        complete = missing == 0
        manifest = {
            "schema": 1,
            "complete": complete,
            "partial": not complete,
            "forced": bool(force and not complete),
            "identity": info.identity,
            "label": info.label,
            "bytes": actual,
            "expected_bytes": expected,
            "rescued_bytes": rescued,
            "missing_bytes": missing,
            "max_missing_percent": max_missing_percent,
        }
        temporary = library / f".{name}.manifest-{uuid.uuid4().hex[:12]}.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _publish_no_replace(partial, destination)
        try:
            _publish_no_replace(temporary, marker)
        except BaseException:
            with contextlib.suppress(OSError):
                destination.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            mapfile.unlink(missing_ok=True)
        if progress:
            progress(expected, expected)
        if complete:
            LOG.info("ISO klaar: %s (%d bytes)", destination, actual)
        else:
            LOG.warning(
                "Beschadigde ISO klaar: %s (%d bytes, %d onleesbaar, geforceerd=%s)",
                destination,
                actual,
                missing,
                force,
            )
        return ImageResult(
            destination, actual, missing, expected, complete, bool(force and not complete)
        )
    except BaseException:
        if process is not None:
            _terminate(process)
        # Keep partial/mapfile for resume. Only a race after publication is
        # rolled back above; hidden session files are intentionally preserved.
        raise
