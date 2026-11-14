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
