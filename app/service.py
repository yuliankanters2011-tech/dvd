"""Single-worker state machine; HTTP requests never perform disc operations."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import __version__, copy, drive, image
from .config import Config

LOG = logging.getLogger("dvd.service")


@dataclass
class Snapshot:
    version: str = __version__
    state: str = "starting"
    message: str = "De speler wordt gecontroleerd…"
    device: str = "/dev/sr0"
    disc: dict[str, Any] | None = None
    nas_folder: str = "/srv/dvd/disc"
    library_folder: str = "/srv/dvd/library"
    copy_enabled: bool = False
    copy_state: str = "idle"
    copy_percent: int = 0
    copied_bytes: int = 0
    total_bytes: int = 0
    import_folder: str = ""
    image_state: str = "idle"
    image_percent: int = 0
    image_file: str = ""
    error: str = ""
    updated_at: float = field(default_factory=time.time)


class Service:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop = threading.Event()
        self.commands: queue.Queue[str] = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._snapshot = Snapshot(
            device=str(config.device),
            nas_folder=str(config.mountpoint),
            library_folder=str(config.library_dir),
            copy_enabled=config.copy_files,
        )
        self._mounted = False
        self._disc: drive.DiscInfo | None = None
        self._handled_identity = ""
        self._copy_done = False
        self._retry_at = 0.0
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

    def request(self, command: str) -> None:
        if command not in {"eject", "copy", "iso", "retry"}:
            raise ValueError("Onbekende opdracht")
        try:
            self.commands.put_nowait(command)
        except queue.Full as exc:
            raise RuntimeError("Er staan al te veel opdrachten in de wachtrij") from exc

    def shutdown(self) -> None:
        self.stop.set()
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

    def _copy(self) -> None:
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

    def _eject(self) -> None:
        if self.snapshot()["copy_state"] == "copying":
            raise RuntimeError("Wacht tot het kopiëren klaar is voordat je uitwerpt")
        if self._mounted:
            drive.unmount(self.config.mountpoint)
            self._mounted = False
        drive.eject(self.config.device)
        self._clear_disc("open", "Schijf uitgeworpen. Plaats een nieuwe cd/dvd.")

    def _command(self, name: str) -> None:
        try:
            if name == "eject":
                self._eject()
            elif name == "copy":
                self._copy()
            elif name == "iso":
                self._iso()
            elif name == "retry":
                self._retry_at = 0
                if not self._mounted:
                    self._mount()
        except (
            drive.DriveError,
            copy.CopyError,
            image.ImageError,
            RuntimeError,
        ) as exc:
            current = self.snapshot()
            if current["copy_state"] == "copying":
                self.update(copy_state="error")
            if current["image_state"] == "working":
                self.update(image_state="error")
            self._error(str(exc))

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
            if command:
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
