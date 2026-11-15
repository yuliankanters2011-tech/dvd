"""Validated configuration, deliberately small enough to audit on a NAS."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .drive import DRIVE_SPEED_MAX

SLEEP_IDLE_MIN = 60
SLEEP_IDLE_MAX = 86400
SLEEP_IDLE_DEFAULT = 600


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


def percentage(name: str, default: float, minimum: float = 0, maximum: float = 100) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} moet een getal zijn") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} moet tussen {minimum} en {maximum} liggen")
    return value


def validate_sleep_idle(seconds: int) -> int:
    if type(seconds) is not int or not (
        seconds == 0 or SLEEP_IDLE_MIN <= seconds <= SLEEP_IDLE_MAX
    ):
        raise ConfigError(
            f"SLEEP_IDLE_SECONDS moet 0 (uit) of {SLEEP_IDLE_MIN} t/m "
            f"{SLEEP_IDLE_MAX} seconden zijn"
        )
    return seconds


def validate_drive_speed(speed: int) -> int:
    if type(speed) is not int or not 0 <= speed <= DRIVE_SPEED_MAX:
        raise ConfigError(
            f"DRIVE_SPEED moet een geheel getal van 0 t/m {DRIVE_SPEED_MAX} zijn; "
            "0 betekent de standaard/maximumsnelheid van de speler"
        )
    return speed


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
    iso_max_missing_percent: float = 0.5
    films_dir: Path = Path("/volume1/video/Films")
    music_dir: Path = Path("/volume1/video/Muziek")
    convert_threads: int = 2
    convert_crf: int = 20
    convert_preset: str = "veryfast"
    convert_audio_bitrate: int = 192
    sleep_idle_seconds: int = SLEEP_IDLE_DEFAULT
    drive_speed: int = 0
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

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
        films = Path(os.environ.get("FILMS_DIR", "/volume1/video/Films"))
        music = Path(os.environ.get("MUSIC_DIR", "/volume1/video/Muziek"))
        for name, path in (("FILMS_DIR", films), ("MUSIC_DIR", music)):
            if not path.is_absolute() or path.resolve() == Path("/"):
                raise ConfigError(f"{name} moet een absoluut pad naar een Jellyfin-map zijn")
        if films.resolve() == music.resolve():
            raise ConfigError("FILMS_DIR en MUSIC_DIR moeten verschillende mappen zijn")
        if films.resolve() in music.resolve().parents or music.resolve() in films.resolve().parents:
            raise ConfigError("FILMS_DIR en MUSIC_DIR mogen niet in elkaar liggen")
        preset = os.environ.get("CONVERT_PRESET", "veryfast").strip()
        if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium"}:
            raise ConfigError("CONVERT_PRESET moet ultrafast t/m medium zijn")
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
            iso_max_missing_percent=percentage("ISO_MAX_MISSING_PERCENT", 0.5),
            films_dir=films,
            music_dir=music,
            convert_threads=integer("CONVERT_THREADS", 2, 1, 16),
            convert_crf=integer("CONVERT_CRF", 20, 1, 51),
            convert_preset=preset,
            convert_audio_bitrate=integer("CONVERT_AUDIO_BITRATE", 192, 64, 512),
            sleep_idle_seconds=validate_sleep_idle(
                integer("SLEEP_IDLE_SECONDS", SLEEP_IDLE_DEFAULT, 0, SLEEP_IDLE_MAX)
            ),
            drive_speed=validate_drive_speed(integer("DRIVE_SPEED", 0, 0, DRIVE_SPEED_MAX)),
            ffmpeg_bin=os.environ.get("FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg",
            ffprobe_bin=os.environ.get("FFPROBE_BIN", "ffprobe").strip() or "ffprobe",
        )
