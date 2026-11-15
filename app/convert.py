"""Python orchestration around FFmpeg: DVD/TS to Jellyfin-ready MP4.

The web layer only chooses a source id, a title, a mode and one of two fixed
destination roots. FFmpeg receives argument lists, never a shell string. Output
is written to a hidden partial file, probed, and published without overwriting.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import dvd
from .config import Config
from .protection import CSS_NOTICE, is_css_sense

LOG = logging.getLogger("dvd.convert")
CHUNK = 1024 * 1024
MARKER_SUFFIX = ".dvd-bridge.json"
MODES = {"video", "audio"}
DESTINATIONS = {"films", "music"}


class ConvertError(RuntimeError):
    """A Dutch, actionable conversion error."""


@dataclass(frozen=True)
class Settings:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    threads: int = 2
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: int = 192

    @classmethod
    def from_config(cls, config: Config) -> Settings:
        return cls(
            ffmpeg=config.ffmpeg_bin,
            ffprobe=config.ffprobe_bin,
            threads=config.convert_threads,
            crf=config.convert_crf,
            preset=config.convert_preset,
            audio_bitrate=config.convert_audio_bitrate,
        )


@dataclass(frozen=True)
class Progress:
    percent: int
    out_seconds: float
    speed: float
    eta_seconds: int | None
    bytes_read: int
    total_bytes: int


@dataclass(frozen=True)
class Result:
    path: Path
    size_bytes: int
    duration: float
    mode: str


ProgressCallback = Callable[[Progress], None]


def safe_media_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    cleaned = re.sub(r"[^\w .()\-]+", "-", normalized, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    # NAS/SMB filesystems usually limit a filename component to 255 bytes.
    cleaned = cleaned.encode("utf-8")[:200].decode("utf-8", errors="ignore").strip(" .-")
    return cleaned or "DVD"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ConvertError(f"{name} ontbreekt. Gebruik de nieuwe DVD Bridge-image met FFmpeg.")
    return path


def _fingerprint(source: dvd.Source, title: dvd.Title, mode: str, settings: Settings) -> str:
    parts = [str(source.path.resolve()), str(title.number), mode, settings.preset]
    parts.extend([str(settings.crf), str(settings.audio_bitrate)])
    if title.title_numbers:
        parts.append(",".join(str(number) for number in title.title_numbers))
    for segment in title.segments:
        try:
            stat = segment.path.stat()
            parts.extend([str(segment.path), str(segment.offset), str(segment.size)])
            parts.extend([str(stat.st_size), str(stat.st_mtime_ns)])
        except OSError:
            parts.extend([str(segment.path), str(segment.offset), str(segment.size)])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _probe(path: Path, ffprobe: str) -> dict:
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConvertError(f"ffprobe kon het resultaat niet controleren: {exc}") from exc
    if completed.returncode != 0:
        raise ConvertError(
            "Het MP4-bestand is niet geldig volgens ffprobe. Mogelijk is de bron "
            "beschadigd, versleuteld of bevat hij geen ondersteund geluid/beeld."
        )
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise ConvertError("ffprobe gaf geen geldig controlerapport") from exc


def _expected_duration(source: dvd.Source, title: dvd.Title, ffprobe: str) -> float:
    if title.duration > 0:
        return title.duration
    if source.kind == "file":
        try:
            completed = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(source.path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return max(0.0, float(completed.stdout.strip()))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return 0.0
    return 0.0


def _command(
    ffmpeg: str,
    source: dvd.Source,
    mode: str,
    settings: Settings,
    partial: Path,
    use_pipe: bool,
) -> list[str]:
    input_format = (
        "mpegts" if source.kind == "file" and source.path.suffix.lower() == ".ts" else "mpeg"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-progress",
        "pipe:1",
        "-y",
        "-fflags",
        "+genpts",
        "-probesize",
        "50M",
        "-analyzeduration",
        "20M",
        "-f",
        input_format,
        "-i",
        "pipe:0" if use_pipe else str(source.path),
    ]
    if mode == "video":
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-sn",
                "-dn",
                "-map_chapters",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                settings.preset,
                "-crf",
                str(settings.crf),
                "-threads",
                str(settings.threads),
                "-vf",
                "yadif=mode=0:parity=-1:deint=1,format=yuv420p",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                f"{settings.audio_bitrate}k",
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_chapters",
                "-1",
                "-c:a",
                "aac",
                "-b:a",
                f"{settings.audio_bitrate}k",
                "-threads",
                str(settings.threads),
            ]
        )
    command.extend(["-max_muxing_queue_size", "1024", "-movflags", "+faststart", str(partial)])
    return command


def _feed(
    process: subprocess.Popen,
    segments: tuple[dvd.Segment, ...],
    stop: threading.Event,
    sent: list[int],
    errors: list[str],
) -> None:
    try:
        for segment in segments:
            if stop.is_set():
                return
            with segment.path.open("rb") as handle:
                handle.seek(segment.offset)
                remaining = segment.size
                while remaining:
                    if stop.is_set():
                        return
                    block = handle.read(min(CHUNK, remaining))
                    if not block:
                        errors.append("De bron werd kleiner tijdens het lezen")
                        return
                    process.stdin.write(block)
                    remaining -= len(block)
                    sent[0] += len(block)
        process.stdin.flush()
    except BrokenPipeError:
        if not stop.is_set():
            errors.append("FFmpeg sloot de invoer voortijdig")
    except OSError as exc:
        if not stop.is_set():
            errors.append(
                CSS_NOTICE
                if is_css_sense(str(exc))
                else f"Kan de bron niet lezen: {exc.strerror or exc}"
            )
    finally:
        with contextlib.suppress(OSError):
            process.stdin.close()


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
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


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 1200))
            return handle.read().decode("utf-8", errors="replace").strip()[-600:]
    except OSError:
        return ""


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    if destination.exists():
        raise ConvertError(f"{destination.name} bestaat al; er wordt niets overschreven")
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise ConvertError(f"{destination.name} bestaat al; er wordt niets overschreven") from exc
    temporary.unlink(missing_ok=True)


def _concat_command(ffmpeg: str, list_path: Path, partial: Path, mode: str) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
    ]
    if mode == "video":
        command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
    else:
        command.extend(["-map", "0:a:0", "-vn"])
    command.extend(["-c", "copy", "-movflags", "+faststart", str(partial)])
    return command


def _run_concat(command: list[str], cwd: Path, stop: threading.Event, log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            cwd=cwd,
            start_new_session=True,
        )
        started = time.monotonic()
        try:
            while True:
                if stop.is_set():
                    raise ConvertError("Omzetten afgebroken")
                try:
                    returncode = process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > 300:
                        raise ConvertError(
                            "FFmpeg reageerde 5 minuten niet tijdens het samenvoegen; "
                            "controleer schijf en USB-voeding"
                        ) from None
            if returncode != 0:
                tail = _log_tail(log_path)
                raise ConvertError(
                    f"FFmpeg stopte met code {returncode} tijdens het samenvoegen. "
                    "De titels hebben mogelijk verschillende audio-/videosporen. "
                    f"Log: {log_path.name} in de state-volume."
                    + (f" Laatste melding: {tail.splitlines()[-1]}" if tail else "")
                )
        except BaseException:
            _terminate(process)
            raise


def _build_combined(
    source: dvd.Source,
    title: dvd.Title,
    mode: str,
    destination_dir: Path,
    state_dir: Path,
    settings: Settings,
    stop: threading.Event,
    progress: ProgressCallback | None,
    partial: Path,
    run_id: str,
    identity: str,
    puid: int | None,
    pgid: int | None,
    expected: float,
    log_path: Path,
) -> None:
    """Encode kept titles one by one, then losslessly join the MP4 parts.

    Sequential parts avoid feeding timestamp resets from different VTS/VOB
    titles into one MPEG demuxer. The hidden staging directory is removed on
    success, cancel and failure; only the final MP4/marker are published.
    """
    total_bytes = title.size_bytes
    completed_bytes = 0
    completed_duration = 0.0
    with tempfile.TemporaryDirectory(
        prefix=f".dvd-bridge-{run_id}.parts.", dir=destination_dir
    ) as staging_name:
        staging = Path(staging_name)
        parts: list[Path] = []
        for index, number in enumerate(title.title_numbers, start=1):
            if stop.is_set():
                raise ConvertError("Omzetten afgebroken")
            part_title = source.title(number)

            def part_progress(
                value: Progress,
                base_duration=completed_duration,
                base_bytes=completed_bytes,
            ) -> None:
                if progress is None:
                    return
                out_seconds = base_duration + value.out_seconds
                read = min(total_bytes, base_bytes + value.bytes_read)
                if expected > 0:
                    percent = min(99, round(out_seconds * 100 / expected))
                    eta = (
                        round((expected - out_seconds) / value.speed)
                        if value.speed > 0 and out_seconds < expected
                        else None
                    )
                else:
                    percent = min(99, round(read * 100 / total_bytes)) if total_bytes else 0
                    eta = None
                progress(Progress(percent, out_seconds, value.speed, eta, read, total_bytes))

            result = convert_title(
                source,
                number,
                mode,
                staging,
                state_dir,
                settings,
                stop,
                part_progress,
                name=f"deel-{index:03d}",
                identity=identity,
                puid=puid,
                pgid=pgid,
            )
            parts.append(result.path)
            completed_bytes += part_title.size_bytes
            completed_duration += result.duration
        if stop.is_set():
            raise ConvertError("Omzetten afgebroken")
        if progress:
            progress(
                Progress(
                    99,
                    completed_duration,
                    0.0,
                    None,
                    total_bytes,
                    total_bytes,
                )
            )
        list_path = staging / "concat.txt"
        list_path.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
        concat_log = log_path.parent / f"{log_path.stem}.concat.log"
        _run_concat(
            _concat_command(settings.ffmpeg, list_path, partial, mode),
            staging,
            stop,
            concat_log,
        )


def convert_title(
    source: dvd.Source,
    title_number: int,
    mode: str,
    destination_dir: Path,
    state_dir: Path,
    settings: Settings,
    stop: threading.Event,
    progress: ProgressCallback | None = None,
    name: str = "",
    identity: str = "",
    puid: int | None = None,
    pgid: int | None = None,
) -> Result:
    if mode not in MODES:
        raise ConvertError("Kies video of audio")
    if stop.is_set():
        raise ConvertError("Omzetten afgebroken")
    if source.protected:
        raise ConvertError(CSS_NOTICE)
    if not source.titles:
        raise ConvertError(source.note or "Deze bron bevat geen ondersteunde titels")
    title = source.title(title_number)
    if not title.segments:
        raise ConvertError("Deze bron bevat geen omzetbare titel")
    ffmpeg = _tool(settings.ffmpeg)
    ffprobe = _tool(settings.ffprobe)
    destination_dir = destination_dir.expanduser().resolve()
    state_dir = state_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    base = safe_media_name(name or source.label)
    if title.number == dvd.ALL_TITLES:
        base = safe_media_name(f"{base} - Alle titels")
    elif source.kind == "dvd" and title.number != source.default_title:
        base = safe_media_name(f"{base} - Titel {title.number:02d}")
    destination = destination_dir / f"{base}.mp4"
    marker = destination_dir / f"{base}{MARKER_SUFFIX}"
    fingerprint = identity or _fingerprint(source, title, mode, settings)
    job_identity = hashlib.sha256(
        f"{fingerprint}|{mode}|{title.number}|{settings.preset}|"
        f"{settings.crf}|{settings.audio_bitrate}".encode()
    ).hexdigest()
    if destination.exists() or marker.exists():
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
            if (
                previous.get("identity") == job_identity
                and previous.get("complete") is True
                and destination.stat().st_size == previous.get("bytes")
            ):
                return Result(
                    destination,
                    destination.stat().st_size,
                    float(previous.get("duration", 0)),
                    mode,
                )
        except (OSError, ValueError, TypeError):
            pass
        raise ConvertError(
            f"{destination.name} bestaat al. Kies een andere naam of verplaats het "
            "bestaande bestand en het bijbehorende .json-bestand; er wordt niets overschreven."
        )
    reserve = (
        title.size_bytes + 64 * 1024 * 1024
        if mode == "video"
        else title.size_bytes // 4 + 64 * 1024 * 1024
    )
    if title.number == dvd.ALL_TITLES and mode == "audio":
        # Keep the encoded parts and the joined copy on the same volume.
        reserve = title.size_bytes // 2 + 64 * 1024 * 1024
    if shutil.disk_usage(destination_dir).free < reserve:
        raise ConvertError("Onvoldoende vrije ruimte in de Jellyfin-doelmap")

    run_id = uuid.uuid4().hex[:12]
    partial = destination_dir / f".{base}.{run_id}.partial.mp4"
    marker_temp = destination_dir / f".{base}.{run_id}.marker.json"
    log_path = state_dir / f"{base}-{job_identity[:10]}.ffmpeg.log"
    expected = _expected_duration(source, title, ffprobe)
    combined = title.number == dvd.ALL_TITLES
    use_pipe = source.kind == "dvd" and not combined
    command = [] if combined else _command(ffmpeg, source, mode, settings, partial, use_pipe)
    LOG.info(
        "Omzetten naar %s: %s %s (%s)",
        destination,
        source.label,
        "alle ondersteunde titels" if combined else f"titel {title.number}",
        mode,
    )
    sent = [0]
    errors: list[str] = []
    feeder: threading.Thread | None = None
    process: subprocess.Popen | None = None
    try:
        if combined:
            _build_combined(
                source,
                title,
                mode,
                destination_dir,
                state_dir,
                settings,
                stop,
                progress,
                partial,
                run_id,
                identity,
                puid,
                pgid,
                expected,
                log_path,
            )
            returncode = 0
            sent[0] = title.size_bytes
        else:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if use_pipe else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log_file,
                    bufsize=0,
                    start_new_session=True,
                )
                if use_pipe:
                    feeder = threading.Thread(
                        target=_feed,
                        args=(process, title.segments, stop, sent, errors),
                        name="dvd-ffmpeg-feed",
                        daemon=True,
                    )
                    feeder.start()
                out_seconds = 0.0
                speed = 0.0
                last_output = time.monotonic()
                last_report = 0.0
                eof = False
                while True:
                    if stop.is_set():
                        raise ConvertError("Omzetten afgebroken")
                    streams = [process.stdout] if process.stdout and not eof else []
                    ready = select.select(streams, [], [], 0.5)[0]
                    if ready:
                        raw = process.stdout.readline()
                        if raw:
                            last_output = time.monotonic()
                            line = raw.decode("utf-8", errors="replace")
                            key, _, value = line.strip().partition("=")
                            if key == "out_time_us" and value.lstrip("-").isdigit():
                                out_seconds = max(0.0, int(value) / 1_000_000)
                            elif key == "speed":
                                try:
                                    speed = max(0.0, float(value.strip().rstrip("x")))
                                except ValueError:
                                    speed = 0.0
                        else:
                            eof = True
                    now = time.monotonic()
                    if progress and now - last_report >= 0.5:
                        if expected > 0:
                            percent = min(99, round(out_seconds * 100 / expected))
                            eta = (
                                round((expected - out_seconds) / speed)
                                if speed > 0 and out_seconds < expected
                                else None
                            )
                        else:
                            percent = (
                                min(99, round(sent[0] * 100 / title.size_bytes))
                                if title.size_bytes
                                else 0
                            )
                            eta = None
                        progress(
                            Progress(percent, out_seconds, speed, eta, sent[0], title.size_bytes)
                        )
                        last_report = now
                    if process.poll() is not None and eof:
                        break
                    if now - last_output > 300:
                        raise ConvertError(
                            "FFmpeg reageerde 5 minuten niet; controleer schijf en USB-voeding"
                        )
                returncode = process.returncode
            if feeder:
                feeder.join(timeout=5)
            if stop.is_set():
                raise ConvertError("Omzetten afgebroken")
            if errors:
                raise ConvertError(errors[0])
            if use_pipe and sent[0] != title.size_bytes:
                raise ConvertError(
                    "De DVD-titel is niet volledig gelezen; controleer schijf en USB-verbinding"
                )
            if returncode != 0:
                tail = _log_tail(log_path)
                raise ConvertError(
                    f"FFmpeg stopte met code {returncode}. De bron kan beschadigd of beveiligd "
                    f"zijn, of bevat geen ondersteund geluid/beeld. "
                    f"Log: {log_path.name} in de state-volume."
                    + (f" Laatste melding: {tail.splitlines()[-1]}" if tail else "")
                )
        report = _probe(partial, ffprobe)
        streams = report.get("streams", [])
        duration = float(report.get("format", {}).get("duration", 0) or 0)
        if duration <= 0:
            raise ConvertError("Het resultaat heeft geen geldige duur")
        if mode == "video" and not any(
            s.get("codec_type") == "video" and s.get("codec_name") == "h264" for s in streams
        ):
            raise ConvertError("Het resultaat bevat geen H.264-videospoor")
        if mode == "audio" and not any(
            s.get("codec_type") == "audio" and s.get("codec_name") == "aac" for s in streams
        ):
            raise ConvertError("Het resultaat bevat geen AAC-audiospoor")
        if expected > 0 and abs(duration - expected) > max(5.0, expected * 0.2):
            raise ConvertError(
                f"De duur wijkt te veel af ({duration:.1f}s van {expected:.1f}s). "
                "Het resultaat is niet gepubliceerd; controleer de bron of kies een andere titel."
            )
        size = partial.stat().st_size
        if puid is not None and pgid is not None:
            os.chown(partial, puid, pgid)
        os.chmod(partial, 0o644)
        manifest = {
            "schema": 1,
            "complete": True,
            "identity": job_identity,
            "source": str(source.path),
            "label": source.label,
            "title": title.number,
            "titles": list(title.title_numbers) if combined else [title.number],
            "mode": mode,
            "bytes": size,
            "duration": duration,
        }
        marker_temp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if puid is not None and pgid is not None:
            os.chown(marker_temp, puid, pgid)
        os.chmod(marker_temp, 0o644)
        _publish_no_replace(partial, destination)
        try:
            _publish_no_replace(marker_temp, marker)
        except BaseException:
            with contextlib.suppress(OSError):
                destination.unlink(missing_ok=True)
            raise
        if progress:
            progress(Progress(100, duration, 0.0, 0, title.size_bytes, title.size_bytes))
        LOG.info("MP4 klaar: %s (%d bytes)", destination, size)
        return Result(destination, size, duration, mode)
    except BaseException:
        if process is not None:
            _terminate(process)
        if feeder:
            feeder.join(timeout=5)
        partial.unlink(missing_ok=True)
        marker_temp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zet DVD VIDEO_TS of losse TS/VOB om naar MP4 voor Jellyfin"
    )
    parser.add_argument(
        "--source", required=True, type=Path, help="VIDEO_TS-map, dvd-root of TS/VOB-bestand"
    )
    parser.add_argument(
        "--title",
        type=int,
        default=0,
        help=(
            "Titelnummer; 0 kiest de langste titel, -1 bewaart alle ondersteunde titels in één MP4"
        ),
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="video")
    parser.add_argument("--destination", choices=sorted(DESTINATIONS), help="films of music")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Expliciete doelmap; alleen gebruiken zonder --destination",
    )
    parser.add_argument("--name", default="", help="Naam voor het MP4-bestand")
    parser.add_argument("--list", action="store_true", help="Toon titels en stop")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
        source = dvd.inspect_source(args.source)
        if args.list:
            print(json.dumps(source.summary(), ensure_ascii=False, indent=2))
            return 0
        if args.output_dir and args.destination:
            raise ConvertError("Gebruik --output-dir of --destination, niet allebei")
        if args.output_dir:
            destination = args.output_dir
        elif args.destination == "music":
            destination = config.music_dir
        elif args.destination == "films":
            destination = config.films_dir
        else:
            destination = config.music_dir if args.mode == "audio" else config.films_dir
        stop = threading.Event()

        def handle_signal(signum: int, _frame: object) -> None:
            LOG.info("Signaal %s; conversie wordt afgebroken", signum)
            stop.set()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        def report(value: Progress) -> None:
            eta = f", nog ~{value.eta_seconds}s" if value.eta_seconds is not None else ""
            print(
                f"\r{value.percent:3d}% · {value.speed:.2f}x{eta}   ",
                end="",
                file=sys.stderr,
                flush=True,
            )

        result = convert_title(
            source,
            args.title,
            args.mode,
            destination,
            config.state_dir,
            Settings.from_config(config),
            stop,
            report,
            name=args.name,
            puid=config.puid,
            pgid=config.pgid,
        )
        print(file=sys.stderr)
        print(f"Klaar: {result.path} ({result.size_bytes} bytes, {result.duration:.1f}s)")
        return 0
    except (dvd.DvdError, ConvertError, OSError, ValueError) as exc:
        print(file=sys.stderr)
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
