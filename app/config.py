"""Validated configuration, deliberately small enough to audit on a NAS."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def boolean(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} moet true of false zijn")


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} moet tussen {minimum} en {maximum} liggen")
    return value


@dataclass(frozen=True)
class Config:
    device: Path = Path("/dev/sr0")
    share_dir: Path = Path("/srv/dvd")
    state_dir: Path = Path("/var/lib/dvd")
    poll_seconds: int = 5
    web_port: int = 8080
    puid: int = 1024
    pgid: int = 100
    copy_files: bool = False
    auto_eject: bool = False

    @property
    def mountpoint(self) -> Path:
        return self.share_dir / "disc"

    @property
    def library_dir(self) -> Path:
        return self.share_dir / "library"

    @classmethod
    def from_env(cls) -> Config:
        device = Path(os.environ.get("DEVICE", "/dev/sr0"))
        if not re.fullmatch(r"/dev/sr[0-9]{1,3}", str(device)):
            raise ConfigError("DEVICE moet /dev/sr0 t/m /dev/sr999 zijn")
        share = Path(os.environ.get("SHARE_DIR", "/srv/dvd"))
        state = Path(os.environ.get("STATE_DIR", "/var/lib/dvd"))
        if not share.is_absolute() or not state.is_absolute():
            raise ConfigError("SHARE_DIR en STATE_DIR moeten absolute paden zijn")
        if share.resolve() == Path("/") or state.resolve() == Path("/"):
            raise ConfigError("De root-map mag niet als doel worden gebruikt")
        if share.resolve() == state.resolve() or share.resolve() in state.resolve().parents:
            raise ConfigError("STATE_DIR mag niet in SHARE_DIR staan")
        # Zero is allowed for a deliberately root-owned DSM shared folder.
        puid = integer("PUID", 1024, 0, 4294967294)
        pgid = integer("PGID", 100, 0, 4294967294)
        return cls(
            device=device,
            share_dir=share,
            state_dir=state,
            poll_seconds=integer("POLL_SECONDS", 5, 2, 300),
            web_port=integer("WEB_PORT", 8080, 1024, 65535),
            puid=puid,
            pgid=pgid,
            copy_files=boolean("COPY_FILES"),
            auto_eject=boolean("AUTO_EJECT"),
        )
