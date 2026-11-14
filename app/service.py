"""Single-worker state machine; HTTP requests never perform disc operations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, convert, copy, drive, dvd, image
from .config import Config

LOG = logging.getLogger("dvd.service")


@dataclass(frozen=True)
class ConvertJob:
    source_id: str
    title: int
    mode: str
    destination: str
    name: str = ""


@dataclass(frozen=True)
class SourceEntry:
    id: str
    location: str
    source: dvd.Source

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "location": self.location, **self.source.summary()}


@dataclass
class Snapshot:
    version: str = __version__
    state: str = "starting"
    message: str = "De speler wordt gecontroleerd…"
    device: str = "/dev/sr0"
    disc: dict[str, Any] | None = None
    nas_folder: str = "/srv/dvd/disc"
    library_folder: str = "/srv/dvd/library"
    films_folder: str = "/volume1/video/Films"
    music_folder: str = "/volume1/video/Muziek"
    copy_enabled: bool = False
    copy_state: str = "idle"
    copy_percent: int = 0
    copied_bytes: int = 0
    total_bytes: int = 0
    import_folder: str = ""
    image_state: str = "idle"
    image_percent: int = 0
    image_file: str = ""
    convert_state: str = "idle"
    convert_percent: int = 0
    convert_speed: float = 0.0
    convert_eta: int | None = None
    convert_file: str = ""
    convert_source: str = ""
    convert_destination: str = ""
    convert_mode: str = ""
    convert_error: str = ""
    convert_bytes: int = 0
    convert_total_bytes: int = 0
    error: str = ""
    updated_at: float = field(default_factory=time.time)


class Service:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop = threading.Event()
        self.commands: queue.Queue[str | ConvertJob] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._snapshot = Snapshot(
            device=str(config.device),
            nas_folder=str(config.mountpoint),
            library_folder=str(config.library_dir),
            films_folder=str(config.films_dir),
            music_folder=str(config.music_dir),
            copy_enabled=config.copy_files,
        )
        self._mounted = False
        self._disc: drive.DiscInfo | None = None
        self._handled_identity = ""
        self._copy_done = False
        self._retry_at = 0.0
        self._convert_stop = threading.Event()
        self._conversion_pending = False
        self._job_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="dvd-worker", daemon=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._snapshot)

    def update(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._snapshot, key, value)
            self._snapshot.updated_at = time.time()

    def start(self) -> None:
        self._worker.start()

    def worker_alive(self) -> bool:
        return self._worker.is_alive()

    def _busy(self) -> bool:
        current = self.snapshot()
        return (
            current["copy_state"] == "copying"
            or current["image_state"] == "working"
            or current["convert_state"] == "working"
        )

    def request(self, command: str) -> None:
        if command == "cancel":
            with self._job_lock:
                if not self._conversion_pending and self.snapshot()["convert_state"] != "working":
                    raise RuntimeError("Er is geen conversie om te annuleren")
                self._convert_stop.set()
            return
        if command not in {"eject", "close", "copy", "iso", "retry"}:
            raise ValueError("Onbekende opdracht")
        with self._job_lock:
            if self._conversion_pending or self.snapshot()["convert_state"] == "working":
                raise RuntimeError("Wacht tot het omzetten klaar is of annuleer de conversie")
            try:
                self.commands.put_nowait(command)
            except queue.Full as exc:
                raise RuntimeError("Er staan al te veel opdrachten in de wachtrij") from exc

    def request_conversion(
        self, source_id: str, title: int, mode: str, destination: str, name: str = ""
    ) -> None:
        if not isinstance(source_id, str) or not source_id or len(source_id) > 64:
            raise ValueError("Ongeldige bron")
        if type(title) is not int or not 0 <= title <= 99:
            raise ValueError("Ongeldig titelnummer")
        if mode not in convert.MODES or destination not in convert.DESTINATIONS:
            raise ValueError("Ongeldige conversie")
        if not isinstance(name, str) or len(name) > 200:
            raise ValueError("Ongeldige naam")
        with self._job_lock:
            if (
                self._conversion_pending
                or self.snapshot()["convert_state"] == "working"
                or not self.commands.empty()
            ):
                raise RuntimeError("Er wordt al omgezet")
            self._convert_stop.clear()
            try:
                self.commands.put_nowait(ConvertJob(source_id, title, mode, destination, name))
            except queue.Full as exc:
                raise RuntimeError("Er staan al te veel opdrachten in de wachtrij") from exc
            self._conversion_pending = True

    def _import_label(self, folder: Path) -> str:
        try:
            marker = json.loads((folder / copy.MARKER).read_text(encoding="utf-8"))
            label = str(marker.get("label", "")).strip()
            if label:
                return label
        except (OSError, ValueError, TypeError):
            pass
        return folder.name

    def _collect_sources(self) -> list[SourceEntry]:
        entries: list[SourceEntry] = []
        if self._mounted and self._disc is not None:
            try:
                source = dvd.inspect_source(self.config.mountpoint, label=self._disc.label)
                if source.titles:
                    entries.append(SourceEntry("disc", "schijf", source))
            except (dvd.DvdError, OSError) as exc:
                LOG.debug("Schijf is geen omzetbare DVD-Video: %s", exc)
        root = self.config.library_dir
        if root.is_dir():
            visited = 0
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                visited += 1
                if visited > 300 or len(entries) >= 100:
                    break
                current = Path(dirpath)
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not name.startswith(".")
                    and name != "@eaDir"
                    and not (current / name).is_symlink()
                ]
                if len(current.relative_to(root).parts) >= 3:
                    dirnames[:] = []
                if current.name.casefold() == "video_ts":
                    try:
                        source = dvd.inspect_source(
                            current, label=self._import_label(current.parent)
                        )
                        if source.titles:
                            ident = hashlib.sha256(str(current.resolve()).encode()).hexdigest()[:16]
                            entries.append(SourceEntry(ident, "bibliotheek", source))
                    except (dvd.DvdError, OSError) as exc:
                        LOG.debug("Overgeslagen DVD-map %s: %s", current, exc)
                    dirnames[:] = []
                    continue
                for filename in filenames:
                    path = current / filename
                    if (
                        path.suffix.lower() in dvd.VIDEO_SUFFIXES
                        and not filename.startswith(".")
                        and not path.is_symlink()
                    ):
                        try:
                            source = dvd.inspect_source(path)
                            ident = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
                            entries.append(SourceEntry(ident, "bibliotheek", source))
                        except (dvd.DvdError, OSError) as exc:
                            LOG.debug("Overgeslagen bestand %s: %s", path, exc)
                    if len(entries) >= 100:
                        break
        entries.sort(key=lambda entry: (entry.location != "schijf", entry.source.label.casefold()))
        return entries

    def media(self) -> dict[str, Any]:
        return {
            "sources": [entry.summary() for entry in self._collect_sources()],
            "destinations": {
                "films": {
                    "path": str(self.config.films_dir),
                    "ready": self.config.films_dir.is_dir(),
                },
                "music": {
                    "path": str(self.config.music_dir),
                    "ready": self.config.music_dir.is_dir(),
                },
            },
        }

    def shutdown(self) -> None:
        self.stop.set()
        self._convert_stop.set()
        self._worker.join(timeout=25)
        # The worker normally unmounts itself. This fallback is limited to the
        # single mountpoint owned by this service, never the host volume.
        if self._mounted:
            try:
                drive.unmount(self.config.mountpoint)
                drive.unlock_tray(self.config.device)
                self._mounted = False
            except drive.DriveError:
                LOG.exception("Schijf kon bij afsluiten niet worden losgekoppeld")

    def _error(self, message: str, state: str = "error") -> None:
        LOG.error("%s", message)
        self.update(state=state, message=message, error=message)
        self._retry_at = time.monotonic() + 30

    def _clear_disc(self, state: str, message: str) -> None:
        if self._mounted:
            drive.unmount(self.config.mountpoint)
            drive.unlock_tray(self.config.device)
            self._mounted = False
        self._disc = None
        self._handled_identity = ""
        self._copy_done = False
        self._retry_at = 0
        self.update(
            state=state,
            message=message,
            disc=None,
            error="",
            copy_state="idle",
            copy_percent=0,
            copied_bytes=0,
            total_bytes=0,
            import_folder="",
            image_state="idle",
            image_percent=0,
            image_file="",
        )

    def _mount(self) -> None:
        info = drive.inspect_disc(self.config.device)
        self._disc = info
        self.update(disc=asdict(info), state="mounting", message="Schijf aankoppelen…")
        drive.mount_disc(
            self.config.device,
            self.config.mountpoint,
            info,
            self.config.puid,
            self.config.pgid,
        )
        self._mounted = True
        self._retry_at = 0
        self.update(state="ready", message="Schijf klaar: open de map op je NAS.", error="")
        LOG.info(
            "Aangekoppeld: %s (%s) op %s",
            info.label,
            info.filesystem,
            self.config.mountpoint,
        )

    def _ensure_not_converting(self) -> None:
        if self.snapshot()["convert_state"] == "working":
            raise RuntimeError("Wacht tot het omzetten klaar is of annuleer de conversie")

    def _copy(self) -> None:
        self._ensure_not_converting()
        if not self._mounted or self._disc is None:
            raise copy.CopyError("Plaats eerst een leesbare schijf")
        if self._copy_done:
            return
        if drive.drive_status(self.config.device) != "disc" or drive.media_changed(
            self.config.device
        ):
            self._clear_disc("empty", "De schijf is verwijderd. Plaats een cd/dvd.")
            raise copy.CopyError("De schijf is tijdens de opdracht verwijderd")
        self.update(
            copy_state="copying",
            copy_percent=0,
            message="Bestanden naar de NAS kopiëren…",
        )

        def progress(done: int, total: int, files: int) -> None:
            self.update(
                copied_bytes=done,
                total_bytes=total,
                copy_percent=min(100, round(done * 100 / total)) if total else 100,
            )

        result = copy.import_disc(
            self.config.mountpoint,
            self.config.library_dir,
            self._disc,
            self.config.puid,
            self.config.pgid,
            self.stop,
            progress,
        )
        self._copy_done = True
        self.update(
            copy_state="done",
            copy_percent=100,
            import_folder=str(result.destination),
            copied_bytes=result.bytes_copied,
            total_bytes=result.bytes_copied,
            state="ready",
            message=f"Import klaar: {result.files} bestanden staan op de NAS.",
        )
        if self.config.auto_eject:
            self._eject()

    def _iso(self) -> None:
        self._ensure_not_converting()
        if not self._mounted or self._disc is None:
            raise image.ImageError("Plaats eerst een leesbare schijf")
        if drive.drive_status(self.config.device) != "disc" or drive.media_changed(
            self.config.device
        ):
            self._clear_disc("empty", "De schijf is verwijderd. Plaats een cd/dvd.")
            raise image.ImageError("De schijf is tijdens de opdracht verwijderd")
        self.update(
            image_state="working",
            image_percent=0,
            message="ISO-bestand maken. Dit kan even duren…",
        )

        def progress(done: int, total: int) -> None:
            self.update(image_percent=min(100, round(done * 100 / total)) if total else 0)

        result = image.create_iso(
            self.config.device,
            self.config.library_dir,
            self.config.state_dir,
            self._disc,
            self.stop,
            progress,
        )
        self.update(
            image_state="done",
            image_percent=100,
            image_file=str(result.path),
            state="ready",
            message="ISO klaar. Koppel het bestand aan in File Station.",
        )

    def _convert(self, job: ConvertJob) -> None:
        self.update(
            convert_state="working",
            convert_percent=0,
            convert_speed=0,
            convert_eta=None,
            convert_file="",
            convert_error="",
            convert_source=job.source_id,
            convert_destination="",
            convert_mode=job.mode,
            convert_bytes=0,
            convert_total_bytes=0,
            message="Conversie wordt voorbereid…",
        )
        if self._convert_stop.is_set():
            raise convert.ConvertError("Omzetten afgebroken")
        entries = {entry.id: entry for entry in self._collect_sources()}
        entry = entries.get(job.source_id)
        if entry is None:
            raise convert.ConvertError(
                "De bron is niet meer beschikbaar. Vernieuw de bronnenlijst."
            )
        live = job.source_id == "disc"
        identity = ""
        if live:
            if not self._mounted or self._disc is None:
                raise convert.ConvertError("Plaats eerst een leesbare dvd")
            if drive.drive_status(self.config.device) != "disc" or drive.media_changed(
                self.config.device
            ):
                self._clear_disc("empty", "De schijf is verwijderd. Plaats een cd/dvd.")
                raise convert.ConvertError("De schijf is tijdens de opdracht verwijderd")
            identity = self._disc.identity
        destination = self.config.films_dir if job.destination == "films" else self.config.music_dir
        if not destination.is_dir():
            raise convert.ConvertError(
                f"De Jellyfin-map {destination} is niet gekoppeld in de container. "
                "Voeg de volumekoppeling toe in Container Manager of compose."
            )
        self.update(
            convert_state="working",
            convert_percent=0,
            convert_speed=0,
            convert_eta=None,
            convert_file="",
            convert_error="",
            convert_source=entry.source.label,
            convert_destination=str(destination),
            convert_mode=job.mode,
            convert_bytes=0,
            convert_total_bytes=0,
            message="Omzetten naar MP4 voor Jellyfin…",
        )

        def progress(value: convert.Progress) -> None:
            self.update(
                convert_percent=value.percent,
                convert_speed=value.speed,
                convert_eta=value.eta_seconds,
                convert_bytes=value.bytes_read,
                convert_total_bytes=value.total_bytes,
            )

        result = convert.convert_title(
            entry.source,
            job.title,
            job.mode,
            destination,
            self.config.state_dir,
            convert.Settings.from_config(self.config),
            self._convert_stop,
            progress,
            name=job.name,
            identity=identity,
            puid=self.config.puid,
            pgid=self.config.pgid,
        )
        self.update(
            convert_state="done",
            convert_percent=100,
            convert_speed=0,
            convert_eta=0,
            convert_file=str(result.path),
            convert_error="",
            message=f"MP4 klaar voor Jellyfin: {result.path}",
        )
        if live and self.config.auto_eject:
            self._eject()

    def _eject(self) -> None:
        if self._busy():
            raise RuntimeError("Wacht tot de huidige bewerking klaar is voordat je uitwerpt")
        if self._mounted:
            drive.unmount(self.config.mountpoint)
            self._mounted = False
        drive.eject(self.config.device)
        self._clear_disc("open", "Schijf uitgeworpen. Plaats een nieuwe cd/dvd.")

    def _close(self) -> None:
        if self._busy():
            raise RuntimeError("Wacht tot de huidige bewerking klaar is voordat je de lade sluit")
        drive.close_tray(self.config.device)
        self.update(state="not_ready", message="Lade sluiten; de schijf wordt gelezen…", error="")

    def _command(self, command: str | ConvertJob) -> None:
        try:
            if isinstance(command, ConvertJob):
                self._convert(command)
            elif command == "eject":
                self._eject()
            elif command == "close":
                self._close()
            elif command == "copy":
                self._copy()
            elif command == "iso":
                self._iso()
            elif command == "retry":
                self._retry_at = 0
                if not self._mounted:
                    self._mount()
        except (
            drive.DriveError,
            copy.CopyError,
            image.ImageError,
            convert.ConvertError,
            dvd.DvdError,
            OSError,
            RuntimeError,
        ) as exc:
            current = self.snapshot()
            if current["copy_state"] == "copying":
                self.update(copy_state="error")
            if current["image_state"] == "working":
                self.update(image_state="error")
            if current["convert_state"] == "working":
                self.update(convert_state="error", convert_error=str(exc))
            self._error(str(exc))
        finally:
            if isinstance(command, ConvertJob):
                with self._job_lock:
                    self._conversion_pending = False

    def _run(self) -> None:
        cfg = self.config
        try:
            drive.check_tools()
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            cfg.share_dir.mkdir(parents=True, exist_ok=True)
            cfg.mountpoint.mkdir(parents=True, exist_ok=True)
            cfg.library_dir.mkdir(parents=True, exist_ok=True)
            for folder in (cfg.mountpoint, cfg.library_dir):
                os.chmod(folder, 0o755)
                os.chown(folder, cfg.puid, cfg.pgid)
            if drive.is_mounted(cfg.mountpoint):
                # Container restarts may inherit a mount through rshared.
                # Never stack a mount or take over an unrelated filesystem.
                source = drive.mount_source(cfg.mountpoint)
                if source not in {str(cfg.device), str(cfg.device.resolve())}:
                    raise drive.DriveError(
                        f"{cfg.mountpoint} is al aangekoppeld aan {source or 'onbekend'}, "
                        "niet aan deze speler. Koppel die map eerst los in DSM."
                    )
                self._mounted = True
                self._disc = drive.inspect_disc(cfg.device)
                self.update(
                    disc=asdict(self._disc),
                    state="ready",
                    message="Bestaande schijf gevonden en beschikbaar.",
                )
        except (OSError, drive.DriveError) as exc:
            self._error(f"Opstarten mislukt: {exc}")

        while not self.stop.is_set():
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                command = None
            if command is not None:
                self._command(command)
                continue
            try:
                status = drive.drive_status(cfg.device)
                if status in {"empty", "open", "missing"}:
                    message = {
                        "empty": "Geen schijf. Plaats een cd/dvd in de speler.",
                        "open": "De lade is open. Plaats een cd/dvd en sluit de lade.",
                        "missing": (
                            f"{cfg.device} ontbreekt. Sluit de speler aan en maak "
                            "de container opnieuw aan."
                        ),
                    }[status]
                    self._clear_disc(status, message)
                elif status == "not_ready":
                    self.update(state="not_ready", message="De speler komt op gang…", error="")
                elif self._mounted and drive.media_changed(cfg.device):
                    self._clear_disc("not_ready", "Nieuwe schijf gedetecteerd…")
                elif (
                    not self._mounted
                    and status in {"disc", "unknown"}
                    and time.monotonic() >= self._retry_at
                ):
                    self._mount()
                    if (
                        cfg.copy_files
                        and self._mounted
                        and self._disc
                        and self._handled_identity != self._disc.identity
                    ):
                        self._handled_identity = self._disc.identity
                        self._copy()
                elif self._mounted and not drive.is_mounted(cfg.mountpoint):
                    # Recover from an external umount without touching files.
                    self._mounted = False
                    self._disc = None
                    self.update(
                        state="not_ready",
                        message="Schijf extern losgekoppeld; opnieuw aankoppelen…",
                    )
            except (drive.DriveError, copy.CopyError, image.ImageError, OSError) as exc:
                current = self.snapshot()
                if current["copy_state"] == "copying":
                    self.update(copy_state="error")
                if current["image_state"] == "working":
                    self.update(image_state="error")
                self._error(str(exc))
            self.stop.wait(cfg.poll_seconds)
        try:
            if self._mounted:
                drive.unmount(cfg.mountpoint)
                drive.unlock_tray(cfg.device)
                self._mounted = False
        except drive.DriveError:
            LOG.exception("Loskoppelen bij afsluiten mislukt")
        self.update(state="stopped", message="Container is gestopt.")
