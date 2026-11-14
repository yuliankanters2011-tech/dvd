import pytest

from app.config import Config, ConfigError


def test_defaults(monkeypatch):
    for key in (
        "DEVICE",
        "SHARE_DIR",
        "STATE_DIR",
        "POLL_SECONDS",
        "WEB_PORT",
        "PUID",
        "PGID",
        "COPY_FILES",
        "AUTO_EJECT",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.device.as_posix() == "/dev/sr0"
    assert cfg.mountpoint.as_posix() == "/srv/dvd/disc"
    assert cfg.copy_files is False


def test_boolean_and_numbers(monkeypatch):
    monkeypatch.setenv("COPY_FILES", "yes")
    monkeypatch.setenv("AUTO_EJECT", "0")
    monkeypatch.setenv("POLL_SECONDS", "2")
    monkeypatch.setenv("PUID", "0")
    cfg = Config.from_env()
    assert cfg.copy_files is True
    assert cfg.auto_eject is False
    assert cfg.poll_seconds == 2
    assert cfg.puid == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("DEVICE", "/dev/sda"),
        ("DEVICE", "/dev/sr0;rm"),
        ("POLL_SECONDS", "1"),
        ("WEB_PORT", "80"),
        ("COPY_FILES", "misschien"),
        ("PUID", "-1"),
        ("SHARE_DIR", "relative"),
    ],
)
def test_invalid(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError):
        Config.from_env()


def test_state_inside_share_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARE_DIR", str(tmp_path / "share"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "share" / "state"))
    with pytest.raises(ConfigError):
        Config.from_env()


def test_converter_defaults(monkeypatch):
    for key in (
        "FILMS_DIR",
        "MUSIC_DIR",
        "CONVERT_THREADS",
        "CONVERT_CRF",
        "CONVERT_PRESET",
        "CONVERT_AUDIO_BITRATE",
        "FFMPEG_BIN",
        "FFPROBE_BIN",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = Config.from_env()
    assert cfg.films_dir.as_posix() == "/volume1/video/Films"
    assert cfg.music_dir.as_posix() == "/volume1/video/Muziek"
    assert cfg.convert_threads == 2
    assert cfg.convert_crf == 20
    assert cfg.convert_preset == "veryfast"
    assert cfg.convert_audio_bitrate == 192


def test_converter_env(monkeypatch, tmp_path):
    monkeypatch.setenv("FILMS_DIR", str(tmp_path / "films"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("CONVERT_THREADS", "4")
    monkeypatch.setenv("CONVERT_CRF", "18")
    monkeypatch.setenv("CONVERT_PRESET", "fast")
    monkeypatch.setenv("CONVERT_AUDIO_BITRATE", "256")
    cfg = Config.from_env()
    assert cfg.convert_threads == 4
    assert cfg.convert_crf == 18
    assert cfg.convert_preset == "fast"
    assert cfg.convert_audio_bitrate == 256


@pytest.mark.parametrize(
    "key,value",
    [
        ("FILMS_DIR", "relative"),
        ("MUSIC_DIR", "/"),
        ("CONVERT_THREADS", "0"),
        ("CONVERT_THREADS", "17"),
        ("CONVERT_CRF", "0"),
        ("CONVERT_CRF", "52"),
        ("CONVERT_PRESET", "placebo"),
        ("CONVERT_AUDIO_BITRATE", "32"),
    ],
)
def test_converter_invalid(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ConfigError):
        Config.from_env()


def test_media_dirs_must_be_distinct_and_not_nested(monkeypatch, tmp_path):
    monkeypatch.setenv("FILMS_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "media"))
    with pytest.raises(ConfigError, match="verschillende"):
        Config.from_env()
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path / "media" / "music"))
    with pytest.raises(ConfigError, match="in elkaar"):
        Config.from_env()
