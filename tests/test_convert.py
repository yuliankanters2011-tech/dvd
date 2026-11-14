import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from dvd_fixture import SECTOR, write_dvd

from app import convert, dvd


@pytest.fixture()
def tools():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg en ffprobe zijn nodig voor de conversietest")
    return convert.Settings(ffmpeg=ffmpeg, ffprobe=ffprobe, threads=1, crf=28, preset="ultrafast")


@pytest.fixture()
def media(tmp_path, tools):
    ts = tmp_path / "film.ts"
    subprocess.run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "mpeg2video",
            "-b:v",
            "400k",
            "-c:a",
            "ac3",
            "-b:a",
            "96k",
            "-f",
            "mpegts",
            "-y",
            str(ts),
        ],
        check=True,
        timeout=60,
        capture_output=True,
    )
    vob = tmp_path / "film.vob"
    subprocess.run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(ts),
            "-map",
            "0:v",
            "-map",
            "0:a",
            "-c",
            "copy",
            "-f",
            "dvd",
            "-y",
            str(vob),
        ],
        check=True,
        timeout=60,
        capture_output=True,
    )
    return ts, vob


def run(source, mode, destination, state, tools, **kwargs):
    return convert.convert_title(
        source, 0, mode, destination, state, tools, threading.Event(), **kwargs
    )


def test_safe_media_name():
    assert convert.safe_media_name("../../Vakantie ✨") == "Vakantie"
    assert convert.safe_media_name("...") == "DVD"
    assert len(convert.safe_media_name("é" * 300).encode("utf-8")) <= 200


def test_command_never_uses_shell_and_maps_streams(tmp_path):
    source = dvd.Source(tmp_path / "film.ts", "film", "file")
    video = convert._command(
        "ffmpeg", source, "video", convert.Settings(), tmp_path / "o.mp4", False
    )
    audio = convert._command(
        "ffmpeg", source, "audio", convert.Settings(), tmp_path / "o.mp4", True
    )
    assert "0:v:0" in video and "libx264" in video and "0:a:0?" in video
    assert "pipe:0" in audio and "-vn" in audio and "aac" in audio
    assert all(isinstance(arg, str) for arg in video + audio)


def test_cancel_before_start_and_empty_source(tmp_path):
    stop = threading.Event()
    stop.set()
    path = tmp_path / "film.ts"
    path.write_bytes(b"x")
    with pytest.raises(convert.ConvertError, match="afgebroken"):
        convert.convert_title(
            dvd.inspect_source(path),
            0,
            "video",
            tmp_path / "out",
            tmp_path / "state",
            convert.Settings(),
            stop,
        )
    empty = dvd.Source(tmp_path, "leeg", "audio_ts", note="DVD-Audio wordt niet ondersteund")
    with pytest.raises(convert.ConvertError, match="DVD-Audio"):
        run(empty, "audio", tmp_path / "out", tmp_path / "state", convert.Settings())


def test_existing_destination_is_never_overwritten(tmp_path, media, tools):
    source = dvd.inspect_source(media[0])
    destination = tmp_path / "films"
    destination.mkdir()
    existing = destination / "film.mp4"
    existing.write_bytes(b"blijf van mijn bestand af")
    with pytest.raises(convert.ConvertError, match="niets overschreven"):
        run(source, "video", destination, tmp_path / "state", tools)
    assert existing.read_bytes() == b"blijf van mijn bestand af"


def test_video_ts_conversion_and_idempotency(tmp_path, media, tools):
    source = dvd.inspect_source(media[0])
    destination = tmp_path / "films"
    state = tmp_path / "state"
    progress = []
    result = convert.convert_title(
        source, 0, "video", destination, state, tools, threading.Event(), progress.append
    )
    assert result.path == destination / "film.mp4"
    assert result.duration == pytest.approx(2.0, abs=0.5)
    assert progress[-1].percent == 100
    marker = json.loads((destination / "film.dvd-bridge.json").read_text())
    assert marker["complete"] is True and marker["mode"] == "video"
    assert marker["bytes"] == result.size_bytes
    assert result.path.stat().st_mode & 0o777 == 0o644
    stamp = result.path.stat().st_mtime_ns
    again = run(source, "video", destination, state, tools)
    assert again.path == result.path
    assert result.path.stat().st_mtime_ns == stamp
    changed = convert.Settings(
        ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe, threads=1, crf=27, preset="ultrafast"
    )
    with pytest.raises(convert.ConvertError, match="bestaat al"):
        run(source, "video", destination, state, changed)
    assert not list(destination.glob(".*.partial.mp4"))


def test_audio_only_from_vob(tmp_path, media, tools):
    source = dvd.inspect_source(media[1])
    result = run(source, "audio", tmp_path / "music", tmp_path / "state", tools)
    report = convert._probe(result.path, tools.ffprobe)
    assert [stream["codec_type"] for stream in report["streams"]] == ["audio"]
    assert report["streams"][0]["codec_name"] == "aac"


def test_dvd_video_ts_pipe_conversion(tmp_path, media, tools):
    data = media[1].read_bytes()
    sectors = len(data) // SECTOR
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, sectors - 1, 0, 2)],
        vob_sectors={1: sectors},
        vob_bytes={1: data},
    )
    source = dvd.inspect_source(directory)
    result = run(source, "video", tmp_path / "films", tmp_path / "state", tools, name="DVD test")
    assert result.path.name == "DVD test.mp4"
    assert result.duration == pytest.approx(2.0, abs=0.5)
    assert not list((tmp_path / "films").glob(".*.partial.mp4"))


def test_cancel_during_conversion_cleans_partial(tmp_path, media, tools):
    source = dvd.inspect_source(media[0])
    stop = threading.Event()
    destination = tmp_path / "films"

    def cancel(_progress):
        stop.set()

    with pytest.raises(convert.ConvertError, match="afgebroken"):
        convert.convert_title(
            source, 0, "video", destination, tmp_path / "state", tools, stop, cancel
        )
    assert not (destination / "film.mp4").exists()
    assert not list(destination.glob(".*.partial.mp4"))


def test_cli_output_dir_and_destination_are_exclusive(tmp_path, media):
    result = subprocess.run(
        [
            "python",
            "-m",
            "app.convert",
            "--source",
            str(media[0]),
            "--output-dir",
            str(tmp_path / "out"),
            "--destination",
            "films",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    assert result.returncode == 2
    assert "niet allebei" in result.stderr
