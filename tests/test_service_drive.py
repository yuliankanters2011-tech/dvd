import json
import time

import pytest

from app import copy, drive, dvd, service
from app.config import Config


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    cfg = Config(
        share_dir=tmp_path / "share",
        state_dir=tmp_path / "state",
        films_dir=tmp_path / "films",
        music_dir=tmp_path / "music",
        puid=0,
        pgid=0,
        sleep_idle_seconds=60,
    )
    cfg.library_dir.mkdir(parents=True)
    cfg.state_dir.mkdir(parents=True)
    cfg.films_dir.mkdir(parents=True)
    cfg.music_dir.mkdir(parents=True)
    item = service.Service(cfg)
    item._mounted = True
    item._disc = drive.DiscInfo("identiteit", "TEST", "udf", 10000)
    item.update(state="ready", disc={"label": "TEST", "identity": "identiteit"})
    monkeypatch.setattr(drive, "drive_status", lambda device: "disc")
    monkeypatch.setattr(drive, "media_changed", lambda device: False)
    monkeypatch.setattr(drive, "is_mounted", lambda target: True)
    monkeypatch.setattr(drive, "unmount", lambda target: None)
    monkeypatch.setattr(drive, "unlock_tray", lambda device: None)
    return item


def test_sleep_unmounts_stops_and_pauses_polling(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(drive, "spin_down", lambda device: calls.append(("stop", device)))
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["drive_sleeping"] is True
    assert snapshot["state"] == "sleeping"
    assert snapshot["drive_sleep_supported"] is True
    assert svc._mounted is False
    assert calls == [("stop", svc.config.device)]

    def forbidden(*a, **k):
        raise AssertionError("sleeping service must not probe the drive")

    monkeypatch.setattr(drive, "drive_status", forbidden)
    monkeypatch.setattr(drive, "media_changed", forbidden)
    monkeypatch.setattr(drive, "is_mounted", forbidden)
    monkeypatch.setattr(drive, "inspect_disc", forbidden)
    svc.stop.set()
    svc._run()
    assert svc.snapshot()["state"] == "stopped"


def test_sleep_refuses_busy_and_pending_work(svc):
    svc.update(convert_state="working")
    with pytest.raises(RuntimeError, match="omzetten"):
        svc.request("sleep")
    svc.update(convert_state="idle", image_state="working")
    with pytest.raises(RuntimeError, match="huidige bewerking"):
        svc.request("sleep")
    svc.update(image_state="idle")
    svc.request("copy")
    with pytest.raises(RuntimeError, match="huidige bewerking"):
        svc.request("sleep")


def test_unsupported_sleep_does_not_break_service_and_disables_auto(svc, monkeypatch):
    def deny(device):
        raise drive.DriveUnsupportedError("Deze speler of usb-brug ondersteunt slapen niet.")

    monkeypatch.setattr(drive, "spin_down", deny)
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["drive_sleeping"] is False
    assert snapshot["drive_sleep_supported"] is False
    assert snapshot["state"] == "not_ready"
    assert "ondersteunt" in snapshot["drive_control_error"]
    svc._last_activity = time.monotonic() - 120
    assert svc._maybe_sleep() is False


def test_auto_sleep_after_idle_and_wake_resumes(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(drive, "spin_down", lambda device: calls.append("stop"))
    monkeypatch.setattr(drive, "spin_up", lambda device: calls.append("start"))
    monkeypatch.setattr(drive, "set_speed", lambda device, speed: calls.append(("speed", speed)))
    svc._last_activity = time.monotonic() - 61
    assert svc._maybe_sleep() is True
    assert svc.snapshot()["drive_sleeping"] is True
    assert "Automatische slaapstand" in svc.snapshot()["message"]
    svc.request("wake")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["drive_sleeping"] is False
    assert snapshot["state"] == "not_ready"
    assert calls == ["stop", "start"]


def test_idle_timer_resets_on_command_and_media_change(svc, monkeypatch):
    monkeypatch.setattr(drive, "spin_down", lambda device: None)
    svc._last_activity = time.monotonic() - 61
    svc.request("retry")
    assert svc._maybe_sleep() is False
    svc._last_activity = time.monotonic() - 61
    monkeypatch.setattr(drive, "media_changed", lambda device: True)
    svc._touch()
    assert svc._maybe_sleep() is False


def test_idle_zero_disables_auto_sleep(svc, monkeypatch):
    monkeypatch.setattr(drive, "spin_down", lambda device: None)
    svc.request_sleep_idle(0)
    svc._last_activity = time.monotonic() - 100000
    assert svc._maybe_sleep() is False
    assert svc.snapshot()["sleep_idle_seconds"] == 0


def test_wake_reapplies_saved_speed(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(drive, "spin_down", lambda device: None)
    monkeypatch.setattr(drive, "spin_up", lambda device: None)
    monkeypatch.setattr(drive, "set_speed", lambda device, speed: calls.append(speed))
    svc.request_speed(6)
    svc._command(svc.commands.get_nowait())
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    svc.request("wake")
    svc._command(svc.commands.get_nowait())
    assert calls == [6, 6]
    assert svc.snapshot()["drive_speed"] == 6


def test_speed_job_persists_and_reports_unsupported(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(drive, "set_speed", lambda device, speed: None)
    svc.request_speed(4)
    job = svc.commands.get_nowait()
    assert isinstance(job, service.DriveSpeedJob)
    svc._command(job)
    assert svc.snapshot()["drive_speed"] == 4
    assert svc.snapshot()["drive_speed_supported"] is True
    data = json.loads((svc.config.state_dir / "drive-settings.json").read_text())
    assert data["drive_speed"] == 4
    assert data["sleep_idle_seconds"] == 60

    def deny(device, speed):
        raise drive.DriveUnsupportedError("Snelheid niet ondersteund.")

    monkeypatch.setattr(drive, "set_speed", deny)
    svc.request_speed(8)
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["drive_speed_supported"] is False
    assert snapshot["drive_speed"] == 4
    assert "niet ondersteund" in snapshot["drive_control_error"]


def test_speed_while_sleeping_is_saved_without_waking(svc, monkeypatch):
    monkeypatch.setattr(drive, "spin_down", lambda device: None)

    def forbidden(*a, **k):
        raise AssertionError("must not touch sleeping drive")

    monkeypatch.setattr(drive, "set_speed", forbidden)
    monkeypatch.setattr(drive, "spin_up", forbidden)
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    svc.request_speed(3)
    assert svc.commands.empty()
    assert svc.snapshot()["drive_sleeping"] is True
    assert svc.snapshot()["drive_speed"] == 3


def test_settings_survive_restart(svc, monkeypatch):
    monkeypatch.setattr(drive, "set_speed", lambda device, speed: None)
    svc.request_speed(5)
    svc._command(svc.commands.get_nowait())
    svc.request_sleep_idle(300)
    restarted = service.Service(svc.config)
    assert restarted.snapshot()["drive_speed"] == 5
    assert restarted.snapshot()["sleep_idle_seconds"] == 300


def test_corrupt_settings_fall_back_to_env(svc):
    (svc.config.state_dir / "drive-settings.json").write_text("{broken", encoding="utf-8")
    restarted = service.Service(svc.config)
    assert restarted.snapshot()["drive_speed"] == 0
    assert restarted.snapshot()["sleep_idle_seconds"] == 60


def test_sleeping_media_lists_only_library(svc, monkeypatch):
    monkeypatch.setattr(drive, "spin_down", lambda device: None)

    def forbidden(*a, **k):
        raise AssertionError("must not inspect mounted disc while sleeping")

    monkeypatch.setattr(dvd, "inspect_source", forbidden)
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    assert svc.media()["sources"] == []


def test_eject_and_close_wake_sleeping_drive(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(drive, "spin_down", lambda device: None)
    monkeypatch.setattr(drive, "spin_up", lambda device: calls.append("start"))
    monkeypatch.setattr(drive, "eject", lambda device: calls.append("eject"))
    monkeypatch.setattr(drive, "close_tray", lambda device: calls.append("close"))
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    svc.request("eject")
    svc._command(svc.commands.get_nowait())
    assert svc.snapshot()["drive_sleeping"] is False
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    svc.request("close")
    svc._command(svc.commands.get_nowait())
    assert calls == ["start", "eject", "start", "close"]


def test_live_job_wakes_and_mounts(svc, monkeypatch):
    calls = []
    disc = drive.DiscInfo("identiteit", "TEST", "udf", 10000)
    monkeypatch.setattr(drive, "spin_down", lambda device: None)
    monkeypatch.setattr(drive, "spin_up", lambda device: calls.append("start"))

    def inspect(device):
        calls.append("inspect")
        return disc

    monkeypatch.setattr(drive, "inspect_disc", inspect)
    monkeypatch.setattr(drive, "mount_disc", lambda *a: calls.append("mount"))
    monkeypatch.setattr(
        copy,
        "import_disc",
        lambda *a, **k: copy.CopyResult(svc.config.library_dir / "TEST", 0, 0, 0),
    )
    svc.request("sleep")
    svc._command(svc.commands.get_nowait())
    svc.request("copy")
    svc._command(svc.commands.get_nowait())
    assert calls[:3] == ["start", "inspect", "mount"]
    assert svc.snapshot()["drive_sleeping"] is False
    assert svc.snapshot()["copy_state"] == "done"


def test_sleep_idle_validation(svc):
    for value in (30, -1, 86401, True, "60", 1.5):
        with pytest.raises(ValueError):
            svc.request_sleep_idle(value)
    for value in (-1, 25, True, "8", 1.5):
        with pytest.raises(ValueError):
            svc.request_speed(value)
