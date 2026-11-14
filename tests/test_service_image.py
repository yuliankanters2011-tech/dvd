import threading

import pytest

from app import copy, drive, image, service
from app.config import Config


@pytest.fixture()
def svc(tmp_path):
    cfg = Config(
        share_dir=tmp_path / "share",
        state_dir=tmp_path / "state",
        films_dir=tmp_path / "films",
        music_dir=tmp_path / "music",
        puid=0,
        pgid=0,
    )
    cfg.library_dir.mkdir(parents=True)
    cfg.state_dir.mkdir(parents=True)
    item = service.Service(cfg)
    item._mounted = True
    item._disc = drive.DiscInfo("identiteit", "TEST", "iso9660", 10000)
    item.update(state="ready", disc={"label": "TEST", "identity": "identiteit"})
    return item


@pytest.fixture(autouse=True)
def drive_present(monkeypatch):
    monkeypatch.setattr(drive, "drive_status", lambda device: "disc")
    monkeypatch.setattr(drive, "media_changed", lambda device: False)


def test_iso_uses_dedicated_stop_and_tolerance(svc, monkeypatch):
    seen = {}

    def fake_create(device, library, state, info, stop, progress, **kwargs):
        seen.update(stop=stop, kwargs=kwargs)
        progress(5000, 10000)
        return image.ImageResult(library / "TEST.iso", 10000, 0, 10000, True)

    monkeypatch.setattr(image, "create_iso", fake_create)
    svc.request("iso")
    svc._command(svc.commands.get_nowait())
    assert seen["stop"] is svc._image_stop
    assert seen["stop"] is not svc.stop
    assert seen["kwargs"]["max_missing_percent"] == svc.config.iso_max_missing_percent
    assert seen["kwargs"]["force"] is False
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "done"
    assert snapshot["image_percent"] == 100
    assert snapshot["image_complete"] is True


def test_heavy_damage_offers_force_then_saves(svc, monkeypatch):
    calls = []

    def fake_create(device, library, state, info, stop, progress, **kwargs):
        calls.append(kwargs)
        if not kwargs.get("force"):
            raise image.ImageIncompleteError(
                "sterk beschadigd",
                missing_bytes=2000,
                expected_bytes=10000,
                rescued_bytes=8000,
                allowed_bytes=50,
            )
        return image.ImageResult(library / "TEST.iso", 10000, 2000, 10000, False, True)

    monkeypatch.setattr(image, "create_iso", fake_create)
    svc.request("iso")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "error"
    assert snapshot["image_force_available"] is True
    assert snapshot["image_missing_bytes"] == 2000
    assert snapshot["state"] == "ready"

    svc.request("iso_force")
    job = svc.commands.get_nowait()
    assert isinstance(job, service.ImageJob) and job.force is True
    assert job.identity == "identiteit"
    svc._command(job)
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "done"
    assert snapshot["image_complete"] is False
    assert snapshot["image_forced"] is True
    assert snapshot["image_force_available"] is False
    assert calls[-1]["force"] is True


def test_force_rejected_without_prompt_or_for_other_disc(svc):
    with pytest.raises(RuntimeError, match="beschadigde ISO"):
        svc.request("iso_force")
    svc.update(image_state="error", image_force_available=True)
    svc.request("iso_force")
    job = svc.commands.get_nowait()
    svc._disc = drive.DiscInfo("anders", "ANDER", "iso9660", 10000)
    svc._command(job)
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "error"
    assert "veranderd" in snapshot["image_error"]


def test_cancel_iso_working_and_prompt(svc):
    svc.update(image_state="working")
    svc.request("cancel_iso")
    assert svc._image_stop.is_set()
    assert svc.snapshot()["image_cancel_requested"] is True
    assert svc._convert_stop.is_set() is False

    svc.update(image_state="error", image_force_available=True, image_cancel_requested=False)
    svc.request("cancel_iso")
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "cancelled"
    assert snapshot["image_force_available"] is False
    assert snapshot["image_resumable"] is True


def test_cancel_copy_uses_dedicated_stop(svc, monkeypatch):
    svc._disc = drive.DiscInfo("id", "TEST", "iso9660", 100)
    seen = {}

    def fake_import(source, library, info, puid, pgid, stop, progress):
        seen["stop"] = stop
        svc.request("cancel_copy")
        raise copy.CopyCancelledError("Kopiëren geannuleerd")

    monkeypatch.setattr(copy, "import_disc", fake_import)
    svc.request("copy")
    job = svc.commands.get_nowait()
    svc._command(job)
    assert seen["stop"] is svc._copy_stop
    assert svc._copy_stop.is_set()
    snapshot = svc.snapshot()
    assert snapshot["copy_state"] == "cancelled"
    assert snapshot["state"] == "ready"


def test_pending_iso_can_be_cancelled_before_worker_starts(svc):
    svc.request("iso")
    svc.request("cancel_iso")
    assert svc._image_stop.is_set()
    with pytest.raises(RuntimeError, match="al een ISO"):
        svc.request("iso")


def test_idle_cancel_commands_rejected(svc):
    for command in ("cancel_iso", "cancel_copy"):
        with pytest.raises(RuntimeError, match="annuleren"):
            svc.request(command)


def test_shutdown_sets_all_stop_events(svc, monkeypatch):
    monkeypatch.setattr(svc._worker, "join", lambda timeout=None: None)
    svc.shutdown()
    assert svc.stop.is_set()
    assert svc._convert_stop.is_set()
    assert svc._image_stop.is_set()
    assert svc._copy_stop.is_set()


def test_clear_disc_resets_image_damage_fields(svc, monkeypatch):
    monkeypatch.setattr(drive, "unmount", lambda target: None)
    monkeypatch.setattr(drive, "unlock_tray", lambda device: None)
    svc.update(
        image_state="error",
        image_force_available=True,
        image_missing_bytes=2000,
        image_error="schade",
    )
    svc._clear_disc("empty", "Geen schijf")
    snapshot = svc.snapshot()
    assert snapshot["image_state"] == "idle"
    assert snapshot["image_force_available"] is False
    assert snapshot["image_missing_bytes"] == 0
    assert snapshot["image_error"] == ""


def test_copy_cancelled_error_is_not_disc_failure(svc, monkeypatch):
    monkeypatch.setattr(
        copy,
        "import_disc",
        lambda *a, **k: (_ for _ in ()).throw(copy.CopyCancelledError("Kopiëren geannuleerd")),
    )
    svc.request("copy")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["copy_state"] == "cancelled"
    assert snapshot["state"] == "ready"
    assert snapshot["error"] == ""


def test_image_stop_event_is_threading_event(svc):
    assert isinstance(svc._image_stop, threading.Event)
    assert isinstance(svc._copy_stop, threading.Event)
